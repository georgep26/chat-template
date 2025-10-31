"""
Lambda handler and core LangChain orchestration for RAG application.
Combines Lambda entrypoint, LangChain chain building, and CLI support.
"""
import json
import argparse
from operator import itemgetter
from typing import Dict, List, Optional
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.documents import Document

from .prompt_config import SYSTEM, PROMPT, llm
from .retrieval import make_retriever, docs_to_context, docs_to_citations
from .history_store import get_history


def build_chain(user_roles: List[str], ui_filters: Dict):
    """
    Build the end-to-end chain:
      question -> retrieve -> inject context -> prompt -> llm
    
    Args:
        user_roles: List of user role strings for RBAC filtering
        ui_filters: Dictionary of UI-specified filters
    
    Returns:
        RunnableWithMessageHistory chain configured for the user's permissions
    """
    retriever = make_retriever(user_roles, ui_filters)

    base_chain = (
        # Attach docs to the state
        RunnablePassthrough.assign(docs=itemgetter("question") | retriever)
        # Build "context" string from docs
        | RunnablePassthrough.assign(context=lambda x: docs_to_context(x["docs"]))
        # Add system prompt
        | RunnablePassthrough.assign(system=lambda _: SYSTEM)
        # Render full prompt
        | PROMPT
        # Send to Bedrock model
        | llm
    )

    # Add message history wrapper so conversation carries forward
    # - input_messages_key: what field on invoke() is treated as the "new user message"
    # - history_messages_key: where we inject conversation history text
    chain_with_history = RunnableWithMessageHistory(
        base_chain,
        get_history,
        input_messages_key="question",
        history_messages_key="chat_history"
    )

    return chain_with_history


def handle_turn(
    conversation_id: str,
    question: str,
    user_roles: List[str],
    ui_filters: Dict,
    chat_history_hint: str = ""
) -> Dict:
    """
    Run one turn of the conversation:
    - generate answer via chain_with_history
    - create citations from retrieved docs
    
    Args:
        conversation_id: Unique identifier for the conversation session
        question: User's question
        user_roles: List of user role strings for RBAC filtering
        ui_filters: Dictionary of UI-specified filters
        chat_history_hint: Optional pre-existing chat history string
    
    Returns:
        Dictionary with 'answer' and 'citations' keys
    """
    chain = build_chain(user_roles, ui_filters)

    # Step 1: run retrieval again here so we can return docs for citations.
    # NOTE: If you don't want double retrieval, you can refactor build_chain
    # to return both the llm output and docs. For clarity, we keep it simple first.
    docs = make_retriever(user_roles, ui_filters).invoke(question)
    citations = docs_to_citations(docs)
    context_str = docs_to_context(docs)

    # Step 2: invoke chain (this writes / reads message history in Postgres automatically)
    out_msg = chain.invoke(
        {
            "question": question,
            "chat_history": chat_history_hint,
            "context": context_str,
            "system": SYSTEM,
        },
        config={"configurable": {"session_id": conversation_id}}
    )

    # Extract answer text from the message
    answer_text = (
        out_msg.content[0].text
        if hasattr(out_msg, "content") and hasattr(out_msg.content[0], "text")
        else str(out_msg.content) if hasattr(out_msg, "content")
        else str(out_msg)
    )

    return {
        "answer": answer_text,
        "citations": citations
    }


def main(
    conversation_id: str,
    question: str,
    user_roles: Optional[List[str]] = None,
    ui_filters: Optional[Dict] = None,
    user_id: Optional[str] = None,
    chat_history_hint: Optional[str] = None
) -> Dict:
    """
    Main function for handling a chat turn.
    Can be called from Lambda handler or CLI.
    
    Args:
        conversation_id: Unique identifier for the conversation session
        question: User's question
        user_roles: List of user role strings (default: [])
        ui_filters: Dictionary of UI-specified filters (default: {})
        user_id: Optional user identifier for logging
        chat_history_hint: Optional pre-existing chat history string
    
    Returns:
        Dictionary with 'answer' and 'citations' keys
    """
    user_roles = user_roles or []
    ui_filters = ui_filters or {}
    chat_history_hint = chat_history_hint or ""
    
    result = handle_turn(
        conversation_id=conversation_id,
        question=question,
        user_roles=user_roles,
        ui_filters=ui_filters,
        chat_history_hint=chat_history_hint
    )
    
    # Optional: explicit logging to an audit table separate from LangChain's internal table
    # from .db import log_message
    # log_message(conversation_id, "user", question, filters={**ui_filters, "roles": user_roles, "user": user_id})
    # log_message(conversation_id, "assistant", result["answer"], citations=result["citations"])
    
    return result


def lambda_handler(event, _ctx):
    """
    Lambda handler entrypoint for AWS Lambda.
    
    Args:
        event: Lambda event dictionary (may contain 'body' if from API Gateway)
        _ctx: Lambda context (unused)
    
    Returns:
        Lambda response dictionary with statusCode, headers, and body
    """
    # Support both direct invoke and API Gateway HTTP API
    raw_body = event.get("body", event)
    payload = json.loads(raw_body) if isinstance(raw_body, str) else raw_body

    conversation_id = payload["conversation_id"]
    question = payload["question"]
    user_roles = payload.get("user_roles", [])
    ui_filters = payload.get("ui_filters", {})
    chat_history = payload.get("chat_history_hint", "")
    user_id = payload.get("user_id", "anon")

    # Call main app logic
    try:
        result = main(
            conversation_id=conversation_id,
            question=question,
            user_roles=user_roles,
            ui_filters=ui_filters,
            user_id=user_id,
            chat_history_hint=chat_history
        )
        
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(result)
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"error": str(e)})
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Chat Application - CLI Mode")
    parser.add_argument(
        "--conversation-id",
        required=True,
        help="Unique identifier for the conversation session"
    )
    parser.add_argument(
        "--question",
        required=True,
        help="User's question"
    )
    parser.add_argument(
        "--user-roles",
        default="",
        help="Comma-separated list of user roles (e.g., 'Finance,HR')"
    )
    parser.add_argument(
        "--ui-filters",
        default="{}",
        help="JSON string of UI filters (e.g., '{\"document_type\": \"Policy\", \"version\": \"2023.4\"}')"
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="User identifier (optional)"
    )
    parser.add_argument(
        "--chat-history-hint",
        default="",
        help="Pre-existing chat history string (optional)"
    )
    
    args = parser.parse_args()
    
    # Parse user_roles from comma-separated string
    user_roles = [r.strip() for r in args.user_roles.split(",")] if args.user_roles else []
    
    # Parse ui_filters from JSON string
    try:
        ui_filters = json.loads(args.ui_filters) if args.ui_filters else {}
    except json.JSONDecodeError:
        print(f"Warning: Invalid JSON in --ui-filters, using empty dict: {args.ui_filters}")
        ui_filters = {}
    
    # Call main function
    result = main(
        conversation_id=args.conversation_id,
        question=args.question,
        user_roles=user_roles,
        ui_filters=ui_filters,
        user_id=args.user_id,
        chat_history_hint=args.chat_history_hint
    )
    
    # Print result as JSON
    print(json.dumps(result, indent=2))

