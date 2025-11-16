"""Lambda handler for RAG chat application."""

import argparse
import json
import os
from typing import Any, Dict, List

import yaml
from langchain_core.messages import BaseMessage, HumanMessage

from graph.graph import build_rag_graph
from memory.factory import create_history_store
from memory.summary import summarize_messages
from api.models import ChatRequest, ChatResponse, Source
from utils.logger import get_logger

logger = get_logger(__name__)

# Build graph once at module level
graph = build_rag_graph()

# Load configuration for summarization threshold
_config_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "..",
    "config",
    "app_config.yml",
)
_config_path = os.path.abspath(_config_path)

with open(_config_path, "r") as f:
    _config = yaml.safe_load(f)

_summarization_threshold = _config.get("rag", {}).get("memory", {}).get("summarization_threshold", 20)


def main(event_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main function to handle chat requests.

    Args:
        event_body: Parsed event body dictionary

    Returns:
        HTTP response with status code, headers, and body
    """
    # Create request model
    req = ChatRequest(**event_body)

    # Initialize memory store
    memory_store = create_history_store()

    # Load prior messages
    prior_messages: List[BaseMessage] = memory_store.get_messages(req.conversation_id)

    # Check if summarization is needed
    if len(prior_messages) > _summarization_threshold:
        # Summarize older messages, keep recent ones
        recent_messages = prior_messages[-10:]  # Keep last 10 messages
        older_messages = prior_messages[:-10]
        summary = summarize_messages(older_messages)
        prior_messages = [summary] + recent_messages

    # Initial state with prior messages and new user message
    state = {
        "messages": prior_messages + [HumanMessage(content=req.message)],
    }

    # Invoke graph
    final_state = graph.invoke(state)

    # Extract new messages (everything after prior_messages + user message)
    new_messages = final_state["messages"][len(prior_messages) + 1 :]

    # Append new messages to memory store
    if new_messages:
        memory_store.append_messages(req.conversation_id, new_messages)

    # Extract answer from final state
    ai_msgs = [m for m in final_state["messages"] if m.type == "ai"]
    answer = ai_msgs[-1].content if ai_msgs else ""

    # Extract sources from final state
    sources = []
    if "sources" in final_state:
        for source_dict in final_state["sources"]:
            sources.append(
                Source(
                    document_id=source_dict.get("document_id", "unknown"),
                    source_type=source_dict.get("source_type", "document"),
                    score=source_dict.get("score", 0.0),
                    snippet=source_dict.get("snippet", ""),
                )
            )

    resp = ChatResponse(
        conversation_id=req.conversation_id,
        answer=answer,
        sources=sources,
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": resp.model_dump_json(),
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
    # Parse request body
    body = event.get("body")
    if isinstance(body, str):
        body = json.loads(body)

    # Call main function
    return main(body)


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
