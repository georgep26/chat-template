"""Lambda handler for RAG chat application."""

import json
import logging
from typing import Any, Dict

from rag_app.api.chat_service import handle_chat
from rag_app.api.models import ChatRequest

logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler for chat requests.

    Args:
        event: Lambda event containing request body
        context: Lambda context object

    Returns:
        HTTP response with status code, headers, and body
    """
    try:
        # Parse request body
        body = event.get("body")
        if isinstance(body, str):
            body = json.loads(body)

        # Create request model
        req = ChatRequest(**body)

        # Handle chat request
        resp = handle_chat(req)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": resp.model_dump_json(),
        }
    except Exception as e:
        logger.error(f"Error processing chat request: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }
