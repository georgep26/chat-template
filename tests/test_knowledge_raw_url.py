"""Tests for knowledge.raw_url.build_raw_document_url."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from knowledge.raw_url import (
    ALLOWED_DISPOSITIONS,
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    RawUrlError,
    build_raw_document_url,
)


class _StubS3:
    """Captures generate_presigned_url calls and returns a deterministic URL."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def generate_presigned_url(self, op: str, **kwargs: Any) -> str:
        self.calls.append({"op": op, **kwargs})
        # Construct a stable string for assertions.
        params = kwargs.get("Params", {})
        return (
            f"https://example.s3.amazonaws.com/{params.get('Key', '')}"
            f"?Signature=x&ExpiresIn={kwargs.get('ExpiresIn', '')}"
        )


def test_happy_path_returns_url_filename_and_content_type():
    s3 = _StubS3()
    result = build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/2021-irc.pdf",
        expected_bucket="kb-bucket",
        s3_client=s3,
    )

    assert result.url.startswith("https://example.s3.amazonaws.com/kb_raw/")
    assert result.filename == "2021-irc.pdf"
    assert result.content_type == "application/pdf"
    assert result.disposition == "inline"
    assert result.ttl_seconds == DEFAULT_TTL_SECONDS
    assert result.expires_at.endswith("Z")

    call = s3.calls[0]
    params = call["Params"]
    assert params["Bucket"] == "kb-bucket"
    assert params["Key"] == "kb_raw/doc-1/original/2021-irc.pdf"
    assert params["ResponseContentDisposition"].startswith("inline; filename=")
    assert "2021-irc.pdf" in params["ResponseContentDisposition"]
    assert call["ExpiresIn"] == DEFAULT_TTL_SECONDS


def test_rejects_bucket_mismatch():
    s3 = _StubS3()
    with pytest.raises(RawUrlError):
        build_raw_document_url(
            location="s3://other-bucket/kb_raw/doc-1/original/x.pdf",
            expected_bucket="kb-bucket",
            s3_client=s3,
        )
    assert s3.calls == []


def test_rejects_non_kb_raw_prefix():
    s3 = _StubS3()
    with pytest.raises(RawUrlError):
        build_raw_document_url(
            location="s3://kb-bucket/kb_index/doc-1/pages/x.md",
            expected_bucket="kb-bucket",
            s3_client=s3,
        )
    assert s3.calls == []


def test_ttl_clamped_to_minimum():
    s3 = _StubS3()
    result = build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/x.pdf",
        expected_bucket="kb-bucket",
        s3_client=s3,
        ttl_seconds=1,
    )
    assert result.ttl_seconds == MIN_TTL_SECONDS
    assert s3.calls[0]["ExpiresIn"] == MIN_TTL_SECONDS


def test_ttl_clamped_to_maximum():
    s3 = _StubS3()
    result = build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/x.pdf",
        expected_bucket="kb-bucket",
        s3_client=s3,
        ttl_seconds=99_999,
    )
    assert result.ttl_seconds == MAX_TTL_SECONDS
    assert s3.calls[0]["ExpiresIn"] == MAX_TTL_SECONDS


def test_rejects_bad_disposition():
    s3 = _StubS3()
    with pytest.raises(RawUrlError):
        build_raw_document_url(
            location="s3://kb-bucket/kb_raw/doc-1/original/x.pdf",
            expected_bucket="kb-bucket",
            s3_client=s3,
            disposition="evil",
        )


def test_attachment_disposition_sets_attachment_filename_header():
    s3 = _StubS3()
    build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/manual.pdf",
        expected_bucket="kb-bucket",
        s3_client=s3,
        disposition="attachment",
    )
    cd = s3.calls[0]["Params"]["ResponseContentDisposition"]
    assert cd.startswith("attachment; filename=")
    assert "manual.pdf" in cd


def test_page_fragment_appended_after_signed_query():
    s3 = _StubS3()
    result = build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/manual.pdf",
        expected_bucket="kb-bucket",
        s3_client=s3,
        page=107,
    )
    # Fragment is unsigned and must come AFTER the signed query string.
    assert result.url.endswith("#page=107")
    assert "?Signature=" in result.url
    # Fragment is after the query — i.e. the '#' index is after the '?' index.
    assert result.url.index("#") > result.url.index("?")


def test_response_filename_override_used_in_content_disposition():
    s3 = _StubS3()
    result = build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/raw-name.pdf",
        expected_bucket="kb-bucket",
        s3_client=s3,
        response_filename="friendly-name.pdf",
    )
    assert result.filename == "friendly-name.pdf"
    cd = s3.calls[0]["Params"]["ResponseContentDisposition"]
    assert "friendly-name.pdf" in cd
    assert "raw-name.pdf" not in cd


def test_unknown_extension_falls_back_to_octet_stream():
    s3 = _StubS3()
    result = build_raw_document_url(
        location="s3://kb-bucket/kb_raw/doc-1/original/datafile.xyz",
        expected_bucket="kb-bucket",
        s3_client=s3,
    )
    assert result.content_type == "application/octet-stream"


def test_rejects_negative_page():
    s3 = _StubS3()
    with pytest.raises(RawUrlError):
        build_raw_document_url(
            location="s3://kb-bucket/kb_raw/doc-1/original/x.pdf",
            expected_bucket="kb-bucket",
            s3_client=s3,
            page=0,
        )


def test_rejects_non_s3_uri():
    s3 = _StubS3()
    with pytest.raises(RawUrlError):
        build_raw_document_url(
            location="https://example.com/file.pdf",
            expected_bucket="kb-bucket",
            s3_client=s3,
        )


def test_allowed_dispositions_constant_matches_validation():
    assert "inline" in ALLOWED_DISPOSITIONS
    assert "attachment" in ALLOWED_DISPOSITIONS
