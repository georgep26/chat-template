"""Known parser profiles for hierarchy-aware document ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from document_ingestion_lambda.triage import PdfTriageResult


@dataclass(frozen=True)
class ParserProfile:
    """A constrained parser profile selected by backend code."""

    profile_id: str
    description: str
    heading_pattern_types: tuple[str, ...]
    default_heading_pattern_type: str
    level_rules: dict[str, Any] = field(default_factory=dict)
    title_cleanup_rules: dict[str, Any] = field(default_factory=dict)
    false_positive_rules: dict[str, Any] = field(default_factory=dict)
    toc_rules: dict[str, Any] = field(default_factory=dict)
    chunking_defaults: dict[str, Any] = field(default_factory=dict)
    citation_display_fields: tuple[str, ...] = ("label", "title", "page_start")


PROFILES: dict[str, ParserProfile] = {
    "building_code": ParserProfile(
        profile_id="building_code",
        description="Technical code documents with alphanumeric section ids such as R303.10.",
        heading_pattern_types=("section_codes", "chapter_heading"),
        default_heading_pattern_type="section_codes",
        level_rules={"section_id_depth": "dot_components"},
        false_positive_rules={"reject_toc_dot_leaders": True, "reject_long_sentence_titles": True},
        chunking_defaults={"target_min_tokens": 300, "target_max_tokens": 3000, "hard_max_tokens": 7000},
        citation_display_fields=("label", "title", "heading_path", "page_start"),
    ),
    "numbered_manual": ParserProfile(
        profile_id="numbered_manual",
        description="Manuals or policies with numeric headings such as 5.4.1.",
        heading_pattern_types=("section_codes", "numbered_headings", "chapter_heading"),
        default_heading_pattern_type="section_codes",
    ),
    "prose_report": ParserProfile(
        profile_id="prose_report",
        description="Reports with prose headings and weaker numeric hierarchy.",
        heading_pattern_types=("chapter_heading",),
        default_heading_pattern_type="chapter_heading",
    ),
    "presentation": ParserProfile(
        profile_id="presentation",
        description="Slide or page-first documents.",
        heading_pattern_types=("page_only",),
        default_heading_pattern_type="page_only",
    ),
    "plain_document": ParserProfile(
        profile_id="plain_document",
        description="Fallback profile for documents without a reliable hierarchy.",
        heading_pattern_types=("page_only",),
        default_heading_pattern_type="page_only",
    ),
}


def get_parser_profile(profile_id: str | None) -> ParserProfile:
    """Return a known parser profile, defaulting to plain document."""
    if profile_id and profile_id in PROFILES:
        return PROFILES[profile_id]
    return PROFILES["plain_document"]


def select_parser_profile(
    *,
    title: str | None,
    filename: str,
    triage: PdfTriageResult,
) -> tuple[ParserProfile, dict[str, Any]]:
    """Select a parser profile from filename/title and triage observations."""
    title_filename = f"{title or ''} {filename}".lower()
    pattern_counts: dict[str, int] = {}
    likely_types: dict[str, int] = {}
    for observation in triage.page_observations:
        if observation.heading_pattern:
            pattern_counts[observation.heading_pattern] = (
                pattern_counts.get(observation.heading_pattern, 0) + 1
            )
        if observation.likely_document_type:
            doc_type = observation.likely_document_type.lower()
            likely_types[doc_type] = likely_types.get(doc_type, 0) + 1
        for candidate in observation.heading_candidates:
            pattern = candidate.heading_pattern_type
            if pattern:
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

    profile_id = "plain_document"
    confidence = 0.4
    if _looks_like_building_code(title_filename) or pattern_counts.get("section_codes", 0) >= 2:
        profile_id = "building_code"
        confidence = 0.85
    elif pattern_counts.get("numbered_headings", 0) or pattern_counts.get("section_codes", 0):
        profile_id = "numbered_manual"
        confidence = 0.75
    elif pattern_counts.get("chapter_heading", 0):
        profile_id = "prose_report"
        confidence = 0.65
    elif any("presentation" in item or "slide" in item for item in likely_types):
        profile_id = "presentation"
        confidence = 0.7

    profile = get_parser_profile(profile_id)
    diagnostics = {
        "profile_id": profile.profile_id,
        "confidence": confidence,
        "pattern_counts": pattern_counts,
        "likely_document_types": likely_types,
    }
    return profile, diagnostics


def _looks_like_building_code(value: str) -> bool:
    return bool(
        re.search(
            r"\b(irc|ibc|nfpa|international residential code|building code|residential code)\b",
            value,
        )
    )
