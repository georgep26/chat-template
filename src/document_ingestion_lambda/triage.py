"""Multimodal triage for PDF ingestion.

Renders a representative sample of pages from a PDF and asks a vision-capable
LLM (default Nova Lite) to answer a small structured-JSON questionnaire per
page. Per-page answers are aggregated into document-level decisions:

  * does this document need OCR? (the embedded text was missing or unusable)
  * does this document have a TOC/section hierarchy worth section-chunking?
  * which pages contain tables or figures?

The output is informational on first ship — :mod:`pdf` records it under
``processing.triage`` but still falls back to the existing page chunker.
Later phases consume the decisions to switch chunking strategy and to
target Textract/figure extraction.

Failure modes always degrade gracefully: if Bedrock is unreachable, the
PDF can't be rendered, or any individual page call fails, the triage
returns ``triage_status="failed"`` and the caller continues with the
deterministic page path.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Sequence

try:  # pragma: no cover - import error reported via PageRenderer.unavailable()
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Result + config dataclasses
# ---------------------------------------------------------------------------


HierarchyKind = str  # "section_codes" | "chapter_heading" | "page_only" | "unknown"
EmbeddedTextQuality = str  # "high" | "medium" | "low" | "scanned_image_only" | "unknown"


@dataclass
class PdfTriageResult:
    """Aggregated decisions from a multimodal pass over sampled PDF pages.

    ``triage_status`` is the load-bearing field for callers — they should
    treat any value other than ``"completed"`` as "fall back to the
    deterministic default path" and not consume the other fields.
    """

    triage_status: str  # "completed" | "skipped" | "failed"
    ocr_required: bool | None = None
    ocr_confidence: float | None = None
    has_hierarchy: bool | None = None
    hierarchy_kind: HierarchyKind | None = None
    hierarchy_confidence: float | None = None
    toc_pages: list[int] = field(default_factory=list)
    table_pages: list[int] = field(default_factory=list)
    figure_pages: list[int] = field(default_factory=list)
    sampled_pages: list[int] = field(default_factory=list)
    raw_decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def skipped(cls, reason: str) -> "PdfTriageResult":
        return cls(triage_status="skipped", warnings=[reason])

    @classmethod
    def failed(cls, reason: str) -> "PdfTriageResult":
        return cls(triage_status="failed", warnings=[reason])


@dataclass
class TriageConfig:
    """Knobs read from app_config.yaml `ingestion:` section.

    Defaults intentionally mirror the values committed to
    ``config/sandbox/app_config.yaml`` so callers can use them in tests
    without round-tripping through yaml.
    """

    enabled: bool = True
    model_id: str = "amazon.nova-lite-v1:0"
    region: str = "us-east-1"
    front_page_sample_count: int = 5
    random_page_sample_count: int = 5
    ocr_threshold: float = 0.30
    hierarchy_threshold: float = 0.50
    # A multi-page TOC is itself overwhelming evidence of section hierarchy
    # — even when the TOC pages themselves don't look like body content with
    # numbered headings. Treat ``toc_pages >= toc_pages_signal`` as a
    # secondary trigger for ``has_hierarchy``.
    toc_pages_signal: int = 3
    temperature: float = 0.0
    max_tokens_per_page: int = 256
    # Render scale: 1.0 ≈ ~72 DPI; 2.0 ≈ ~144 DPI. Nova Lite handles either
    # but 2.0 gives clearer headings on small-text PDFs at a modest cost.
    render_scale: float = 2.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


PageRenderer = Callable[[Path, int, float], bytes]
"""(pdf_path, 1-indexed-page-number, scale) -> PNG bytes."""


def multimodal_triage(
    *,
    pdf_path: Path,
    page_count: int,
    config: TriageConfig,
    bedrock_client: Any,
    render_page_image: PageRenderer | None = None,
    rng: random.Random | None = None,
) -> PdfTriageResult:
    """Run the per-page triage scan.

    Args:
        pdf_path: Path to the PDF on local disk.
        page_count: Total page count of the PDF (from ``scan_pdf``).
        config: Triage knobs.
        bedrock_client: A boto3 ``bedrock-runtime`` client (or stub).
        render_page_image: Override for page rendering (tests pass a stub).
        rng: Inject a seeded Random for deterministic test sampling.
    """
    if not config.enabled:
        return PdfTriageResult.skipped("triage_disabled")
    if page_count <= 0:
        return PdfTriageResult.skipped("zero_pages")
    if render_page_image is None:
        if pdfium is None:
            return PdfTriageResult.failed("pypdfium2_not_available")
        render_page_image = _render_page_with_pdfium

    sampled = _select_sampled_pages(
        page_count=page_count,
        front_count=config.front_page_sample_count,
        random_count=config.random_page_sample_count,
        rng=rng or random.Random(0),
    )

    raw_decisions: list[dict[str, Any]] = []
    warnings: list[str] = []

    for page_number in sampled:
        try:
            png_bytes = render_page_image(pdf_path, page_number, config.render_scale)
        except Exception as exc:
            warnings.append(f"page_{page_number}_render_failed:{type(exc).__name__}")
            continue

        try:
            decision = _ask_model_about_page(
                bedrock_client=bedrock_client,
                page_number=page_number,
                png_bytes=png_bytes,
                config=config,
            )
            raw_decisions.append(decision)
        except Exception as exc:
            warnings.append(f"page_{page_number}_model_failed:{type(exc).__name__}")
            continue

    if not raw_decisions:
        return PdfTriageResult.failed("no_decisions_collected")

    aggregated = _aggregate_decisions(raw_decisions, config)
    aggregated.triage_status = "completed"
    aggregated.sampled_pages = sampled
    aggregated.warnings = warnings
    aggregated.raw_decisions = raw_decisions
    return aggregated


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _select_sampled_pages(
    *,
    page_count: int,
    front_count: int,
    random_count: int,
    rng: random.Random,
) -> list[int]:
    """Pick front-N + random-N pages, 1-indexed, deduped, sorted.

    If the document is small enough that front + random would cover everything,
    just return ``range(1, page_count + 1)``.
    """
    front_count = max(0, front_count)
    random_count = max(0, random_count)

    if front_count + random_count >= page_count:
        return list(range(1, page_count + 1))

    front = list(range(1, min(front_count, page_count) + 1))
    interior_population = [p for p in range(front_count + 1, page_count + 1)]
    if random_count > 0 and interior_population:
        random_pages = rng.sample(
            interior_population, k=min(random_count, len(interior_population))
        )
    else:
        random_pages = []
    return sorted(set(front + random_pages))


def _ask_model_about_page(
    *,
    bedrock_client: Any,
    page_number: int,
    png_bytes: bytes,
    config: TriageConfig,
) -> dict[str, Any]:
    """Send one page image to Bedrock Converse and parse the JSON answer.

    The model is prompted to answer a small fixed schema. If the model
    returns text that isn't strict JSON (extra prose, code fences, etc.),
    we strip what we can and re-parse.
    """
    system_prompt = (
        "You are analyzing one page of a structured document for an ingestion "
        "pipeline. Look at the page image and answer the following questions. "
        "Return ONLY a single JSON object with these keys (no prose, no code "
        "fences):\n"
        "  page_number: int (echo back the page number we tell you)\n"
        "  embedded_text_quality: one of \"high\" | \"medium\" | \"low\" | \"scanned_image_only\"\n"
        "    - \"high\": page is digital-native, text is crisp, all readable\n"
        "    - \"medium\": legible but slight scanning artifacts\n"
        "    - \"low\": noticeable scan artifacts, OCR likely needed for reliable extraction\n"
        "    - \"scanned_image_only\": page is essentially an image with no underlying text\n"
        "  appears_to_be_toc: bool — is this page a table of contents or index?\n"
        "  contains_table: bool — does this page contain at least one tabular layout?\n"
        "  contains_figure: bool — does this page contain at least one figure, chart, "
        "diagram, illustration, or photograph (NOT just a table or text)?\n"
        "  heading_pattern: one of \"section_codes\" | \"chapter_heading\" | \"page_only\" | \"unknown\"\n"
        "    - \"section_codes\": numeric/alphanumeric section identifiers like R311.7.5.1\n"
        "    - \"chapter_heading\": prose chapter headings like \"Chapter 5: Foundations\"\n"
        "    - \"page_only\": no heading structure visible; just running text\n"
        "    - \"unknown\": cannot tell from this page alone"
    )

    user_text = (
        f"This is page {page_number}. Answer the JSON schema described in the "
        "system instructions."
    )

    request_body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": user_text},
                    {
                        "image": {
                            "format": "png",
                            "source": {"bytes": png_bytes},
                        }
                    },
                ],
            }
        ],
        "system": [{"text": system_prompt}],
        "inferenceConfig": {
            "temperature": config.temperature,
            "maxTokens": config.max_tokens_per_page,
        },
    }

    response = bedrock_client.converse(
        modelId=config.model_id,
        **request_body,
    )

    text = _extract_text_from_converse_response(response)
    parsed = _parse_decision_json(text, page_number)
    parsed["page_number"] = page_number
    return parsed


def _extract_text_from_converse_response(response: dict[str, Any]) -> str:
    """Pull the first text content block out of a Converse response."""
    output = response.get("output") or {}
    message = output.get("message") or {}
    content = message.get("content") or []
    for item in content:
        if isinstance(item, dict) and "text" in item:
            return str(item["text"])
    return ""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_decision_json(text: str, page_number: int) -> dict[str, Any]:
    """Robustly extract the JSON object the model emitted.

    Defaults each field to a safe ``unknown``/``None`` value when missing.
    Unparseable input is recorded as ``parse_error`` in the returned dict
    so the aggregator can ignore that page without crashing the whole run.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Trim common ```json fences.
        stripped = stripped.strip("`")
        stripped = re.sub(r"^json\s*", "", stripped, flags=re.IGNORECASE)
    try:
        return _coerce_decision(json.loads(stripped))
    except Exception:
        pass

    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return _coerce_decision(json.loads(match.group(0)))
        except Exception:
            pass

    return {
        "page_number": page_number,
        "embedded_text_quality": "unknown",
        "appears_to_be_toc": False,
        "contains_table": False,
        "contains_figure": False,
        "heading_pattern": "unknown",
        "parse_error": True,
    }


def _coerce_decision(raw: Any) -> dict[str, Any]:
    """Normalize types from a parsed JSON answer."""
    if not isinstance(raw, dict):
        raise ValueError("not_an_object")

    def b(key: str) -> bool:
        v = raw.get(key)
        return bool(v) if isinstance(v, bool) else False

    def s(key: str, allowed: Sequence[str], default: str = "unknown") -> str:
        v = raw.get(key)
        if isinstance(v, str) and v in allowed:
            return v
        return default

    return {
        "embedded_text_quality": s(
            "embedded_text_quality",
            ("high", "medium", "low", "scanned_image_only"),
        ),
        "appears_to_be_toc": b("appears_to_be_toc"),
        "contains_table": b("contains_table"),
        "contains_figure": b("contains_figure"),
        "heading_pattern": s(
            "heading_pattern",
            ("section_codes", "chapter_heading", "page_only"),
        ),
    }


def _aggregate_decisions(
    decisions: list[dict[str, Any]], config: TriageConfig
) -> PdfTriageResult:
    """Reduce per-page answers to a document-level decision."""
    usable = [d for d in decisions if not d.get("parse_error")]
    total = len(usable)
    if total == 0:
        return PdfTriageResult.failed("all_pages_unparseable")

    needs_ocr_pages = sum(
        1
        for d in usable
        if d.get("embedded_text_quality") in ("low", "scanned_image_only")
    )
    ocr_fraction = needs_ocr_pages / total
    ocr_required = ocr_fraction >= config.ocr_threshold
    ocr_confidence = (
        ocr_fraction if ocr_required else (1.0 - ocr_fraction)
    )

    # Pick the dominant non-"page_only" heading pattern.
    pattern_counts: dict[str, int] = {}
    for d in usable:
        pat = d.get("heading_pattern", "unknown")
        if pat in ("section_codes", "chapter_heading"):
            pattern_counts[pat] = pattern_counts.get(pat, 0) + 1
    dominant_pattern = None
    dominant_count = 0
    for pat, count in pattern_counts.items():
        if count > dominant_count:
            dominant_pattern, dominant_count = pat, count
    hierarchy_fraction = dominant_count / total if dominant_pattern else 0.0

    toc_pages = sorted(
        d["page_number"] for d in usable if d.get("appears_to_be_toc")
    )

    # Primary signal: a critical mass of sampled pages share the same
    # numbered/chapter heading pattern. Secondary signal: a multi-page TOC.
    # The TOC trigger handles a common pitfall where 30 front-matter pages
    # of a structured code document include 10-20 pages of TOC (which the
    # model correctly classifies as ``page_only`` because TOCs are lists
    # of references, not bodies of numbered sections). Without this
    # secondary path, a heavily-TOC'd front sample drags
    # ``hierarchy_fraction`` below threshold and section chunking never
    # triggers on the exact documents that need it most.
    toc_signal_strength = (
        min(1.0, len(toc_pages) / max(config.toc_pages_signal, 1))
        if config.toc_pages_signal > 0
        else 0.0
    )
    has_hierarchy = (
        hierarchy_fraction >= config.hierarchy_threshold
        or len(toc_pages) >= config.toc_pages_signal
    )
    if has_hierarchy:
        # Default to section_codes when triggered solely by TOC presence,
        # since most code/spec documents use numeric section identifiers.
        hierarchy_kind = dominant_pattern or "section_codes"
        # When the TOC trigger is what carried us over the line, surface
        # that strength so the downstream section-chunker confidence gate
        # doesn't reject on a near-zero ``hierarchy_fraction``.
        effective_confidence = max(hierarchy_fraction, toc_signal_strength)
    else:
        hierarchy_kind = "page_only"
        effective_confidence = hierarchy_fraction
    table_pages = sorted(
        d["page_number"] for d in usable if d.get("contains_table")
    )
    figure_pages = sorted(
        d["page_number"] for d in usable if d.get("contains_figure")
    )

    return PdfTriageResult(
        triage_status="completed",
        ocr_required=ocr_required,
        ocr_confidence=round(ocr_confidence, 3),
        has_hierarchy=has_hierarchy,
        hierarchy_kind=hierarchy_kind,
        hierarchy_confidence=round(effective_confidence, 3),
        toc_pages=toc_pages,
        table_pages=table_pages,
        figure_pages=figure_pages,
    )


def _render_page_with_pdfium(pdf_path: Path, page_number: int, scale: float) -> bytes:
    """Render a single 1-indexed PDF page to PNG bytes via pypdfium2."""
    if pdfium is None:
        raise RuntimeError("pypdfium2 is not available")
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_number < 1 or page_number > len(doc):
            raise ValueError(f"page_number_out_of_range:{page_number}")
        page = doc[page_number - 1]
        try:
            bitmap = page.render(scale=scale)
            try:
                pil_image = bitmap.to_pil()
                buffer = BytesIO()
                pil_image.save(buffer, format="PNG")
                return buffer.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


# Convenience for callers that want to log a triage decision in JSON form
# without leaking the bulky raw_decisions list. asdict() on the dataclass
# already works; this exists so callers can request a slim version.


def triage_summary_dict(triage: PdfTriageResult) -> dict[str, Any]:
    """Compact dict suitable for storing under ``processing.triage`` in metadata."""
    return {
        "triage_status": triage.triage_status,
        "ocr_required": triage.ocr_required,
        "ocr_confidence": triage.ocr_confidence,
        "has_hierarchy": triage.has_hierarchy,
        "hierarchy_kind": triage.hierarchy_kind,
        "hierarchy_confidence": triage.hierarchy_confidence,
        "sampled_pages": triage.sampled_pages,
        "toc_pages": triage.toc_pages,
        "table_pages": triage.table_pages,
        "figure_pages": triage.figure_pages,
        "warnings": triage.warnings,
    }


__all__ = [
    "PdfTriageResult",
    "TriageConfig",
    "multimodal_triage",
    "triage_summary_dict",
]
