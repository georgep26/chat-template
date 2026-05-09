"""Tests for the document ingestion Lambda landing-zone workflow."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from document_ingestion_lambda import main as ingestion_main
from document_ingestion_lambda.file_type_processors.pdf import generate_document_id
from document_ingestion_lambda.main import (
    DocumentIngestionService,
    extract_s3_object_refs,
)
from document_ingestion_lambda.models import S3ObjectRef


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, object]] = {}

    def add_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        last_modified: datetime | None = None,
    ) -> None:
        self.objects[(bucket, key)] = {
            "Body": body,
            "ContentType": content_type,
            "LastModified": last_modified or datetime(2026, 5, 9, tzinfo=timezone.utc),
        }

    def head_object(self, Bucket: str, Key: str) -> dict[str, object]:
        stored = self.objects[(Bucket, Key)]
        return {
            "ContentType": stored["ContentType"],
            "LastModified": stored["LastModified"],
        }

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        Path(Filename).write_bytes(self.objects[(Bucket, Key)]["Body"])  # type: ignore[arg-type]

    def copy_object(
        self,
        Bucket: str,
        Key: str,
        CopySource: dict[str, str],
        MetadataDirective: str,
    ) -> None:
        del MetadataDirective
        source = self.objects[(CopySource["Bucket"], CopySource["Key"])]
        self.objects[(Bucket, Key)] = dict(source)

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "ContentType": ContentType,
            "LastModified": datetime.now(timezone.utc),
        }


def test_extracts_s3_records_from_eventbridge_event():
    event = {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {
            "bucket": {"name": "docs-bucket"},
            "object": {"key": "kb_landing/tenant/collection/My+File.pdf"},
        },
    }

    records = list(extract_s3_object_refs(event))

    assert records == [
        S3ObjectRef(bucket="docs-bucket", key="kb_landing/tenant/collection/My File.pdf")
    ]


def test_extracts_s3_records_from_direct_test_event():
    records = list(extract_s3_object_refs({"bucket": "docs-bucket", "key": "kb_landing/a.pdf"}))

    assert records == [S3ObjectRef(bucket="docs-bucket", key="kb_landing/a.pdf")]


def test_process_pdf_copies_raw_file_and_writes_metadata():
    fake_s3 = FakeS3Client()
    fake_s3.add_object(
        "source-bucket",
        "kb_landing/user-123/building-code/Code Sample 2021.pdf",
        b"%PDF-1.4\nnot a complete pdf",
        "application/pdf",
    )
    config = {
        "environment": "sandbox",
        "document_bucket_name": "documents-bucket",
        "landing_prefix": "kb_landing/",
        "raw_prefix": "kb_raw/",
        "index_prefix": "kb_index/",
        "artifacts_prefix": "kb_artifacts/",
        "catalog_prefix": "kb_catalog/sandbox/",
        "default_tenant_id": "sandbox",
        "default_collection_id": "default",
        "pdf_text_probe_pages": 5,
        "pdf_text_available_min_chars": 100,
    }
    service = DocumentIngestionService(fake_s3, config, random_suffix=lambda: "abc123")

    result = service.process_object(
        S3ObjectRef(
            bucket="source-bucket",
            key="kb_landing/user-123/building-code/Code Sample 2021.pdf",
        )
    )

    assert result.status == "processed"
    assert result.document_type == "pdf"
    assert result.document_id == "pdf_code_sample_2021_abc123"
    assert (
        result.raw_s3_uri
        == "s3://documents-bucket/kb_raw/pdf_code_sample_2021_abc123/original/Code Sample 2021.pdf"
    )

    metadata_obj = fake_s3.objects[
        ("documents-bucket", "kb_raw/pdf_code_sample_2021_abc123/document_metadata.json")
    ]
    metadata = json.loads(metadata_obj["Body"].decode("utf-8"))  # type: ignore[union-attr]

    assert metadata["metadata_schema_version"] == "0.1"
    assert metadata["landing_s3_uri"] == (
        "s3://source-bucket/kb_landing/user-123/building-code/Code Sample 2021.pdf"
    )
    assert metadata["document_id"] == "pdf_code_sample_2021_abc123"
    assert metadata["document_title"] == "Code Sample 2021"
    assert metadata["source_status"] == "processing"
    assert metadata["tenant_id"] == "user-123"
    assert metadata["collection_id"] == "building-code"
    assert metadata["processing"]["stage"] == "initial_metadata_complete"
    assert metadata["processing"]["chunking"]["status"] == "placeholder"


def test_non_pdf_landing_object_raises_not_implemented_without_copying():
    fake_s3 = FakeS3Client()
    fake_s3.add_object(
        "documents-bucket",
        "kb_landing/user-123/building-code/slides.pptx",
        b"pptx bytes",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    service = DocumentIngestionService(
        fake_s3,
        {
            "document_bucket_name": "documents-bucket",
            "landing_prefix": "kb_landing/",
            "raw_prefix": "kb_raw/",
            "default_tenant_id": "default",
            "default_collection_id": "default",
            "pdf_text_probe_pages": 5,
            "pdf_text_available_min_chars": 100,
        },
        random_suffix=lambda: "abc123",
    )

    try:
        service.process_object(
            S3ObjectRef(
                bucket="documents-bucket",
                key="kb_landing/user-123/building-code/slides.pptx",
            )
        )
    except NotImplementedError as exc:
        assert str(exc) == "PowerPoint ingestion is not implemented yet."
    else:
        raise AssertionError("Expected PowerPoint ingestion to raise NotImplementedError")

    assert len(fake_s3.objects) == 1


def test_document_id_does_not_repeat_year_from_title():
    document_id = generate_document_id("pdf", "Code Sample 2021", "Code Sample 2021.pdf", "abc123")

    assert document_id == "pdf_code_sample_2021_abc123"


def test_lambda_handler_reads_ingestion_section_from_app_config():
    fake_s3 = FakeS3Client()
    fake_s3.add_object(
        "source-bucket",
        "custom_landing/user-123/building-code/Code Sample 2021.pdf",
        b"%PDF-1.4\nnot a complete pdf",
        "application/pdf",
    )
    original_env = dict(os.environ)
    original_read_config = ingestion_main.read_config
    original_boto3_client = ingestion_main.boto3.client

    try:
        os.environ.clear()
        os.environ.update({"APP_CONFIG_PATH": "config/test/app_config.yaml"})
        ingestion_main.read_config = lambda path: {
            "ingestion": {
                "document_bucket_name": "config-bucket",
                "landing_prefix": "custom_landing/",
                "raw_prefix": "custom_raw/",
                "default_tenant_id": "tenant-from-config",
                "default_collection_id": "collection-from-config",
                "pdf_text_probe_pages": 9,
                "pdf_text_available_min_chars": 250,
            }
        }
        ingestion_main.boto3.client = lambda service_name: fake_s3

        response = ingestion_main.lambda_handler(
            {
                "bucket": "source-bucket",
                "key": "custom_landing/user-123/building-code/Code Sample 2021.pdf",
            },
            None,
        )
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        ingestion_main.read_config = original_read_config
        ingestion_main.boto3.client = original_boto3_client

    assert response["processed_count"] == 1
    assert response["results"][0]["status"] == "processed"
    metadata_keys = [
        key
        for bucket, key in fake_s3.objects
        if bucket == "config-bucket"
        and key.startswith("custom_raw/pdf_code_sample_2021_")
        and key.endswith("/document_metadata.json")
    ]
    assert len(metadata_keys) == 1
