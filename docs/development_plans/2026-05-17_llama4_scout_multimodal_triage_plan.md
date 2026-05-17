# Plan: Replace Nova Lite triage with Llama 4 Scout combined extraction

Created: 2026-05-17
Status: Implementation plan
Related:
- `2026-05-16_hierarchical_chunking_detailed_implementation_plan.md`
- `2026-05-16_hierarchical_chunking_strategy_options.md`
- `hierachical_chunking_refinement.md`
- `docs/working_files/llm_extraction_test/page_99_cross_model_comparison.md`

## Problem

The current multimodal triage step (`amazon.nova-lite-v1:0`) has three
critical failures on building-code documents:

1. **Content filter blocks 70%+ of body pages.** Nova Lite's non-disableable
   RAI filter returns `stopReason: content_filtered` with 0 output tokens on
   pages containing fire-resistance, heating, and similar regulatory text.
   This means the pipeline gets no visible-text observations for those pages.

2. **Text quality comparison degrades.** `text_quality.py` compares triage
   `raw_visible_text` with embedded PDF text to decide OCR need. When 73 of
   100 pages parse-error or get blocked, only 27 observations are available,
   making the text-quality report unreliable.

3. **Hierarchy heading detection is impoverished.** The deterministic
   hierarchy builder (`hierarchy.py`) selects heading patterns from triage
   observations. With most pages failing, only front-matter + a handful of
   body pages contribute heading candidates, leading to false positives (the
   `101167926` order-number regex matches we diagnosed) and duplicate node
   IDs.

Additionally, the Sonnet hierarchy review step (`hierarchy_review_model_mode:
"always"`) sends the entire broken 385-node hierarchy + 430KB triage payload
to Claude Sonnet 4.6, which either times out (botocore `ReadTimeoutError` at
60s default × 3 retries) or runs the Lambda close to its 900s ceiling.

## Validated solution

Testing in `docs/working_files/llm_extraction_test/llama_4_scout/` proved
that **Meta Llama 4 Scout 17B** (`us.meta.llama4-scout-17b-instruct-v1:0`)
can produce, in a single call per page:

- **Verbatim text transcription** (92.4% char coverage vs pdftotext, no
  hallucinations, correct reference numbers, correct section-code prefixes).
- **TOC detection** with structured `toc_entries` (label, title, page_ref).
- **Section heading extraction** with labels, titles, heading pattern type.
- **Table/figure descriptions** rich enough for vector-search embedding
  (captions, multi-sentence descriptions with example search queries).

All at **$0.25 projected cost for the full 100-page IRC sample** with zero
content-filter refusals. The combined extraction v2 prompt was validated on
pages 22 (TOC), 81 (figure/map), 93 (table-heavy), and 99 (section body).

## Design

### Architecture change: two-model pipeline → single-model full-page extraction

**Current architecture:**
```
Nova Lite (structure-only triage, often blocked)
  → text_quality.py compares visible vs embedded text
  → hierarchy.py builds nodes from heading candidates + embedded text regex
  → hierarchy_review.py sends full hierarchy to Sonnet (expensive, times out)
  → hierarchy_chunking.py builds adaptive chunks
```

**New architecture:**
```
Llama 4 Scout (combined extraction: text + headings + TOC + tables/figures)
  → text_quality.py compares model text vs embedded text (reliable now)
  → hierarchy.py builds nodes from heading candidates + model text
  → hierarchy_review.py: disabled or gated on low confidence only
  → hierarchy_chunking.py builds adaptive chunks (same as before)
```

Key differences:
1. **One Bedrock call per page** replaces Nova Lite's structure-only call.
   The output is richer (includes verbatim text), so downstream steps have
   better inputs.
2. **Embedded PDF text remains the primary text source.** The model's
   `raw_visible_text` is used for gap detection: when a page's embedded text
   is suspiciously short compared to what the model saw, the longer source
   wins for that page. The model text is also stored as a reference artifact.
3. **Heading candidates come directly from the model** rather than being
   inferred solely from regex on embedded text. The regex remains as a
   validation layer.
4. **Text quality comparison becomes meaningful** because the model actually
   returns text on every page (no content filter), giving reliable per-page
   mismatch ratios.
5. **Sonnet hierarchy review becomes optional** because the model-extracted
   headings + validated regex should produce a clean hierarchy without needing
   a $3/M-token correction pass.

### What stays the same

- `PdfTriageResult` and `PageTriageObservation` data structures.
- `text_quality.py` comparison logic (unchanged — just gets better inputs).
- `hierarchy_chunking.py` (unchanged — consumes `DocumentHierarchy`).
- `section_chunking.py` regex detection (used as validation, not sole source).
- S3 artifact layout and catalog metadata schema.
- The Lambda handler, `DocumentIngestionService`, and orchestration in
  `pdf.py` remain structurally the same.

## Implementation steps

### 1. Update the triage system prompt

**File:** `src/document_ingestion_lambda/triage.py`

Replace `_TRIAGE_SYSTEM_PROMPT` (line 146) with the validated combined
extraction v2 prompt from
`docs/working_files/llm_extraction_test/llama_4_scout/combined_extraction_v2/system_prompt.md`.

Key changes:
- Adds `raw_visible_text` back (verbatim transcription) — this was removed in
  our v3 Nova Lite structure-only variant but works on Llama 4 Scout.
- Enriches table/figure `description` instructions for vector-search quality.
- Adds Unicode-only formatting rules (no LaTeX output).
- Keeps the same JSON schema keys so `_coerce_decision()` and
  `_decision_to_observation()` continue to work.

Add a model-specific user-message template:
```python
_TRIAGE_USER_TEXT = (
    "This is page {page_number}. "
    "Return the JSON object described in the system instructions."
)
```

### 2. Update triage config to support Llama 4 Scout

**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py`
(lines 424-440 in `_build_triage_config()`)

**File:** `config/sandbox/app_config.yaml`

Config changes:
```yaml
ingestion:
  # Switch triage model to Llama 4 Scout.
  pdf_triage_model_id: "us.meta.llama4-scout-17b-instruct-v1:0"
  # Increase output token budget for combined extraction.
  # Page 22 (TOC) used 6,064 tokens; 8192 gives headroom.
  pdf_triage_max_tokens_per_page: 8192
  # Keep full-page triage enabled — Scout handles all page types.
  pdf_full_page_triage_enabled: true
```

No code changes needed in `_build_triage_config()` — it already reads
`pdf_triage_model_id` and passes it through to `TriageConfig.model_id`.

### 3. Add Bedrock client timeout configuration

**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py`
(lines 133-143 in `_get_bedrock_client()`)

The current code uses default botocore timeouts (60s read, 60s connect, 3
retries). Llama 4 Scout page calls take 8-20s; the Sonnet review call is
where timeouts occur. Add explicit timeout config:

```python
from botocore.config import Config as BotoConfig

def _get_bedrock_client(self) -> Any | None:
    if self._bedrock_client is not None:
        return self._bedrock_client
    region = self._ingestion_config.get("pdf_triage_region") or "us-east-1"
    boto_config = BotoConfig(
        read_timeout=120,
        connect_timeout=10,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    self._bedrock_client = _maybe_boto3_client(
        "bedrock-runtime", region, config=boto_config
    )
    return self._bedrock_client
```

Update `_maybe_boto3_client` to accept and forward `config`.

### 4. Embedded text stays primary; model text detects gaps and supplements

**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py`
**File:** `src/document_ingestion_lambda/file_type_processors/text_quality.py`

The embedded PDF text (pypdf) remains the **source of truth** for chunk text.
The model's `raw_visible_text` serves two purposes:

1. **Gap detection** — the text quality comparison identifies pages where the
   embedded text layer is missing content that the model can see.
2. **Per-page fallback** — when a specific page exceeds the mismatch
   threshold, use whichever source (embedded or model) has more text for that
   page.

The model text output is also **stored as a reference artifact** so it can be
consulted later without re-running the model.

**Decision logic in `process()`:**
1. Run triage → `PdfTriageResult` with `raw_visible_text` per page.
2. Extract embedded text → `PdfTextExtractionResult` (pypdf).
3. Build text quality report comparing model text vs embedded text
   (existing `text_quality.py` logic — no change).
4. Build the final text extraction result:
   - Start with embedded text as the base for every page.
   - For pages where `mismatch_ratio >= page_mismatch_threshold`: compare
     char counts between embedded and model text. Use whichever is longer.
   - For pages where OCR/Textract is recommended at the document level: run
     Textract as before (unchanged).
5. Pass the final merged text to hierarchy builder and chunk builder.

**New helper:**

```python
def _merge_text_sources(
    *,
    embedded: PdfTextExtractionResult,
    triage: PdfTriageResult,
    text_quality_report: TextQualityReport,
    page_mismatch_threshold: float,
) -> PdfTextExtractionResult:
    """Merge embedded and model text, preferring embedded except on flagged pages."""
    model_text_by_page = {
        obs.page_number: obs.raw_visible_text
        for obs in triage.page_observations
        if not obs.parse_error and obs.raw_visible_text
    }
    suspicious_pages = set(text_quality_report.pages_over_text_mismatch_threshold)

    merged_pages = []
    for page in embedded.pages:
        model_text = model_text_by_page.get(page.page_number, "")
        if page.page_number in suspicious_pages and len(model_text) > len(page.text):
            merged_pages.append(PdfPageText(
                page_number=page.page_number,
                text=model_text,
            ))
        else:
            merged_pages.append(page)

    # Add pages the embedded layer missed entirely but the model captured.
    embedded_page_numbers = {p.page_number for p in embedded.pages}
    for page_number, text in model_text_by_page.items():
        if page_number not in embedded_page_numbers and text.strip():
            merged_pages.append(PdfPageText(page_number=page_number, text=text))

    merged_pages.sort(key=lambda p: p.page_number)
    return PdfTextExtractionResult(
        extraction_status="merged_embedded_and_model",
        pages=merged_pages,
    )
```

**Store model text as a reference artifact:**

Write the full model-extracted text to S3 alongside existing triage artifacts:

```
kb_raw/{document_id}/triage/model_extracted_text.json
```

Format:
```json
{
  "extraction_model_id": "us.meta.llama4-scout-17b-instruct-v1:0",
  "pages": [
    {"page_number": 1, "text": "..."},
    {"page_number": 2, "text": "..."}
  ],
  "total_pages_with_text": 95,
  "total_chars": 142000
}
```

This artifact can be consumed later for re-processing, debugging, or as input
to future pipeline steps without re-running the model.

### 5. Improve heading candidate usage in hierarchy builder

**File:** `src/document_ingestion_lambda/file_type_processors/hierarchy.py`

Currently the hierarchy builder:
1. Selects a heading pattern from triage observations (line 218-231).
2. Runs regex on the joined extracted text to find boundaries (line 82-91).

With Llama 4 Scout providing high-quality `heading_candidates` per page, we
can **supplement the regex with model-observed headings**:

```python
def _merge_model_headings_into_boundaries(
    boundaries: list[SectionBoundary],
    triage: PdfTriageResult,
    joined_text: _JoinedText,
    min_section_chars: int,
) -> list[SectionBoundary]:
    """Add model-observed headings that the regex missed."""
    existing_ids = {b.section_id for b in boundaries}
    for obs in triage.page_observations:
        for candidate in obs.heading_candidates:
            if candidate.label in existing_ids:
                continue
            # Attempt to locate this heading in the joined text.
            offset = _find_heading_in_text(
                joined_text.text, candidate.label, candidate.title
            )
            if offset is not None:
                # Insert a boundary at this offset; end will be resolved later.
                ...
    # Re-sort and re-compute end offsets.
    ...
```

This is the key integration: headings the regex missed (because triage
previously didn't cover those pages, or because the heading format is
irregular) can now be recovered from the model's observations. The model
already knows "R303.10 Required heating" is a heading from visual context,
even if the regex might miss it in some edge cases.

### 6. Fix duplicate hierarchy node IDs

**File:** `src/document_ingestion_lambda/file_type_processors/hierarchy.py`
(lines 234-282 in `_nodes_from_boundaries()`)

The root cause we diagnosed: `_SECTION_CODE_RE` matches header/footer noise
like `101167926` on every page. Two fixes:

**a) Tighten the section-code regex for `building_code` profile:**

**File:** `src/document_ingestion_lambda/file_type_processors/section_chunking.py`

```python
# Require a 1-3 letter prefix for building_code profile.
_SECTION_CODE_BUILDING_RE = re.compile(
    r"^\s*(?P<id>[A-Z]{1,3}\d+(?:\.\d+)*)\s+(?P<title>\S.+?)\s*$",
    re.MULTILINE,
)
```

Add `_select_pattern()` to pick `_SECTION_CODE_BUILDING_RE` when the profile
is `building_code`, keeping the existing `_SECTION_CODE_RE` for other
profiles.

**b) Deduplicate boundaries before building nodes:**

```python
def _deduplicate_boundaries(
    boundaries: list[SectionBoundary],
) -> list[SectionBoundary]:
    """Keep only the first occurrence of each section_id."""
    seen: set[str] = set()
    result = []
    for boundary in boundaries:
        if boundary.section_id in seen:
            continue
        seen.add(boundary.section_id)
        result.append(boundary)
    return result
```

### 7. Gate the Sonnet hierarchy review

**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py`
(lines 720-833)

**File:** `config/sandbox/app_config.yaml`

Change sandbox config:
```yaml
hierarchy_review_model_mode: "auto"  # Was "always"
hierarchy_review_min_confidence: 0.80
```

In `auto` mode, skip the review when:
- The hierarchy validates cleanly (`validation.errors` is empty).
- Confidence is ≥ threshold.
- The model-extracted headings align with the regex-detected boundaries.

This saves the Sonnet call (and its timeout risk) on documents where the
deterministic builder produces a clean hierarchy. For the IRC sample, with the
regex fix + model headings, this should be the case.

### 8. Use model table/figure descriptions for asset chunks

**Files:**
- `src/document_ingestion_lambda/file_type_processors/table_extraction.py`
- `src/document_ingestion_lambda/file_type_processors/figure_extraction.py`

Currently these modules call Textract + Nova Lite to extract and summarize
tables/figures. The Llama 4 Scout combined extraction already produces rich
descriptions per page. We can:

1. **Skip separate table/figure summarization calls** when the triage model
   already produced a description for that table/figure.
2. **Store model-generated descriptions as the chunk summary text** instead of
   (or alongside) a separate Nova Lite summary call.
3. **Still use Textract for structured table data** (CSV/JSON of cells) when
   table extraction is enabled — the model description is for the embedding,
   not for the raw data.

This eliminates Nova Lite table/figure summary calls entirely for the cost of
no additional Bedrock calls — the descriptions are already in the triage
output.

### 9. Update Lambda timeout and memory

**File:** `scripts/deploy/resources/document_ingestion_lambda.sh` (or
equivalent infra config)

Current Lambda: timeout=900s, memory=2048MB.

With Llama 4 Scout at ~10s per page × 100 pages = ~1000s sequential. This
exceeds the 900s Lambda limit. Options:

**a) Parallelize triage calls.** Process pages in batches using
`ThreadPoolExecutor(max_workers=6)`. 100 pages / 6 workers × 10s avg = ~170s
for triage. Total pipeline ~250-350s.

**b) Increase Lambda timeout to 900s (already there).** With parallelized
triage, the total should fit well within 900s.

**c) Consider async/step-function for very large documents.** Out of scope
for initial implementation — the IRC 100-page sample should fit in a single
Lambda invocation with parallel triage.

### 10. Add parallel triage execution

**File:** `src/document_ingestion_lambda/triage.py`
(lines 198-241 in `multimodal_triage()`)

Replace the serial `for page_number in sampled:` loop with a
`ThreadPoolExecutor`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def multimodal_triage(...) -> PdfTriageResult:
    ...
    max_workers = min(8, len(sampled))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _process_single_page,
                pdf_path=pdf_path,
                page_number=page_number,
                config=config,
                bedrock_client=bedrock_client,
                render_page_image=render_page_image,
            ): page_number
            for page_number in sampled
        }
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                decision = future.result()
                raw_decisions.append(decision)
            except Exception as exc:
                warnings.append(f"page_{page_number}_failed:{type(exc).__name__}")
    ...
```

This reduces a 100-page document from ~17 minutes serial to ~2-3 minutes
parallel, well within Lambda budget.

## Config changes summary

```yaml
ingestion:
  # ---- Model change ----
  pdf_triage_model_id: "us.meta.llama4-scout-17b-instruct-v1:0"
  pdf_triage_max_tokens_per_page: 8192

  # ---- Hierarchy review gating ----
  hierarchy_review_model_mode: "auto"  # Was "always"

  # ---- Table/figure extraction (use model descriptions) ----
  pdf_table_extraction_enabled: false   # Model descriptions replace Textract summaries
  pdf_figure_extraction_enabled: false  # Model descriptions replace Nova Lite summaries
```

## Verification plan

1. **Unit tests:** Update `tests/test_document_ingestion_triage.py` to stub
   Llama 4 Scout responses (same JSON schema, richer content). Verify
   `_coerce_decision()` and aggregation work with model-extracted text.

2. **Hierarchy tests:** Update `tests/test_hierarchy_builder.py` with the
   tightened regex. Verify the IRC sample no longer produces duplicate node
   IDs or `101167926` false positives.

3. **Integration test (sandbox):** Re-ingest the IRC 100-page sample with:
   - Llama 4 Scout as triage model
   - Full-page triage enabled
   - Parallel execution (6-8 workers)
   - Sonnet review disabled or gated on confidence
   - Verify: no timeouts, document_hierarchy.json is valid, chunks have
     correct section IDs

4. **Quality check:** After ingestion, ask the heating question through the
   RAG Lambda. Confirm:
   - R303.10 is in the retrieved chunk
   - Citation resolves to R303.10 hierarchy node
   - Source card shows correct section label and page

5. **Cost check:** Confirm the Bedrock bill for the full ingestion is in the
   $0.25 range (vs. ~$5-10 for the old Nova Lite + Sonnet pipeline that
   mostly failed anyway).

## Implementation order

1. Fix regex + deduplicate (Step 6) — smallest, safest, immediately fixes the
   duplicate-ID validation errors.
2. Update triage prompt (Step 1) — drop-in prompt replacement.
3. Switch model ID + raise max tokens (Step 2) — config change only.
4. Add Bedrock client timeouts (Step 3) — defensive improvement.
5. Parallelize triage (Step 10) — needed to fit within Lambda budget.
6. Add per-page text merge + store model text artifact (Step 4) — enables
   reliable text quality comparison and per-page gap filling.
7. Merge model headings into hierarchy (Step 5) — quality improvement.
8. Gate Sonnet review (Step 7) — cost/timeout reduction.
9. Use model descriptions for table/figure chunks (Step 8) — eliminates
   separate summarization calls.
10. Update Lambda config if needed (Step 9) — verify timing.

## Cost comparison

| Approach | Per 100-page doc | Notes |
| --- | ---: | --- |
| Current (Nova Lite triage + Sonnet review) | ~$5-10 | Mostly fails; Sonnet times out |
| Llama 4 Scout combined extraction (parallel) | **~$0.25** | Tested and validated |
| Llama 4 Scout + Haiku fallback (if needed) | ~$0.25-1.00 | Only if Scout quality proves insufficient on edge cases |

## Risks

| Risk | Mitigation |
| --- | --- |
| Llama 4 Scout becomes unavailable on Bedrock | Model ID is config-driven; fallback to Claude Haiku 4.5 or Pixtral |
| Scout quality degrades on non-IRC documents | Test with a policy doc and a report-style PDF before production rollout |
| Parallel Bedrock calls hit account throttling | Start with 6 workers; monitor `ThrottlingException` and back off |
| Model text has minor formatting artifacts (Unicode fractions) | Post-process with a small regex normalizer before text is indexed |
| TOC pages generate very large JSON responses | Already tested: page 22 used 6,064 output tokens, well within 8192 budget |

## Decisions

1. **Embedded PDF text is the primary source of truth.** The model's
   `raw_visible_text` is a comparison/gap-detection layer, not a replacement.
   On pages where the text quality report flags a mismatch above threshold,
   whichever source (embedded or model) has more text wins for that page. The
   model text is stored at `kb_raw/{document_id}/triage/model_extracted_text.json`
   as a reference artifact for future use.

2. **Llama 4 Scout replaces Nova Lite as the universal triage model.** At
   $0.25/doc it's cheap enough for all document types without maintaining two
   prompt paths. No Nova Lite fallback needed.

3. **Textract is still used for structured table cell extraction** when the
   pipeline needs cell-level CSV/JSON for direct lookup. The model's rich
   description is for the search-embedding layer; Textract gives raw tabular
   data. But the separate Nova Lite table-summary call is eliminated — the
   model description from triage replaces it.

## Open questions

1. **What mismatch threshold should trigger the per-page text swap?** Starting
   point: reuse the existing `pdf_triage_text_mismatch_threshold` (currently
   0.50). Validate on the IRC sample — pages with tables/figures may have
   legitimate embedded-text gaps even when the model text is correct.

2. **Should the model text artifact include pages where `parse_error=True`?**
   Recommendation: no — only store pages where the model returned valid JSON
   with non-empty `raw_visible_text`. Document the page numbers that were
   skipped so debugging is straightforward.

3. **Should the heading-merge step (Step 5) require the heading to appear in
   the primary text before accepting it?** Recommendation: yes — this prevents
   model hallucinations from creating phantom hierarchy nodes. The model
   observes the heading visually; the regex or substring search confirms it
   exists in the authoritative text layer.
