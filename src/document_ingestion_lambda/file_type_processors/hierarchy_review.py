"""Optional model review for deterministic document hierarchies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from document_ingestion_lambda.file_type_processors.hierarchy import (
    DocumentHierarchy,
    HierarchyNode,
    validate_hierarchy,
)


@dataclass
class HierarchyReviewConfig:
    """Configuration for optional hierarchy model review."""

    mode: str = "never"  # auto | always | never
    model_id: str = "us.anthropic.claude-sonnet-4-6"
    region: str = "us-east-1"
    min_confidence: float = 0.80
    temperature: float = 0.0
    max_tokens: int = 8192


@dataclass
class HierarchyReviewResult:
    """Result of optional model review."""

    status: str
    hierarchy: DocumentHierarchy
    issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    request: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    raw_response_text: str | None = None


def should_review_hierarchy(
    hierarchy: DocumentHierarchy,
    *,
    config: HierarchyReviewConfig,
) -> bool:
    """Return whether the review model should be called."""
    mode = (config.mode or "never").lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    if mode != "auto":
        return False
    generation = hierarchy.generation or {}
    confidence = float(generation.get("confidence") or 0.0)
    validation = generation.get("validation") or {}
    return (
        confidence < config.min_confidence
        or bool(validation.get("errors"))
        or hierarchy.profile == "plain_document"
    )


def review_hierarchy(
    *,
    hierarchy: DocumentHierarchy,
    triage_payload: dict[str, Any],
    text_quality_payload: dict[str, Any],
    profile_payload: dict[str, Any],
    bedrock_client: Any,
    config: HierarchyReviewConfig,
) -> HierarchyReviewResult:
    """Ask a model to correct a deterministic hierarchy and validate the result."""
    request = build_hierarchy_review_request(
        hierarchy=hierarchy,
        triage_payload=triage_payload,
        text_quality_payload=text_quality_payload,
        profile_payload=profile_payload,
        config=config,
    )
    response = bedrock_client.converse(modelId=config.model_id, **request)
    text = _extract_text_from_converse_response(response)
    parsed = _parse_json_object(text)
    if not isinstance(parsed, dict):
        return HierarchyReviewResult(
            status="failed",
            hierarchy=hierarchy,
            warnings=["review_response_not_json"],
            request=request,
            raw_response_text=text,
        )

    corrected = parsed.get("corrected_hierarchy")
    if not isinstance(corrected, dict):
        return HierarchyReviewResult(
            status="no_correction",
            hierarchy=hierarchy,
            issues=parsed.get("issues") if isinstance(parsed.get("issues"), list) else [],
            request=request,
            raw_response=parsed,
            raw_response_text=text,
        )

    candidate = _hierarchy_from_payload(corrected, fallback=hierarchy)
    candidate.generation = {
        **(candidate.generation or {}),
        "source": "sonnet_corrected",
        "review_status": parsed.get("review_status"),
        "review_confidence": parsed.get("confidence"),
        "issues": parsed.get("issues") if isinstance(parsed.get("issues"), list) else [],
    }
    validation = validate_hierarchy(candidate)
    candidate.generation["validation"] = validation
    if not validation["valid"]:
        return HierarchyReviewResult(
            status="invalid_correction",
            hierarchy=hierarchy,
            issues=parsed.get("issues") if isinstance(parsed.get("issues"), list) else [],
            warnings=validation["errors"],
            request=request,
            raw_response=parsed,
            raw_response_text=text,
        )

    return HierarchyReviewResult(
        status="corrected",
        hierarchy=candidate,
        issues=parsed.get("issues") if isinstance(parsed.get("issues"), list) else [],
        request=request,
        raw_response=parsed,
        raw_response_text=text,
    )


def build_hierarchy_review_request(
    *,
    hierarchy: DocumentHierarchy,
    triage_payload: dict[str, Any],
    text_quality_payload: dict[str, Any],
    profile_payload: dict[str, Any],
    config: HierarchyReviewConfig,
) -> dict[str, Any]:
    """Build the exact Converse request body sent to the hierarchy review model."""
    return _build_request(
        hierarchy=hierarchy,
        triage_payload=triage_payload,
        text_quality_payload=text_quality_payload,
        profile_payload=profile_payload,
        config=config,
    )


def _build_request(
    *,
    hierarchy: DocumentHierarchy,
    triage_payload: dict[str, Any],
    text_quality_payload: dict[str, Any],
    profile_payload: dict[str, Any],
    config: HierarchyReviewConfig,
) -> dict[str, Any]:
    system_prompt = (
        "You review deterministic document hierarchy JSON for an ingestion pipeline. "
        "Return only JSON. You may correct missing nodes, false positives, parentage, "
        "page ranges, and table/figure placement when supported by the supplied evidence. "
        "Do not invent headings that are not supported by the page observations, TOC, or text."
    )
    payload = {
        "deterministic_hierarchy": hierarchy.to_dict(),
        "triage": triage_payload,
        "text_quality": text_quality_payload,
        "profile": profile_payload,
        "expected_response_schema": {
            "review_status": "ok | corrected | rejected",
            "confidence": 0.0,
            "recommended_action": "use_deterministic_hierarchy | use_corrected_hierarchy",
            "corrected_hierarchy": "same shape as deterministic_hierarchy or null",
            "issues": [],
            "profile_suggestion": None,
            "textract_recommendation": "none | selected_pages_only | full_document",
        },
    }
    return {
        "messages": [{"role": "user", "content": [{"text": json.dumps(payload)}]}],
        "system": [{"text": system_prompt}],
        "inferenceConfig": {
            "temperature": config.temperature,
            "maxTokens": config.max_tokens,
        },
    }


def _extract_text_from_converse_response(response: dict[str, Any]) -> str:
    output = response.get("output") or {}
    message = output.get("message") or {}
    for item in message.get("content") or []:
        if isinstance(item, dict) and "text" in item:
            return str(item["text"])
    return ""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = re.sub(r"^json\s*", "", stripped, flags=re.IGNORECASE)
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None


def _hierarchy_from_payload(payload: dict[str, Any], *, fallback: DocumentHierarchy) -> DocumentHierarchy:
    nodes_payload = payload.get("nodes")
    nodes = []
    if isinstance(nodes_payload, list):
        for item in nodes_payload:
            if not isinstance(item, dict):
                continue
            try:
                nodes.append(
                    HierarchyNode(
                        hierarchy_node_id=str(item["hierarchy_node_id"]),
                        label=item.get("label"),
                        title=str(item.get("title") or item.get("label") or ""),
                        node_type=str(item.get("node_type") or "section"),
                        parent_id=item.get("parent_id"),
                        children=list(item.get("children") or []),
                        heading_path=list(item.get("heading_path") or []),
                        page_start=item.get("page_start"),
                        page_end=item.get("page_end"),
                        char_start=item.get("char_start"),
                        char_end=item.get("char_end"),
                        token_estimate=item.get("token_estimate"),
                        source="sonnet_corrected",
                        evidence=list(item.get("evidence") or []),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    if not nodes:
        return fallback
    return DocumentHierarchy(
        document_id=str(payload.get("document_id") or fallback.document_id),
        schema_version=str(payload.get("schema_version") or fallback.schema_version),
        profile=str(payload.get("profile") or fallback.profile),
        heading_pattern_type=str(
            payload.get("heading_pattern_type") or fallback.heading_pattern_type
        ),
        source_location=str(payload.get("source_location") or fallback.source_location),
        root_nodes=list(payload.get("root_nodes") or []),
        nodes=nodes,
        generation=dict(payload.get("generation") or {}),
    )
