"""HTTP/Lambda event parsing helpers.

These helpers keep API-specific parsing/response logic out of the core RAG workflow.
"""

import json
from typing import Any, Dict, Optional, Tuple


def json_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build a standard JSON HTTP response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def normalize_headers(headers: Any) -> Dict[str, str]:
    """Normalize incoming headers to lowercase keys."""
    if not isinstance(headers, dict):
        return {}

    normalized: Dict[str, str] = {}
    for key, value in headers.items():
        if key is None or value is None:
            continue
        normalized[str(key).lower()] = str(value)

    return normalized


def extract_http_metadata(event: Dict[str, Any]) -> Tuple[Optional[str], str, Optional[str]]:
    """Extract method/path/request-id from API-style events."""
    request_context = event.get("requestContext") if isinstance(event, dict) else {}
    request_context = request_context if isinstance(request_context, dict) else {}

    http_data = request_context.get("http") if isinstance(request_context.get("http"), dict) else {}
    method = http_data.get("method")
    if not method and isinstance(event.get("httpMethod"), str):
        method = event.get("httpMethod")

    path = event.get("rawPath") or event.get("path") or "/"
    trace_id = request_context.get("requestId")
    return method, path, trace_id


def parse_event_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse request body for direct Lambda and HTTP invocations."""
    body = event.get("body")
    if body is None:
        if not isinstance(event, dict):
            raise ValueError("Request event must be a JSON object.")
        return dict(event)

    if isinstance(body, str):
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    if isinstance(body, dict):
        return dict(body)

    raise ValueError("Request body must be a JSON object.")


def prepare_body_for_main(event_body: Dict[str, Any], auth_user_id: str, trace_id: Optional[str]) -> Dict[str, Any]:
    """Inject authenticated identity and trace metadata for main().

    When auth is used, the token is the source of truth: body["user_id"] is always
    set to the authenticated user. Any client-sent user_id is overwritten (deprecated).
    """
    body = dict(event_body)
    body["user_id"] = auth_user_id
    if trace_id:
        body["trace_id"] = trace_id

    return body
