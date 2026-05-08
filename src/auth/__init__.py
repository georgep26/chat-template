"""Authentication utilities for RAG Lambda."""

from .cognito_jwt import (
    AuthConfigurationError,
    AuthContext,
    AuthError,
    authenticate_request,
)
from .http_event import (
    extract_http_metadata,
    json_response,
    normalize_headers,
    parse_event_body,
    prepare_body_for_main,
)

__all__ = [
    "AuthConfigurationError",
    "AuthContext",
    "AuthError",
    "authenticate_request",
    "extract_http_metadata",
    "json_response",
    "normalize_headers",
    "parse_event_body",
    "prepare_body_for_main",
]
