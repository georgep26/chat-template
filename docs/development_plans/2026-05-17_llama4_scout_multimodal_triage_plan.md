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

---

## Revision (2026-05-17): two-pass extraction with Sonnet hierarchy

### What changed and why

After completing the per-page Scout tests above, we ran a follow-up
investigation in
`docs/working_files/llm_extraction_test/full_document_hierarchy/`:

- **Full-doc Llama 4 Scout hierarchy call:** $0.02/doc, returns only 11
  nodes (chapters and top-level sections only). Iterated through prompt
  variants v1→v4 with increasingly aggressive completeness instructions;
  output plateaued at ~40-43 nodes. Every variant ended with
  `stop_reason: end_turn` and well under the 8K output cap. Scout
  self-limits on long-input structured-enumeration tasks regardless of
  prompt engineering.
- **Full-doc Claude Sonnet 4.6 hierarchy call:** $0.67/doc, returns 247
  nodes across 7 depth levels, captures R303.10 with correct parentage,
  no duplicate labels, no missing parents.

This makes Sonnet the right tool for the global hierarchy pass and **lets
us delete all the per-profile regex tuning** the user raised concerns
about. The final architecture is two-pass:

```
Per-page Llama 4 Scout ($0.25/doc)
  → verbatim text + heading_candidates + table/figure descriptions
  ↓
Full-doc Sonnet 4.6 ($0.67/doc)
  → globally-consistent hierarchy tree (247+ nodes, full depth)
  ↓
Hierarchy locator (deterministic, free)
  → map each Sonnet node (label, title, page) to (char_start, char_end)
    in the joined chunk text
  ↓
hierarchy_chunking.py (unchanged)
  → adaptive chunks with hierarchy index for each chunk
```

**Total: ~$0.92/doc.** No per-profile regex, no Sonnet-review timeout
risk (the review step disappears entirely — Sonnet IS the hierarchy
builder), no profile-tuning treadmill for new document types.

### Superseded steps

- **Step 5 (Merge model headings into hierarchy):** No longer needed.
  Sonnet produces a complete hierarchy in one call; Scout's per-page
  `heading_candidates` become a validation layer (drop any Sonnet node
  whose label doesn't appear in any Scout per-page output) rather than a
  source of nodes.
- **Step 6 (Tighten section-code regex, add `_SECTION_CODE_BUILDING_RE`):**
  No longer needed. Sonnet distinguishes real headings from
  header/footer noise like `101167926` using global context. The regex
  becomes obsolete for hierarchy detection; we keep it only as a
  defensive fallback for cases where Sonnet fails (e.g., model
  unavailable, JSON parse failure).
- **Step 7 (Gate the Sonnet hierarchy review):** No longer needed.
  There is no separate "review" call — Sonnet is the primary hierarchy
  builder. `hierarchy_review.py` is removed from the pipeline.

### New step: Hierarchy locator

**File:** `src/document_ingestion_lambda/file_type_processors/hierarchy_locator.py` (new)

**Purpose:** Convert Sonnet's hierarchy output (label + title + page) into
`(char_start, char_end)` spans in the joined chunk text so the existing
chunker and citation resolver can use it.

**Inputs:**
- `nodes: list[dict]` — Sonnet's parsed `nodes` array from the hierarchy
  call. Each entry has `label`, `title`, `node_type`, `parent_label`,
  `page_start`, `page_end`, `depth`.
- `joined_text: str` — the final merged chunk text (embedded PDF text with
  per-page swap to model text where flagged, per Step 4).
- `page_offsets: dict[int, int]` — map from page number to the char offset
  of that page's `=== PAGE N ===` marker (or first body char) in
  `joined_text`. Built once at join time.

**Output:** `list[HierarchyNode]` (the existing dataclass from
`hierarchy.py`), populated with `char_start`, `char_end`, `heading_path`,
parent/child relationships.

**Disambiguation strategy: have Sonnet emit a per-node `body_anchor`.**

A label like `R303.10` appears in PDF-extracted text in three shapes:

1. **Real heading** — at the start of a line, immediately followed by the
   title and body prose.
2. **Cross-reference in body prose** — e.g. `...follow R303.10 for heating
   requirements.` Mid-sentence, surrounded by prose.
3. **Running page header** — some PDFs repeat the parent section banner
   (e.g. `R303 LIGHT, VENTILATION AND HEATING`) at the top of every page.

A naive `str.find(label)` will frequently hit the wrong occurrence.
Instead, we ask Sonnet to emit a `body_anchor` per node — a verbatim 40-80
character snippet of body text that appears somewhere after the heading.
This snippet is unique enough to pin down the heading's location.

**Prompt schema addition.** Add one field to each node in the hierarchy
prompt:

```
"body_anchor": "A verbatim 40-80 character snippet from the document text
that appears anywhere after this heading. The snippet must be specific
enough to appear only once in the document — typically the first sentence
or two of body content includes specific terms (cross-references, numeric
thresholds, distinctive nouns) that make it unique. Copy the text exactly,
including punctuation and Unicode characters."
```

**Algorithm — locate via anchor, walk back to label line-start:**

```python
import re

_LEADING_WS = r"^[ \t]*"  # line-start with optional indentation

def locate_heading(
    label: str,
    body_anchor: str,
    page_start: int,
    joined_text: str,
    page_offsets: dict[int, int],
) -> tuple[int | None, str]:
    """
    Return (char_offset, confidence_tier).
    confidence_tier is one of:
      "anchored"           — anchor found, label walk-back succeeded
      "anchored_full_doc"  — anchor only found outside page window
      "anchor_not_found"   — anchor missing entirely (likely hallucination
                             or extraction artifact)
      "anchor_orphan"      — anchor found but no label match within 500
                             chars before it
    """
    # Scope the anchor search to page_start ± 1 first; fall back to full
    # document if not found (Sonnet's page numbers are usually right but
    # not perfect for headings that straddle a page break).
    lo = page_offsets.get(max(1, page_start - 1), 0)
    hi = page_offsets.get(page_start + 2, len(joined_text))
    haystack = joined_text[lo:hi]

    anchor_pos = haystack.find(body_anchor)
    if anchor_pos != -1:
        anchor_abs = lo + anchor_pos
        tier = "anchored"
    else:
        # Drift across the page window — try the full doc.
        anchor_abs = joined_text.find(body_anchor)
        if anchor_abs == -1:
            return None, "anchor_not_found"
        tier = "anchored_full_doc"

    # Walk back from the anchor to the nearest line-start matching the
    # label followed by whitespace. The trailing-whitespace requirement
    # makes the regex self-correcting: looking up `R303` correctly
    # ignores `R303.1`, `R3030`, `R303A` because those don't end in
    # whitespace after `R303`.
    pattern = re.compile(
        rf"{_LEADING_WS}{re.escape(label)}[ \t]",
        re.MULTILINE,
    )
    walk_back_start = max(0, anchor_abs - 500)
    snippet = joined_text[walk_back_start:anchor_abs]
    matches = list(pattern.finditer(snippet))
    if not matches:
        return None, "anchor_orphan"

    # The last line-start label match before the anchor is the heading.
    return walk_back_start + matches[-1].start(), tier
```

**Why this works (and why the algorithm is simpler than it might appear):**

The anchor's only job is "be a unique location marker." It does NOT have
to belong to the section whose heading we're locating — for example, if
`R303` has no body text and `R303.1` follows immediately, the anchor for
`R303` can be text from `R303.1`'s body. The walk-back still finds the
correct `R303` heading because:

- The anchor lands us in the right neighborhood.
- The label regex `^[ \t]*R303[ \t]` matches `R303 ` (heading line) but
  not `R303.1` (subsection — `.` is not whitespace), `R3030` (number — no
  whitespace), or `Section R303` (mid-line — fails the `^` anchor).
- The last matching line-start before the anchor is the right heading by
  construction.

This also handles all three failure modes from the disambiguation
discussion above:

- **Cross-references** don't have the anchor following them, so the
  anchor lands us in a different neighborhood and we don't even look at
  the cross-reference's surrounding text.
- **Page-header banners** don't have the anchor following them either.
- **Real heading** is the only line-start label match that has the anchor
  text downstream of it. Self-validating.

**Defensive handling per tier:**

- `"anchored"` — high confidence. Use the offset directly.
- `"anchored_full_doc"` — confidence is good but Sonnet's page number
  drifted. Log a warning so we can monitor accuracy of `page_start`
  values over time.
- `"anchor_not_found"` — Sonnet emitted an anchor that doesn't appear in
  the text. Drop the node and log. Indicates either a hallucination or
  text-extraction artifact (e.g., character drift between Sonnet's input
  text and the joined chunk text — they should be identical, but
  per-page model-text swaps in Step 4 mean they can diverge on flagged
  pages).
- `"anchor_orphan"` — anchor found but no label match within 500 chars
  before it. Means Sonnet's anchor is from a section earlier in the
  document than the label, or the label is wrong. Drop the node and log.

Log per-tier counts after every ingestion run so we can monitor anchor
quality across documents and tune the prompt if a particular tier becomes
common.

3. **Deduplicate by label.** If two emitted nodes share a label (should be
   impossible, but defensive), keep the first by `char_start`.

4. **Sort located nodes by `char_start`** and assign `char_end` from the
   next same-or-shallower-depth sibling's `char_start`:

   ```python
   nodes.sort(key=lambda n: n.char_start)
   for i, node in enumerate(nodes):
       next_start = len(joined_text)
       for later in nodes[i + 1:]:
           if later.char_start > node.char_start:
               next_start = later.char_start
               break
       node.char_end = next_start
   ```

5. **Propagate parent `char_end` to span the last descendant.** Reuse the
   existing logic in `hierarchy.py:270-281` so a parent like `R303` spans
   from its heading to the end of `R303.10`, not just to the next sibling.

6. **Build `heading_path` from `parent_label` chain.** Walk up the parent
   chain to produce the array of labels from root to this node, used by
   `hierarchy_index_for_range()` and citation display.

7. **Validate the assembled hierarchy** with the existing
   `validate_hierarchy()` function from `hierarchy.py`. If validation
   fails (duplicate IDs, missing parents, invalid ranges), log the errors
   and fall back to the deterministic regex builder. This is the only
   path where the legacy regex still runs.

**Page-marker insertion** is the load-bearing detail. We need a single
helper that owns building the joined text AND the `page_offsets` map so
the chunker and the locator agree on offsets:

```python
def join_pages_with_markers(
    pages: list[PdfPageText],
) -> tuple[str, dict[int, int]]:
    parts: list[str] = []
    page_offsets: dict[int, int] = {}
    cursor = 0
    for page in pages:
        marker = f"=== PAGE {page.page_number} ===\n"
        page_offsets[page.page_number] = cursor
        parts.append(marker)
        cursor += len(marker)
        parts.append(page.text)
        cursor += len(page.text)
        parts.append("\n\n")
        cursor += 2
    return "".join(parts), page_offsets
```

When chunks are built from this joined text, their
`chunk_char_start` / `chunk_char_end` are already in the same coordinate
space as the hierarchy nodes — citation resolution then works without
extra mapping.

### Updated process orchestration

`PdfProcessor.process()` order changes to:

1. Scan PDF (unchanged).
2. **Per-page Scout triage** (Step 1-3, 10) — parallel, produces text,
   headings, TOC, table/figure descriptions per page. Store full model
   output as `kb_raw/{document_id}/triage/page_observations.json` and
   `kb_raw/{document_id}/triage/model_extracted_text.json`.
3. Extract embedded text (pypdf, unchanged).
4. Build text quality report (unchanged).
5. **Build merged joined text + page_offsets map** (Step 4 + new helper).
6. Run OCR/Textract if recommended (unchanged).
7. **Full-doc Sonnet hierarchy call.** Send the merged joined text to
   Sonnet 4.6 with the validated full-doc hierarchy prompt
   (`docs/working_files/llm_extraction_test/full_document_hierarchy/system_prompt.md`).
8. **Hierarchy locator** (new step). Convert Sonnet nodes →
   `HierarchyNode` objects with char ranges.
9. Validate hierarchy. On failure, fall back to the deterministic regex
   builder (kept as legacy fallback only).
10. Write `document_hierarchy.json` (unchanged shape).
11. Build hierarchy chunks via existing `hierarchy_chunking.py` (unchanged).
12. Persist all artifacts.

### Updated config

```yaml
ingestion:
  # ---- Per-page triage (Llama 4 Scout) ----
  pdf_triage_model_id: "us.meta.llama4-scout-17b-instruct-v1:0"
  pdf_triage_max_tokens_per_page: 8192
  pdf_full_page_triage_enabled: true

  # ---- Full-doc hierarchy (Sonnet 4.6) ----
  pdf_hierarchy_model_id: "us.anthropic.claude-sonnet-4-6"
  pdf_hierarchy_max_output_tokens: 32000          # ~247 nodes × ~80 tokens
  pdf_hierarchy_read_timeout_seconds: 600         # Sonnet on 125K input takes ~150s

  # ---- Chunking strategy for documents larger than Sonnet's context ----
  pdf_hierarchy_single_call_max_pages: 100        # Take single-call path if ≤ this
  pdf_hierarchy_chunk_pages: 80                   # Pages per chunk when chunking
  pdf_hierarchy_chunk_overlap_pages: 1            # Page overlap between adjacent chunks
  pdf_hierarchy_orphan_handling: "promote"        # promote | drop — for unresolved parent_label

  # ---- Hierarchy locator ----
  pdf_hierarchy_locator_page_window: 1            # Search heading on page_start ± N pages
  pdf_hierarchy_locator_drop_unlocatable: true    # Drop Sonnet nodes we can't find

  # ---- Deprecated (removed from pipeline) ----
  # hierarchy_review_model_mode: removed — no separate review pass
  # hierarchy_review_model_id: removed
  # pdf_section_chunking_enabled: kept for fallback only
```

### Updated cost projection

| Step | Per 100-page doc |
| --- | ---: |
| Per-page Scout (parallel, 100 pages) | $0.25 |
| Full-doc Sonnet hierarchy | $0.67 |
| Hierarchy locator (pure Python) | $0.00 |
| **Total** | **$0.92** |

### Handling documents larger than Sonnet's context window

The full 2021 IRC at ~1.26M tokens does NOT fit in Sonnet's 1M context.
For documents like this, we run Sonnet in **rolling chunks of 80 pages
with 1 page of overlap**, threading the running hierarchy through each
call.

**Defaults (configurable):**

```yaml
ingestion:
  pdf_hierarchy_chunk_pages: 80           # Pages per Sonnet call when chunking
  pdf_hierarchy_chunk_overlap_pages: 1    # Overlap between adjacent chunks
  pdf_hierarchy_single_call_max_pages: 100  # If page_count ≤ this, single call
```

80 pages was chosen as a safe default: a typical body page is ~1,500
Sonnet tokens, so 80 pages ≈ 120K input tokens, leaving comfortable
headroom under 1M for the prompt + running hierarchy + output. The
constant is configurable per environment for tuning.

**Single-call vs chunked path.** After joining pages, check the page
count against `pdf_hierarchy_single_call_max_pages`. If `page_count ≤
max_pages`, take the existing single-call path (one Sonnet call, all
pages). Otherwise enter the chunked path.

**Chunked path flow:**

1. **Split joined text by page markers** into ordered chunks of
   `chunk_pages` pages with `overlap_pages` of overlap. Critical
   constraint: the `=== PAGE N ===` markers in each chunk preserve the
   absolute page number (e.g., chunk 2 starts with `=== PAGE 80 ===`,
   not `=== PAGE 1 ===`) so the locator's `page_offsets` map and
   Sonnet's emitted `page_start` values stay in the same coordinate
   space.

2. **First chunk (pages 1 to `chunk_pages`)** uses the standalone
   hierarchy prompt unchanged. Returns nodes for the first chunk.

3. **Subsequent chunks (pages `N` to `N + chunk_pages - 1`, with
   1-page overlap)** use an augmented prompt that:
   - Includes the running hierarchy from all prior chunks as a
     flattened list with `label`, `title`, `node_type`, `parent_label`,
     `depth`, `page_start` (omit `body_anchor` to save tokens — they're
     already located).
   - Tells Sonnet: this hierarchy was generated from pages 1 to
     `(N - 1)`. The text below contains pages `(N - 1)` (overlap) through
     `(N + chunk_pages - 2)`. Add new nodes that appear in the new
     pages. Do NOT re-emit nodes already in the running hierarchy. If a
     section from the running hierarchy continues into this chunk
     (parent of nodes you're emitting), reference it by label in
     `parent_label` without re-emitting it.
   - Each new node still emits a `body_anchor` scoped to text within
     the current chunk.

4. **Stitch outputs:**
   - Concatenate all chunks' `nodes` lists in order.
   - Deduplicate by `label`, keeping the first occurrence. The overlap
     page may produce duplicates if Sonnet re-emits a node it already
     emitted in the previous chunk despite the instruction.
   - Validate every `parent_label` resolves to a node in the combined
     set. Unresolved parents are logged and the orphan node is promoted
     to root depth (or dropped, per config — see open questions).
   - Run the locator (`locate_heading()` per node) on the FULL joined
     text. Page-scoped search means a node from chunk 5 with
     `page_start=380` is searched only in pages 379-381 of the joined
     text — no cross-chunk interference.

**Augmented prompt addition (subsequent chunks):**

```
## Existing hierarchy from previous chunks

The following hierarchy was already built from pages 1 to {previous_end}.
Do NOT re-emit any of these nodes. Use their labels as `parent_label`
references for new nodes where appropriate.

[Flat list of running hierarchy: label, title, node_type, depth, parent_label, page_start]

## Your task for this chunk

The text below contains pages {chunk_start} (overlap with previous chunk)
through {chunk_end}. Identify and emit NEW headings that appear in pages
{chunk_start + 1} through {chunk_end}.

If a section from the running hierarchy continues into this chunk (i.e.,
you see new subsections whose `parent_label` is already in the running
hierarchy), set their `parent_label` to the existing label. Do not
re-emit the parent.

If pages {chunk_start + 1} through {chunk_end} contain NO new headings
(e.g., a section spans the chunk with no subsections appearing yet),
return an empty `nodes` array.
```

**Why the 1-page overlap?**

- A heading near a chunk boundary might be visible in only one chunk
  depending on where the cut falls. The overlap ensures any heading that
  starts on the boundary page is seen by both chunks.
- Sonnet has the running hierarchy and is instructed not to re-emit, so
  the overlap doesn't normally cause duplicates. Dedup-by-label is the
  defense if it does.
- Cost is minimal: 1 page in 80 = 1.25% overhead per chunk call.

**Cost projection for a full 1109-page IRC document:**

- Chunks: ceil(1109 / (80 - 1)) ≈ 15 calls
- Per-call input: ~100K tokens of text + ~5-15K tokens of running
  hierarchy (grows as the doc is processed) ≈ 110K avg
- Per-call output: ~5-20K tokens
- Per-call cost: ~$0.33-0.55
- **Total: ~$5-8 per document.** Plus per-page Scout at $0.25 × 11 = ~$2.75
  for the per-page pass. Combined: **~$8-11 per full 1109-page document.**

This is still dramatically cheaper than the current broken pipeline
($5-10 per failed 100-page run × many retries) and is the right shape
for documents of any size.

**Open question:** what to do with orphan nodes whose `parent_label`
doesn't resolve. Two options:

- **Promote to root:** Treat as a top-level node. May produce some
  artificial parts/chapters but loses no information.
- **Drop:** Discard the node entirely. Safer for citation quality but
  loses any actual content.

Recommendation: promote to root by default with a logged warning, so we
can review the orphan rate per ingestion. Make this configurable
(`pdf_hierarchy_orphan_handling: promote | drop`).

### What's eliminated

- `hierarchy_review.py` and the Sonnet-review pass (replaced by direct
  Sonnet hierarchy call).
- `_SECTION_CODE_BUILDING_RE` and per-profile regex variants (Sonnet
  handles structure detection globally).
- The "merge model headings into regex boundaries" step (no longer
  needed — Sonnet IS the heading source).
- `parser_profiles.py` regex selection — profiles become metadata-only
  tags ("this is a building_code") for downstream display; they no longer
  drive any code path.

### Risks specific to this revision

| Risk | Mitigation |
| --- | --- |
| Sonnet 4.6 fails or returns malformed JSON | Fall back to deterministic regex builder; alarm on the failure rate |
| Sonnet output exceeds `pdf_hierarchy_max_output_tokens` | Detect `stop_reason: max_tokens`; raise the cap or retry with smaller input |
| Document exceeds Sonnet's 1M context | Chunked Sonnet path activates above `pdf_hierarchy_single_call_max_pages` (default 100). Rolling 80-page chunks with 1-page overlap, threading running hierarchy through each call. See chunking section. |
| Chunked-path overlap produces duplicate nodes | Dedup-by-label after stitching keeps the first occurrence. Sonnet's instruction to not re-emit known nodes plus the overlap-only-on-boundary design keeps duplicate rate low |
| Subsequent chunk emits an orphan node (`parent_label` not in running hierarchy) | Configurable handling: `promote` to root (default) or `drop`. Both log the orphan rate per ingestion for monitoring |
| Running hierarchy in subsequent prompts grows too large for long documents | A 1100-page IRC produces ~500-1000 nodes; flat representation in the prompt is ~12-25K tokens, well under context budget. For pathological cases, future enhancement: include only the last N chapters' hierarchy in the prompt |
| Sonnet emits a heading not in body (hallucination) | Locator returns None → node is dropped; counts logged for monitoring |
| Heading is split across pages and locator misses it | ± 1 page search window; tune to ± 2 if monitoring shows misses |
| Locator finds the wrong occurrence (cross-reference, TOC, page banner) | Anchor-first algorithm: the body_anchor is unique enough to pin the right neighborhood; cross-references and banners don't have the anchor text following them so they're never even examined |
| Sonnet emits a non-unique body_anchor | Page scoping handles most cases; for truly ambiguous anchors the walk-back still resolves correctly within each candidate's local neighborhood. Prompt instruction emphasizes "specific enough to appear only once" |
| body_anchor not found in joined text | Returns `"anchor_not_found"` tier → node dropped + logged. Indicates either a Sonnet hallucination or text drift between Sonnet's input and the chunk-joined text. Per-tier counts surface this in monitoring |
| body_anchor present but no label line-start match nearby | Returns `"anchor_orphan"` tier → node dropped + logged. Usually means Sonnet's anchor was taken from a section earlier than where the label appears |
| Sonnet's `page_start` is off by 1-2 pages (heading straddles page break) | Anchor search uses ±1 page window, then falls back to full-doc search, returning `"anchored_full_doc"` tier (still high confidence — the anchor itself is unique) |

### Verification additions

Add to the existing verification plan:

6. **Locator unit tests:** Stub Sonnet output and verify locator produces
   correct char offsets for the IRC sample. Confirm `R303.10` ends up at
   the expected offset within page 99's text segment.
7. **End-to-end test:** Re-ingest the IRC 100-page sample. Confirm:
   - Sonnet hierarchy call returns 247+ nodes.
   - Locator successfully positions ≥95% of those nodes (some legitimate
     drops expected for nodes Sonnet may emit that don't have body text
     in the 100-page slice).
   - Chunks built from the hierarchy contain R303.10.
   - Heating query resolves to `cited_hierarchy_node = "R303.10"`.
