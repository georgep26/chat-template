"""Tests for the Knowledge API Lambda (vector search + chunk fetch primitives)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from auth.cognito_jwt import AuthContext, AuthError
from knowledge import vector_search
from knowledge.artifact_fetch import (
    ALLOWED_KEY_PREFIX,
    ArtifactFetchError,
    FetchedArtifact,
    fetch_chunk_content,
)
from knowledge_api_lambda import main as knowledge_main
from langchain_core.documents import Document


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeChunkReadStore:
    """In-memory chunk store recording calls and serving prepared rows."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = rows or []
        self.list_calls: List[Dict[str, Any]] = []
        self.get_calls: List[Dict[str, Any]] = []

    def list_chunks_for_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        limit: int,
        offset: int,
        access_tiers: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        self.list_calls.append(
            {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "limit": limit,
                "offset": offset,
            }
        )
        scoped = [
            row
            for row in self.rows
            if row.get("document_id") == document_id
            and row.get("tenant_id") == tenant_id
        ]
        return scoped[offset : offset + limit]

    def get_chunk(
        self,
        *,
        chunk_id: str,
        tenant_id: str,
        access_tiers: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        self.get_calls.append(
            {"chunk_id": chunk_id, "tenant_id": tenant_id}
        )
        for row in self.rows:
            if (
                row.get("chunk_id") == chunk_id
                and row.get("tenant_id") == tenant_id
            ):
                return row
        return None


class FakeDocumentReadStore:
    """In-memory document_catalog store recording calls and serving rows."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = rows or []
        self.get_calls: List[Dict[str, Any]] = []

    def get_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        access_tiers: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        self.get_calls.append(
            {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "access_tiers": list(access_tiers or ()),
            }
        )
        for row in self.rows:
            if (
                row.get("document_id") == document_id
                and row.get("tenant_id") == tenant_id
                and (
                    not access_tiers
                    or row.get("access_tier") in access_tiers
                )
            ):
                return row
        return None


class _StubRetriever:
    """Replaces ``langchain_aws.AmazonKnowledgeBasesRetriever`` for /search tests."""

    captured: List[Dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        _StubRetriever.captured.append(kwargs)

    def invoke(self, query: str) -> List[Document]:
        return [
            Document(
                page_content="hit content",
                metadata={
                    "score": 0.85,
                    "source_metadata": {
                        "document_id": "doc-1",
                        "chunk_id": "doc-1_chunk_001",
                        "page_number": 5,
                        "document_type": "pdf",
                    },
                },
            )
        ]


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _chunk_row(
    *,
    chunk_id: str,
    document_id: str = "doc-1",
    tenant_id: str = "tenant-a",
    chunk_type: str = "page",
    chunk_index: int = 0,
    page_start: int = 1,
    page_end: int = 1,
    document_title: str = "Doc Title",
    location: str = "s3://kb-bucket/kb_index/doc-1/pages/doc-1_chunk_001.md",
    source_location: str = "s3://kb-bucket/kb_raw/doc-1/original/doc.pdf",
    access_tier: str = "public",
) -> Dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "tenant_id": tenant_id,
        "collection_id": "default",
        "chunk_type": chunk_type,
        "chunk_index": chunk_index,
        "document_title": document_title,
        "title": None,
        "chapter": None,
        "section": None,
        "page_start": page_start,
        "page_end": page_end,
        "location": location,
        "source_location": source_location,
        "content_type": "text/markdown",
        "token_count": 200,
        "char_count": 1234,
        "access_tier": access_tier,
        "metadata": {},
    }


def _patch_store(
    monkeypatch,
    store: FakeChunkReadStore,
    default_tenant: str = "tenant-a",
    document_store: Optional["FakeDocumentReadStore"] = None,
) -> None:
    monkeypatch.setattr(knowledge_main, "_STORE", store)
    monkeypatch.setattr(knowledge_main, "_DEFAULT_TENANT_ID", default_tenant)
    monkeypatch.setattr(knowledge_main, "_KB_ID", "STUBKBID")
    monkeypatch.setattr(knowledge_main, "_KB_REGION", "us-east-1")
    monkeypatch.setattr(knowledge_main, "_DOCUMENT_BUCKET", "kb-bucket")
    if document_store is not None:
        monkeypatch.setattr(knowledge_main, "_DOCUMENT_STORE", document_store)


def _patch_auth(
    monkeypatch, *, user_id: str = "user-1", claims: Optional[Dict[str, Any]] = None
) -> None:
    auth_context = AuthContext(user_id=user_id, claims=claims or {"sub": user_id})

    def fake_authenticate(headers):
        del headers
        return auth_context

    monkeypatch.setattr(knowledge_main, "authenticate_request", fake_authenticate)


def _http_event(
    method: str,
    path: str,
    *,
    query: Optional[Dict[str, str]] = None,
    body: Optional[Any] = None,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": {"Authorization": "Bearer fake"},
        "rawPath": path,
    }
    if query is not None:
        event["queryStringParameters"] = query
    if body is not None:
        event["body"] = json.dumps(body)
    return event


@pytest.fixture(autouse=True)
def _reset_retriever_capture():
    _StubRetriever.captured = []
    yield
    _StubRetriever.captured = []


# ---------------------------------------------------------------------------
# Auth + routing
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(monkeypatch):
    store = FakeChunkReadStore([_chunk_row(chunk_id="c1")])
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(
        knowledge_main,
        "authenticate_request",
        lambda headers: (_ for _ in ()).throw(
            AuthError("MissingAuthorization", "Missing Authorization header.", 401)
        ),
    )

    event = _http_event("GET", "/chunks/c1")
    event["headers"] = {}
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 401
    assert body["error"] == "MissingAuthorization"
    assert store.get_calls == []


def test_options_returns_204(monkeypatch):
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)

    event = _http_event("OPTIONS", "/search")
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 204


def test_unknown_route_returns_404(monkeypatch):
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    event = _http_event("DELETE", "/chunks/c1")
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 404


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------


def test_search_filters_by_tenant_from_jwt_claims(monkeypatch):
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(
        monkeypatch, claims={"sub": "u", "custom:tenant_id": "tenant-from-jwt"}
    )
    monkeypatch.setattr(vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever)

    event = _http_event("POST", "/search", body={"query": "stair height", "top_k": 3})
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["result_count"] == 1
    assert body["sources"][0]["document_id"] == "doc-1"
    assert body["sources"][0]["chunk_id"] == "doc-1_chunk_001"

    # Authz filter must include the JWT tenant, not the default.
    cfg = _StubRetriever.captured[0]["retrieval_config"]["vectorSearchConfiguration"]
    bf = cfg["filter"]
    flat = json.dumps(bf)
    assert "tenant-from-jwt" in flat
    assert "tenant-a" not in flat


def test_search_user_filter_cannot_widen_scope(monkeypatch):
    """User filter naming tenant_id is appended; server clause stays in place."""
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)
    monkeypatch.setattr(vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever)

    event = _http_event(
        "POST",
        "/search",
        body={
            "query": "x",
            "filters": {"key": "tenant_id", "equals": "tenant-b"},
        },
    )
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 200

    bf = _StubRetriever.captured[0]["retrieval_config"][
        "vectorSearchConfiguration"
    ]["filter"]
    # tenant-a is in the server authz block; tenant-b is appended (over-narrow but safe).
    flat = json.dumps(bf)
    assert "tenant-a" in flat
    assert "tenant-b" in flat


def test_search_rejects_invalid_filter(monkeypatch):
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)
    monkeypatch.setattr(vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever)

    event = _http_event(
        "POST",
        "/search",
        body={"query": "x", "filters": {"key": "x", "regex_match": "^foo"}},
    )
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error"] == "InvalidFilter"


def test_search_validation_error_when_query_missing(monkeypatch):
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    event = _http_event("POST", "/search", body={"top_k": 5})
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# /documents/{id}/chunks
# ---------------------------------------------------------------------------


def test_list_document_chunks_returns_summaries_and_paginates(monkeypatch):
    rows = [
        _chunk_row(chunk_id=f"c{i}", chunk_index=i, document_id="doc-1")
        for i in range(75)
    ]
    store = FakeChunkReadStore(rows)
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    event = _http_event(
        "GET",
        "/documents/doc-1/chunks",
        query={"limit": "50", "offset": "0"},
    )
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert len(body["chunks"]) == 50
    assert body["next_offset"] == 50
    assert "content" not in body["chunks"][0]


def test_list_document_chunks_filters_by_tenant(monkeypatch):
    rows = [
        _chunk_row(chunk_id="c1", tenant_id="tenant-a"),
        _chunk_row(chunk_id="c2", tenant_id="tenant-b"),
    ]
    store = FakeChunkReadStore(rows)
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "u", "custom:tenant_id": "tenant-a"})

    event = _http_event("GET", "/documents/doc-1/chunks")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    chunk_ids = [c["chunk_id"] for c in body["chunks"]]
    assert chunk_ids == ["c1"]


def test_list_chunks_rejects_invalid_pagination(monkeypatch):
    store = FakeChunkReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    event = _http_event(
        "GET", "/documents/doc-1/chunks", query={"limit": "abc"}
    )
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# /chunks/{chunk_id}
# ---------------------------------------------------------------------------


def test_get_chunk_404_when_not_owned(monkeypatch):
    rows = [_chunk_row(chunk_id="c1", tenant_id="tenant-other")]
    store = FakeChunkReadStore(rows)
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    event = _http_event("GET", "/chunks/c1")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "ChunkNotFound"


def test_get_chunk_returns_metadata_plus_content(monkeypatch):
    rows = [_chunk_row(chunk_id="c1")]
    store = FakeChunkReadStore(rows)
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    def fake_fetch(*, location, expected_bucket, max_bytes, **kwargs):
        del kwargs
        assert expected_bucket == "kb-bucket"
        assert location.endswith("doc-1_chunk_001.md")
        return FetchedArtifact(
            content="# Page content here.\n",
            bucket=expected_bucket,
            key="kb_index/doc-1/pages/doc-1_chunk_001.md",
            content_length=21,
            content_type="text/markdown",
        )

    monkeypatch.setattr(knowledge_main, "fetch_chunk_content", fake_fetch)

    event = _http_event("GET", "/chunks/c1")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    chunk = body["chunk"]
    assert chunk["chunk_id"] == "c1"
    assert chunk["content"] == "# Page content here.\n"
    assert chunk["page_start"] == 1


def test_get_chunk_502_when_artifact_fetch_fails(monkeypatch):
    rows = [_chunk_row(chunk_id="c1")]
    store = FakeChunkReadStore(rows)
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    def boom(**kwargs):
        del kwargs
        raise ArtifactFetchError("boom")

    monkeypatch.setattr(knowledge_main, "fetch_chunk_content", boom)

    event = _http_event("GET", "/chunks/c1")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 502
    assert body["error"] == "ArtifactFetchError"


# ---------------------------------------------------------------------------
# artifact_fetch.fetch_chunk_content (prefix + size validation)
# ---------------------------------------------------------------------------


class _StubS3:
    def __init__(self, *, content_length: int, body: bytes):
        self.content_length = content_length
        self.body = body
        self.head_calls: List[Dict[str, Any]] = []
        self.get_calls: List[Dict[str, Any]] = []

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        return {"ContentLength": self.content_length, "ContentType": "text/markdown"}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": _BytesBody(self.body)}


class _BytesBody:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body


def test_fetch_rejects_non_kb_index_prefix():
    s3 = _StubS3(content_length=10, body=b"hello\n")
    with pytest.raises(ArtifactFetchError):
        fetch_chunk_content(
            location="s3://kb-bucket/kb_raw/doc-1/original/doc.pdf",
            expected_bucket="kb-bucket",
            s3_client=s3,
        )
    assert s3.head_calls == []  # never made the call


def test_fetch_rejects_wrong_bucket():
    s3 = _StubS3(content_length=10, body=b"hello\n")
    with pytest.raises(ArtifactFetchError):
        fetch_chunk_content(
            location="s3://other-bucket/kb_index/doc-1/pages/x.md",
            expected_bucket="kb-bucket",
            s3_client=s3,
        )


def test_fetch_rejects_oversize_content_length():
    body = b"x" * 1024
    s3 = _StubS3(content_length=10_000, body=body)
    with pytest.raises(ArtifactFetchError):
        fetch_chunk_content(
            location="s3://kb-bucket/kb_index/doc-1/pages/x.md",
            expected_bucket="kb-bucket",
            s3_client=s3,
            max_bytes=1024,
        )


def test_fetch_returns_decoded_text_when_within_limits():
    body = "# Heading\nbody".encode("utf-8")
    s3 = _StubS3(content_length=len(body), body=body)
    artifact = fetch_chunk_content(
        location="s3://kb-bucket/kb_index/doc-1/pages/x.md",
        expected_bucket="kb-bucket",
        s3_client=s3,
        max_bytes=1024,
    )
    assert artifact.content == "# Heading\nbody"
    assert artifact.bucket == "kb-bucket"
    assert artifact.key.startswith(ALLOWED_KEY_PREFIX)


# ---------------------------------------------------------------------------
# /documents/{id}/raw-url
# ---------------------------------------------------------------------------


def _document_row(
    *,
    document_id: str = "doc-1",
    tenant_id: str = "tenant-a",
    access_tier: str = "public",
    filename: str = "2021-irc.pdf",
    location: str = "s3://kb-bucket/kb_raw/doc-1/original/2021-irc.pdf",
) -> Dict[str, Any]:
    return {
        "document_id": document_id,
        "tenant_id": tenant_id,
        "access_tier": access_tier,
        "filename": filename,
        "location": location,
        "source_location": location,
        "document_title": "Doc",
        "document_type": "pdf",
        "source_status": "current",
        "metadata": {},
    }


def _patch_raw_url_builder(monkeypatch):
    """Replace build_raw_document_url with a deterministic stub for routing tests."""
    from knowledge.raw_url import RawDocumentUrl

    calls: List[Dict[str, Any]] = []

    def fake_build(**kwargs):
        calls.append(kwargs)
        return RawDocumentUrl(
            url="https://stub.example/file?sig=x",
            expires_at="2026-05-10T03:00:00Z",
            filename=kwargs.get("response_filename") or "file.pdf",
            content_type="application/pdf",
            disposition=kwargs.get("disposition", "inline"),
            ttl_seconds=kwargs.get("ttl_seconds", 300),
        )

    monkeypatch.setattr(knowledge_main, "build_raw_document_url", fake_build)
    return calls


def test_get_raw_url_returns_200_for_owned_document(monkeypatch):
    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    calls = _patch_raw_url_builder(monkeypatch)

    event = _http_event("GET", "/documents/doc-1/raw-url")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["url"].startswith("https://stub.example/")
    assert body["filename"] == "2021-irc.pdf"
    assert body["disposition"] == "inline"
    assert body["ttl_seconds"] == 300

    # Builder received the expected bucket + a kb_raw/ location, filename hint.
    assert calls[0]["expected_bucket"] == "kb-bucket"
    assert calls[0]["location"].startswith("s3://kb-bucket/kb_raw/")
    assert calls[0]["response_filename"] == "2021-irc.pdf"


def test_get_raw_url_passes_page_and_disposition_query_params(monkeypatch):
    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    calls = _patch_raw_url_builder(monkeypatch)

    event = _http_event(
        "GET",
        "/documents/doc-1/raw-url",
        query={"disposition": "attachment", "page": "107", "ttl": "120"},
    )
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 200

    call = calls[0]
    assert call["disposition"] == "attachment"
    assert call["page"] == 107
    assert call["ttl_seconds"] == 120


def test_get_raw_url_404_when_document_not_in_tenant(monkeypatch):
    docs = FakeDocumentReadStore([_document_row(tenant_id="tenant-other")])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    _patch_raw_url_builder(monkeypatch)

    event = _http_event("GET", "/documents/doc-1/raw-url")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "DocumentNotFound"


def test_get_raw_url_404_when_document_unknown(monkeypatch):
    docs = FakeDocumentReadStore([])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    _patch_raw_url_builder(monkeypatch)

    event = _http_event("GET", "/documents/missing/raw-url")
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 404


def test_get_raw_url_uses_v1_catalog_access_tiers(monkeypatch):
    """The endpoint must scope to ('public', 'internal') so it matches the Library API."""
    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    _patch_raw_url_builder(monkeypatch)

    event = _http_event("GET", "/documents/doc-1/raw-url")
    knowledge_main.lambda_handler(event, None)

    assert docs.get_calls[0]["access_tiers"] == ["public", "internal"]


def test_get_raw_url_400_on_invalid_disposition(monkeypatch):
    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    _patch_raw_url_builder(monkeypatch)

    event = _http_event(
        "GET", "/documents/doc-1/raw-url", query={"disposition": "evil"}
    )
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 400
    assert body["error"] == "BadRequest"


def test_get_raw_url_400_on_invalid_ttl(monkeypatch):
    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)
    _patch_raw_url_builder(monkeypatch)

    event = _http_event(
        "GET", "/documents/doc-1/raw-url", query={"ttl": "abc"}
    )
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 400


def test_get_raw_url_502_on_builder_error(monkeypatch):
    """Catalog row with a location outside kb_raw/ surfaces as 502."""
    from knowledge.raw_url import RawUrlError

    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    _patch_auth(monkeypatch)

    def boom(**kwargs):
        del kwargs
        raise RawUrlError("prefix mismatch")

    monkeypatch.setattr(knowledge_main, "build_raw_document_url", boom)

    event = _http_event("GET", "/documents/doc-1/raw-url")
    response = knowledge_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 502
    assert body["error"] == "RawUrlError"


def test_get_raw_url_401_when_no_auth(monkeypatch):
    docs = FakeDocumentReadStore([_document_row()])
    _patch_store(monkeypatch, FakeChunkReadStore(), document_store=docs)
    monkeypatch.setattr(
        knowledge_main,
        "authenticate_request",
        lambda headers: (_ for _ in ()).throw(
            AuthError("MissingAuthorization", "Missing.", 401)
        ),
    )
    _patch_raw_url_builder(monkeypatch)

    event = _http_event("GET", "/documents/doc-1/raw-url")
    event["headers"] = {}
    response = knowledge_main.lambda_handler(event, None)
    assert response["statusCode"] == 401
