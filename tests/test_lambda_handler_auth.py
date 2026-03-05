"""Tests for lambda handler auth behavior and tenant isolation."""

import copy
import json

from langchain_core.messages import AIMessage

from src.rag_lambda.auth.cognito_jwt import AuthContext, AuthError
from src.rag_lambda.main import lambda_handler, main


class FakeStore:
    """In-memory fake history store for handler tests."""

    def __init__(self):
        self.get_calls = []
        self.append_calls = []

    def get_messages(self, conversation_id):
        self.get_calls.append(conversation_id)
        return []

    def append_messages(self, conversation_id, messages, metadata=None):
        self.append_calls.append((conversation_id, messages, metadata))


class FakeGraph:
    """Simple fake graph that always returns one AI answer."""

    def invoke(self, state, config=None):
        messages = state["messages"] + [AIMessage(content="hello from test")]
        return {
            "messages": messages,
            "sources": [
                {
                    "document_id": "doc-1",
                    "source_type": "document",
                    "score": 0.9,
                    "chunk": "test chunk",
                }
            ],
        }


def _mock_config():
    return {
        "rag_chat": {
            "chat_history_store": {
                "memory_backend_type": "aurora_data_api",
                "db_cluster_arn": "arn:aws:rds:us-east-1:123456789012:cluster:test",
                "db_credentials_secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
                "database_name": "chat_template_db",
                "table_name": "chat_history",
                "region": "us-east-1",
            },
            "summarization": {
                "summarization_threshold": 20,
                "model": {"id": "amazon.nova-micro-v1:0", "temperature": 0.0},
            },
            "retrieval": {"region": "us-east-1", "knowledge_base_id": "kb-123", "number_of_results": 5},
        }
    }


def _patch_runtime(monkeypatch, store):
    monkeypatch.setattr("src.rag_lambda.main.read_config", lambda path: copy.deepcopy(_mock_config()))
    monkeypatch.setattr("src.rag_lambda.main.create_history_store", lambda **kwargs: store)
    monkeypatch.setattr("src.rag_lambda.main.summarization_check", lambda **kwargs: kwargs["messages"])
    monkeypatch.setattr("src.rag_lambda.main.build_rag_graph", lambda: FakeGraph())


def test_lambda_handler_missing_token_returns_401(monkeypatch):
    """Missing authorization header is rejected when auth is enabled."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {
                "conversation_id": "conv-1",
                "user_id": "user-1",
                "message": "hello",
            }
        ),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 401
    assert body["error"] == "MissingAuthorization"


def test_lambda_handler_bad_token_returns_401(monkeypatch):
    """Invalid token is returned as normalized auth error."""
    monkeypatch.setattr(
        "src.rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: (_ for _ in ()).throw(
            AuthError("InvalidToken", "Token is invalid.", 401)
        ),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps({"conversation_id": "conv-1", "message": "hello"}),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 401
    assert body == {"error": "InvalidToken", "detail": "Token is invalid."}


def test_lambda_handler_mismatched_user_id_overwritten_by_token(monkeypatch):
    """Body user_id is ignored when auth is used; token subject is the source of truth."""
    store = FakeStore()
    _patch_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "src.rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: AuthContext(user_id="token-user", claims={"sub": "token-user"}),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps(
            {
                "conversation_id": "conv-1",
                "user_id": "different-user",
                "message": "hello",
            }
        ),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["conversation_id"] == "conv-1"
    assert "answer" in body
    # Conversation is scoped to authenticated user, not body user_id
    assert store.append_calls
    scoped_id = store.append_calls[0][0]
    assert scoped_id == "token-user:conv-1"


def test_lambda_handler_success_and_no_config_leak(monkeypatch):
    """Successful call returns 200 and omits backend config payload."""
    store = FakeStore()
    _patch_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "src.rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: AuthContext(user_id="token-user", claims={"sub": "token-user"}),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps({"conversation_id": "conv-1", "message": "hello"}),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["conversation_id"] == "conv-1"
    assert "answer" in body
    assert "config" not in body


def test_main_scopes_conversation_id_by_user(monkeypatch):
    """Same conversation_id across prepared users writes to different internal keys."""
    store = FakeStore()
    _patch_runtime(monkeypatch, store)

    request_body_user_a = {
        "conversation_id": "shared-conv",
        "message": "hello",
        "user_id": "user-a",
    }
    request_body_user_b = {
        "conversation_id": "shared-conv",
        "message": "hello",
        "user_id": "user-b",
    }

    resp_user_a = main(request_body_user_a)
    resp_user_b = main(request_body_user_b)

    assert resp_user_a["statusCode"] == 200
    assert resp_user_b["statusCode"] == 200

    scoped_ids = [call[0] for call in store.append_calls]
    assert "user-a:shared-conv" in scoped_ids
    assert "user-b:shared-conv" in scoped_ids
    assert "user-a:shared-conv" != "user-b:shared-conv"
