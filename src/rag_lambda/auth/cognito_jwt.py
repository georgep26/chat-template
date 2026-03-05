"""Cognito JWT authentication helpers for Lambda requests."""

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import urlopen

import jwt
from jwt import ExpiredSignatureError, InvalidIssuerError, InvalidSignatureError, PyJWTError
from jwt.algorithms import RSAAlgorithm

from utils.logger import get_logger

log = get_logger(__name__)

_JWKS_CACHE: Dict[str, Dict[str, Any]] = {}
_JWKS_CACHE_LOCK = threading.Lock()
_DEFAULT_JWKS_CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context."""

    user_id: str
    claims: Dict[str, Any]


class AuthError(Exception):
    """Raised when request authentication fails."""

    def __init__(self, code: str, detail: str, status_code: int = 401):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class AuthConfigurationError(Exception):
    """Raised when auth is enabled but server-side configuration is invalid."""


@dataclass(frozen=True)
class CognitoConfig:
    """Cognito validation settings loaded from environment."""

    region: str
    user_pool_id: str
    app_client_id: str
    issuer: str
    jwks_cache_ttl_seconds: int


def scope_conversation_id(user_id: str, conversation_id: str) -> str:
    """Create a tenant-scoped conversation id to prevent cross-user collisions."""
    return f"{user_id}:{conversation_id}"


def _parse_bool(raw_value: Optional[str], default_value: bool) -> bool:
    """Parse bool-like strings. Empty/None returns the provided default."""
    if raw_value is None:
        return default_value

    value = raw_value.strip().lower()
    if value == "":
        return default_value
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False

    raise AuthConfigurationError(f"Invalid boolean value: '{raw_value}'")


def _auth_required() -> bool:
    """Return whether auth is required for this environment."""
    default_required = True
    return _parse_bool(os.getenv("AUTH_REQUIRED"), default_required)


def _extract_bearer_token(headers: Optional[Dict[str, Any]]) -> str:
    """Extract bearer token from authorization header."""
    if not isinstance(headers, dict):
        raise AuthError("MissingAuthorization", "Authorization header is required.")

    auth_header = headers.get("authorization") or headers.get("Authorization")
    if not auth_header:
        raise AuthError("MissingAuthorization", "Authorization header is required.")

    parts = str(auth_header).split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError(
            "InvalidAuthorizationHeader",
            "Authorization header must be in the form 'Bearer <token>'.",
        )

    return parts[1].strip()


def _load_cognito_config() -> CognitoConfig:
    """Load Cognito settings from environment for token verification."""
    region = (os.getenv("COGNITO_REGION") or os.getenv("AWS_REGION") or "").strip()
    user_pool_id = (os.getenv("COGNITO_USER_POOL_ID") or "").strip()
    app_client_id = (os.getenv("COGNITO_APP_CLIENT_ID") or "").strip()

    if not region:
        raise AuthConfigurationError("COGNITO_REGION (or AWS_REGION) must be set when auth is enabled.")
    if not user_pool_id:
        raise AuthConfigurationError("COGNITO_USER_POOL_ID must be set when auth is enabled.")
    if not app_client_id:
        raise AuthConfigurationError("COGNITO_APP_CLIENT_ID must be set when auth is enabled.")

    ttl_raw = (os.getenv("COGNITO_JWKS_CACHE_TTL_SECONDS") or str(_DEFAULT_JWKS_CACHE_TTL_SECONDS)).strip()
    try:
        ttl_seconds = int(ttl_raw)
    except ValueError as exc:
        raise AuthConfigurationError("COGNITO_JWKS_CACHE_TTL_SECONDS must be an integer.") from exc

    if ttl_seconds <= 0:
        raise AuthConfigurationError("COGNITO_JWKS_CACHE_TTL_SECONDS must be greater than zero.")

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

    return CognitoConfig(
        region=region,
        user_pool_id=user_pool_id,
        app_client_id=app_client_id,
        issuer=issuer,
        jwks_cache_ttl_seconds=ttl_seconds,
    )


def _fetch_jwks(issuer: str) -> Dict[str, Any]:
    """Fetch JWKS for a Cognito issuer."""
    jwks_url = f"{issuer}/.well-known/jwks.json"
    try:
        with urlopen(jwks_url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AuthConfigurationError(f"Unable to fetch Cognito JWKS from {jwks_url}") from exc

    if "keys" not in payload or not isinstance(payload["keys"], list):
        raise AuthConfigurationError("Cognito JWKS payload is missing keys.")

    keys_by_kid: Dict[str, Any] = {}
    for jwk in payload["keys"]:
        kid = jwk.get("kid")
        if not kid:
            continue
        keys_by_kid[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))

    if not keys_by_kid:
        raise AuthConfigurationError("Cognito JWKS did not return usable signing keys.")

    return keys_by_kid


def _get_cached_jwks(issuer: str, ttl_seconds: int, force_refresh: bool = False) -> Dict[str, Any]:
    """Get JWKS from cache, fetching from Cognito when cache is stale."""
    cache_key = f"{issuer}/.well-known/jwks.json"
    now = time.time()

    with _JWKS_CACHE_LOCK:
        entry = _JWKS_CACHE.get(cache_key)

    if not force_refresh and entry and entry.get("expires_at", 0) > now:
        return entry["keys_by_kid"]

    keys_by_kid = _fetch_jwks(issuer)
    with _JWKS_CACHE_LOCK:
        _JWKS_CACHE[cache_key] = {
            "keys_by_kid": keys_by_kid,
            "expires_at": now + ttl_seconds,
        }

    return keys_by_kid


def _get_signing_key(token: str, config: CognitoConfig) -> Any:
    """Resolve signing key for token kid from Cognito JWKS."""
    try:
        header = jwt.get_unverified_header(token)
    except PyJWTError as exc:
        raise AuthError("InvalidTokenHeader", "Token header is invalid.") from exc

    algorithm = header.get("alg")
    if algorithm != "RS256":
        raise AuthError("InvalidTokenAlgorithm", "Only RS256 tokens are supported.")

    kid = header.get("kid")
    if not kid:
        raise AuthError("InvalidTokenHeader", "Token header is missing kid.")

    keys_by_kid = _get_cached_jwks(config.issuer, config.jwks_cache_ttl_seconds)
    key = keys_by_kid.get(kid)
    if key is not None:
        return key

    # Refresh once in case Cognito rotated keys.
    keys_by_kid = _get_cached_jwks(config.issuer, config.jwks_cache_ttl_seconds, force_refresh=True)
    key = keys_by_kid.get(kid)
    if key is None:
        raise AuthError("UnknownSigningKey", "Token signing key is not recognized.")

    return key


def validate_cognito_token(token: str) -> Dict[str, Any]:
    """Validate a Cognito JWT and return claims."""
    config = _load_cognito_config()
    signing_key = _get_signing_key(token, config)

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=config.issuer,
            options={
                "require": ["exp", "iss", "sub"],
                "verify_aud": False,
            },
        )
    except ExpiredSignatureError as exc:
        raise AuthError("TokenExpired", "Token has expired.") from exc
    except InvalidSignatureError as exc:
        raise AuthError("InvalidTokenSignature", "Token signature is invalid.") from exc
    except InvalidIssuerError as exc:
        raise AuthError("InvalidTokenIssuer", "Token issuer is invalid.") from exc
    except PyJWTError as exc:
        raise AuthError("InvalidToken", "Token is invalid.") from exc

    token_use = claims.get("token_use")
    if token_use == "id":
        if claims.get("aud") != config.app_client_id:
            raise AuthError("InvalidTokenAudience", "Token audience does not match app client.")
    elif token_use == "access":
        if claims.get("client_id") != config.app_client_id:
            raise AuthError("InvalidTokenClient", "Token client_id does not match app client.")
    else:
        raise AuthError("InvalidTokenUse", "Token use must be id or access.")

    subject = claims.get("sub")
    if not subject:
        raise AuthError("MissingSubject", "Token subject is required.")

    return claims


def authenticate_request(headers: Optional[Dict[str, Any]], fallback_user_id: Optional[str] = None) -> AuthContext:
    """Authenticate a request and return a user context."""
    if not _auth_required():
        user_id = fallback_user_id or "dev-anonymous"
        return AuthContext(user_id=user_id, claims={})

    token = _extract_bearer_token(headers)
    claims = validate_cognito_token(token)
    user_id = str(claims["sub"])

    return AuthContext(user_id=user_id, claims=claims)
