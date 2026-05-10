"""Tests for the shared knowledge primitives' vector search and authz layer."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from langchain_core.documents import Document

from knowledge import vector_search
from knowledge.authz import (
    DEFAULT_ACCESS_TIERS,
    build_bedrock_authz_filter,
    build_sql_authz_clause,
    merge_user_filter_with_authz,
)


# build_bedrock_authz_filter ---------------------------------------------


def test_authz_filter_with_default_tiers_emits_tenant_only():
    """v1 default tiers are empty — only tenant_id is enforced at the chunk layer."""
    out = build_bedrock_authz_filter(tenant_id="tenant-a")
    assert out == {"equals": {"key": "tenant_id", "value": "tenant-a"}}


def test_authz_filter_combines_tenant_and_access_tier_when_tiers_provided():
    out = build_bedrock_authz_filter(
        tenant_id="tenant-a", access_tiers=["public", "internal"]
    )
    assert out == {
        "andAll": [
            {"equals": {"key": "tenant_id", "value": "tenant-a"}},
            {"in": {"key": "access_tier", "value": ["public", "internal"]}},
        ]
    }


def test_authz_filter_collapses_single_tier_to_equals():
    out = build_bedrock_authz_filter(tenant_id="tenant-a", access_tiers=["public"])
    assert out == {
        "andAll": [
            {"equals": {"key": "tenant_id", "value": "tenant-a"}},
            {"equals": {"key": "access_tier", "value": "public"}},
        ]
    }


def test_authz_filter_requires_tenant():
    with pytest.raises(ValueError):
        build_bedrock_authz_filter(tenant_id="")


# merge_user_filter_with_authz -------------------------------------------


def test_merge_intersects_user_filter_under_andAll():
    """User-supplied filters become an additional clause inside andAll."""
    merged = merge_user_filter_with_authz(
        tenant_id="tenant-a",
        user_filter={"key": "edition_year", "greater_than_or_equals": 2021},
    )
    assert merged == {
        "andAll": [
            {"equals": {"key": "tenant_id", "value": "tenant-a"}},
            {"greaterThanOrEquals": {"key": "edition_year", "value": 2021}},
        ]
    }


def test_merge_intersects_user_filter_when_explicit_tiers_provided():
    merged = merge_user_filter_with_authz(
        tenant_id="tenant-a",
        access_tiers=["public", "internal"],
        user_filter={"key": "edition_year", "greater_than_or_equals": 2021},
    )
    assert merged["andAll"][0]["andAll"][0] == {
        "equals": {"key": "tenant_id", "value": "tenant-a"}
    }
    assert merged["andAll"][1] == {
        "greaterThanOrEquals": {"key": "edition_year", "value": 2021}
    }


def test_merge_with_no_user_filter_returns_authz_only():
    merged = merge_user_filter_with_authz(tenant_id="tenant-a", user_filter=None)
    assert merged == build_bedrock_authz_filter(tenant_id="tenant-a")


def test_merge_with_legacy_dict_user_filter_translates_correctly():
    merged = merge_user_filter_with_authz(
        tenant_id="tenant-a",
        user_filter={"document_type": ["pdf", "word"]},
    )
    # andAll([tenant_id_authz, translated_user_filter])
    assert merged["andAll"][0] == {"equals": {"key": "tenant_id", "value": "tenant-a"}}
    assert merged["andAll"][1] == {
        "orAll": [
            {"equals": {"key": "document_type", "value": "pdf"}},
            {"equals": {"key": "document_type", "value": "word"}},
        ]
    }


# build_sql_authz_clause -------------------------------------------------


def test_sql_clause_default_emits_tenant_only():
    """v1 default chunk-side authz is tenant_id only — access_tier filtering deferred."""
    clause, params = build_sql_authz_clause(tenant_id="tenant-a")
    assert clause == " AND tenant_id = :_authz_tenant_id"
    assert [p["name"] for p in params] == ["_authz_tenant_id"]
    assert params[0]["value"] == {"stringValue": "tenant-a"}


def test_sql_clause_appends_access_tier_when_tiers_provided():
    clause, params = build_sql_authz_clause(
        tenant_id="tenant-a", access_tiers=["public", "internal"]
    )
    assert " AND tenant_id = :_authz_tenant_id" in clause
    assert " AND access_tier IN (:_authz_tier_0, :_authz_tier_1)" in clause
    assert [p["name"] for p in params] == [
        "_authz_tenant_id",
        "_authz_tier_0",
        "_authz_tier_1",
    ]


def test_sql_clause_supports_custom_prefix():
    clause, params = build_sql_authz_clause(
        tenant_id="tenant-a", parameter_prefix="scope"
    )
    assert ":scope_tenant_id" in clause
    assert params[0]["name"] == "scope_tenant_id"


# search() integration ---------------------------------------------------


class _StubRetriever:
    """Stand-in for langchain_aws.AmazonKnowledgeBasesRetriever."""

    captured: List[Dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        _StubRetriever.captured.append(kwargs)

    def invoke(self, query: str) -> List[Document]:
        return [
            Document(
                page_content="hit content",
                metadata={
                    "score": 0.9,
                    "source_metadata": {
                        "document_id": "doc-1",
                        "chunk_id": "doc-1_chunk_001",
                        "page_number": 5,
                        "document_type": "pdf",
                    },
                },
            )
        ]


@pytest.fixture(autouse=True)
def _reset_capture():
    _StubRetriever.captured = []
    yield
    _StubRetriever.captured = []


def test_search_always_applies_server_authz_filter(monkeypatch):
    """Even with no user filter, tenant + access_tier must be passed to Bedrock."""
    monkeypatch.setattr(
        vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever
    )

    results = vector_search.search(
        query="stair riser height",
        knowledge_base_id="ABCDEFG",
        region="us-east-1",
        tenant_id="tenant-a",
        top_k=5,
    )

    assert len(_StubRetriever.captured) == 1
    cfg = _StubRetriever.captured[0]["retrieval_config"]["vectorSearchConfiguration"]
    assert cfg["numberOfResults"] == 5
    bf = cfg["filter"]
    # Server clause is always present.
    assert bf == {"equals": {"key": "tenant_id", "value": "tenant-a"}}

    assert len(results) == 1
    r = results[0]
    assert r.document_id == "doc-1"
    assert r.chunk_id == "doc-1_chunk_001"
    assert r.source_type == "pdf"
    assert r.score == 0.9
    # source_metadata fields are flattened to top-level for caller convenience.
    assert r.metadata["page_number"] == 5
    assert r.metadata["score"] == 0.9


def test_search_user_filter_is_andAll_merged_under_authz(monkeypatch):
    """A user-supplied filter cannot remove or replace the server authz clause."""
    monkeypatch.setattr(
        vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever
    )

    vector_search.search(
        query="x",
        knowledge_base_id="KB",
        region="us-east-1",
        tenant_id="tenant-a",
        user_filter={"document_type": ["pdf"]},
        top_k=3,
    )

    bf = _StubRetriever.captured[0]["retrieval_config"][
        "vectorSearchConfiguration"
    ]["filter"]
    # Top-level is andAll([authz_block, user_block]).
    assert "andAll" in bf
    assert len(bf["andAll"]) == 2
    user_clause = bf["andAll"][1]
    assert user_clause == {"equals": {"key": "document_type", "value": "pdf"}}


def test_search_user_filter_cannot_widen_tenant_scope(monkeypatch):
    """A user filter that names tenant_id is appended — never replacing the authz clause.

    The Bedrock retrieval will then receive an impossibly-narrow query
    (tenant-a AND tenant-b), but the server clause is still in place.
    """
    monkeypatch.setattr(
        vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever
    )

    vector_search.search(
        query="x",
        knowledge_base_id="KB",
        region="us-east-1",
        tenant_id="tenant-a",
        user_filter={"key": "tenant_id", "equals": "tenant-b"},
        top_k=3,
    )

    bf = _StubRetriever.captured[0]["retrieval_config"][
        "vectorSearchConfiguration"
    ]["filter"]
    # Server tenant clause is the first child; the user's tenant_id clause is appended.
    assert bf["andAll"][0] == {"equals": {"key": "tenant_id", "value": "tenant-a"}}
    assert bf["andAll"][1] == {"equals": {"key": "tenant_id", "value": "tenant-b"}}


def test_search_rejects_empty_query(monkeypatch):
    monkeypatch.setattr(
        vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever
    )
    with pytest.raises(ValueError):
        vector_search.search(
            query="   ",
            knowledge_base_id="KB",
            region="us-east-1",
            tenant_id="tenant-a",
        )


def test_search_rejects_zero_top_k(monkeypatch):
    monkeypatch.setattr(
        vector_search, "AmazonKnowledgeBasesRetriever", _StubRetriever
    )
    with pytest.raises(ValueError):
        vector_search.search(
            query="x",
            knowledge_base_id="KB",
            region="us-east-1",
            tenant_id="tenant-a",
            top_k=0,
        )
