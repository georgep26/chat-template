"""Unit tests for Cognito JWT validation helpers."""

import pytest
from jwt import ExpiredSignatureError, InvalidIssuerError, InvalidSignatureError

from auth import cognito_jwt


@pytest.fixture
def auth_env(monkeypatch):
    """Configure auth environment variables for tests."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_abc123")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "client-123")


def test_validate_cognito_token_success(auth_env, monkeypatch):
    """Valid ID token returns claims when signature checks pass."""
    monkeypatch.setattr(cognito_jwt, "_get_signing_key", lambda token, config: "mock-key")
    monkeypatch.setattr(
        cognito_jwt.jwt,
        "decode",
        lambda *args, **kwargs: {
            "sub": "user-123",
            "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc123",
            "token_use": "id",
            "aud": "client-123",
            "exp": 9999999999,
        },
    )

    claims = cognito_jwt.validate_cognito_token("valid-token")
    assert claims["sub"] == "user-123"


def test_validate_cognito_token_expired(auth_env, monkeypatch):
    """Expired token maps to TokenExpired auth error."""
    monkeypatch.setattr(cognito_jwt, "_get_signing_key", lambda token, config: "mock-key")

    def _expired(*args, **kwargs):
        raise ExpiredSignatureError("expired")

    monkeypatch.setattr(cognito_jwt.jwt, "decode", _expired)

    with pytest.raises(cognito_jwt.AuthError) as exc_info:
        cognito_jwt.validate_cognito_token("expired-token")

    assert exc_info.value.code == "TokenExpired"


def test_validate_cognito_token_invalid_signature(auth_env, monkeypatch):
    """Invalid signature maps to InvalidTokenSignature auth error."""
    monkeypatch.setattr(cognito_jwt, "_get_signing_key", lambda token, config: "mock-key")

    def _invalid_signature(*args, **kwargs):
        raise InvalidSignatureError("bad sig")

    monkeypatch.setattr(cognito_jwt.jwt, "decode", _invalid_signature)

    with pytest.raises(cognito_jwt.AuthError) as exc_info:
        cognito_jwt.validate_cognito_token("bad-signature")

    assert exc_info.value.code == "InvalidTokenSignature"


def test_validate_cognito_token_invalid_issuer(auth_env, monkeypatch):
    """Wrong issuer maps to InvalidTokenIssuer auth error."""
    monkeypatch.setattr(cognito_jwt, "_get_signing_key", lambda token, config: "mock-key")

    def _invalid_issuer(*args, **kwargs):
        raise InvalidIssuerError("wrong issuer")

    monkeypatch.setattr(cognito_jwt.jwt, "decode", _invalid_issuer)

    with pytest.raises(cognito_jwt.AuthError) as exc_info:
        cognito_jwt.validate_cognito_token("wrong-issuer")

    assert exc_info.value.code == "InvalidTokenIssuer"


def test_validate_cognito_token_invalid_audience(auth_env, monkeypatch):
    """Mismatched audience is rejected for ID tokens."""
    monkeypatch.setattr(cognito_jwt, "_get_signing_key", lambda token, config: "mock-key")
    monkeypatch.setattr(
        cognito_jwt.jwt,
        "decode",
        lambda *args, **kwargs: {
            "sub": "user-123",
            "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc123",
            "token_use": "id",
            "aud": "different-client",
            "exp": 9999999999,
        },
    )

    with pytest.raises(cognito_jwt.AuthError) as exc_info:
        cognito_jwt.validate_cognito_token("wrong-aud")

    assert exc_info.value.code == "InvalidTokenAudience"
