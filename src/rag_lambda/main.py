"""Lambda handler for RAG chat application."""

import argparse
import json
import os
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from .graph.nodes import answer_node, clarify_node, extract_text_content, rewrite_node, split_node
from .graph.state import MessagesState
from .graph.retrieval import retrieve_node
from .memory.factory import create_history_store
from .memory.chat_summary import summarization_check
from .api.models import ChatRequest, ChatResponse, Source
from .auth import (
    AuthConfigurationError,
    AuthError,
    authenticate_request,
    extract_http_metadata,
    json_response,
    normalize_headers,
    parse_event_body,
    prepare_body_for_main,
    scope_conversation_id,
)
from utils.aws_utils import get_db_credentials_from_secret
from utils.config import read_config
from utils.logger import get_logger

log = get_logger(__name__)


def build_rag_graph():
    """Build and compile the RAG LangGraph with query pipeline enhancements."""
    graph = StateGraph(MessagesState)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("split", split_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("answer", answer_node)
    graph.set_entry_point("rewrite")
    graph.add_edge("rewrite", "clarify")
    graph.add_edge("clarify", "split")
    graph.add_edge("split", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def main(event_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main function to handle chat requests.

    Args:
        event_body: Parsed event body dictionary

    Returns:
        HTTP response with status code, headers, and body
    """
    # Load configuration
    config_path = os.getenv("APP_CONFIG_PATH", "config/app_config.yml")
    log.info(f"Loading configuration from: {config_path}")
    
    config = read_config(config_path)
    rag_chat_config = config.get("rag_chat", {})
    chat_history_config = rag_chat_config.get("chat_history_store", {})
    summarization_config = rag_chat_config.get("summarization", {})
    retrieval_config = rag_chat_config.get("retrieval", {})
    log.info("Configuration loaded successfully")

    # Create request model
    req = ChatRequest(**event_body)
    log.info(f"Processing chat request for conversation_id: {req.conversation_id}, user_id: {req.user_id}")

    # Identity/trace values are prepared by lambda_handler before invoking main().
    effective_user_id = req.user_id or "anonymous"
    storage_conversation_id = scope_conversation_id(effective_user_id, req.conversation_id)
    trace_id = event_body.get("trace_id") if isinstance(event_body.get("trace_id"), str) else None

    # Extract memory backend type and prepare configuration
    # All other keys in chat_history_config will be passed directly to the factory
    memory_backend_type = chat_history_config.pop("memory_backend_type")
    
    # Get region from retrieval config for postgres backend (if needed)
    region = retrieval_config.get("region", "us-east-1")
    
    # For postgres backend, we need to fetch credentials from Secrets Manager
    if memory_backend_type == "postgres":
        db_connection_secret_name = chat_history_config.get("db_connection_secret_name")
        if not db_connection_secret_name:
            raise ValueError("db_connection_secret_name is required for postgres backend")
        
        db_creds = get_db_credentials_from_secret(db_connection_secret_name, region=region)
        log.info(f"Retrieved database credentials from secret: {db_connection_secret_name}")
        chat_history_config["db_creds"] = db_creds
    
    # For aurora_data_api backend, add region if not already specified
    elif memory_backend_type == "aurora_data_api":
        if "region" not in chat_history_config:
            chat_history_config["region"] = region
        log.info(f"Using Aurora Data API with cluster ARN: {chat_history_config.get('db_cluster_arn')}")
    
    # Create memory store - pass all remaining config keys directly
    log.info(f"Creating memory store with backend type: {memory_backend_type}")
    memory_store = create_history_store(
        memory_backend_type=memory_backend_type,
        **chat_history_config
    )

    # Load prior messages
    log.info(f"Loading prior messages for conversation_id: {req.conversation_id}")
    prior_messages: List[BaseMessage] = memory_store.get_messages(storage_conversation_id)
    log.info(f"Loaded {len(prior_messages)} prior messages from conversation history")

    # Check if summarization is needed for long conversations
    log.info(f"Checking if summarization is needed (threshold: {summarization_config.get('summarization_threshold')})")
    prior_messages = summarization_check(
        messages=prior_messages,
        summarization_threshold=summarization_config.get("summarization_threshold"),
        summarization_model_config=summarization_config.get("model"),
    )
    log.info(f"After summarization check: {len(prior_messages)} messages in conversation history")

    # Initial state with prior messages and new user message
    state = {
        "messages": prior_messages + [HumanMessage(content=req.message)],
        "retrieval_config": retrieval_config,
    }
    
    # Add retrieval filters to state if provided
    if req.retrieval_filters:
        state["retrieval_filters"] = req.retrieval_filters
        log.info(f"Retrieval filters applied: {req.retrieval_filters}")

    # Build graph
    log.info("Building RAG graph")
    graph = build_rag_graph()

    # Prepare config for graph invocation (LangGraph expects config in "configurable" key)
    graph_config = {
        "configurable": {
            "rag_chat": rag_chat_config,
        }
    }

    # Invoke graph with config
    log.info("Invoking RAG graph (includes query rewrite, clarification, splitting, retrieval, and answer generation)")
    final_state = graph.invoke(state, config=graph_config)
    log.info("RAG graph execution completed")

    # Extract new messages (everything after prior_messages + user message)
    new_messages = final_state["messages"][len(prior_messages) + 1 :]

    # Prepare metadata with retrieval_filters if they were used
    metadata = None
    if req.retrieval_filters:
        metadata = {"retrieval_filters": req.retrieval_filters}

    # Append new messages to memory store
    if new_messages:
        memory_store.append_messages(storage_conversation_id, new_messages, metadata=metadata)

    # Extract answer from final state
    ai_msgs = [m for m in final_state["messages"] if m.type == "ai"]
    answer = extract_text_content(ai_msgs[-1].content) if ai_msgs else ""
    log.info(f"Extracted answer (length: {len(answer)} characters)")

    # Extract sources from final state
    sources = []
    if "sources" in final_state:
        for source_dict in final_state["sources"]:
            sources.append(
                Source(
                    document_id=source_dict.get("document_id", "unknown"),
                    source_type=source_dict.get("source_type", "document"),
                    score=source_dict.get("score", 0.0),
                    chunk=source_dict.get("chunk", ""),
                )
            )
        log.info(f"Retrieved {len(sources)} sources from knowledge base")
    else:
        log.info("No sources found in final state")

    resp = ChatResponse(
        conversation_id=req.conversation_id,
        answer=answer,
        sources=sources,
        trace_id=trace_id,
    )

    log.info(f"Response completed for conversation_id: {req.conversation_id} with {len(sources)} sources")
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": resp.model_dump_json(exclude_none=True),
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler for chat requests.

    Args:
        event: Lambda event containing request body
        context: Lambda context object

    Returns:
        HTTP response with status code, headers, and body
    """
    event = event if isinstance(event, dict) else {}
    headers = normalize_headers(event.get("headers"))
    method, path, trace_id = extract_http_metadata(event)

    # Handle HTTP requests while preserving support for direct Lambda invocation.
    if method:
        method = method.upper()
        if method == "OPTIONS":
            return json_response(200, {})
        if method != "POST":
            return json_response(405, {"error": "MethodNotAllowed", "detail": "Only POST is supported."})
        if path not in {"/", "/chat"}:
            return json_response(404, {"error": "NotFound", "detail": "Path not found."})

    try:
        body = parse_event_body(event)
    except (TypeError, ValueError) as exc:
        return json_response(400, {"error": "BadRequest", "detail": str(exc)})

    fallback_user_id = body.get("user_id") if isinstance(body, dict) else None
    fallback_user_id = fallback_user_id if isinstance(fallback_user_id, str) else None

    try:
        auth_context = authenticate_request(headers, fallback_user_id=fallback_user_id)
        prepared_body = prepare_body_for_main(body, auth_context.user_id, trace_id)
        return main(prepared_body)
    except ValidationError as exc:
        return json_response(400, {"error": "ValidationError", "detail": str(exc)})
    except AuthError as exc:
        log.warning("Authentication failed: %s", exc.code)
        return json_response(exc.status_code, {"error": exc.code, "detail": exc.detail})
    except AuthConfigurationError as exc:
        log.error("Authentication configuration error: %s", str(exc))
        return json_response(
            500,
            {
                "error": "AuthConfigurationError",
                "detail": "Server auth configuration is invalid.",
            },
        )
    except ValueError as exc:
        return json_response(400, {"error": "BadRequest", "detail": str(exc)})
    except Exception:
        log.exception("Unhandled error while processing request.")
        return json_response(
            500,
            {
                "error": "InternalServerError",
                "detail": "An unexpected error occurred.",
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="RAG chat application - process chat requests"
    )
    parser.add_argument(
        "--event-body",
        type=str,
        required=True,
        help="JSON string containing the event body (e.g., '{\"conversation_id\": \"123\", \"user_id\": \"user1\", \"message\": \"Hello\"}')",
    )
    args = parser.parse_args()
    # Parse the event_body JSON string
    event_body = json.loads(args.event_body)

    # Call main function
    main(event_body)
