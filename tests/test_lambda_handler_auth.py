"""Tests for lambda handler auth behavior and tenant isolation."""

import copy
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from auth.cognito_jwt import AuthContext, AuthError
from rag_lambda.main import lambda_handler, main
from memory.base import ConversationAccessError, ConversationRecord


class FakeStore:
    """In-memory fake history store for handler tests."""

    def __init__(self):
        self.next_id = "00000000-0000-4000-8000-000000000001"
        self.owners = {}
        self.get_calls = []
        self.append_calls = []
        self.trace_calls = []

    def resolve_or_create_conversation(
        self,
        *,
        user_id,
        conversation_id=None,
        request_id=None,
        title_seed=None,
        metadata=None,
    ):
        del request_id, title_seed
        if conversation_id:
            owner = self.owners.get(conversation_id)
            if owner != user_id:
                raise ConversationAccessError("Conversation was not found for this user.")
            return ConversationRecord(
                conversation_id=conversation_id,
                user_id=user_id,
                metadata=metadata or {},
                message_count=0,
            )

        conversation_id = self.next_id
        self.owners[conversation_id] = user_id
        return ConversationRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            metadata=metadata or {},
            created=True,
            message_count=0,
        )

    def get_messages(self, conversation_id, limit=None):
        del limit
        self.get_calls.append(conversation_id)
        return []

    def append_messages(self, conversation_id, messages, metadata=None):
        self.append_calls.append((conversation_id, messages, metadata))

    def append_trace_events(self, conversation_id, trace_events):
        self.trace_calls.append((conversation_id, trace_events))


class FakeGraph:
    """Simple fake graph that always returns one AI answer."""

    def invoke(self, state, config=None):
        collector = config["configurable"].get("trace_collector") if config else None
        if collector:
            step = collector.start_step("fake")
            collector.complete_step(
                step,
                label="Fake step",
                detail="Completed fake graph execution.",
                output_summary={"ok": True},
            )
        messages = state["messages"] + [
            AIMessage(name="rewriter", content="internal rewrite"),
            SystemMessage(name="subqueries", content="internal subquery"),
            AIMessage(content="hello from test"),
        ]
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
    monkeypatch.setattr("rag_lambda.main.read_config", lambda path: copy.deepcopy(_mock_config()))
    monkeypatch.setattr("rag_lambda.main.create_history_store", lambda **kwargs: store)
    monkeypatch.setattr("rag_lambda.main.summarization_check", lambda **kwargs: kwargs["messages"])
    monkeypatch.setattr("rag_lambda.main.build_rag_graph", lambda: FakeGraph())


def test_lambda_handler_missing_token_returns_401(monkeypatch):
    """Missing authorization header is rejected when auth is enabled."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {
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
        "rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: (_ for _ in ()).throw(
            AuthError("InvalidToken", "Token is invalid.", 401)
        ),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps({"message": "hello"}),
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
        "rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: AuthContext(user_id="token-user", claims={"sub": "token-user"}),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps(
            {
                "user_id": "different-user",
                "message": "hello",
            }
        ),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["conversation_id"] == store.next_id
    assert "answer" in body
    # Conversation ownership comes from the authenticated user, not body user_id.
    assert store.owners[body["conversation_id"]] == "token-user"
    assert store.append_calls
    assert store.append_calls[0][0] == body["conversation_id"]


def test_lambda_handler_success_and_no_config_leak(monkeypatch):
    """Successful call returns 200 and omits backend config payload."""
    store = FakeStore()
    _patch_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: AuthContext(user_id="token-user", claims={"sub": "token-user"}),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps({"message": "hello"}),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["conversation_id"] == store.next_id
    assert "answer" in body
    assert "steps" in body
    assert "config" not in body


def test_main_uses_backend_conversation_id_as_storage_key(monkeypatch):
    """Backend conversation id is the public API id and the storage key."""
    store = FakeStore()
    _patch_runtime(monkeypatch, store)

    request_body = {
        "message": "hello",
        "user_id": "user-a",
    }

    resp = main(request_body)
    body = json.loads(resp["body"])

    assert resp["statusCode"] == 200
    assert body["conversation_id"] == store.next_id
    assert store.get_calls == [store.next_id]
    assert store.append_calls[0][0] == store.next_id
    stored_messages = store.append_calls[0][1]
    assert [type(message) for message in stored_messages] == [HumanMessage, AIMessage]
    assert stored_messages[0].content == "hello"
    assert stored_messages[1].content == "hello from test"
    assert all(getattr(message, "name", None) not in {"rewriter", "subqueries"} for message in stored_messages)


def test_lambda_handler_rejects_conversation_owned_by_another_user(monkeypatch):
    """Users cannot load another user's backend conversation id."""
    store = FakeStore()
    store.owners["00000000-0000-4000-8000-000000000002"] = "user-a"
    _patch_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "rag_lambda.main.authenticate_request",
        lambda headers, fallback_user_id=None: AuthContext(user_id="user-b", claims={"sub": "user-b"}),
    )

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"Authorization": "Bearer fake"},
        "body": json.dumps(
            {
                "conversation_id": "00000000-0000-4000-8000-000000000002",
                "message": "hello",
            }
        ),
    }

    response = lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "ConversationNotFound"


def test_main_returns_sanitized_steps(monkeypatch):
    """Synchronous response includes frontend-safe graph step events."""
    store = FakeStore()
    _patch_runtime(monkeypatch, store)

    request_body = {
        "message": "hello",
        "user_id": "user-a",
    }

    response = main(request_body)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["steps"] == [
        {
            "label": "Fake step",
            "status": "completed",
            "detail": "Completed fake graph execution.",
        }
    ]
    assert store.trace_calls
