"""PDF document processor for the document ingestion Lambda."""

from __future__ import annotations

import json
import math
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from document_ingestion_lambda.file_type_processors.common import (
    infer_landing_scope,
    require_default_access_tier,
)
from document_ingestion_lambda.models import IngestionResult, S3ObjectRef
from document_ingestion_lambda.triage import (
    PdfTriageResult,
    TriageConfig,
    multimodal_triage,
    page_observations_as_dicts,
    triage_debug_dict,
    triage_summary_dict,
)
from utils.logger import get_logger

# Section chunking is imported lazily inside `process()` to keep this module
# import-graph free of cycles (section_chunking imports back from this module).

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:  # pragma: no cover - only imported in real runtimes
    import boto3
except ImportError:
    boto3 = None  # type: ignore[assignment]


log = get_logger(__name__)


def _maybe_boto3_client(
    service: str,
    region: str | None = None,
    config: Any | None = None,
) -> Any | None:
    """Best-effort boto3 client construction.

    Returns None when boto3 isn't importable (lightweight tests) so callers
    can treat the absence as 'skip this step'. Tolerates test-stub
    ``boto3.client`` replacements that don't accept ``region_name``.
    """
    if boto3 is None:
        return None
    kwargs: dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    if config:
        kwargs["config"] = config
    try:
        return boto3.client(service, **kwargs)
    except TypeError:
        return boto3.client(service)


@dataclass
class PdfScanResult:
    """Metadata collected during an initial PDF scan."""

    extraction_status: str
    title: str | None = None
    page_count: int | None = None
    probed_pages: int = 0
    extracted_text_char_count: int = 0
    text_available: bool | None = None
    ocr_required: bool | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PdfPageText:
    """Text extracted from one PDF page."""

    page_number: int
    text: str


@dataclass
class PdfTextExtractionResult:
    """Text extracted from the full PDF for deterministic chunk generation."""

    extraction_status: str
    pages: list[PdfPageText] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class JoinedPageText:
    """Full joined text with page-offset mapping for shared coordinate space.

    Used by the Sonnet hierarchy call, the hierarchy locator, and the chunker
    so they all operate on the same char-offset coordinate system.
    """

    text: str
    page_offsets: dict[int, int]  # page_number → char offset of that page's start


def join_pages_with_markers(pages: list[PdfPageText]) -> JoinedPageText:
    """Build joined text with page markers and page→offset map.

    Each page is preceded by a marker line ``=== PAGE N ===\\n`` so both the
    hierarchy model and the locator algorithm can reference absolute page
    positions. The returned ``page_offsets`` map gives the char position of
    each marker's first character.
    """
    parts: list[str] = []
    page_offsets: dict[int, int] = {}
    cursor = 0
    for page in sorted(pages, key=lambda p: p.page_number):
        marker = f"=== PAGE {page.page_number} ===\n"
        page_offsets[page.page_number] = cursor
        parts.append(marker)
        cursor += len(marker)
        body = page.text if page.text else ""
        parts.append(body)
        cursor += len(body)
        parts.append("\n\n")
        cursor += 2
    return JoinedPageText(text="".join(parts), page_offsets=page_offsets)


@dataclass
class PdfExtraArtifact:
    """Additional S3 object generated while building a PDF chunk."""

    key: str
    body: bytes
    content_type: str


@dataclass
class PdfChunkArtifact:
    """S3 artifact and metadata generated for one PDF chunk."""

    key: str
    sidecar_key: str
    markdown: str
    sidecar_metadata: dict[str, Any]
    catalog_metadata: dict[str, Any]
    extra_objects: list[PdfExtraArtifact] = field(default_factory=list)


class PdfProcessor:
    """Processes PDF landing-zone objects into the durable document layout."""

    def __init__(
        self,
        s3_client: Any,
        ingestion_config: dict[str, Any],
        random_suffix: Callable[[], str],
        bedrock_client: Any | None = None,
        textract_client: Any | None = None,
    ) -> None:
        self._s3 = s3_client
        self._ingestion_config = ingestion_config
        self._random_suffix = random_suffix
        # Lazily created on first use. Tests inject stubs directly.
        self._bedrock_client = bedrock_client
        self._textract_client = textract_client

    def _get_bedrock_client(self) -> Any | None:
        """Return a Bedrock Runtime client, creating one on first use.

        Returns None when boto3 is not available (e.g. lightweight test envs);
        callers must treat this as "skip the Bedrock-backed step".
        """
        if self._bedrock_client is not None:
            return self._bedrock_client
        region = self._ingestion_config.get("pdf_triage_region") or "us-east-1"
        try:
            from botocore.config import Config as BotoConfig
            boto_config = BotoConfig(
                read_timeout=120,
                connect_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            )
        except ImportError:
            boto_config = None
        self._bedrock_client = _maybe_boto3_client("bedrock-runtime", region, config=boto_config)
        return self._bedrock_client

    def _get_textract_client(self) -> Any | None:
        if self._textract_client is not None:
            return self._textract_client
        # Textract is regional; align with Bedrock region so cross-region
        # data transfer doesn't surprise us.
        region = self._ingestion_config.get("pdf_triage_region") or "us-east-1"
        self._textract_client = _maybe_boto3_client("textract", region)
        return self._textract_client

    def _extract_text(
        self,
        *,
        pdf_path: Path,
        embedded_text_extraction: PdfTextExtractionResult,
        text_quality_report: Any,
        raw_bucket: str,
        raw_key: str,
    ) -> tuple[PdfTextExtractionResult, dict[str, Any] | None]:
        """Return text extraction, switching to Textract OCR when backend checks recommend it.

        The multimodal triage model only extracts visible page observations.
        OCR decisions are made by deterministic text-quality checks or an
        explicit ``pdf_always_ocr`` config override.
        """
        cfg = self._ingestion_config
        ocr_enabled = _as_bool(cfg.get("pdf_ocr_enabled"), default=True)
        always_ocr = _as_bool(cfg.get("pdf_always_ocr"), default=False)
        ocr_required = ocr_enabled and (
            always_ocr or bool(getattr(text_quality_report, "ocr_recommended", False))
        )
        if not ocr_required:
            return embedded_text_extraction, None

        from document_ingestion_lambda.file_type_processors.ocr import (
            OcrConfig,
            extract_pdf_text_via_ocr,
        )

        textract_client = self._get_textract_client()
        if textract_client is None:
            return (
                embedded_text_extraction,
                {"status": "skipped", "warnings": ["textract_client_unavailable"]},
            )
        config = OcrConfig(
            enabled=True,
            poll_interval_seconds=float(cfg.get("pdf_ocr_poll_interval_seconds") or 5.0),
            max_wait_seconds=float(cfg.get("pdf_ocr_max_wait_seconds") or 540.0),
        )
        extraction, ocr_result = extract_pdf_text_via_ocr(
            raw_s3_bucket=raw_bucket,
            raw_s3_key=raw_key,
            textract_client=textract_client,
            config=config,
        )
        diagnostics = {
            "status": ocr_result.status,
            "job_id": ocr_result.job_id,
            "page_count": ocr_result.page_count,
            "warnings": ocr_result.warnings,
        }
        return extraction, diagnostics

    def _merge_text_sources(
        self,
        *,
        embedded: "PdfTextExtractionResult",
        triage: "PdfTriageResult",
        text_quality_report: Any,
    ) -> list["PdfPageText"]:
        """Merge embedded and model text, preferring embedded except on flagged pages.

        Returns a list of PdfPageText ready for join_pages_with_markers().
        """
        model_text_by_page = {
            obs.page_number: obs.raw_visible_text
            for obs in triage.page_observations
            if not obs.parse_error and obs.raw_visible_text
        }
        suspicious_pages = set(
            text_quality_report.pages_over_text_mismatch_threshold
            if hasattr(text_quality_report, "pages_over_text_mismatch_threshold")
            else []
        )

        merged: list[PdfPageText] = []
        embedded_page_numbers: set[int] = set()
        for page in embedded.pages:
            embedded_page_numbers.add(page.page_number)
            model_text = model_text_by_page.get(page.page_number, "")
            if page.page_number in suspicious_pages and len(model_text) > len(page.text):
                merged.append(PdfPageText(page_number=page.page_number, text=model_text))
            else:
                merged.append(page)

        for page_number, text in model_text_by_page.items():
            if page_number not in embedded_page_numbers and text.strip():
                merged.append(PdfPageText(page_number=page_number, text=text))

        merged.sort(key=lambda p: p.page_number)
        return merged

    def _extract_assets(
        self,
        *,
        pdf_path: Path,
        raw_bucket: str,
        raw_key: str,
        document_metadata: dict[str, Any],
        destination_bucket: str,
        hierarchy: Any | None = None,
        triage: Any | None = None,
    ) -> tuple[list["PdfChunkArtifact"], dict[str, Any]]:
        """Build table + figure chunks using Scout-flagged page targeting.

        - Tables: per-page Textract sync ``AnalyzeDocument`` on each page Scout
          flagged with ``contains_table``. Scout's description is the chunk
          embedding text; Textract's grid is the chunk body.
        - Figures: Scout's description is the chunk text; the artifact is the
          full rendered page PNG. The RAG layer attaches the PNG to the
          multimodal answer model's context when the chunk is retrieved.
        """
        from document_ingestion_lambda.file_type_processors.figure_extraction_targeted import (
            TargetedFigureExtractionConfig,
            extract_figures_targeted,
        )
        from document_ingestion_lambda.file_type_processors.table_extraction_targeted import (
            TargetedTableExtractionConfig,
            extract_tables_targeted,
        )
        from document_ingestion_lambda.triage import _render_page_with_pdfium

        cfg = self._ingestion_config
        tables_enabled = _as_bool(cfg.get("pdf_table_extraction_enabled"), default=True)
        figures_enabled = _as_bool(cfg.get("pdf_figure_extraction_enabled"), default=True)
        if not tables_enabled and not figures_enabled:
            return [], {
                "table_extraction": {"status": "skipped", "warnings": ["disabled"]},
                "figure_extraction": {"status": "skipped", "warnings": ["disabled"]},
            }
        if triage is None or triage.triage_status != "completed":
            return [], {
                "table_extraction": {"status": "skipped", "warnings": ["triage_unavailable"]},
                "figure_extraction": {"status": "skipped", "warnings": ["triage_unavailable"]},
            }

        artifacts: list["PdfChunkArtifact"] = []
        diagnostics: dict[str, Any] = {}
        render_scale = float(cfg.get("pdf_triage_render_scale") or 2.0)

        if tables_enabled:
            textract_client = self._get_textract_client()
            if textract_client is None:
                diagnostics["table_extraction"] = {
                    "status": "skipped",
                    "warnings": ["textract_client_unavailable"],
                }
            else:
                table_config = TargetedTableExtractionConfig(
                    enabled=True,
                    max_parallel_pages=int(
                        cfg.get("pdf_table_extraction_max_parallel_pages") or 6
                    ),
                    sync_timeout_seconds=int(
                        cfg.get("pdf_textract_sync_timeout_seconds") or 60
                    ),
                    max_tables=int(cfg.get("pdf_max_table_extractions") or 500),
                )
                table_artifacts, table_result = extract_tables_targeted(
                    pdf_path=pdf_path,
                    triage=triage,
                    hierarchy=hierarchy,
                    document_metadata=document_metadata,
                    destination_bucket=destination_bucket,
                    index_prefix=cfg["index_prefix"],
                    artifacts_prefix=cfg.get("artifacts_prefix") or "kb_artifacts/",
                    textract_client=textract_client,
                    page_renderer=_render_page_with_pdfium,
                    render_scale=render_scale,
                    config=table_config,
                )
                artifacts.extend(table_artifacts)
                diagnostics["table_extraction"] = {
                    "status": table_result.status,
                    "pages_processed": table_result.pages_processed,
                    "pages_failed": table_result.pages_failed,
                    "tables_detected": table_result.tables_detected,
                    "warnings": table_result.warnings,
                }
        else:
            diagnostics["table_extraction"] = {"status": "skipped", "warnings": ["disabled"]}

        if figures_enabled:
            figure_config = TargetedFigureExtractionConfig(
                enabled=True,
                render_scale=render_scale,
                max_figures=int(cfg.get("pdf_max_figure_extractions") or 200),
            )
            figure_artifacts, figure_result = extract_figures_targeted(
                pdf_path=pdf_path,
                triage=triage,
                hierarchy=hierarchy,
                document_metadata=document_metadata,
                destination_bucket=destination_bucket,
                index_prefix=cfg["index_prefix"],
                artifacts_prefix=cfg.get("artifacts_prefix") or "kb_artifacts/",
                page_renderer=_render_page_with_pdfium,
                config=figure_config,
            )
            artifacts.extend(figure_artifacts)
            diagnostics["figure_extraction"] = {
                "status": figure_result.status,
                "figure_count": figure_result.figure_count,
                "pages_with_figures": figure_result.pages_with_figures,
                "warnings": figure_result.warnings,
            }
        else:
            diagnostics["figure_extraction"] = {"status": "skipped", "warnings": ["disabled"]}

        return artifacts, diagnostics

    def _build_chunk_artifacts(
        self,
        *,
        text_extraction: PdfTextExtractionResult,
        hierarchy_build: Any | None,
        document_metadata: dict[str, Any],
        destination_bucket: str,
    ) -> tuple[list["PdfChunkArtifact"], str]:
        """Decide chunking strategy and emit chunks."""
        cfg = self._ingestion_config
        embedding_token_limit = int(cfg["pdf_embedding_chunk_token_limit_titan_v2"])
        embedding_char_limit = int(cfg["pdf_embedding_chunk_char_limit_titan_v2"])

        hierarchy_enabled = _as_bool(cfg.get("pdf_hierarchy_chunking_enabled"), default=True)
        if hierarchy_enabled and hierarchy_build is not None and hierarchy_build.hierarchy.nodes:
            from document_ingestion_lambda.file_type_processors.hierarchy_chunking import (
                HierarchyChunkingConfig,
                build_hierarchy_chunk_artifacts,
            )

            artifacts = build_hierarchy_chunk_artifacts(
                joined_text=hierarchy_build.joined_text,
                hierarchy=hierarchy_build.hierarchy,
                document_metadata=document_metadata,
                destination_bucket=destination_bucket,
                index_prefix=cfg["index_prefix"],
                raw_prefix=cfg["raw_prefix"],
                config=HierarchyChunkingConfig(
                    target_min_tokens=int(cfg.get("pdf_hierarchy_target_min_tokens") or 300),
                    target_max_tokens=int(cfg.get("pdf_hierarchy_target_max_tokens") or 3000),
                    hard_max_tokens=int(cfg.get("pdf_hierarchy_hard_max_tokens") or 7000),
                    merge_small_leaf_nodes=_as_bool(
                        cfg.get("pdf_hierarchy_merge_small_leaf_nodes"), default=True
                    ),
                    split_large_nodes_by_children=_as_bool(
                        cfg.get("pdf_hierarchy_split_large_nodes_by_children"), default=True
                    ),
                    store_index=_as_bool(cfg.get("pdf_hierarchy_store_index"), default=True),
                    inline_index_max_nodes=int(
                        cfg.get("pdf_hierarchy_inline_index_max_nodes") or 40
                    ),
                    embedding_chunk_token_limit=embedding_token_limit,
                    embedding_chunk_char_limit=embedding_char_limit,
                ),
            )
            if artifacts:
                return artifacts, "adaptive_hierarchy"

        # Fall back to the deterministic page chunker.
        artifacts = build_page_chunk_artifacts(
            text_extraction=text_extraction,
            document_metadata=document_metadata,
            destination_bucket=destination_bucket,
            index_prefix=cfg["index_prefix"],
            default_chunk_tokens=int(cfg["pdf_default_chunk_tokens"]),
            chunk_overlap=float(cfg["pdf_default_chunk_overlap"]),
            embedding_chunk_token_limit=embedding_token_limit,
        )
        return artifacts, "pdf_page_text"

    def _run_triage(self, pdf_path: Path, scan: PdfScanResult) -> PdfTriageResult:
        """Run multimodal triage when enabled; always-recoverable.

        Returns a "skipped" result when triage is disabled or boto3 is
        unavailable, and a "failed" result on any exception. The caller
        treats both as "no signal, proceed with deterministic defaults".
        """
        config = self._build_triage_config()
        if not config.enabled:
            return PdfTriageResult.skipped("triage_disabled")
        page_count = scan.page_count or 0
        if page_count <= 0:
            return PdfTriageResult.skipped("no_page_count")

        client = self._get_bedrock_client()
        if client is None:
            return PdfTriageResult.skipped("bedrock_client_unavailable")

        try:
            return multimodal_triage(
                pdf_path=pdf_path,
                page_count=page_count,
                config=config,
                bedrock_client=client,
            )
        except Exception as exc:
            return PdfTriageResult.failed(
                f"triage_unexpected_error:{type(exc).__name__}"
            )

    def _build_triage_config(self) -> TriageConfig:
        cfg = self._ingestion_config
        return TriageConfig(
            enabled=_as_bool(cfg.get("pdf_triage_enabled"), default=True),
            model_id=str(cfg.get("pdf_triage_model_id") or "us.meta.llama4-scout-17b-instruct-v1:0"),
            region=str(cfg.get("pdf_triage_region") or "us-east-1"),
            front_page_sample_count=int(cfg.get("pdf_front_page_sample_count") or 5),
            random_page_sample_count=int(cfg.get("pdf_random_page_sample_count") or 5),
            full_page_triage_enabled=_as_bool(
                cfg.get("pdf_full_page_triage_enabled"), default=False
            ),
            ocr_threshold=float(cfg.get("pdf_triage_ocr_threshold") or 0.30),
            hierarchy_threshold=float(cfg.get("pdf_triage_hierarchy_threshold") or 0.50),
            temperature=float(cfg.get("pdf_triage_temperature") or 0.0),
            max_tokens_per_page=int(cfg.get("pdf_triage_max_tokens_per_page") or 8192),
            render_scale=float(cfg.get("pdf_triage_render_scale") or 2.0),
            parallel_workers=int(cfg.get("pdf_triage_parallel_workers") or 6),
        )

    def process(
        self,
        obj: S3ObjectRef,
        destination_bucket: str,
        head: dict[str, Any],
    ) -> IngestionResult:
        """Process a PDF S3 object into raw storage and metadata."""
        filename = Path(obj.key).name
        access_tier = require_default_access_tier(self._ingestion_config)
        tenant_id, collection_id = infer_landing_scope(
            obj.key,
            self._ingestion_config["landing_prefix"],
            self._ingestion_config["default_tenant_id"],
            self._ingestion_config["default_collection_id"],
        )

        tmp_path = Path(tempfile.gettempdir()) / f"ingest-{uuid.uuid4()}-{filename}"
        try:
            self._s3.download_file(obj.bucket, obj.key, str(tmp_path))
            pdf_scan = scan_pdf(
                tmp_path,
                int(self._ingestion_config["pdf_text_probe_pages"]),
                int(self._ingestion_config["pdf_text_available_min_chars"]),
            )

            triage = self._run_triage(tmp_path, pdf_scan)

            title = pdf_scan.title or title_from_filename(filename)
            document_id = generate_document_id("pdf", title, filename, self._random_suffix())
            triage_config = self._build_triage_config()
            debug_enabled = _as_bool(
                self._ingestion_config.get("debug_artifacts_enabled"),
                default=False,
            )
            debug_include_model_payloads = _as_bool(
                self._ingestion_config.get("debug_include_model_payloads"),
                default=False,
            )
            log.info(
                "PDF triage complete: document_id=%s status=%s full_page=%s sampled_pages=%s observations=%s warnings=%s",
                document_id,
                triage.triage_status,
                triage.full_page_triage_enabled,
                len(triage.sampled_pages),
                len(triage.page_observations),
                triage.warnings,
            )

            raw_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "original",
                filename,
            )
            metadata_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "document_metadata.json",
            )

            self._s3.copy_object(
                Bucket=destination_bucket,
                Key=raw_key,
                CopySource={"Bucket": obj.bucket, "Key": obj.key},
                MetadataDirective="COPY",
            )

            uploaded_at = _isoformat(head.get("LastModified"))
            now = datetime.now(timezone.utc).isoformat()
            raw_s3_uri = f"s3://{destination_bucket}/{raw_key}"
            metadata = {
                "metadata_schema_version": self._ingestion_config["metadata_schema_version"],
                "record_type": "document",
                "landing_s3_uri": obj.uri,
                "document_id": document_id,
                "document_title": title,
                "document_description": build_document_description("pdf", title, pdf_scan),
                "document_type": "pdf",
                "filename": filename,
                "location": raw_s3_uri,
                "source_location": raw_s3_uri,
                "uploaded_at": uploaded_at,
                "ingested_at": now,
                "source_status": "processing",
                "access_tier": access_tier,
                "tenant_id": tenant_id,
                "collection_id": collection_id,
                "custom_metadata": {},
                "processing": {
                    "stage": "initial_metadata_complete",
                    "pdf": asdict(pdf_scan),
                    "triage": triage_summary_dict(triage),
                    "chunking": {
                        "status": "not_started",
                    },
                },
            }

            triage_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "triage",
                "page_observations.json",
            )
            text_quality_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "triage",
                "text_quality_report.json",
            )
            hierarchy_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "document_hierarchy.json",
            )
            nova_triage_debug_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "debug",
                "triage_debug.json",
            )

            embedded_text_extraction = extract_pdf_text_pages(tmp_path)
            from document_ingestion_lambda.file_type_processors.text_quality import (
                build_text_quality_report,
            )

            text_quality_report = build_text_quality_report(
                text_extraction=embedded_text_extraction,
                triage=triage,
                page_mismatch_threshold=float(
                    self._ingestion_config.get("pdf_triage_text_mismatch_threshold") or 0.50
                ),
                document_mismatch_threshold=float(
                    self._ingestion_config.get("pdf_triage_document_text_mismatch_threshold")
                    or 0.30
                ),
                min_visible_chars=int(
                    self._ingestion_config.get("pdf_text_quality_min_visible_chars")
                    or self._ingestion_config.get("pdf_text_available_min_chars")
                    or 100
                ),
            )
            log.info(
                "PDF text quality complete: document_id=%s ocr_recommended=%s suspicious_pages=%s/%s reason=%s",
                document_id,
                text_quality_report.ocr_recommended,
                len(text_quality_report.pages_over_text_mismatch_threshold),
                text_quality_report.triaged_page_count,
                text_quality_report.reason,
            )
            self._s3.put_object(
                Bucket=destination_bucket,
                Key=triage_key,
                Body=json.dumps(
                    {"page_observations": page_observations_as_dicts(triage)},
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
                ContentType="application/json",
            )
            self._s3.put_object(
                Bucket=destination_bucket,
                Key=text_quality_key,
                Body=json.dumps(
                    text_quality_report.to_dict(),
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
                ContentType="application/json",
            )
            if debug_enabled:
                self._s3.put_object(
                    Bucket=destination_bucket,
                    Key=nova_triage_debug_key,
                    Body=json.dumps(
                        triage_debug_dict(
                            triage,
                            config=triage_config,
                            include_model_payloads=debug_include_model_payloads,
                        ),
                        indent=2,
                        sort_keys=True,
                    ).encode("utf-8"),
                    ContentType="application/json",
                )
                log.info(
                    "Wrote Nova Lite triage debug artifact: document_id=%s s3_uri=s3://%s/%s",
                    document_id,
                    destination_bucket,
                    nova_triage_debug_key,
                )

            # --- Text extraction: embedded + OCR fallback ---
            text_extraction, ocr_diagnostics = self._extract_text(
                pdf_path=tmp_path,
                embedded_text_extraction=embedded_text_extraction,
                text_quality_report=text_quality_report,
                raw_bucket=destination_bucket,
                raw_key=raw_key,
            )
            if ocr_diagnostics is not None:
                metadata["processing"]["ocr"] = ocr_diagnostics

            # --- Per-page text merge: embedded (source of truth) + model gap-fill ---
            merged_pages = self._merge_text_sources(
                embedded=text_extraction,
                triage=triage,
                text_quality_report=text_quality_report,
            )
            joined = join_pages_with_markers(merged_pages)

            # --- Persist model_extracted_text.json ---
            model_text_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "triage",
                "model_extracted_text.json",
            )
            model_text_pages = []
            for obs in triage.page_observations:
                if not obs.parse_error and obs.raw_visible_text:
                    model_text_pages.append({
                        "page_number": obs.page_number,
                        "text": obs.raw_visible_text,
                        "char_count": len(obs.raw_visible_text),
                    })
            self._s3.put_object(
                Bucket=destination_bucket,
                Key=model_text_key,
                Body=json.dumps(
                    {
                        "extraction_model_id": triage_config.model_id,
                        "pages": model_text_pages,
                        "total_pages_with_text": len(model_text_pages),
                        "total_chars": sum(p["char_count"] for p in model_text_pages),
                    },
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
                ContentType="application/json",
            )
            log.info(
                "Wrote model_extracted_text.json: document_id=%s pages=%s total_chars=%s",
                document_id,
                len(model_text_pages),
                sum(p["char_count"] for p in model_text_pages),
            )

            # --- Hierarchy extraction via Sonnet 4.6 ---
            hierarchy_build = None
            profile_diagnostics: dict[str, Any] = {"profile_id": "plain_document"}
            if _as_bool(self._ingestion_config.get("pdf_hierarchy_enabled"), default=True):
                from document_ingestion_lambda.file_type_processors.hierarchy_locator import (
                    build_hierarchy_from_sonnet_nodes,
                )
                from document_ingestion_lambda.file_type_processors.hierarchy_sonnet import (
                    HierarchySonnetConfig,
                    extract_hierarchy,
                )
                from document_ingestion_lambda.file_type_processors.parser_profiles import (
                    select_parser_profile,
                )

                profile, profile_diagnostics = select_parser_profile(
                    title=title,
                    filename=filename,
                    triage=triage,
                )

                sonnet_config = HierarchySonnetConfig(
                    model_id=str(
                        self._ingestion_config.get("pdf_hierarchy_model_id")
                        or "us.anthropic.claude-sonnet-4-6"
                    ),
                    region=str(
                        self._ingestion_config.get("pdf_triage_region") or "us-east-1"
                    ),
                    max_output_tokens=int(
                        self._ingestion_config.get("pdf_hierarchy_max_output_tokens") or 32000
                    ),
                    temperature=0.0,
                    single_call_max_pages=int(
                        self._ingestion_config.get("pdf_hierarchy_single_call_max_pages") or 100
                    ),
                )

                # Build a Bedrock client with a longer timeout for the Sonnet call.
                try:
                    from botocore.config import Config as BotoConfig
                    sonnet_timeout = int(
                        self._ingestion_config.get("pdf_hierarchy_read_timeout_seconds") or 600
                    )
                    sonnet_boto_config = BotoConfig(
                        read_timeout=sonnet_timeout,
                        connect_timeout=10,
                        retries={"max_attempts": 1, "mode": "standard"},
                    )
                except ImportError:
                    sonnet_boto_config = None
                sonnet_client = _maybe_boto3_client(
                    "bedrock-runtime", sonnet_config.region, config=sonnet_boto_config
                )

                if sonnet_client is not None:
                    sonnet_result = extract_hierarchy(
                        joined_text=joined.text,
                        page_count=len(merged_pages),
                        config=sonnet_config,
                        bedrock_client=sonnet_client,
                    )

                    # --- Diagnostic: persist Sonnet response + joined text ---
                    if debug_enabled:
                        sonnet_debug_key = join_s3_key(
                            self._ingestion_config["raw_prefix"],
                            document_id,
                            "debug",
                            "sonnet_hierarchy_response.json",
                        )
                        sonnet_debug_payload = {
                            "model_id": sonnet_config.model_id,
                            "status": sonnet_result.status,
                            "document_profile": sonnet_result.document_profile,
                            "heading_pattern": sonnet_result.heading_pattern,
                            "warnings": sonnet_result.warnings,
                            "usage": sonnet_result.usage,
                            "node_count": len(sonnet_result.nodes),
                            "raw_response_text": sonnet_result.raw_response_text,
                            "parsed_nodes": [
                                {
                                    "label": n.label,
                                    "title": n.title,
                                    "node_type": n.node_type,
                                    "parent_label": n.parent_label,
                                    "page_start": n.page_start,
                                    "page_end": n.page_end,
                                    "depth": n.depth,
                                    "body_anchor": n.body_anchor,
                                }
                                for n in sonnet_result.nodes
                            ],
                        }
                        self._s3.put_object(
                            Bucket=destination_bucket,
                            Key=sonnet_debug_key,
                            Body=json.dumps(
                                sonnet_debug_payload,
                                indent=2,
                                sort_keys=True,
                                ensure_ascii=False,
                            ).encode("utf-8"),
                            ContentType="application/json",
                        )
                        joined_debug_key = join_s3_key(
                            self._ingestion_config["raw_prefix"],
                            document_id,
                            "debug",
                            "joined_text.txt",
                        )
                        self._s3.put_object(
                            Bucket=destination_bucket,
                            Key=joined_debug_key,
                            Body=joined.text.encode("utf-8"),
                            ContentType="text/plain; charset=utf-8",
                        )
                        page_offsets_key = join_s3_key(
                            self._ingestion_config["raw_prefix"],
                            document_id,
                            "debug",
                            "page_offsets.json",
                        )
                        self._s3.put_object(
                            Bucket=destination_bucket,
                            Key=page_offsets_key,
                            Body=json.dumps(
                                joined.page_offsets,
                                indent=2,
                                sort_keys=True,
                            ).encode("utf-8"),
                            ContentType="application/json",
                        )
                        log.info(
                            "Wrote Sonnet hierarchy debug artifacts: document_id=%s sonnet=%s joined=%s page_offsets=%s",
                            document_id,
                            sonnet_debug_key,
                            joined_debug_key,
                            page_offsets_key,
                        )

                    if sonnet_result.status == "completed" and sonnet_result.nodes:
                        hierarchy = build_hierarchy_from_sonnet_nodes(
                            sonnet_nodes=sonnet_result.nodes,
                            joined=joined,
                            document_id=document_id,
                            source_location=raw_s3_uri,
                            profile=profile.profile_id if hasattr(profile, "profile_id") else str(profile_diagnostics.get("profile_id", "unknown")),
                            heading_pattern=sonnet_result.heading_pattern,
                        )
                        if hierarchy is not None:
                            from document_ingestion_lambda.file_type_processors.hierarchy import (
                                HierarchyBuildResult,
                            )
                            hierarchy_build = HierarchyBuildResult(
                                hierarchy=hierarchy,
                                joined_text=joined,
                                boundaries=[],
                                diagnostics=hierarchy.generation.get("validation", {}),
                            )
                            log.info(
                                "Sonnet hierarchy located: document_id=%s nodes=%s source=%s",
                                document_id,
                                len(hierarchy.nodes),
                                hierarchy.generation.get("source"),
                            )
                        else:
                            log.warning(
                                "Hierarchy locator returned None (below 50%% success); falling back to deterministic builder"
                            )
                    else:
                        log.warning(
                            "Sonnet hierarchy extraction failed: document_id=%s status=%s warnings=%s",
                            document_id,
                            sonnet_result.status,
                            sonnet_result.warnings,
                        )

                # Fallback to deterministic builder if Sonnet path didn't produce a hierarchy.
                if hierarchy_build is None:
                    from document_ingestion_lambda.file_type_processors.hierarchy import (
                        build_deterministic_hierarchy,
                    )
                    hierarchy_build = build_deterministic_hierarchy(
                        document_metadata=metadata,
                        text_extraction=text_extraction,
                        triage=triage,
                        profile=profile,
                        min_section_chars=int(
                            self._ingestion_config.get("pdf_section_min_chars") or 40
                        ),
                    )
                    log.info(
                        "Deterministic hierarchy fallback: document_id=%s nodes=%s validation=%s",
                        document_id,
                        len(hierarchy_build.hierarchy.nodes),
                        hierarchy_build.hierarchy.generation.get("validation"),
                    )

                hierarchy_location = f"s3://{destination_bucket}/{hierarchy_key}"
                metadata["hierarchy_available"] = bool(hierarchy_build.hierarchy.nodes)
                metadata["hierarchy_location"] = hierarchy_location
                metadata["hierarchy_profile"] = hierarchy_build.hierarchy.profile
                metadata["hierarchy_confidence"] = hierarchy_build.hierarchy.generation.get(
                    "confidence"
                )
                metadata["hierarchy_source"] = hierarchy_build.hierarchy.generation.get(
                    "source",
                    "deterministic",
                )
                metadata["available_lookup_modes"] = [
                    "semantic_search",
                    "chunk_fetch",
                    "hierarchy_fetch",
                    "raw_document_url",
                ]

            metadata["processing"]["text_quality"] = text_quality_report.to_dict()
            metadata["processing"]["parser_profile"] = profile_diagnostics
            if hierarchy_build is not None:
                metadata["processing"]["hierarchy"] = {
                    "status": hierarchy_build.hierarchy.generation.get("status"),
                    "source": hierarchy_build.hierarchy.generation.get(
                        "source",
                        "deterministic",
                    ),
                    "hierarchy_location": f"s3://{destination_bucket}/{hierarchy_key}",
                    "node_count": len(hierarchy_build.hierarchy.nodes),
                    "validation": hierarchy_build.hierarchy.generation.get("validation"),
                }

            chunk_artifacts, chunking_strategy = self._build_chunk_artifacts(
                text_extraction=text_extraction,
                hierarchy_build=hierarchy_build,
                document_metadata=metadata,
                destination_bucket=destination_bucket,
            )

            asset_artifacts, asset_diagnostics = self._extract_assets(
                pdf_path=tmp_path,
                raw_bucket=destination_bucket,
                raw_key=raw_key,
                document_metadata=metadata,
                destination_bucket=destination_bucket,
                hierarchy=hierarchy_build.hierarchy if hierarchy_build else None,
                triage=triage,
            )
            chunk_artifacts.extend(asset_artifacts)

            if debug_enabled:
                debug_artifacts = {
                    "triage_debug": f"s3://{destination_bucket}/{nova_triage_debug_key}",
                    "model_extracted_text": f"s3://{destination_bucket}/{model_text_key}",
                }
                metadata["processing"]["debug_artifacts"] = debug_artifacts

            # Triage page observations already written above; write text quality
            # and hierarchy artifacts below.
            self._s3.put_object(
                Bucket=destination_bucket,
                Key=text_quality_key,
                Body=json.dumps(
                    text_quality_report.to_dict(),
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
                ContentType="application/json",
            )
            if hierarchy_build is not None:
                self._s3.put_object(
                    Bucket=destination_bucket,
                    Key=hierarchy_key,
                    Body=json.dumps(
                        hierarchy_build.hierarchy.to_dict(),
                        indent=2,
                        sort_keys=True,
                    ).encode("utf-8"),
                    ContentType="application/json",
                )

            for artifact in chunk_artifacts:
                self._s3.put_object(
                    Bucket=destination_bucket,
                    Key=artifact.key,
                    Body=artifact.markdown.encode("utf-8"),
                    ContentType="text/markdown",
                )
                self._s3.put_object(
                    Bucket=destination_bucket,
                    Key=artifact.sidecar_key,
                    Body=json.dumps(
                        artifact.sidecar_metadata,
                        indent=2,
                        sort_keys=True,
                    ).encode("utf-8"),
                    ContentType="application/json",
                )
                for extra_object in artifact.extra_objects:
                    self._s3.put_object(
                        Bucket=destination_bucket,
                        Key=extra_object.key,
                        Body=extra_object.body,
                        ContentType=extra_object.content_type,
                    )

            metadata["processing"]["pdf_text_extraction"] = {
                "extraction_status": text_extraction.extraction_status,
                "page_count": len(text_extraction.pages),
                "warnings": text_extraction.warnings,
            }
            metadata["processing"]["chunking"] = {
                "status": "complete" if chunk_artifacts else "skipped",
                "strategy": chunking_strategy,
                "chunk_count": len(chunk_artifacts),
                "index_prefix": join_s3_key(self._ingestion_config["index_prefix"], document_id),
                "default_chunk_tokens": int(self._ingestion_config["pdf_default_chunk_tokens"]),
                "chunk_overlap": float(self._ingestion_config["pdf_default_chunk_overlap"]),
                "embedding_chunk_token_limit": int(
                    self._ingestion_config["pdf_embedding_chunk_token_limit_titan_v2"]
                ),
                "embedding_chunk_char_limit": int(
                    self._ingestion_config["pdf_embedding_chunk_char_limit_titan_v2"]
                ),
            }
            metadata["processing"]["table_extraction"] = asset_diagnostics["table_extraction"]
            metadata["processing"]["figure_extraction"] = asset_diagnostics["figure_extraction"]
            if not chunk_artifacts:
                metadata["processing"]["chunking"]["message"] = (
                    "No extractable PDF page text was available for chunk generation."
                )
            metadata["processing"]["stage"] = (
                "chunk_artifacts_complete" if chunk_artifacts else "initial_metadata_complete"
            )

            self._s3.put_object(
                Bucket=destination_bucket,
                Key=metadata_key,
                Body=json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"),
                ContentType="application/json",
            )

            return IngestionResult(
                landing_s3_uri=obj.uri,
                status="processed",
                tenant_id=tenant_id,
                collection_id=collection_id,
                document_type="pdf",
                document_id=document_id,
                raw_s3_uri=raw_s3_uri,
                metadata_s3_uri=f"s3://{destination_bucket}/{metadata_key}",
                document_metadata=metadata,
                chunks=[artifact.catalog_metadata for artifact in chunk_artifacts],
            )
        finally:
            tmp_path.unlink(missing_ok=True)


def scan_pdf(path: Path, probe_pages: int, text_available_min_chars: int) -> PdfScanResult:
    """Scan a PDF for basic metadata and embedded text availability."""
    if PdfReader is None:
        return PdfScanResult(
            extraction_status="pypdf_not_available",
            text_available=None,
            ocr_required=None,
            warnings=["pypdf is not installed; PDF text probing was skipped."],
        )

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        metadata = reader.metadata or {}
        title = _clean_pdf_metadata_value(getattr(metadata, "title", None))

        pages_to_probe = min(max(probe_pages, 0), page_count)
        text_char_count = 0
        warnings: list[str] = []
        for page_index in range(pages_to_probe):
            try:
                text = reader.pages[page_index].extract_text() or ""
                text_char_count += len(text.strip())
            except Exception as exc:  # pragma: no cover - depends on PDF internals
                warnings.append(f"page_{page_index + 1}_text_extract_failed:{type(exc).__name__}")

        text_available = text_char_count >= text_available_min_chars
        return PdfScanResult(
            extraction_status="completed",
            title=title,
            page_count=page_count,
            probed_pages=pages_to_probe,
            extracted_text_char_count=text_char_count,
            text_available=text_available,
            ocr_required=not text_available,
            warnings=warnings,
        )
    except Exception as exc:
        return PdfScanResult(
            extraction_status="failed",
            text_available=None,
            ocr_required=None,
            warnings=[f"pdf_scan_failed:{type(exc).__name__}"],
        )


def extract_pdf_text_pages(path: Path) -> PdfTextExtractionResult:
    """Extract normalized text from every PDF page."""
    if PdfReader is None:
        return PdfTextExtractionResult(
            extraction_status="pypdf_not_available",
            warnings=["pypdf is not installed; PDF page text extraction was skipped."],
        )

    try:
        reader = PdfReader(str(path))
        pages: list[PdfPageText] = []
        warnings: list[str] = []
        for page_index, page in enumerate(reader.pages):
            page_number = page_index + 1
            try:
                text = clean_extracted_text(page.extract_text() or "")
                if text:
                    pages.append(PdfPageText(page_number=page_number, text=text))
            except Exception as exc:  # pragma: no cover - depends on PDF internals
                warnings.append(f"page_{page_number}_text_extract_failed:{type(exc).__name__}")
        return PdfTextExtractionResult(
            extraction_status="completed",
            pages=pages,
            warnings=warnings,
        )
    except Exception as exc:
        return PdfTextExtractionResult(
            extraction_status="failed",
            warnings=[f"pdf_text_extraction_failed:{type(exc).__name__}"],
        )


def build_page_chunk_artifacts(
    *,
    text_extraction: PdfTextExtractionResult,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    default_chunk_tokens: int,
    chunk_overlap: float,
    embedding_chunk_token_limit: int,
) -> list[PdfChunkArtifact]:
    """Build page-based markdown chunks and Bedrock sidecar metadata."""
    document_id = document_metadata["document_id"]
    max_tokens = max(1, min(default_chunk_tokens, embedding_chunk_token_limit))
    artifacts: list[PdfChunkArtifact] = []
    chunk_index = 0

    for page in text_extraction.pages:
        text_parts = split_text_by_estimated_tokens(page.text, max_tokens, chunk_overlap)
        part_count = len(text_parts)
        for part_index, text in enumerate(text_parts, start=1):
            chunk_index += 1
            chunk_id = build_page_chunk_id(document_id, page.page_number, part_index, part_count)
            key = join_s3_key(index_prefix, document_id, "pages", f"{chunk_id}.md")
            metadata = {
                "metadata_schema_version": document_metadata["metadata_schema_version"],
                "record_type": "chunk",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "tenant_id": document_metadata["tenant_id"],
                "collection_id": document_metadata["collection_id"],
                "document_title": document_metadata["document_title"],
                "title": build_page_chunk_title(
                    document_metadata["document_title"],
                    page.page_number,
                    part_index,
                    part_count,
                ),
                "document_type": document_metadata["document_type"],
                "filename": document_metadata["filename"],
                "location": f"s3://{destination_bucket}/{key}",
                "source_location": document_metadata["source_location"],
                "chunk_type": "page",
                "chunk_index": chunk_index,
                "page_start": page.page_number,
                "page_end": page.page_number,
                "content_type": "text/markdown",
                "token_count": estimate_token_count(text),
                "char_count": len(text),
                "access_tier": document_metadata.get("access_tier", "restricted"),
                "custom_metadata": {
                    "part_index": part_index,
                    "part_count": part_count,
                    "extraction_status": text_extraction.extraction_status,
                },
                "processing": {
                    "strategy": "pdf_page_text",
                    "estimated_token_counter": "whitespace_word_count",
                    "configured_default_chunk_tokens": default_chunk_tokens,
                    "configured_chunk_overlap": chunk_overlap,
                    "configured_embedding_chunk_token_limit": embedding_chunk_token_limit,
                },
            }
            artifacts.append(
                PdfChunkArtifact(
                    key=key,
                    sidecar_key=f"{key}.metadata.json",
                    markdown=build_chunk_markdown(metadata, text),
                    sidecar_metadata=build_bedrock_sidecar_metadata(metadata),
                    catalog_metadata=metadata,
                )
            )

    return artifacts


def _attach_parent_hierarchy_to_asset_artifacts(
    artifacts: list[PdfChunkArtifact],
    hierarchy: Any,
) -> None:
    """Attach nearest hierarchy metadata to table/figure chunk artifacts."""
    from document_ingestion_lambda.file_type_processors.hierarchy import (
        nearest_hierarchy_node_for_page,
    )

    for artifact in artifacts:
        metadata = artifact.catalog_metadata
        node = nearest_hierarchy_node_for_page(
            hierarchy=hierarchy,
            page_number=metadata.get("page_start"),
        )
        if node is None:
            continue
        metadata["parent_hierarchy_node_id"] = node.hierarchy_node_id
        metadata["hierarchy_node_id"] = metadata.get("hierarchy_node_id") or node.hierarchy_node_id
        metadata["hierarchy_profile"] = getattr(hierarchy, "profile", None)
        metadata["section"] = metadata.get("section") or node.label
        metadata["chapter"] = metadata.get("chapter") or (
            node.heading_path[0] if node.heading_path else None
        )
        metadata["heading_path"] = metadata.get("heading_path") or "/".join(node.heading_path)
        custom = metadata.setdefault("custom_metadata", {})
        if isinstance(custom, dict):
            custom["parent_hierarchy_node_id"] = node.hierarchy_node_id
            custom["hierarchy_profile"] = getattr(hierarchy, "profile", None)
        artifact.sidecar_metadata = build_bedrock_sidecar_metadata(metadata)


def _as_bool(value: Any, *, default: bool) -> bool:
    """Coerce config values (str/bool/None) to bool with a sane default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    if value is None:
        return default
    return default


def clean_extracted_text(text: str) -> str:
    """Normalize PDF extractor output while preserving paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text_by_estimated_tokens(
    text: str,
    max_tokens: int,
    overlap_fraction: float,
    max_chars: int | None = None,
) -> list[str]:
    """Split text deterministically when text exceeds the embedding budget."""
    words = re.findall(r"\S+", text)
    if not words:
        return []
    max_tokens = max(1, max_tokens)
    max_chars = max_chars if max_chars and max_chars > 0 else None
    if estimate_token_count(text) <= max_tokens and (
        max_chars is None or len(text) <= max_chars
    ):
        return [text.strip()]

    overlap_tokens = int(max_tokens * max(0.0, min(overlap_fraction, 0.8)))
    parts: list[str] = []
    start = 0
    while start < len(words):
        end = start
        part_words: list[str] = []
        part_char_count = 0
        while end < len(words):
            word = words[end]
            candidate_char_count = part_char_count + len(word) + (1 if part_words else 0)
            candidate_word_count = len(part_words) + 1
            candidate_token_count = max(
                candidate_word_count,
                math.ceil(candidate_char_count / 2),
            )
            exceeds_token_budget = candidate_token_count > max_tokens
            exceeds_char_budget = max_chars is not None and candidate_char_count > max_chars
            if part_words and (exceeds_token_budget or exceeds_char_budget):
                break
            part_words.append(word)
            part_char_count = candidate_char_count
            end += 1

        if not part_words:
            part_words = [words[start]]
            end = start + 1

        parts.append(" ".join(part_words))
        if end >= len(words):
            break
        if overlap_tokens:
            start = max(end - overlap_tokens, start + 1)
        else:
            start = end
    return parts


def estimate_token_count(text: str) -> int:
    """Return a conservative deterministic token estimate for chunk sizing."""
    word_count = len(re.findall(r"\S+", text))
    char_count = len(text)
    return max(word_count, math.ceil(char_count / 2))


def build_page_chunk_id(
    document_id: str,
    page_number: int,
    part_index: int,
    part_count: int,
) -> str:
    """Build a stable page chunk id for a document upload."""
    base = f"{document_id}_page_{page_number:04d}"
    if part_count > 1:
        return f"{base}_part_{part_index:02d}"
    return base


def build_page_chunk_title(
    document_title: str,
    page_number: int,
    part_index: int,
    part_count: int,
) -> str:
    """Build a readable title for page chunks."""
    title = f"{document_title} - page {page_number}"
    if part_count > 1:
        title = f"{title}, part {part_index}"
    return title


def build_chunk_markdown(metadata: dict[str, Any], text: str) -> str:
    """Create the markdown artifact indexed by Bedrock Knowledge Bases."""
    header = [
        f"# {metadata['title']}",
        "",
        f"- document_id: {metadata['document_id']}",
        f"- chunk_id: {metadata['chunk_id']}",
        f"- source: {metadata['source_location']}",
        f"- page: {metadata['page_start']}",
        "",
    ]
    return "\n".join(header) + text.strip() + "\n"


def build_bedrock_sidecar_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Create a Bedrock S3 metadata sidecar for one index artifact."""
    sidecar_fields: dict[str, Any] = {
        "metadata_schema_version": metadata["metadata_schema_version"],
        "record_type": metadata["record_type"],
        "document_id": metadata["document_id"],
        "chunk_id": metadata["chunk_id"],
        "tenant_id": metadata["tenant_id"],
        "collection_id": metadata["collection_id"],
        "document_title": metadata["document_title"],
        "title": metadata["title"],
        "document_type": metadata["document_type"],
        "filename": metadata["filename"],
        "source_location": metadata["source_location"],
        "location": metadata["location"],
        "chunk_type": metadata["chunk_type"],
        "chunk_index": metadata["chunk_index"],
        "page_start": metadata["page_start"],
        "page_end": metadata["page_end"],
        "access_tier": metadata["access_tier"],
    }
    optional_fields = (
        "chapter",
        "section",
        "heading_path",
        "content_type",
        "token_count",
        "char_count",
        "asset_location",
        "summary_location",
        "description",
        "caption",
        "hierarchy_node_id",
        "hierarchy_profile",
        "hierarchy_path",
        "parent_hierarchy_node_id",
        "hierarchy_index_location",
    )
    for key in optional_fields:
        value = metadata.get(key)
        if value not in (None, ""):
            sidecar_fields[key] = value

    include_for_embedding = {
        "metadata_schema_version": False,
        "record_type": False,
        "document_id": False,
        "chunk_id": False,
        "tenant_id": False,
        "collection_id": True,
        "document_title": True,
        "title": True,
        "document_type": True,
        "filename": True,
        "source_location": False,
        "location": False,
        "chunk_type": True,
        "chunk_index": False,
        "page_start": False,
        "page_end": False,
        "access_tier": False,
        "chapter": True,
        "section": True,
        "heading_path": True,
        "content_type": False,
        "token_count": False,
        "char_count": False,
        "asset_location": False,
        "summary_location": False,
        "description": True,
        "caption": True,
        "hierarchy_node_id": True,
        "hierarchy_profile": True,
        "hierarchy_path": True,
        "parent_hierarchy_node_id": True,
        "hierarchy_index_location": False,
    }
    return {
        "metadataAttributes": {
            key: bedrock_metadata_attribute(
                value,
                include_for_embedding=include_for_embedding[key],
            )
            for key, value in sidecar_fields.items()
            if value is not None
        }
    }


def bedrock_metadata_attribute(value: Any, include_for_embedding: bool) -> dict[str, Any]:
    """Format a metadata value for Bedrock Knowledge Base S3 sidecars."""
    if isinstance(value, bool):
        typed_value = {"type": "BOOLEAN", "booleanValue": value}
    elif isinstance(value, int) and not isinstance(value, bool):
        typed_value = {"type": "NUMBER", "numberValue": value}
    elif isinstance(value, float):
        typed_value = {"type": "NUMBER", "numberValue": value}
    else:
        typed_value = {"type": "STRING", "stringValue": str(value)}
    return {"value": typed_value, "includeForEmbedding": include_for_embedding}


def generate_document_id(
    document_type: str,
    title: str | None,
    filename: str,
    suffix: str,
) -> str:
    """Generate a readable document identifier with a unique suffix."""
    base_title = title or title_from_filename(filename)
    year = first_year(base_title) or first_year(filename)
    title_slug = slugify(base_title)[:48]
    parts = [slugify(document_type), title_slug]
    if year and year not in title_slug:
        parts.append(year)
    parts.append(slugify(suffix))
    return "_".join(part for part in parts if part)


def build_document_description(document_type: str, title: str, pdf_scan: PdfScanResult) -> str:
    """Build a short document description for catalog metadata."""
    details = [f"{document_type.upper()} document", title]
    if pdf_scan.page_count is not None:
        details.append(f"{pdf_scan.page_count} pages")
    return " - ".join(details)


def title_from_filename(filename: str) -> str:
    """Create a readable title from a filename."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return re.sub(r"\s+", " ", cleaned) or "Untitled document"


def first_year(value: str | None) -> str:
    """Return the first 19xx or 20xx year in a string."""
    if not value:
        return ""
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else ""


def slugify(value: str | None) -> str:
    """Convert a value into a lowercase underscore slug."""
    if not value:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def join_s3_key(*parts: str) -> str:
    """Join S3 key parts without duplicate slashes."""
    return "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _clean_pdf_metadata_value(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
