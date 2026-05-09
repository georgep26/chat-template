"""Shared models for the document ingestion Lambda."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class S3ObjectRef:
    """Reference to an S3 object."""

    bucket: str
    key: str

    @property
    def uri(self) -> str:
        """Return the S3 URI for this object."""
        return f"s3://{self.bucket}/{self.key}"


@dataclass
class IngestionResult:
    """Result returned by one document ingestion attempt."""

    landing_s3_uri: str
    status: str
    job_id: str | None = None
    document_type: str | None = None
    document_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    metadata_s3_uri: str | None = None
    raw_s3_uri: str | None = None
    message: str | None = None
    document_metadata: dict[str, Any] = field(default_factory=dict)
    chunks: list[dict[str, Any]] = field(default_factory=list)
