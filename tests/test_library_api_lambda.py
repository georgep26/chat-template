"""Tests for the Library API Lambda (read-side document catalog)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from auth.cognito_jwt import AuthContext, AuthError
from library_api_lambda import main as library_main


class FakeCatalogReadStore:
    """In-memory catalog store recording calls and serving prepared rows."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = rows or []
        self.list_calls: List[Dict[str, Any]] = []
        self.get_calls: List[Dict[str, Any]] = []

    def list_documents(
        self,
        *,
        tenant_id: str,
        access_tiers: List[str],
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        self.list_calls.append(
            {
                "tenant_id": tenant_id,
                "access_tiers": list(access_tiers),
                "limit": limit,
                "offset": offset,
            }
        )
        scoped = [
            row
            for row in self.rows
            if row.get("tenant_id") == tenant_id and row.get("access_tier") in access_tiers
        ]
        return scoped[offset : offset + limit]

    def get_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        access_tiers: List[str],
    ) -> Optional[Dict[str, Any]]:
        self.get_calls.append(
            {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "access_tiers": list(access_tiers),
            }
        )
        for row in self.rows:
            if (
                row.get("document_id") == document_id
                and row.get("tenant_id") == tenant_id
                and row.get("access_tier") in access_tiers
            ):
                return row
        return None


def _row(
    *,
    document_id: str,
    tenant_id: str = "tenant-a",
    document_title: str = "Doc",
    access_tier: str = "public",
    source_status: str = "current",
    document_type: str = "pdf",
    filename: str = "doc.pdf",
    collection_id: str = "default",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = {
        "document_id": document_id,
        "tenant_id": tenant_id,
        "collection_id": collection_id,
        "document_title": document_title,
        "document_description": None,
        "filename": filename,
        "document_type": document_type,
        "source_status": source_status,
        "access_tier": access_tier,
        "location": f"s3://bucket/kb_index/{document_id}/",
        "source_location": f"s3://bucket/kb_raw/{document_id}/{filename}",
        "publish_date": None,
        "version_label": None,
        "jurisdiction": None,
        "code_family": None,
        "uploaded_at": "2026-05-09T12:00:00Z",
        "ingested_at": "2026-05-09T12:15:00Z",
        "metadata": {},
    }
    if extra:
        base.update(extra)
    return base


def _patch_store(monkeypatch, store: FakeCatalogReadStore, default_tenant: str = "tenant-a") -> None:
    monkeypatch.setattr(library_main, "_STORE", store)
    monkeypatch.setattr(library_main, "_DEFAULT_TENANT_ID", default_tenant)


def _patch_auth(monkeypatch, *, user_id: str = "user-1", claims: Optional[Dict[str, Any]] = None) -> None:
    auth_context = AuthContext(user_id=user_id, claims=claims or {"sub": user_id})

    def fake_authenticate(headers):
        del headers
        return auth_context

    monkeypatch.setattr(library_main, "authenticate_request", fake_authenticate)


def _http_event(method: str, path: str, *, query: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": {"Authorization": "Bearer fake"},
        "rawPath": path,
    }
    if query is not None:
        event["queryStringParameters"] = query
    return event


# --------------------------------------------------------------------------
# Auth + routing
# --------------------------------------------------------------------------


def test_missing_token_returns_401(monkeypatch):
    """Missing Authorization header propagates as 401."""
    store = FakeCatalogReadStore([_row(document_id="d1")])
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(
        library_main,
        "authenticate_request",
        lambda headers: (_ for _ in ()).throw(
            AuthError("MissingAuthorization", "Missing Authorization header.", 401)
        ),
    )

    event = _http_event("GET", "/documents")
    event["headers"] = {}

    response = library_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 401
    assert body["error"] == "MissingAuthorization"
    assert store.list_calls == []


def test_options_returns_204(monkeypatch):
    """CORS preflight short-circuits before auth and returns 204."""
    store = FakeCatalogReadStore()
    _patch_store(monkeypatch, store)

    event = _http_event("OPTIONS", "/documents")
    response = library_main.lambda_handler(event, None)

    assert response["statusCode"] == 204
    assert store.list_calls == []


def test_unknown_route_returns_404(monkeypatch):
    """Unknown paths or methods produce a normalized 404."""
    store = FakeCatalogReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch)

    event = _http_event("POST", "/documents")
    response = library_main.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "NotFound"


# --------------------------------------------------------------------------
# /documents (list)
# --------------------------------------------------------------------------


def test_list_documents_filters_by_jwt_tenant(monkeypatch):
    """Tenant_id is read from the JWT custom claim, not from query params."""
    store = FakeCatalogReadStore(
        [
            _row(document_id="d1", tenant_id="tenant-a", document_title="A"),
            _row(document_id="d2", tenant_id="tenant-b", document_title="B"),
        ]
    )
    _patch_store(monkeypatch, store, default_tenant="tenant-default")
    _patch_auth(
        monkeypatch,
        claims={"sub": "user-1", "custom:tenant_id": "tenant-b"},
    )

    response = library_main.lambda_handler(_http_event("GET", "/documents"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert [doc["document_id"] for doc in body["documents"]] == ["d2"]
    assert store.list_calls[0]["tenant_id"] == "tenant-b"


def test_list_documents_uses_default_tenant_when_no_claim(monkeypatch):
    """With no tenant claim, the configured default_tenant_id is used."""
    store = FakeCatalogReadStore([_row(document_id="d1", tenant_id="tenant-default")])
    _patch_store(monkeypatch, store, default_tenant="tenant-default")
    _patch_auth(monkeypatch, claims={"sub": "user-1"})

    response = library_main.lambda_handler(_http_event("GET", "/documents"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert store.list_calls[0]["tenant_id"] == "tenant-default"
    assert [doc["document_id"] for doc in body["documents"]] == ["d1"]


def test_list_documents_excludes_restricted_for_v1(monkeypatch):
    """v1 only exposes public + internal access tiers; restricted rows stay hidden."""
    store = FakeCatalogReadStore(
        [
            _row(document_id="d1", access_tier="public"),
            _row(document_id="d2", access_tier="internal"),
            _row(document_id="d3", access_tier="restricted"),
        ]
    )
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(_http_event("GET", "/documents"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    ids = sorted(doc["document_id"] for doc in body["documents"])
    assert ids == ["d1", "d2"]
    assert "restricted" not in store.list_calls[0]["access_tiers"]


def test_list_documents_pagination(monkeypatch):
    """Limit and offset query params propagate to the store."""
    rows = [_row(document_id=f"d{i:02d}") for i in range(75)]
    store = FakeCatalogReadStore(rows)
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(
        _http_event("GET", "/documents", query={"limit": "10", "offset": "20"}),
        None,
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert store.list_calls[0]["limit"] == 10
    assert store.list_calls[0]["offset"] == 20
    assert len(body["documents"]) == 10
    # next_offset is set when the page is full.
    assert body["next_offset"] == 30


def test_list_documents_invalid_limit_returns_400(monkeypatch):
    """Out-of-range limit yields a 400 BadRequest."""
    store = FakeCatalogReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(
        _http_event("GET", "/documents", query={"limit": "9999"}),
        None,
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 400
    assert body["error"] == "BadRequest"


# --------------------------------------------------------------------------
# /documents/{document_id} (detail)
# --------------------------------------------------------------------------


def test_get_document_returns_row_for_authorized_tenant(monkeypatch):
    """Happy path: tenant-scoped lookup returns the full row."""
    store = FakeCatalogReadStore(
        [_row(document_id="irc-2021", tenant_id="tenant-a", document_title="2021 IRC")]
    )
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(_http_event("GET", "/documents/irc-2021"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["document"]["document_id"] == "irc-2021"
    assert body["document"]["document_title"] == "2021 IRC"


def test_get_document_returns_404_for_other_tenant(monkeypatch):
    """A document owned by another tenant is not visible (404)."""
    store = FakeCatalogReadStore(
        [_row(document_id="irc-2021", tenant_id="tenant-other")]
    )
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(_http_event("GET", "/documents/irc-2021"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "DocumentNotFound"


def test_get_document_returns_404_for_unknown_id(monkeypatch):
    """Unknown document_id returns 404, not 500."""
    store = FakeCatalogReadStore()
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(_http_event("GET", "/documents/nope"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "DocumentNotFound"


def test_get_document_excludes_restricted_for_v1(monkeypatch):
    """A restricted document is not visible even within the tenant for v1."""
    store = FakeCatalogReadStore(
        [_row(document_id="sop-1", tenant_id="tenant-a", access_tier="restricted")]
    )
    _patch_store(monkeypatch, store)
    _patch_auth(monkeypatch, claims={"sub": "user-1", "custom:tenant_id": "tenant-a"})

    response = library_main.lambda_handler(_http_event("GET", "/documents/sop-1"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 404
    assert body["error"] == "DocumentNotFound"
