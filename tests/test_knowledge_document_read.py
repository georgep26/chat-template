"""Tests for the shared knowledge.document_read.DocumentReadStore."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from knowledge.document_read import DocumentReadStore


def _string_col(v: str) -> Dict[str, Any]:
    return {"stringValue": v}


def _null_col() -> Dict[str, Any]:
    return {"isNull": True}


def _row(
    *,
    document_id: str,
    tenant_id: str = "tenant-a",
    access_tier: str = "public",
    document_title: str = "Title",
    location: str = "s3://kb-bucket/kb_raw/doc-1/original/file.pdf",
    metadata: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    return [
        _string_col(document_id),       # document_id
        _string_col(tenant_id),         # tenant_id
        _string_col("default"),         # collection_id
        _string_col(document_title),    # document_title
        _null_col(),                    # document_description
        _string_col("file.pdf"),        # filename
        _string_col("pdf"),             # document_type
        _string_col("current"),         # source_status
        _string_col(access_tier),       # access_tier
        _string_col(location),          # location
        _string_col(location),          # source_location
        _null_col(),                    # publish_date
        _null_col(),                    # version_label
        _null_col(),                    # jurisdiction
        _null_col(),                    # code_family
        _string_col("2026-05-09T00:00:00Z"),  # uploaded_at
        _string_col("2026-05-09T00:00:00Z"),  # ingested_at
        _string_col(json.dumps(metadata or {})),  # metadata
    ]


class FakeRdsClient:
    """Captures execute_statement calls and returns prepared records."""

    def __init__(self, records: List[List[Dict[str, Any]]] | None = None) -> None:
        self.records = records or []
        self.calls: List[Dict[str, Any]] = []

    def execute_statement(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return {"records": list(self.records)}


def _store(rds: FakeRdsClient) -> DocumentReadStore:
    return DocumentReadStore(
        db_cluster_arn="arn:cluster",
        db_credentials_secret_arn="arn:secret",
        database_name="appdb",
        rds_data_client=rds,
    )


def test_get_document_emits_tenant_clause_and_excludes_failed():
    rds = FakeRdsClient(records=[_row(document_id="doc-1")])
    store = _store(rds)

    row = store.get_document(document_id="doc-1", tenant_id="tenant-a")

    assert row is not None
    assert row["document_id"] == "doc-1"
    assert row["tenant_id"] == "tenant-a"
    assert row["location"].startswith("s3://kb-bucket/kb_raw/")

    sql = rds.calls[0]["sql"]
    assert "FROM document_catalog" in sql
    assert "document_id = :document_id" in sql
    assert "source_status <> 'failed'" in sql
    assert ":_authz_tenant_id" in sql

    param_names = [p["name"] for p in rds.calls[0]["parameters"]]
    assert "document_id" in param_names
    assert "_authz_tenant_id" in param_names


def test_get_document_returns_none_when_no_records():
    rds = FakeRdsClient(records=[])
    store = _store(rds)

    row = store.get_document(document_id="missing", tenant_id="tenant-a")
    assert row is None


def test_get_document_with_access_tiers_emits_in_clause():
    rds = FakeRdsClient(records=[_row(document_id="doc-1")])
    store = _store(rds)

    store.get_document(
        document_id="doc-1",
        tenant_id="tenant-a",
        access_tiers=["public", "internal"],
    )
    sql = rds.calls[0]["sql"]
    assert "access_tier IN" in sql
    param_names = [p["name"] for p in rds.calls[0]["parameters"]]
    assert "_authz_tier_0" in param_names
    assert "_authz_tier_1" in param_names


def test_get_document_parses_metadata_json_column():
    rds = FakeRdsClient(
        records=[_row(document_id="doc-1", metadata={"jurisdiction": "SD"})]
    )
    store = _store(rds)

    row = store.get_document(document_id="doc-1", tenant_id="tenant-a")
    assert row is not None
    assert row["metadata"] == {"jurisdiction": "SD"}


def test_create_document_read_store_returns_none_when_arn_missing():
    from knowledge.document_read import create_document_read_store

    config = {
        "rag_chat": {
            "chat_history_store": {
                "db_cluster_arn": "${DB_CLUSTER_ARN}",  # unhydrated placeholder
                "db_credentials_secret_arn": "arn:secret",
                "database_name": "appdb",
            }
        }
    }
    assert create_document_read_store(config) is None
