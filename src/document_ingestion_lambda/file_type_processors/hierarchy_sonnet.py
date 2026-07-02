"""Full-document hierarchy extraction via Claude Sonnet 4.6.

Sends the joined page text to Sonnet in one (or more, for large documents)
Bedrock Converse calls and returns a structured hierarchy tree. Each node
carries a ``body_anchor`` — a verbatim text snippet used by the locator to
determine exact char offsets.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class HierarchySonnetConfig:
    """Configuration for the Sonnet hierarchy extraction pass."""

    model_id: str = "us.anthropic.claude-sonnet-4-6"
    region: str = "us-east-1"
    max_output_tokens: int = 32000
    read_timeout_seconds: int = 600
    temperature: float = 0.0
    single_call_max_pages: int = 100
    chunk_pages: int = 80
    chunk_overlap_pages: int = 1


@dataclass
class SonnetHierarchyNode:
    """One node emitted by Sonnet's hierarchy pass."""

    label: str
    title: str
    node_type: str
    parent_label: str | None
    page_start: int | None
    page_end: int | None
    depth: int
    body_anchor: str


@dataclass
class SonnetHierarchyResult:
    """Full result from the Sonnet hierarchy extraction."""

    status: str  # "completed" | "failed"
    document_profile: str | None = None
    heading_pattern: str | None = None
    nodes: list[SonnetHierarchyNode] = field(default_factory=list)
    raw_response_text: str = ""
    warnings: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


_HIERARCHY_SYSTEM_PROMPT = """\
You are a document-cataloging assistant. The user will provide the full
extracted text of a published reference document. The text was extracted
page-by-page from a PDF and includes page markers in the form
`=== PAGE N ===`.

Your job is to enumerate EVERY hierarchical heading in the document body
and return them as a complete structured JSON list.

CRITICAL REQUIREMENT — full-depth enumeration:
- Enumerate every section, subsection, AND sub-subsection in the body.
- For a building code, this means sections like R303.10, R303.5.1, etc.
- Expect the output to contain HUNDREDS of nodes for a typical multi-chapter
  document. Process the document end-to-end. Do not abridge.

What to include:
- Parts, Chapters, Sections, Subsections, Sub-subsections — go as deep as
  the document does.

What NOT to include:
- Table-of-contents listings (only emit body occurrences).
- Page headers/footers (e.g. order numbers like `101167926`, running titles).
- Cross-references in prose (e.g. "see R303.10" in body text is a reference,
  not a heading).
- Copyright/license boilerplate.

Important rules:
- Use plain Unicode characters for all symbols. Do not use LaTeX.
- Do not refuse. Do not return prose. Return only JSON.
- Process the entire document. Do not stop after the first chapter.

Required JSON schema:

{
  "document_profile": "building_code | policy | technical_standard | manual | report | unknown",
  "heading_pattern": "section_codes | chapter_heading | numbered_headings | mixed | unknown",
  "nodes": [
    {
      "label": "Heading id exactly as in body, e.g. R303.10",
      "title": "Short title, e.g. Required heating",
      "node_type": "part | chapter | section | subsection | subsubsection",
      "parent_label": "Label of containing node, or null for top-level",
      "page_start": 1,
      "page_end": 1,
      "depth": 1,
      "body_anchor": "A verbatim 40-80 character snippet from the document text that appears anywhere after this heading. The snippet must be specific enough to appear only once in the document. Copy the text exactly, including punctuation and Unicode characters."
    }
  ],
  "notes": []
}

Output the nodes in the order they appear in the document."""


def extract_hierarchy(
    *,
    joined_text: str,
    page_count: int,
    config: HierarchySonnetConfig,
    bedrock_client: Any,
) -> SonnetHierarchyResult:
    """Run the Sonnet hierarchy extraction (single-call path).

    For documents exceeding ``config.single_call_max_pages``, use
    ``extract_hierarchy_chunked()`` instead.
    """
    user_text = (
        "Below is the full extracted text of a published reference document, "
        "page by page. Return the JSON hierarchy described in the system "
        "instructions.\n\n"
        f"{joined_text}"
    )

    request_body = {
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "system": [{"text": _HIERARCHY_SYSTEM_PROMPT}],
        "inferenceConfig": {
            "temperature": config.temperature,
            "maxTokens": config.max_output_tokens,
        },
    }

    log.info(
        "Starting Sonnet hierarchy extraction: model_id=%s input_chars=%s page_count=%s",
        config.model_id,
        len(joined_text),
        page_count,
    )

    try:
        response = bedrock_client.converse(modelId=config.model_id, **request_body)
    except Exception as exc:
        log.exception("Sonnet hierarchy call failed: %s", type(exc).__name__)
        return SonnetHierarchyResult(
            status="failed",
            warnings=[f"bedrock_call_failed:{type(exc).__name__}:{exc}"],
        )

    raw_text = _extract_text(response)
    usage = response.get("usage") or {}
    stop_reason = response.get("stopReason")

    log.info(
        "Sonnet hierarchy response received: stop_reason=%s output_tokens=%s",
        stop_reason,
        usage.get("outputTokens"),
    )

    if stop_reason == "max_tokens":
        log.warning("Sonnet hierarchy response truncated at max_tokens")

    parsed = _try_parse_json(raw_text)
    if not isinstance(parsed, dict):
        return SonnetHierarchyResult(
            status="failed",
            raw_response_text=raw_text,
            usage=usage,
            warnings=["response_not_parseable_json"],
        )

    nodes = _parse_nodes(parsed.get("nodes"))

    log.info(
        "Sonnet hierarchy parsed: document_profile=%s nodes=%s",
        parsed.get("document_profile"),
        len(nodes),
    )

    return SonnetHierarchyResult(
        status="completed",
        document_profile=parsed.get("document_profile"),
        heading_pattern=parsed.get("heading_pattern"),
        nodes=nodes,
        raw_response_text=raw_text,
        usage=usage,
    )


def _parse_nodes(raw_nodes: Any) -> list[SonnetHierarchyNode]:
    """Parse the nodes array from the Sonnet response."""
    if not isinstance(raw_nodes, list):
        return []
    nodes: list[SonnetHierarchyNode] = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not label or not isinstance(label, str):
            continue
        nodes.append(
            SonnetHierarchyNode(
                label=str(label).strip(),
                title=str(item.get("title") or "").strip(),
                node_type=str(item.get("node_type") or "section"),
                parent_label=item.get("parent_label") if isinstance(item.get("parent_label"), str) else None,
                page_start=int(item["page_start"]) if item.get("page_start") is not None else None,
                page_end=int(item["page_end"]) if item.get("page_end") is not None else None,
                depth=int(item.get("depth") or 1),
                body_anchor=str(item.get("body_anchor") or "").strip(),
            )
        )
    return nodes


def _extract_text(response: dict[str, Any]) -> str:
    output = response.get("output") or {}
    message = output.get("message") or {}
    for item in message.get("content") or []:
        if isinstance(item, dict) and "text" in item:
            return str(item["text"])
    return ""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = re.sub(r"^json\s*", "", stripped, flags=re.IGNORECASE)
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None
