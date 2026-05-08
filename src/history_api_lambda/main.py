"""Lambda handler for the history API (list, get-messages, rename, delete)."""

import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage
from pydantic import ValidationError

from auth import (
    AuthConfigurationError,
    AuthError,
    authenticate_request,
    extract_http_metadata,
    json_response,
    normalize_headers,
    parse_event_body,
)
from memory.base import ChatHistoryStore, ConversationAccessError, ConversationRecord
from memory.factory import create_history_store
from utils.aws_utils import get_db_credentials_from_secret
from utils.config import read_config
from utils.logger import get_logger

from .api.models import (
    ConversationSummary,
    DeleteResponse,
    GetMessagesResponse,
    ListConversationsResponse,
    MessageRecord,
    RenameRequest,
    RenameResponse,
)

log = get_logger(__name__)


# Cache the store across warm invocations.
_STORE: Optional[ChatHistoryStore] = None


def _build_store() -> ChatHistoryStore:
    """Construct the chat history store from app config."""
    config_path = os.getenv("APP_CONFIG_PATH", "config/app_config.yml")
    config = read_config(config_path)
    rag_chat_config = config.get("rag_chat", {})
    chat_history_config = dict(rag_chat_config.get("chat_history_store", {}))
    retrieval_config = rag_chat_config.get("retrieval", {})

    memory_backend_type = chat_history_config.pop("memory_backend_type")
    region = retrieval_config.get("region", "us-east-1")

    if memory_backend_type == "postgres":
        secret_name = chat_history_config.get("db_connection_secret_name")
        if not secret_name:
            raise ValueError("db_connection_secret_name is required for postgres backend")
        chat_history_config["db_creds"] = get_db_credentials_from_secret(secret_name, region=region)
    elif memory_backend_type == "aurora_data_api":
        if "region" not in chat_history_config:
            chat_history_config["region"] = region

    return create_history_store(memory_backend_type=memory_backend_type, **chat_history_config)


def _get_store() -> ChatHistoryStore:
    global _STORE
    if _STORE is None:
        _STORE = _build_store()
    return _STORE


def _conversation_summary(record: ConversationRecord) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=record.conversation_id,
        title=record.title,
        status=record.status,
        message_count=record.message_count,
        created_at=record.created_at,
        last_message_at=record.last_message_at,
    )


# LangChain message type → frontend role.
_ROLE_BY_TYPE = {"human": "user", "ai": "assistant", "system": "system"}


def _message_record(message: BaseMessage) -> Optional[MessageRecord]:
    role = _ROLE_BY_TYPE.get(message.type)
    if role is None:
        return None
    content = message.content
    if isinstance(content, list):
        # LangChain occasionally wraps content as list of parts; concatenate text segments.
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        content = "".join(parts)
    if not isinstance(content, str):
        content = str(content)
    return MessageRecord(role=role, content=content)


def _parse_int(value: Optional[str], *, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid integer value: {value!r}")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Value out of range [{minimum}, {maximum}]: {parsed}")
    return parsed


_CONV_PATTERN = re.compile(r"^/conversations/([^/]+)(?:/(messages|rename))?$")


def _route(method: str, path: str) -> Optional[Dict[str, Any]]:
    """Match method+path to a route descriptor or return None for 404."""
    if path == "/conversations" and method == "GET":
        return {"name": "list"}
    match = _CONV_PATTERN.match(path)
    if not match:
        return None
    conversation_id, suffix = match.group(1), match.group(2)
    if suffix == "messages" and method == "GET":
        return {"name": "get_messages", "conversation_id": conversation_id}
    if suffix == "rename" and method == "POST":
        return {"name": "rename", "conversation_id": conversation_id}
    if suffix is None and method == "DELETE":
        return {"name": "delete", "conversation_id": conversation_id}
    return None


def _handle_list(event: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    if not isinstance(qs, dict):
        qs = {}
    limit = _parse_int(qs.get("limit"), default=50, minimum=1, maximum=200)
    offset = _parse_int(qs.get("offset"), default=0, minimum=0, maximum=10_000)

    store = _get_store()
    records = store.list_conversations(user_id=user_id, limit=limit, offset=offset)
    summaries = [_conversation_summary(r) for r in records]
    next_offset = offset + limit if len(records) == limit else None
    response = ListConversationsResponse(conversations=summaries, next_offset=next_offset)
    return json_response(200, response.model_dump())


def _handle_get_messages(conversation_id: str, user_id: str) -> Dict[str, Any]:
    store = _get_store()
    messages: List[BaseMessage] = store.get_messages_for_user(
        conversation_id=conversation_id,
        user_id=user_id,
    )
    records = [r for r in (_message_record(m) for m in messages) if r is not None]
    response = GetMessagesResponse(conversation_id=conversation_id, messages=records)
    return json_response(200, response.model_dump())


def _handle_rename(conversation_id: str, user_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    rename_req = RenameRequest(**body)
    store = _get_store()
    record = store.rename_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
        title=rename_req.title,
    )
    response = RenameResponse(conversation=_conversation_summary(record))
    return json_response(200, response.model_dump())


def _handle_delete(conversation_id: str, user_id: str) -> Dict[str, Any]:
    store = _get_store()
    store.delete_conversation(conversation_id=conversation_id, user_id=user_id)
    response = DeleteResponse(conversation_id=conversation_id, deleted=True)
    return json_response(200, response.model_dump())


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler for history API requests."""
    event = event if isinstance(event, dict) else {}
    headers = normalize_headers(event.get("headers"))
    method, path, _trace_id = extract_http_metadata(event)

    if not method:
        return json_response(400, {"error": "BadRequest", "detail": "Missing HTTP method."})

    method = method.upper()
    if method == "OPTIONS":
        return json_response(204, {})

    route = _route(method, path or "/")
    if route is None:
        return json_response(404, {"error": "NotFound", "detail": "Path not found."})

    try:
        auth_context = authenticate_request(headers)
    except AuthError as exc:
        log.warning("Authentication failed: %s", exc.code)
        return json_response(exc.status_code, {"error": exc.code, "detail": exc.detail})
    except AuthConfigurationError as exc:
        log.error("Authentication configuration error: %s", str(exc))
        return json_response(
            500,
            {"error": "AuthConfigurationError", "detail": "Server auth configuration is invalid."},
        )

    user_id = auth_context.user_id

    try:
        if route["name"] == "list":
            return _handle_list(event, user_id)
        if route["name"] == "get_messages":
            return _handle_get_messages(route["conversation_id"], user_id)
        if route["name"] == "rename":
            try:
                body = parse_event_body(event)
            except (TypeError, ValueError) as exc:
                return json_response(400, {"error": "BadRequest", "detail": str(exc)})
            return _handle_rename(route["conversation_id"], user_id, body)
        if route["name"] == "delete":
            return _handle_delete(route["conversation_id"], user_id)
        return json_response(404, {"error": "NotFound", "detail": "Path not found."})
    except ValidationError as exc:
        return json_response(400, {"error": "ValidationError", "detail": str(exc)})
    except ConversationAccessError as exc:
        return json_response(404, {"error": "ConversationNotFound", "detail": str(exc)})
    except ValueError as exc:
        return json_response(400, {"error": "BadRequest", "detail": str(exc)})
    except Exception:
        log.exception("Unhandled error while processing history API request.")
        return json_response(
            500,
            {"error": "InternalServerError", "detail": "An unexpected error occurred."},
        )
