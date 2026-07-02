"""Multimodal page observation for PDF ingestion.

The multimodal pass renders selected PDF pages and asks a vision-capable model
to extract observable page facts: visible text, heading candidates, TOC signals,
tables, and figures. The model does not decide whether OCR is required and does
not generate the canonical document hierarchy. Those decisions are handled by
deterministic backend code after triage.

Compatibility fields such as ``ocr_required`` and ``has_hierarchy`` remain on
``PdfTriageResult`` during the migration so older tests and metadata readers can
continue to inspect summaries, but the PDF processor should not use those
fields as source-of-truth decisions.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Sequence

from utils.logger import get_logger

try:  # pragma: no cover - import error reported via multimodal_triage()
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None  # type: ignore[assignment]


log = get_logger(__name__)


HierarchyKind = str
EmbeddedTextQuality = str


@dataclass
class HeadingCandidate:
    """A heading-like text span observed on a rendered page."""

    label: str
    title: str | None = None
    raw_text: str = ""
    heading_pattern_type: str | None = None
    confidence: float | None = None


@dataclass
class PageTableObservation:
    """A table-like object observed on a rendered page."""

    caption: str | None = None
    title: str | None = None
    description: str | None = None
    bbox: dict[str, float] | None = None
    confidence: float | None = None


@dataclass
class PageFigureObservation:
    """A figure-like object observed on a rendered page."""

    caption: str | None = None
    title: str | None = None
    description: str | None = None
    bbox: dict[str, float] | None = None
    confidence: float | None = None


@dataclass
class PageTriageObservation:
    """Structured observations extracted from one rendered page."""

    page_number: int
    raw_visible_text: str = ""
    likely_document_type: str | None = None
    heading_candidates: list[HeadingCandidate] = field(default_factory=list)
    appears_to_be_toc_page: bool = False
    toc_entries: list[dict[str, Any]] = field(default_factory=list)
    tables: list[PageTableObservation] = field(default_factory=list)
    figures: list[PageFigureObservation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    heading_pattern: str = "unknown"
    embedded_text_quality: str = "unknown"
    parse_error: bool = False


@dataclass
class PdfTriageResult:
    """Aggregated page-observation result.

    New code should consume ``page_observations`` and deterministic downstream
    reports rather than using the compatibility decision fields.
    """

    triage_status: str  # "completed" | "skipped" | "failed"
    page_observations: list[PageTriageObservation] = field(default_factory=list)
    sampled_pages: list[int] = field(default_factory=list)
    full_page_triage_enabled: bool = False
    warnings: list[str] = field(default_factory=list)

    # Compatibility/summary fields. These are derived from observations only.
    ocr_required: bool | None = None
    ocr_confidence: float | None = None
    has_hierarchy: bool | None = None
    hierarchy_kind: HierarchyKind | None = None
    hierarchy_confidence: float | None = None
    toc_pages: list[int] = field(default_factory=list)
    table_pages: list[int] = field(default_factory=list)
    figure_pages: list[int] = field(default_factory=list)
    raw_decisions: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def skipped(cls, reason: str) -> "PdfTriageResult":
        return cls(triage_status="skipped", warnings=[reason])

    @classmethod
    def failed(cls, reason: str) -> "PdfTriageResult":
        return cls(triage_status="failed", warnings=[reason])


@dataclass
class TriageConfig:
    """Knobs read from app_config.yaml ``ingestion`` values."""

    enabled: bool = True
    model_id: str = "us.meta.llama4-scout-17b-instruct-v1:0"
    region: str = "us-east-1"
    front_page_sample_count: int = 5
    random_page_sample_count: int = 5
    full_page_triage_enabled: bool = False
    ocr_threshold: float = 0.30
    hierarchy_threshold: float = 0.50
    toc_pages_signal: int = 3
    temperature: float = 0.0
    max_tokens_per_page: int = 8192
    render_scale: float = 2.0
    parallel_workers: int = 6


PageRenderer = Callable[[Path, int, float], bytes]


_TRIAGE_SYSTEM_PROMPT = """\
You are a document-cataloging assistant in a search-indexing pipeline. The
user will provide a single rendered page from a published reference document
(such as a building code, technical standard, policy, or manual) that is being
added to a searchable archive.

Your job is to extract structured observations from the page so the indexer
can build an accurate catalog. You are not creating new advice and you are not
recommending any action. You are recording what is on the page.

Return ONLY one JSON object. No prose before or after. Do not refuse.

Important formatting rules:
- Use plain Unicode characters for all symbols: ², ³, °, ®, ¼, ½, ¾, etc.
- Never use LaTeX notation such as $^{2}$, $\\frac{1}{4}$, or $^{\\circ}$.
- Never wrap text in dollar signs or backslash commands.

Required JSON schema:

{
  "page_number": int,
  "raw_visible_text": "Full verbatim transcription of all visible text on the page, preserving paragraph breaks. Transcribe exactly — do not paraphrase.",
  "likely_document_type": "string or null (e.g. building_code, policy, technical_standard, manual, report)",
  "heading_pattern": "one of: section_codes | chapter_heading | numbered_headings | page_only | unknown",
  "heading_candidates": [
    {
      "label": "The heading identifier, e.g. R303.10",
      "title": "Short heading title, e.g. Required heating",
      "heading_pattern_type": "section_codes | chapter_heading | numbered_headings",
      "confidence": 0.0
    }
  ],
  "appears_to_be_toc": "bool — true when the page is a table of contents",
  "toc_entries": [
    {
      "label": "Section label from the TOC line",
      "title": "Section title from the TOC line",
      "page_reference": "Page number shown in the TOC"
    }
  ],
  "contains_table": "bool",
  "tables": [
    {
      "caption": "Visible table caption/title exactly as printed",
      "description": "A detailed description of the table for semantic search. Include: (1) what the table tabulates and its columns/rows structure, (2) the key variables, thresholds, or categories it covers, (3) 2-4 example questions a user might ask that this table would answer. Write 3-6 sentences. This text will be embedded in a vector database, so include enough detail for someone searching by topic to find this table."
    }
  ],
  "contains_figure": "bool",
  "figures": [
    {
      "caption": "Visible figure caption/title exactly as printed",
      "description": "A detailed description of the figure for semantic search. Include: (1) what the figure depicts (map, chart, diagram, photo, schematic, etc.), (2) the key information it conveys — regions, zones, categories, values, relationships, (3) 2-4 example questions a user might ask that this figure would answer. Write 3-6 sentences. This text will be embedded in a vector database, so include enough detail for someone searching by topic to find this figure."
    }
  ],
  "notes": ["Array of short uncertainty notes, if any"]
}"""


_TRIAGE_USER_TEXT = "This is page {page_number}. Return the JSON object described in the system instructions."


def multimodal_triage(
    *,
    pdf_path: Path,
    page_count: int,
    config: TriageConfig,
    bedrock_client: Any,
    render_page_image: PageRenderer | None = None,
    rng: random.Random | None = None,
) -> PdfTriageResult:
    """Run multimodal page observation over selected pages."""
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
        full_page_triage_enabled=config.full_page_triage_enabled,
    )

    raw_decisions: list[dict[str, Any]] = []
    warnings: list[str] = []

    def _process_single_page(page_number: int) -> dict[str, Any] | None:
        """Render + model-call for one page. Returns decision or None on failure."""
        try:
            png_bytes = render_page_image(pdf_path, page_number, config.render_scale)
        except Exception as exc:
            warnings.append(f"page_{page_number}_render_failed:{type(exc).__name__}")
            log.warning(
                "Triage page render failed: page=%s error_type=%s",
                page_number,
                type(exc).__name__,
            )
            return None
        try:
            decision = _ask_model_about_page(
                bedrock_client=bedrock_client,
                page_number=page_number,
                png_bytes=png_bytes,
                config=config,
            )
            log.info(
                "Triage page complete: page=%s of %s raw_text_chars=%s headings=%s tables=%s figures=%s parse_error=%s",
                page_number,
                page_count,
                len(str(decision.get("raw_visible_text") or "")),
                len(decision.get("heading_candidates") or []),
                len(decision.get("tables") or []),
                len(decision.get("figures") or []),
                bool(decision.get("parse_error")),
            )
            return decision
        except Exception as exc:
            warnings.append(f"page_{page_number}_model_failed:{type(exc).__name__}")
            log.warning(
                "Triage page model call failed: page=%s error_type=%s",
                page_number,
                type(exc).__name__,
            )
            return None

    max_workers = min(config.parallel_workers, len(sampled))
    if max_workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        log.info(
            "Starting parallel triage: pages=%s workers=%s model_id=%s",
            len(sampled),
            max_workers,
            config.model_id,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_process_single_page, page_number): page_number
                for page_number in sampled
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    raw_decisions.append(result)
    else:
        for page_number in sampled:
            result = _process_single_page(page_number)
            if result is not None:
                raw_decisions.append(result)

    if not raw_decisions:
        return PdfTriageResult.failed("no_decisions_collected")

    aggregated = _aggregate_decisions(raw_decisions, config)
    aggregated.triage_status = "completed"
    aggregated.sampled_pages = sampled
    aggregated.full_page_triage_enabled = config.full_page_triage_enabled
    aggregated.warnings = warnings
    aggregated.raw_decisions = raw_decisions
    return aggregated


def _select_sampled_pages(
    *,
    page_count: int,
    front_count: int,
    random_count: int,
    rng: random.Random,
    full_page_triage_enabled: bool = False,
) -> list[int]:
    """Pick full-page or front-N plus random-N pages, 1-indexed and sorted."""
    if page_count <= 0:
        return []
    if full_page_triage_enabled:
        return list(range(1, page_count + 1))

    front_count = max(0, front_count)
    random_count = max(0, random_count)

    if front_count + random_count >= page_count:
        return list(range(1, page_count + 1))

    front = list(range(1, min(front_count, page_count) + 1))
    interior_population = [p for p in range(front_count + 1, page_count + 1)]
    random_pages = (
        rng.sample(interior_population, k=min(random_count, len(interior_population)))
        if random_count > 0 and interior_population
        else []
    )
    return sorted(set(front + random_pages))


def _ask_model_about_page(
    *,
    bedrock_client: Any,
    page_number: int,
    png_bytes: bytes,
    config: TriageConfig,
) -> dict[str, Any]:
    """Send one rendered page to Bedrock Converse and parse the JSON answer."""
    user_text = _TRIAGE_USER_TEXT.format(page_number=page_number)
    request_body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": user_text},
                    {"image": {"format": "png", "source": {"bytes": png_bytes}}},
                ],
            }
        ],
        "system": [{"text": _TRIAGE_SYSTEM_PROMPT}],
        "inferenceConfig": {
            "temperature": config.temperature,
            "maxTokens": config.max_tokens_per_page,
        },
    }

    response = bedrock_client.converse(modelId=config.model_id, **request_body)
    text = _extract_text_from_converse_response(response)
    parsed = _parse_decision_json(text, page_number)
    parsed["page_number"] = page_number
    parsed["_model_id"] = config.model_id
    parsed["_model_response_text"] = text
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
    """Robustly extract and normalize the JSON object emitted by the model."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = re.sub(r"^json\s*", "", stripped, flags=re.IGNORECASE)
    try:
        return _coerce_decision(json.loads(stripped))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return _coerce_decision(json.loads(match.group(0)))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return {
        "page_number": page_number,
        "raw_visible_text": "",
        "likely_document_type": None,
        "heading_candidates": [],
        "appears_to_be_toc": False,
        "appears_to_be_toc_page": False,
        "toc_entries": [],
        "contains_table": False,
        "tables": [],
        "contains_figure": False,
        "figures": [],
        "heading_pattern": "unknown",
        "embedded_text_quality": "unknown",
        "notes": [],
        "parse_error": True,
    }


def _coerce_decision(raw: Any) -> dict[str, Any]:
    """Normalize parsed JSON into the page-observation schema.

    The function accepts both the old compact triage schema and the new richer
    observation schema so existing tests and stubs remain useful.
    """
    if not isinstance(raw, dict):
        raise ValueError("not_an_object")

    def b(key: str, default: bool = False) -> bool:
        value = raw.get(key)
        return value if isinstance(value, bool) else default

    def s(key: str, allowed: Sequence[str], default: str = "unknown") -> str:
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            return value
        return default

    def optional_str(key: str) -> str | None:
        value = raw.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    heading_pattern = s(
        "heading_pattern",
        ("section_codes", "chapter_heading", "numbered_headings", "page_only", "unknown"),
    )
    raw_heading_candidates = raw.get("heading_candidates")
    heading_candidates = (
        _coerce_heading_candidates(raw_heading_candidates)
        if isinstance(raw_heading_candidates, list)
        else []
    )

    contains_table = b("contains_table")
    raw_tables = raw.get("tables")
    tables = _coerce_observation_items(raw_tables) if isinstance(raw_tables, list) else []
    if contains_table and not tables:
        tables = [{"caption": None, "title": None, "description": None, "bbox": None, "confidence": None}]

    contains_figure = b("contains_figure")
    raw_figures = raw.get("figures")
    figures = _coerce_observation_items(raw_figures) if isinstance(raw_figures, list) else []
    if contains_figure and not figures:
        figures = [{"caption": None, "title": None, "description": None, "bbox": None, "confidence": None}]

    appears_to_be_toc = b("appears_to_be_toc", b("appears_to_be_toc_page"))
    toc_entries_raw = raw.get("toc_entries")
    toc_entries = toc_entries_raw if isinstance(toc_entries_raw, list) else []
    notes_raw = raw.get("notes")

    return {
        "raw_visible_text": str(raw.get("raw_visible_text") or ""),
        "likely_document_type": optional_str("likely_document_type"),
        "heading_candidates": heading_candidates,
        "appears_to_be_toc": appears_to_be_toc,
        "appears_to_be_toc_page": appears_to_be_toc,
        "toc_entries": toc_entries,
        "contains_table": contains_table or bool(tables),
        "tables": tables,
        "contains_figure": contains_figure or bool(figures),
        "figures": figures,
        "heading_pattern": heading_pattern,
        "embedded_text_quality": s(
            "embedded_text_quality",
            ("high", "medium", "low", "scanned_image_only", "unknown"),
        ),
        "notes": [str(item) for item in notes_raw] if isinstance(notes_raw, list) else [],
    }


def _coerce_heading_candidates(value: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        raw_text = item.get("raw_text") or label or ""
        if not isinstance(label, str) or not label.strip():
            continue
        candidates.append(
            {
                "label": label.strip(),
                "title": item.get("title") if isinstance(item.get("title"), str) else None,
                "raw_text": str(raw_text).strip(),
                "heading_pattern_type": item.get("heading_pattern_type")
                if isinstance(item.get("heading_pattern_type"), str)
                else item.get("heading_pattern")
                if isinstance(item.get("heading_pattern"), str)
                else None,
                "confidence": _coerce_float(item.get("confidence")),
            }
        )
    return candidates


def _coerce_observation_items(value: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        items.append(
            {
                "caption": item.get("caption") if isinstance(item.get("caption"), str) else None,
                "title": item.get("title") if isinstance(item.get("title"), str) else None,
                "description": item.get("description")
                if isinstance(item.get("description"), str)
                else None,
                "bbox": bbox if isinstance(bbox, dict) else None,
                "confidence": _coerce_float(item.get("confidence")),
            }
        )
    return items


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aggregate_decisions(
    decisions: list[dict[str, Any]], config: TriageConfig
) -> PdfTriageResult:
    """Reduce page observations to a result with compatibility summaries."""
    usable = []
    for decision in decisions:
        if decision.get("parse_error"):
            continue
        coerced = _coerce_decision(decision)
        coerced["page_number"] = decision.get("page_number")
        usable.append(coerced)
    if not usable:
        return PdfTriageResult.failed("all_pages_unparseable")

    observations = [_decision_to_observation(d) for d in usable]
    total = len(observations)

    low_text_count = sum(
        1
        for obs in observations
        if obs.embedded_text_quality in ("low", "scanned_image_only")
    )
    low_text_fraction = low_text_count / total
    ocr_required = low_text_fraction >= config.ocr_threshold

    pattern_counts: dict[str, int] = {}
    for obs in observations:
        if obs.heading_pattern in ("section_codes", "chapter_heading", "numbered_headings"):
            pattern_counts[obs.heading_pattern] = pattern_counts.get(obs.heading_pattern, 0) + 1
        for candidate in obs.heading_candidates:
            pattern = candidate.heading_pattern_type
            if pattern in ("section_codes", "chapter_heading", "numbered_headings"):
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

    dominant_pattern = None
    dominant_count = 0
    for pattern, count in pattern_counts.items():
        if count > dominant_count:
            dominant_pattern = pattern
            dominant_count = count
    hierarchy_fraction = dominant_count / total if dominant_pattern else 0.0
    toc_pages = sorted(obs.page_number for obs in observations if obs.appears_to_be_toc_page)
    toc_signal_strength = (
        min(1.0, len(toc_pages) / max(config.toc_pages_signal, 1))
        if config.toc_pages_signal > 0
        else 0.0
    )
    has_hierarchy = (
        hierarchy_fraction >= config.hierarchy_threshold
        or len(toc_pages) >= config.toc_pages_signal
    )
    hierarchy_kind = dominant_pattern or ("section_codes" if has_hierarchy else "page_only")
    hierarchy_confidence = max(hierarchy_fraction, toc_signal_strength) if has_hierarchy else hierarchy_fraction

    return PdfTriageResult(
        triage_status="completed",
        page_observations=observations,
        ocr_required=ocr_required,
        ocr_confidence=round(low_text_fraction if ocr_required else 1.0 - low_text_fraction, 3),
        has_hierarchy=has_hierarchy,
        hierarchy_kind=hierarchy_kind,
        hierarchy_confidence=round(hierarchy_confidence, 3),
        toc_pages=toc_pages,
        table_pages=sorted(obs.page_number for obs in observations if obs.tables),
        figure_pages=sorted(obs.page_number for obs in observations if obs.figures),
    )


def _decision_to_observation(decision: dict[str, Any]) -> PageTriageObservation:
    return PageTriageObservation(
        page_number=int(decision.get("page_number") or 0),
        raw_visible_text=str(decision.get("raw_visible_text") or ""),
        likely_document_type=decision.get("likely_document_type")
        if isinstance(decision.get("likely_document_type"), str)
        else None,
        heading_candidates=[
            HeadingCandidate(**candidate)
            for candidate in decision.get("heading_candidates", [])
            if isinstance(candidate, dict)
        ],
        appears_to_be_toc_page=bool(
            decision.get("appears_to_be_toc_page") or decision.get("appears_to_be_toc")
        ),
        toc_entries=decision.get("toc_entries") if isinstance(decision.get("toc_entries"), list) else [],
        tables=[
            PageTableObservation(**item)
            for item in decision.get("tables", [])
            if isinstance(item, dict)
        ],
        figures=[
            PageFigureObservation(**item)
            for item in decision.get("figures", [])
            if isinstance(item, dict)
        ],
        notes=decision.get("notes") if isinstance(decision.get("notes"), list) else [],
        heading_pattern=str(decision.get("heading_pattern") or "unknown"),
        embedded_text_quality=str(decision.get("embedded_text_quality") or "unknown"),
        parse_error=bool(decision.get("parse_error")),
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


def page_observations_as_dicts(triage: PdfTriageResult) -> list[dict[str, Any]]:
    """Return full page observations as JSON-serializable dictionaries."""
    return [asdict(observation) for observation in triage.page_observations]


def triage_summary_dict(triage: PdfTriageResult) -> dict[str, Any]:
    """Compact dict suitable for ``document_metadata.processing.triage``."""
    heading_pattern_counts: dict[str, int] = {}
    for observation in triage.page_observations:
        if observation.heading_pattern:
            heading_pattern_counts[observation.heading_pattern] = (
                heading_pattern_counts.get(observation.heading_pattern, 0) + 1
            )

    return {
        "triage_status": triage.triage_status,
        "full_page_triage_enabled": triage.full_page_triage_enabled,
        "sampled_pages": triage.sampled_pages,
        "page_observation_count": len(triage.page_observations),
        "toc_pages": triage.toc_pages,
        "table_pages": triage.table_pages,
        "figure_pages": triage.figure_pages,
        "heading_pattern_counts": heading_pattern_counts,
        "ocr_required": triage.ocr_required,
        "ocr_confidence": triage.ocr_confidence,
        "has_hierarchy": triage.has_hierarchy,
        "hierarchy_kind": triage.hierarchy_kind,
        "hierarchy_confidence": triage.hierarchy_confidence,
        "warnings": triage.warnings,
    }


def triage_debug_dict(
    triage: PdfTriageResult,
    *,
    config: TriageConfig,
    include_model_payloads: bool,
) -> dict[str, Any]:
    """Return a full triage debug payload for durable S3 inspection."""
    payload: dict[str, Any] = {
        "model": {
            "model_id": config.model_id,
            "region": config.region,
            "temperature": config.temperature,
            "max_tokens_per_page": config.max_tokens_per_page,
        },
        "sampling": {
            "full_page_triage_enabled": config.full_page_triage_enabled,
            "front_page_sample_count": config.front_page_sample_count,
            "random_page_sample_count": config.random_page_sample_count,
            "sampled_pages": triage.sampled_pages,
            "render_scale": config.render_scale,
        },
        "prompt": {
            "system": _TRIAGE_SYSTEM_PROMPT,
            "user_template": "This is page {page_number}. Return the JSON schema from the system instructions.",
            "image_input": "Rendered PDF page PNG bytes are sent in the Converse image content block and are not serialized in this debug JSON.",
        },
        "summary": triage_summary_dict(triage),
        "page_observations": page_observations_as_dicts(triage),
    }
    if include_model_payloads:
        payload["raw_model_decisions"] = triage.raw_decisions
    return payload


__all__ = [
    "HeadingCandidate",
    "PageFigureObservation",
    "PageTableObservation",
    "PageTriageObservation",
    "PdfTriageResult",
    "TriageConfig",
    "multimodal_triage",
    "page_observations_as_dicts",
    "triage_debug_dict",
    "triage_summary_dict",
]
