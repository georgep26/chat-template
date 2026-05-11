"""Tests for the multimodal PDF triage pass.

We never call Bedrock or render a real PDF in tests. The Bedrock client is
a stub that records `converse` calls and returns canned answers per page;
page rendering is replaced by a callable that returns deterministic dummy
bytes.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from document_ingestion_lambda.triage import (
    PdfTriageResult,
    TriageConfig,
    _aggregate_decisions,
    _coerce_decision,
    _parse_decision_json,
    _select_sampled_pages,
    multimodal_triage,
    triage_summary_dict,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubBedrockClient:
    """Records `converse` calls and replays a queue of pre-baked answers.

    Pass `answers={page_number: dict_or_json_text}` to map specific pages
    to specific JSON answers. Any pages not in the map get the
    `default_answer`.
    """

    def __init__(
        self,
        *,
        answers: dict[int, Any] | None = None,
        default_answer: Any | None = None,
        raise_for_pages: set[int] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._answers = answers or {}
        self._default = default_answer
        self._raise_for_pages = raise_for_pages or set()

    def converse(
        self,
        *,
        modelId: str,
        messages: list[dict[str, Any]],
        system: list[dict[str, Any]],
        inferenceConfig: dict[str, Any],
    ) -> dict[str, Any]:
        # Recover the user text (it includes the page number).
        page_number = None
        for msg in messages:
            for item in msg.get("content", []):
                text = item.get("text", "") if isinstance(item, dict) else ""
                if "page " in text:
                    try:
                        # "This is page N." → pull N.
                        page_number = int(
                            text.split("This is page ")[1].split(".")[0]
                        )
                    except (IndexError, ValueError):
                        page_number = None

        self.calls.append(
            {
                "modelId": modelId,
                "page_number": page_number,
                "messages": messages,
                "system": system,
                "inferenceConfig": inferenceConfig,
            }
        )

        if page_number in self._raise_for_pages:
            raise RuntimeError(f"stub_failure_for_page_{page_number}")

        answer = self._answers.get(page_number, self._default)
        if answer is None:
            answer = {
                "embedded_text_quality": "high",
                "appears_to_be_toc": False,
                "contains_table": False,
                "contains_figure": False,
                "heading_pattern": "page_only",
            }

        text = answer if isinstance(answer, str) else json.dumps(answer)
        return {"output": {"message": {"content": [{"text": text}]}}}


def fake_render(_path: Path, page_number: int, _scale: float) -> bytes:
    """Return deterministic 'PNG' bytes for testing."""
    return f"page-{page_number}".encode("utf-8")


def base_config(**overrides: Any) -> TriageConfig:
    cfg = TriageConfig(
        enabled=True,
        front_page_sample_count=2,
        random_page_sample_count=2,
        ocr_threshold=0.30,
        hierarchy_threshold=0.50,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# Page sampling
# ---------------------------------------------------------------------------


def test_sampled_pages_returns_full_range_when_doc_is_small():
    pages = _select_sampled_pages(
        page_count=3, front_count=5, random_count=5, rng=random.Random(0)
    )
    assert pages == [1, 2, 3]


def test_sampled_pages_combines_front_and_random_dedup_sorted():
    rng = random.Random(0)
    pages = _select_sampled_pages(
        page_count=100, front_count=3, random_count=4, rng=rng
    )
    assert pages[:3] == [1, 2, 3]
    assert len(pages) == 7
    assert pages == sorted(set(pages))
    assert all(p > 3 for p in pages[3:])


def test_sampled_pages_handles_zero_random():
    pages = _select_sampled_pages(
        page_count=10, front_count=4, random_count=0, rng=random.Random(0)
    )
    assert pages == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_decision_json_strips_code_fences():
    raw = '```json\n{"embedded_text_quality": "high", "appears_to_be_toc": false, "contains_table": false, "contains_figure": false, "heading_pattern": "section_codes"}\n```'
    out = _parse_decision_json(raw, page_number=5)
    assert out["heading_pattern"] == "section_codes"
    assert out["embedded_text_quality"] == "high"
    assert "parse_error" not in out  # the page_number is set by the caller


def test_parse_decision_json_extracts_embedded_object():
    raw = 'Here is my answer: {"embedded_text_quality": "low", "appears_to_be_toc": true, "contains_table": false, "contains_figure": false, "heading_pattern": "chapter_heading"} done.'
    out = _parse_decision_json(raw, page_number=7)
    assert out["appears_to_be_toc"] is True
    assert out["embedded_text_quality"] == "low"


def test_parse_decision_json_falls_back_on_garbage():
    out = _parse_decision_json("not json at all", page_number=9)
    assert out.get("parse_error") is True
    assert out["heading_pattern"] == "unknown"


def test_coerce_decision_rejects_unknown_enum_values():
    raw = {
        "embedded_text_quality": "bogus",
        "appears_to_be_toc": False,
        "contains_table": False,
        "contains_figure": False,
        "heading_pattern": "garbage",
    }
    out = _coerce_decision(raw)
    assert out["embedded_text_quality"] == "unknown"
    assert out["heading_pattern"] == "unknown"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_aggregate_reports_ocr_required_when_threshold_met():
    decisions = [
        {"page_number": 1, "embedded_text_quality": "high", "heading_pattern": "page_only", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False},
        {"page_number": 2, "embedded_text_quality": "low", "heading_pattern": "page_only", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False},
        {"page_number": 3, "embedded_text_quality": "scanned_image_only", "heading_pattern": "page_only", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False},
    ]
    out = _aggregate_decisions(decisions, base_config(ocr_threshold=0.30))
    assert out.ocr_required is True
    assert out.ocr_confidence is not None and out.ocr_confidence > 0.5


def test_aggregate_reports_hierarchy_when_majority_consistent():
    decisions = [
        {"page_number": p, "embedded_text_quality": "high", "heading_pattern": "section_codes", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False}
        for p in (1, 2, 3, 4)
    ] + [
        {"page_number": 5, "embedded_text_quality": "high", "heading_pattern": "page_only", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False}
    ]
    out = _aggregate_decisions(decisions, base_config(hierarchy_threshold=0.50))
    assert out.has_hierarchy is True
    assert out.hierarchy_kind == "section_codes"


def test_aggregate_uses_toc_pages_as_secondary_hierarchy_trigger():
    """A multi-page TOC implies hierarchy even when individual pages look text-only.

    Scenario: a code document's 14-page TOC gets classified as ``page_only``
    (a TOC is a list of references, not numbered headings), dragging
    hierarchy_fraction below threshold. The toc_pages signal rescues it.
    """
    # 15 pages, all classified as page_only by Nova Lite — but 4 of them are
    # flagged as TOC pages. Without the toc trigger, hierarchy_fraction=0.0.
    decisions = []
    for p in range(1, 16):
        decisions.append({
            "page_number": p,
            "embedded_text_quality": "high",
            "heading_pattern": "page_only",
            "appears_to_be_toc": p in (5, 6, 7, 8),
            "contains_table": False,
            "contains_figure": False,
        })
    out = _aggregate_decisions(decisions, base_config(toc_pages_signal=3))
    assert out.has_hierarchy is True
    assert out.hierarchy_kind == "section_codes"  # default for code-style docs
    # hierarchy_confidence is derived from toc-signal strength
    # (4 toc pages / 3 signal threshold = 1.33 → capped at 1.0) so the
    # downstream section-chunker confidence gate can clear.
    assert out.hierarchy_confidence == 1.0


def test_aggregate_no_hierarchy_when_pages_are_text_only():
    decisions = [
        {"page_number": p, "embedded_text_quality": "high", "heading_pattern": "page_only", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False}
        for p in (1, 2, 3)
    ]
    out = _aggregate_decisions(decisions, base_config())
    assert out.has_hierarchy is False
    assert out.hierarchy_kind == "page_only"


def test_aggregate_collects_table_and_figure_pages():
    decisions = [
        {"page_number": 1, "embedded_text_quality": "high", "heading_pattern": "section_codes", "appears_to_be_toc": False, "contains_table": True, "contains_figure": False},
        {"page_number": 7, "embedded_text_quality": "high", "heading_pattern": "section_codes", "appears_to_be_toc": False, "contains_table": False, "contains_figure": True},
        {"page_number": 9, "embedded_text_quality": "high", "heading_pattern": "section_codes", "appears_to_be_toc": True, "contains_table": False, "contains_figure": False},
    ]
    out = _aggregate_decisions(decisions, base_config())
    assert out.table_pages == [1]
    assert out.figure_pages == [7]
    assert out.toc_pages == [9]


def test_aggregate_ignores_pages_with_parse_errors():
    decisions = [
        {"page_number": 1, "embedded_text_quality": "high", "heading_pattern": "page_only", "appears_to_be_toc": False, "contains_table": False, "contains_figure": False},
        {"page_number": 2, "parse_error": True, "heading_pattern": "unknown"},
    ]
    out = _aggregate_decisions(decisions, base_config())
    # Only page 1 was usable → no OCR needed (1 high = 0/1 needs OCR).
    assert out.ocr_required is False


def test_aggregate_all_unparseable_fails():
    decisions = [{"page_number": 1, "parse_error": True}]
    out = _aggregate_decisions(decisions, base_config())
    assert out.triage_status == "failed"


# ---------------------------------------------------------------------------
# End-to-end multimodal_triage()
# ---------------------------------------------------------------------------


def test_triage_completes_for_structured_document(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    client = StubBedrockClient(
        default_answer={
            "embedded_text_quality": "high",
            "appears_to_be_toc": False,
            "contains_table": True,
            "contains_figure": False,
            "heading_pattern": "section_codes",
        }
    )

    out = multimodal_triage(
        pdf_path=pdf,
        page_count=6,
        config=base_config(front_page_sample_count=2, random_page_sample_count=2),
        bedrock_client=client,
        render_page_image=fake_render,
        rng=random.Random(0),
    )
    assert out.triage_status == "completed"
    assert out.has_hierarchy is True
    assert out.hierarchy_kind == "section_codes"
    assert out.ocr_required is False
    assert out.table_pages  # at least one
    assert out.sampled_pages == sorted(out.sampled_pages)
    # The client received one converse call per sampled page.
    assert len(client.calls) == len(out.sampled_pages)


def test_triage_marks_ocr_needed_on_scanned_pages(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    client = StubBedrockClient(
        default_answer={
            "embedded_text_quality": "scanned_image_only",
            "appears_to_be_toc": False,
            "contains_table": False,
            "contains_figure": False,
            "heading_pattern": "page_only",
        }
    )

    out = multimodal_triage(
        pdf_path=pdf,
        page_count=5,
        config=base_config(front_page_sample_count=2, random_page_sample_count=2),
        bedrock_client=client,
        render_page_image=fake_render,
        rng=random.Random(0),
    )
    assert out.triage_status == "completed"
    assert out.ocr_required is True


def test_triage_fails_gracefully_when_every_page_call_errors(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    client = StubBedrockClient(raise_for_pages={1, 2, 3, 4, 5})

    out = multimodal_triage(
        pdf_path=pdf,
        page_count=3,
        config=base_config(front_page_sample_count=2, random_page_sample_count=1),
        bedrock_client=client,
        render_page_image=fake_render,
        rng=random.Random(0),
    )
    assert out.triage_status == "failed"


def test_triage_skipped_when_disabled(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")
    out = multimodal_triage(
        pdf_path=pdf,
        page_count=3,
        config=base_config(enabled=False),
        bedrock_client=StubBedrockClient(),
        render_page_image=fake_render,
    )
    assert out.triage_status == "skipped"


def test_triage_skipped_when_zero_pages(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")
    out = multimodal_triage(
        pdf_path=pdf,
        page_count=0,
        config=base_config(),
        bedrock_client=StubBedrockClient(),
        render_page_image=fake_render,
    )
    assert out.triage_status == "skipped"


def test_triage_summary_dict_contains_load_bearing_fields():
    triage = PdfTriageResult(
        triage_status="completed",
        ocr_required=False,
        ocr_confidence=0.9,
        has_hierarchy=True,
        hierarchy_kind="section_codes",
        hierarchy_confidence=0.8,
        sampled_pages=[1, 2, 3],
        toc_pages=[],
        table_pages=[2],
        figure_pages=[],
        warnings=[],
    )
    out = triage_summary_dict(triage)
    # raw_decisions is intentionally omitted from the summary to keep
    # document_metadata.json small.
    assert "raw_decisions" not in out
    assert out["triage_status"] == "completed"
    assert out["has_hierarchy"] is True
    assert out["hierarchy_kind"] == "section_codes"
    assert out["table_pages"] == [2]
