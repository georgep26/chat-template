# Implementation Plan: RAG Document Ingestion Pipeline

Created: 2026-05-17
Status: Implementation plan
Supersedes (in part):
- `2026-05-16_hierarchical_chunking_strategy_options.md`
- `2026-05-16_hierarchical_chunking_detailed_implementation_plan.md`
- `hierachical_chunking_refinement.md`
- `2026-05-17_llama4_scout_multimodal_triage_plan.md` (this plan consolidates that one's revisions into a single coherent document)

Reference test outputs that informed this plan:
- `docs/working_files/llm_extraction_test/page_99_cross_model_comparison.md`
- `docs/working_files/llm_extraction_test/llama_4_scout/combined_extraction_v2/`
- `docs/working_files/llm_extraction_test/full_document_hierarchy/comparison_report.md`

## 1. Goals

The ingestion pipeline must produce two things from any uploaded reference
document (PDF first; structure generalizes to other formats later):

1. **Retrieval chunks with accurate, precise citations.** Each chunk carries
   enough metadata that when the RAG answer model cites a verified quote,
   the backend can resolve that quote to the deepest hierarchy node that
   contains it (e.g., `R303.10 Required heating`), not just the retrieval
   chunk that happened to be embedded.

2. **Clean metadata for direct fetch and agent navigation.** External
   callers (the Knowledge API, MCP tools, the Library API, the frontend)
   need to fetch documents, sections, tables, and figures by id without
   re-running expensive models. The hierarchy tree, chunk catalog, and
   asset artifacts must be authoritative and durable.

The pipeline must also degrade gracefully when individual steps fail and
must work at any document size from a 1-page memo to the full 1109-page
2021 IRC.

## 2. How this plan addresses the core concerns

### 2.1 Accurate citations

The citation pipeline (in `src/rag_lambda/citations.py`) verifies that the
answer model's `<cite source="N">QUOTE</cite>` tags contain text that
actually appears in the retrieved chunk. Once a quote is verified, the
backend records its char offset within the chunk, then maps that offset
back to the deepest hierarchy node that contains it.

For this to work end-to-end, every chunk needs:

- `chunk_char_start`, `chunk_char_end` — where this chunk sits in the
  document's joined text.
- A `hierarchy_index_inline` (or pointer) — the hierarchy nodes that
  overlap this chunk, each with `char_start`, `char_end`, `heading_path`,
  page range.

For citation resolution to work, **the chunker and the hierarchy locator
must use the same joined text and the same char-offset coordinate
system.** This plan enforces that with a single `join_pages_with_markers()`
helper that owns building the joined text AND the `page_offsets` map, so
chunk offsets and hierarchy node offsets line up automatically.

### 2.2 Clean metadata for fetching

Every artifact this pipeline writes is consumable by other agents:

- `document_hierarchy.json` — full tree with labels, titles, parent ids,
  page ranges, char ranges. Knowledge API `GET /documents/{id}/hierarchy`
  and `GET /documents/{id}/hierarchy-nodes/{node_id}` read it directly.
- `document_chunks.metadata.json` (Bedrock sidecars) — scalar fields only
  (no large nested indexes) so Bedrock filters and the frontend stay
  responsive.
- `hierarchy_indexes/{chunk_id}.hierarchy_index.json` — per-chunk
  hierarchy index for citation resolution and rich source display.
- `model_extracted_text.json` — Llama-OCR'd text per page, kept as a
  reference artifact for future re-processing without re-running the
  model.
- `tables/` and `figures/` artifacts — Markdown summary + raw structured
  data + linked `parent_hierarchy_node_id`.

The Library and Knowledge APIs already have authorization wired through
the catalog tables. They just need this plan's artifacts to be accurate
and consistent.

### 2.3 No per-profile regex treadmill

Sonnet 4.6 acts as the hierarchy reasoner over text. The IRC's
`101167926` order-number false positives, R303 vs R303.1 vs R3030
ambiguity, and TOC vs body disambiguation — all the issues that the
deterministic regex builder struggled with — Sonnet handles using global
context. We do not add per-document-type regex variants. The legacy
deterministic builder is retained only as a defensive fallback for
documents where Sonnet fails (e.g., model unavailable, malformed JSON).

## 3. Pipeline overview

```
S3 upload (kb_landing/...)
  ↓
PdfProcessor.process()
  │
  ├─ 1. PDF scan            (pypdf metadata, page count, basic text probe)
  ├─ 2. Per-page triage     (Llama 4 Scout, parallel) ────────────┐
  ├─ 3. Embedded text       (pypdf, all pages, source of truth)   │
  ├─ 4. Text quality report (compare model text vs embedded)      │
  ├─ 5. Build merged text   (per-page swap + page markers)        │
  ├─ 6. OCR fallback        (Textract, only if recommended)       │
  ├─ 7. Full-doc hierarchy  (Sonnet 4.6, single or chunked) ──────┤
  ├─ 8. Hierarchy locator   (anchor-based, pure Python)           │
  ├─ 9. Adaptive chunking   (existing hierarchy_chunking.py)      │
  ├─ 10. Asset linking      (Textract tables + Scout descriptions)│
  └─ 11. Persist artifacts  (S3 + Aurora catalog) ────────────────┘
```

**Cost projection:**

| Document size | Scout triage | Sonnet hierarchy | Textract (targeted) | **Total** |
| --- | ---: | ---: | ---: | ---: |
| 100-page IRC sample | $0.25 | $0.67 (single call) | ~$0.23 | **~$1.15** |
| 1109-page full IRC | ~$2.75 | ~$5-8 (15 chunked calls) | ~$2.25 | **~$10-13** |

Textract runs only on pages Scout flags as containing tables (~15% of
pages for the IRC sample), giving accurate cell-level grids without
paying for the whole document.

## 4. Step-by-step

### Step 1 — PDF scan
**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py`
(`scan_pdf()`, existing)

Read PDF metadata with pypdf: page count, embedded title, basic text
availability check on the first ~5 pages. No changes from current code.

### Step 2 — Per-page triage with Llama 4 Scout
**File:** `src/document_ingestion_lambda/triage.py`

- **Model:** `us.meta.llama4-scout-17b-instruct-v1:0` (Bedrock cross-region
  inference profile).
- **Execution:** `ThreadPoolExecutor(max_workers=6-8)` parallel over all
  pages. Reduces 100-page processing from ~17min serial to ~2-3min.
- **Prompt:** validated `combined_extraction_v2` prompt at
  `docs/working_files/llm_extraction_test/llama_4_scout/combined_extraction_v2/system_prompt.md`.
  Schema includes `raw_visible_text`, `heading_candidates`,
  `appears_to_be_toc`, `toc_entries`, `tables` with rich descriptions,
  `figures` with rich descriptions, `likely_document_type`, `notes`.
- **No content-filter issues** on regulated text (validated in
  `nova_lite/prompt_refinement/` tests — Scout doesn't have Nova's RAI
  filter).
- **Cost:** ~$0.0025/page → ~$0.25 / 100 pages.

#### Artifacts written

```
kb_raw/{document_id}/triage/page_observations.json      # full Scout JSON per page
kb_raw/{document_id}/triage/model_extracted_text.json   # consolidated raw text
```

#### Raw model text retention (called out explicitly)

The full text Llama 4 Scout produces per page is preserved in S3 at
`kb_raw/{document_id}/triage/model_extracted_text.json`. Format:

```json
{
  "extraction_model_id": "us.meta.llama4-scout-17b-instruct-v1:0",
  "extraction_run_at": "2026-05-17T15:30:00Z",
  "pages": [
    {
      "page_number": 1,
      "text": "...",
      "char_count": 5832
    }
  ],
  "total_pages_with_text": 95,
  "total_pages_skipped_parse_error": 2,
  "total_chars": 437012
}
```

This artifact is **never used directly in the chunker** (embedded PDF text
is the source of truth — see Step 5). It exists so we can:

- Re-process the document with a different chunking strategy without
  re-running Scout.
- Debug text-quality issues without paying for the model again.
- Provide it to future pipeline experiments (e.g., training-data
  generation, alternative hierarchy approaches).
- Restore it as the primary text source if we later find the embedded
  text is systematically unreliable for some doc type.

Stored alongside the original document under
`kb_raw/{document_id}/`, with the same access tier and tenant
authorization as the catalog row.

### Step 3 — Embedded PDF text extraction
**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py`
(`extract_pdf_text_pages()`, existing)

pypdf extracts text per page. **This is the source of truth for chunk
text.** Scout's `raw_visible_text` is a comparison/gap-detection layer.

### Step 4 — Text quality report
**File:** `src/document_ingestion_lambda/file_type_processors/text_quality.py`

Existing logic compares Scout's `raw_visible_text` against the embedded
PDF text per page. Output:

- Per-page `mismatch_ratio` (1.0 - token_overlap_ratio).
- Per-page `suspicious` flag (mismatch above `pdf_triage_text_mismatch_threshold`, default 0.50).
- Document-level `ocr_recommended` flag (suspicious page ratio above
  `pdf_triage_document_text_mismatch_threshold`, default 0.30).
- `pages_over_text_mismatch_threshold` list.

Now that Scout reliably returns text on every page (no content filter),
this report becomes meaningful — under Nova Lite only ~27 of 100 pages
produced observations, so the report was based on too little data.

### Step 5 — Build merged joined text
**File:** `src/document_ingestion_lambda/file_type_processors/pdf.py` (new helper)

A single helper produces both `joined_text` and the `page_offsets` map.

```python
def join_pages_with_markers(
    pages: list[PdfPageText],
) -> tuple[str, dict[int, int]]:
    """Build joined text with absolute page markers and page→offset map."""
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

**Per-page merge rule** (before joining):

1. Default per page: use embedded text.
2. If page is in `text_quality_report.pages_over_text_mismatch_threshold`
   AND Scout's `raw_visible_text` for that page is longer than the
   embedded text → use Scout's text for that page.
3. If embedded text is missing for a page that Scout captured → use
   Scout's text.
4. After merge, every page has a text source; join with markers.

The `joined_text` and `page_offsets` from this step are used by:
- Step 7 (Sonnet sees this joined text)
- Step 8 (locator searches this joined text)
- Step 9 (chunker operates on this joined text)

All three live in the same coordinate space.

### Step 6 — OCR fallback (Textract)
**Files:**
- `src/document_ingestion_lambda/file_type_processors/ocr.py`
- `src/document_ingestion_lambda/file_type_processors/pdf.py` (`_extract_text()`)

Existing path, unchanged. If `text_quality_report.ocr_recommended` is
true, run Textract `StartDocumentTextDetection` on the raw PDF. OCR'd
text replaces the embedded text per page before Step 5's join.

Selected-page OCR (a future enhancement) would run Textract only on
flagged pages. Current implementation OCRs the whole document; tune
later if cost warrants.

### Step 7 — Full-document hierarchy with Sonnet 4.6

**File:** `src/document_ingestion_lambda/file_type_processors/hierarchy_sonnet.py` (new)

Validated approach: Sonnet 4.6 reads the joined text and emits a JSON
hierarchy with labels, titles, parent labels, page ranges, depth, and a
`body_anchor` per node (40-80 char snippet from after the heading). On
the 100-page IRC sample it produces 247 nodes across 7 depth levels with
no duplicates and no missing parents.

#### Single-call path (default for ≤ `pdf_hierarchy_single_call_max_pages`, default 100)

One Bedrock Converse call. Input = `joined_text` + system prompt
(`docs/working_files/llm_extraction_test/full_document_hierarchy/system_prompt.md`
with added `body_anchor` field).

```python
{
  "label": "R303.10",
  "title": "Required heating",
  "node_type": "subsection",
  "parent_label": "R303",
  "page_start": 99,
  "page_end": 99,
  "depth": 3,
  "body_anchor": "Where the winter design temperature in Table R301.2 is below 60°F"
}
```

**Prompt instruction for `body_anchor`:**

```
"body_anchor": "A verbatim 40-80 character snippet from the document text
that appears anywhere after this heading. The snippet must be specific
enough to appear only once in the document — typically the first sentence
or two of body content includes specific terms (cross-references, numeric
thresholds, distinctive nouns) that make it unique. Copy the text exactly,
including punctuation and Unicode characters."
```

No special-case rules needed for headings-immediately-followed-by-headings;
the locator's walk-back algorithm handles that case automatically.

#### Chunked path (auto-triggered above the single-call threshold)

For documents that don't fit in Sonnet's 1M context window (the full
IRC at ~1.26M tokens is the canonical case):

- **Chunk size:** `pdf_hierarchy_chunk_pages` (default 80).
- **Overlap:** `pdf_hierarchy_chunk_overlap_pages` (default 1).
- **Page numbering:** absolute. Chunk 2 starts with `=== PAGE 80 ===`,
  not `=== PAGE 1 ===`. Sonnet's emitted `page_start` values stay in
  the same coordinate space as the locator's `page_offsets`.

**First chunk:** standalone prompt, full nodes with body_anchors.

**Subsequent chunks:** augmented prompt:

```
## Existing hierarchy from previous chunks

The following hierarchy was already built from pages 1 to {previous_end}.
Do NOT re-emit any of these nodes. Use their labels as `parent_label`
references for new nodes where appropriate.

[Flat list: label, title, node_type, depth, parent_label, page_start
 — omit body_anchor to save tokens since prior nodes are already located]

## Your task for this chunk

The text below contains pages {chunk_start} (overlap with previous chunk)
through {chunk_end}. Identify and emit NEW headings that appear in pages
{chunk_start + 1} through {chunk_end}.

If a section from the running hierarchy continues into this chunk (i.e.,
you see new subsections whose `parent_label` is already in the running
hierarchy), set their `parent_label` to the existing label. Do not
re-emit the parent.

If pages {chunk_start + 1} through {chunk_end} contain NO new headings,
return an empty `nodes` array.
```

**Stitching:**

1. Concatenate all chunks' `nodes` in order.
2. Dedup by `label`, keeping first occurrence (overlap may produce dupes).
3. Validate `parent_label` references resolve in the combined set.
4. **Orphan handling** (`pdf_hierarchy_orphan_handling`):
   - `promote` (default): unresolved parents set to None, node moves to
     root depth, warning logged.
   - `drop`: discard the node entirely.

#### Cost summary

| Path | 100-page doc | 1109-page doc |
| --- | ---: | ---: |
| Single call | $0.67 | n/a (won't fit) |
| Chunked (15 calls) | n/a (overkill) | $5-8 |

### Step 8 — Hierarchy locator

**File:** `src/document_ingestion_lambda/file_type_processors/hierarchy_locator.py` (new)

Convert Sonnet's nodes (label + title + page + body_anchor) into
`HierarchyNode` objects with `(char_start, char_end)` populated.

#### The disambiguation problem

A label like `R303.10` can appear in PDF-extracted text as:

1. **Real heading** — at line start, immediately followed by title.
2. **Cross-reference in body prose** — `...follow R303.10 for heating...`
3. **Running page header** — `R303 LIGHT, VENTILATION AND HEATING`
   banner at the top of each page.

Naive `str.find(label)` returns the wrong occurrence. The body_anchor
approach eliminates these failure modes because cross-references and
banners don't have the anchor text following them.

#### Algorithm

```python
import re

_LEADING_WS = r"^[ \t]*"

def locate_heading(
    label: str,
    body_anchor: str,
    page_start: int,
    joined_text: str,
    page_offsets: dict[int, int],
) -> tuple[int | None, str]:
    """Return (char_offset, confidence_tier)."""
    # 1. Find anchor scoped to page_start ± 1
    lo = page_offsets.get(max(1, page_start - 1), 0)
    hi = page_offsets.get(page_start + 2, len(joined_text))
    haystack = joined_text[lo:hi]
    anchor_pos = haystack.find(body_anchor)

    if anchor_pos != -1:
        anchor_abs = lo + anchor_pos
        tier = "anchored"
    else:
        # Fall back to full-doc search
        anchor_abs = joined_text.find(body_anchor)
        if anchor_abs == -1:
            return None, "anchor_not_found"
        tier = "anchored_full_doc"

    # 2. Walk back to nearest line-start matching label + whitespace
    pattern = re.compile(
        rf"{_LEADING_WS}{re.escape(label)}[ \t]",
        re.MULTILINE,
    )
    walk_back_start = max(0, anchor_abs - 500)
    snippet = joined_text[walk_back_start:anchor_abs]
    matches = list(pattern.finditer(snippet))
    if not matches:
        return None, "anchor_orphan"

    return walk_back_start + matches[-1].start(), tier
```

**Why this is robust:**

- The label regex `^[ \t]*{label}[ \t]` requires whitespace after the
  label. `R303 ` matches the heading but not `R303.1`, `R3030`, `R303A`.
- The line-start anchor (`^` with `MULTILINE`) excludes mid-sentence
  cross-references.
- The body_anchor anchors us in the right neighborhood; even if the
  anchor text belongs to a child section, the walk-back finds the
  correct parent heading.

**Confidence tiers** for monitoring:
- `anchored` — high confidence (anchor + label both matched on the
  expected page).
- `anchored_full_doc` — anchor found outside expected page (Sonnet's
  page_start drifted). Log for monitoring.
- `anchor_not_found` — drop node, log warning.
- `anchor_orphan` — anchor found but no label match nearby; drop node.

#### Char-end assignment

After all nodes have `char_start`:

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

Then propagate parent `char_end` to span the last descendant — reuses
existing logic at `hierarchy.py:270-281`.

#### Validation

Run `validate_hierarchy()` (existing). On failure, fall back to the
legacy deterministic regex builder. This is the only path where the
old regex still runs.

Write `kb_raw/{document_id}/document_hierarchy.json`.

### Step 9 — Adaptive hierarchy chunking

**File:** `src/document_ingestion_lambda/file_type_processors/hierarchy_chunking.py`
(existing, unchanged)

#### What "adaptive" means

The chunker walks the hierarchy tree and selects which nodes become
retrieval chunks based on size. Rules in `_select_chunk_nodes()`
(`hierarchy_chunking.py:196-225`):

1. For each node, compute `token_estimate` (already populated during
   hierarchy build).
2. If `token_estimate ≤ pdf_hierarchy_hard_max_tokens` (default 7000) OR
   the node has no children → emit the node as a chunk.
3. Otherwise → recurse into children. The parent is "too big to chunk
   directly"; split it by going one level deeper.

This means:
- Small leaf nodes (a few hundred tokens) become their own chunks.
- Medium parents (e.g., R303 at ~3000 tokens) become a single chunk
  with R303.1...R303.10 visible in the chunk's hierarchy index.
- Large parents (e.g., R301 at ~17k tokens) are skipped; their children
  R301.1, R301.2, etc. become the chunks instead.

The same hierarchy tree adapts to documents with very different size
distributions — no per-document tuning required.

#### Configurable knobs

```yaml
pdf_hierarchy_target_min_tokens: 300       # below: candidate for merging
pdf_hierarchy_target_max_tokens: 3000      # sweet spot for embeddings
pdf_hierarchy_hard_max_tokens: 7000        # over this: must split by children
pdf_hierarchy_merge_small_leaf_nodes: true # combine small siblings (future)
pdf_hierarchy_split_large_nodes_by_children: true
pdf_hierarchy_store_index: true            # write hierarchy_index sidecar
pdf_hierarchy_inline_index_max_nodes: 40   # inline if small, else S3 only
```

#### Token-based final splitting

After node selection, each node's text may still exceed Bedrock's
embedding token limit (`pdf_embedding_chunk_token_limit_titan_v2`,
8192). For these, `split_text_by_estimated_tokens()` breaks the node
into parts with deterministic `chunk_id` suffixes
(`{document_id}_section_r303_part_001`,
`{document_id}_section_r303_part_002`).

Part-level chunks share the same `hierarchy_node_id` and
`hierarchy_index` but get different `chunk_id` and `part_index`.

#### Per-chunk artifacts

For each emitted chunk:

```
kb_index/{document_id}/sections/{chunk_id}.md             # markdown body
kb_index/{document_id}/sections/{chunk_id}.md.metadata.json  # Bedrock sidecar
kb_raw/{document_id}/hierarchy_indexes/{chunk_id}.hierarchy_index.json
```

Chunk catalog metadata (Aurora):
- `chunk_id`, `chunk_type: "section"`, `document_id`, `chunk_index`,
  `hierarchy_node_id`, `hierarchy_profile`, `hierarchy_path`,
  `hierarchy_index_location` (S3), `hierarchy_index_inline` (if small)
- `chapter`, `section`, `heading_path` (slash-joined)
- `page_start`, `page_end` (computed from `char_to_page` for the part)
- `chunk_char_start`, `chunk_char_end` (in joined text)
- `token_count`, `char_count`
- `custom_metadata.part_index`, `custom_metadata.part_count`

Bedrock sidecar (subset of above, scalar fields only) for KB retrieval
metadata. Large nested indexes (`hierarchy_index_inline` arrays beyond
40 nodes) live in the Aurora row and S3 sidecar, not in the Bedrock
sidecar.

#### Hierarchy index per chunk

`hierarchy_index_for_range()` (existing, `hierarchy.py:168-195`) selects
all hierarchy nodes whose `[char_start, char_end]` overlaps the chunk's
`[chunk_char_start, chunk_char_end]`. Each entry has node id, label,
title, node_type, heading_path, depth, char range, page range — all in
the chunk's local coordinate space (char_start subtracted).

When the chunk is retrieved during RAG, the answer LLM sees:

```xml
<source number="1">
  <metadata>
    document_title: 2021 International Residential Code
    title: R303 LIGHT, VENTILATION AND HEATING
    heading_path: CHAPTER 3/R303
    page_start: 98
    page_end: 99
  </metadata>
  <hierarchy_index>
    - R303 LIGHT, VENTILATION AND HEATING, pages 98-99
    - R303.10 Required heating, page 99, chars 7889-8397
  </hierarchy_index>
  <chunk>
    R303 LIGHT, VENTILATION AND HEATING ...
    R303.10 Required heating. Where the winter design temperature ...
  </chunk>
</source>
```

When the answer cites a quote from R303.10, citation normalization
verifies the quote, computes its offset in the chunk, walks the
hierarchy index, finds R303.10 as the deepest containing node, and
returns `cited_hierarchy_node = "R303.10"` in the response.

### Step 10 — Asset linking for tables and figures

**Files:**
- `src/document_ingestion_lambda/file_type_processors/table_extraction.py`
- `src/document_ingestion_lambda/file_type_processors/figure_extraction.py`
- `src/document_ingestion_lambda/file_type_processors/pdf.py` (`_extract_assets()`)

Tables and figures are **first-class chunks**, separate from the section
text chunks. They're individually citable, individually retrievable, and
each carries a link back to the hierarchy node that contains them.

#### Why we keep Textract for tables (instead of letting Scout produce the grid)

We tested Scout's direct table markdown extraction on page 93 of the IRC
sample (TABLE R302.1(1) and R302.1(2), both 4-column fire-resistance
tables). Results in
`docs/working_files/llm_extraction_test/llama_4_scout/table_extraction_page_93/`
and the Textract comparison in
`docs/working_files/llm_extraction_test/textract/table_extraction_page_93/`.

For complex multi-column code tables, **Scout's grid was structurally
wrong:**

- Collapsed 4-column tables to 3 columns (merged the condition + rating
  columns).
- Dropped cell values (`0 hours`, `NA`) that fell into the dropped column.
- Renumbered and paraphrased footnote markers (`ᵃ`, `ᵇ` became `1`, `2`
  with rewritten text). For building-code documents where exact wording
  is the regulatory rule, this is unacceptable.
- Broke multi-row cell structures (e.g., `Openings in walls` rows had
  empty cells with stray values).

Textract on the same page produced **structurally correct, cell-accurate
output:** 4 columns preserved, all values intact including `0 hours` and
`NA`, footnote markers preserved as Unicode superscripts (`woodᵃ`,
`3 feetᵃ`, `woodᵇ,c`).

We therefore keep Textract for the grid and use Scout for the
vector-search description. Targeted per-page invocation keeps cost low
(see "Cost projection" below).

#### Table flow

1. **Identify table pages from Scout's per-page output.** During Step 2,
   Scout's `tables[]` array on each page observation flags which pages
   contain tables. Build the candidate set:

   ```python
   table_pages = sorted({
       obs.page_number
       for obs in triage.page_observations
       if obs.tables  # non-empty list = Scout saw at least one table
   })
   ```

   On the IRC 100-page sample this yields ~10-30 pages.

2. **Run Textract per table page.** For each page in `table_pages`,
   crop a single-page PDF (or render to PNG) and call Textract's
   synchronous `AnalyzeDocument` with `FeatureTypes=["TABLES"]`.

   - **Why per-page sync, not whole-doc async:** the targeted approach
     means we pay $0.015 per flagged page rather than $0.015 per page in
     the whole document. For a 1109-page document with ~150 table
     pages, that's $2.25 vs $16.65 — a 7× saving.
   - Synchronous `AnalyzeDocument` returns within a few seconds per
     page. Parallelize across pages with the same `ThreadPoolExecutor`
     pattern used for Scout triage.
   - If a page exceeds Textract's sync size limit (5 MB / 1 page),
     fall back to async `StartDocumentAnalysis` for that page.

3. **Reconstruct each table** as a (row, column) grid using existing
   `_table_block_to_grid()` and emit canonical pipe-style markdown via
   existing `_grid_to_markdown()` (both in `table_extraction.py`).

   ```markdown
   | Header 1 | Header 2 | Header 3 | Header 4 |
   | --- | --- | --- | --- |
   | cell | cell | cell | cell |
   ```

4. **Description for embedding** — use the description Llama 4 Scout
   produced in Step 2. Each Scout per-page observation includes:

   ```json
   "tables": [
     {
       "caption": "TABLE R302.1(1) EXTERIOR WALLS",
       "description": "This table provides minimum fire-resistance
                       ratings and fire separation distances for
                       exterior walls, projections, openings in walls,
                       and penetrations... Example questions this
                       table would answer include: What is the minimum
                       fire-resistance rating required for a
                       fire-resistance-rated wall with a 0-foot fire
                       separation distance? ..."
     }
   ]
   ```

   The description is vector-search-optimized (3-6 sentences with example
   questions). It becomes the table chunk's embedded body alongside the
   Textract markdown grid.

5. **Match Scout description to Textract table.** Both produce one or
   more tables for the same page. Match by:
   - **Order on page** — Textract returns tables top-to-bottom by
     bounding-box Y coordinate. Scout's `tables[]` array is in the
     same visual order. First-to-first match is reliable.
   - **Caption similarity check** — if Scout's `caption` field is
     present, do a fuzzy substring check against Textract's first row
     to validate the order-based match (defense-in-depth).
   - If the counts differ (Scout reports 2 tables, Textract finds 1 or
     3), log a warning and emit whatever Textract found. Scout's
     description for the unmatched table can still be emitted as a
     description-only chunk (no grid), with a `grid_status: "missing"`
     flag.

6. **Link to parent hierarchy node** using
   `nearest_hierarchy_node_for_page()` (existing, `hierarchy.py:198-215`):

   ```python
   node = nearest_hierarchy_node_for_page(
       hierarchy=hierarchy,
       page_number=table.page_number,
   )
   # Sets metadata["parent_hierarchy_node_id"] = node.hierarchy_node_id
   # Sets metadata["section"] = node.label
   # Sets metadata["heading_path"] = "/".join(node.heading_path)
   ```

   For a table on page 93, that's `R302.1` (deepest containing
   hierarchy node) rather than `R302` or `CHAPTER 3`.

#### Table chunk artifact

```
kb_index/{document_id}/tables/{chunk_id}.summary.md
kb_index/{document_id}/tables/{chunk_id}.summary.md.metadata.json
kb_artifacts/{document_id}/tables/{chunk_id}.json  # raw Textract grid
```

The `.summary.md` body looks like:

```markdown
# TABLE R302.1(1) EXTERIOR WALLS

## Description (for vector search)

{Llama 4 Scout description — vector-search-optimized prose}

## Table

| EXTERIOR WALL ELEMENT | (condition) | MINIMUM FIRE-RESISTANCE RATING | MINIMUM FIRE SEPARATION DISTANCE |
| --- | --- | --- | --- |
| Walls | Fire-resistance rated | 1 hour—tested in accordance with ASTM E119... | 0 feet |
| ... | ... | ... | ... |

## Footnotes

{Verbatim footnote text extracted from page text}
```

This combines the searchable description (for embeddings) with the
cell-accurate Textract grid (for the answer LLM to read when the chunk
is retrieved) and verbatim footnotes (extracted from pdftotext, not
paraphrased by any model).

Chunk catalog metadata:
- `chunk_type: "table"`
- `caption`, `description`, `summary_location`
- `asset_location` (raw Textract JSON of Block records)
- `parent_hierarchy_node_id`, `section`, `heading_path`
- `page_start`, `page_end`
- `grid_status`: `"present"` (Textract returned cells) | `"missing"`
  (Scout flagged but Textract found nothing — rare; chunk is still
  emitted with description-only body)
- Standard scalar fields (tenant, collection, access_tier, etc.)

#### Figure flow

Figures are intentionally simpler than tables. We do **not** attempt to
crop individual figures from the page — cropping reliably requires
either accurate Textract bounding boxes (unreliable on technical PDFs)
or model-emitted bboxes (Scout's confidence on bbox coords is low). The
two failure modes — wrong crop bounds and partial figure cropping — are
both worse than just providing the full page.

Instead:

1. **Detect figures from Scout's per-page output.** Each page
   observation includes a `figures` array with caption + description
   per figure (validated on page 81's seismic map). For pages where
   `figures` is non-empty, we emit one figure chunk per detected
   figure on that page.
2. **Description = Scout's description directly.** No separate Bedrock
   call. This is the only text content the chunk's embedding sees.
3. **Artifact = full rendered page as a PNG.** We already render every
   page during Step 2 (the same PNG fed to Scout). Save that PNG as the
   figure chunk's artifact:

   ```
   kb_artifacts/{document_id}/pages/page_{page_number}.png
   ```

   Multiple figure chunks on the same page share the same page-PNG
   artifact (deduplicated at the S3 path level). The full page provides
   visual context — the figure plus its caption and any surrounding
   labels are all in frame, which is more useful to an answer model
   than a tight crop that might miss the legend.
4. **Link to parent hierarchy node** same as tables —
   `nearest_hierarchy_node_for_page()`.

#### Figure chunk artifact

```
kb_index/{document_id}/figures/{chunk_id}.summary.md
kb_index/{document_id}/figures/{chunk_id}.summary.md.metadata.json
kb_artifacts/{document_id}/pages/page_{page_number}.png   # full page PNG
```

The `.summary.md` body contains only the description (the embedded
text). The image is referenced via metadata, not inlined in the markdown
body — the RAG path swaps it in dynamically at answer time:

```markdown
# FIGURE R301.2.2.1(6) SEISMIC DESIGN CATEGORIES—UNITED STATES

## Description (for vector search)

{Llama 4 Scout description — multi-sentence with example questions}

## Visual reference

The figure appears on page {page_number} of the source document. The
full rendered page is stored at the asset location below and will be
provided to the multimodal answer model when this chunk is retrieved.
```

Chunk catalog metadata:
- `chunk_type: "figure"`
- `caption`, `description`, `summary_location`
- `asset_location` = `s3://.../kb_artifacts/{document_id}/pages/page_{page_number}.png`
- `asset_content_type: "image/png"`
- `asset_kind: "rendered_page"` — signals to the RAG layer that this is
  a whole-page render, not a cropped figure
- `parent_hierarchy_node_id`, `section`, `heading_path`
- `page_start`, `page_end` (same value for figures — single page)
- Standard scalar fields (tenant, collection, access_tier, etc.)

#### Retrieval-side integration (RAG Lambda)

When the RAG Lambda retrieves a chunk with `chunk_type: "figure"` (or
any chunk with `asset_content_type` starting with `image/`), it:

1. **Resolves and authorizes** `asset_location` through the existing
   chunk authorization path (tenant + access_tier checks).
2. **Reads the PNG bytes** from S3.
3. **Adds an image content block** to the Bedrock Converse `messages`
   alongside the chunk's text. Roughly:

   ```python
   content_blocks = [
       {"text": format_source_block(chunk)},
       {"image": {
           "format": "png",
           "source": {"bytes": page_png_bytes},
       }},
   ]
   ```

4. **The answer model (multimodal)** can now reason over both the
   description text and the rendered page image when generating its
   answer. Citation verification against the chunk's text (the
   description) remains unchanged.

This means:
- The **embedding** only sees the Scout description — semantic search
  matches on what the figure depicts.
- The **answer model** sees both the description and the visual when
  the chunk is selected as a source.
- We never have to crop figures, never have to trust Textract figure
  bboxes, and the answer model gets full visual context (figure +
  caption + legend + adjacent labels).

Caveat: this requires the production answer model to be multimodal.
Claude Sonnet/Haiku 4.x families are multimodal so this works today.
If a future answer model is text-only, we'd fall back to using only the
description (no image attached) — graceful degradation handled by the
RAG layer checking `asset_content_type` before attaching.

#### What's eliminated

- Separate Nova Lite calls to summarize each table — Scout's description
  from Step 2 is reused.
- Separate Nova Lite calls to describe each figure — same.
- Whole-document Textract `StartDocumentAnalysis` — replaced with
  per-page sync `AnalyzeDocument` only on Scout-flagged table pages.

Net effect: zero additional Bedrock calls for asset summarization. Cost
savings ≈ $0.05-0.10/doc on documents with many tables/figures.

#### Asset extraction cost projection

Textract pricing: $15 per 1,000 pages for the Tables feature (= $0.015 /
page). With targeted invocation only on pages Scout flags as containing
tables:

| Document | Total pages | Pages with tables (est.) | Textract cost |
| --- | ---: | ---: | ---: |
| IRC 100-page sample | 100 | ~15 | ~$0.23 |
| Full 1109-page IRC | 1109 | ~150 | ~$2.25 |
| (For reference: blanket whole-doc Textract on full IRC) | 1109 | 1109 | $16.64 |

The targeted approach saves ~7× on the full IRC versus running Textract
on every page.

Combined per-document cost picture:

| Document | Scout triage | Sonnet hierarchy | Textract (tables only) | **Total** |
| --- | ---: | ---: | ---: | ---: |
| IRC 100 pages | $0.25 | $0.67 | ~$0.23 | **~$1.15** |
| Full IRC 1109 pages | ~$2.75 | ~$5-8 | ~$2.25 | **~$10-13** |

### Step 11 — Persistence

Write to S3 and Aurora:

```
kb_raw/{document_id}/
  original/{filename}                          # untouched source
  document_metadata.json                        # processing diagnostics + status
  document_hierarchy.json                       # full tree (Sonnet output + char ranges)
  hierarchy_indexes/{chunk_id}.hierarchy_index.json   # per-chunk index
  triage/
    page_observations.json                      # full Scout per-page JSON
    model_extracted_text.json                   # consolidated raw model text
    text_quality_report.json                    # mismatch analysis

kb_index/{document_id}/
  sections/{chunk_id}.md                        # section text body
  sections/{chunk_id}.md.metadata.json          # Bedrock sidecar
  tables/{chunk_id}.summary.md                  # description + markdown grid
  tables/{chunk_id}.summary.md.metadata.json
  figures/{chunk_id}.summary.md                 # description + image link
  figures/{chunk_id}.summary.md.metadata.json

kb_artifacts/{document_id}/
  tables/{chunk_id}.json                        # raw Textract grid
  pages/page_{page_number}.png                  # full-page render, shared by figure chunks on that page
```

Aurora updates:
- `document_catalog` row with hierarchy_location, hierarchy_profile,
  hierarchy_source, available_lookup_modes.
- `document_chunks` rows for each retrieval chunk with metadata above.
- `document_ingestion_jobs` final status.

## 5. Config changes summary

```yaml
ingestion:
  # ---- Per-page triage (Llama 4 Scout) ----
  pdf_triage_model_id: "us.meta.llama4-scout-17b-instruct-v1:0"
  pdf_triage_max_tokens_per_page: 8192
  pdf_full_page_triage_enabled: true
  pdf_triage_parallel_workers: 6

  # ---- Full-doc hierarchy (Sonnet 4.6) ----
  pdf_hierarchy_model_id: "us.anthropic.claude-sonnet-4-6"
  pdf_hierarchy_max_output_tokens: 32000
  pdf_hierarchy_read_timeout_seconds: 600

  # ---- Sonnet chunking (for documents exceeding context window) ----
  pdf_hierarchy_single_call_max_pages: 100
  pdf_hierarchy_chunk_pages: 80
  pdf_hierarchy_chunk_overlap_pages: 1
  pdf_hierarchy_orphan_handling: "promote"   # promote | drop

  # ---- Hierarchy locator ----
  pdf_hierarchy_locator_page_window: 1
  pdf_hierarchy_locator_drop_unlocatable: true

  # ---- Adaptive chunking (existing knobs) ----
  pdf_hierarchy_target_min_tokens: 300
  pdf_hierarchy_target_max_tokens: 3000
  pdf_hierarchy_hard_max_tokens: 7000
  pdf_hierarchy_store_index: true
  pdf_hierarchy_inline_index_max_nodes: 40

  # ---- Table extraction (Textract, targeted) ----
  pdf_table_extraction_enabled: true
  pdf_table_extraction_strategy: "scout_targeted"  # scout_targeted | whole_doc
  pdf_table_extraction_max_parallel_pages: 6       # ThreadPool size for per-page Textract calls
  pdf_textract_sync_timeout_seconds: 60
  pdf_textract_async_fallback_max_wait_seconds: 240  # If sync fails (e.g., page too large)
  # Note: no longer running whole-doc Textract; we call Textract sync
  # AnalyzeDocument per page only when Scout flagged contains_table.

  # ---- Figure extraction ----
  pdf_figure_extraction_enabled: true
  pdf_figure_artifact_strategy: "full_page_png"   # full_page_png is the only supported strategy today
  pdf_figure_render_scale: 2.0                    # Reuses the same render scale as triage
  # Figures use Scout's description directly. The artifact is the full
  # rendered page; the RAG layer attaches the page PNG to the answer
  # model's multimodal context when this chunk is retrieved.

  # ---- Removed ----
  # hierarchy_review_model_mode: removed (Sonnet is now the primary builder)
  # hierarchy_review_model_id: removed
  # pdf_table_summary_model_id: removed (Scout descriptions replace it)
  # pdf_figure_summary_model_id: removed
```

## 6. Implementation order

Recommended sequence (each step independently testable):

1. **Build the shared `join_pages_with_markers()` helper.** Unit-test
   the page_offsets math.
2. **Update triage prompt** in `triage.py` to the validated v2 combined
   prompt. Add `_TRIAGE_USER_TEXT` constant.
3. **Switch triage config** to Llama 4 Scout model id, raise
   max_tokens_per_page to 8192.
4. **Add Bedrock client timeout config** (`BotoConfig(read_timeout=120,
   connect_timeout=10, retries={"max_attempts": 2})`).
5. **Parallelize triage** with ThreadPoolExecutor in `multimodal_triage()`.
6. **Persist model_extracted_text.json** alongside page_observations.json.
7. **Build the Sonnet hierarchy module** (`hierarchy_sonnet.py`) with
   single-call path first.
8. **Build the hierarchy locator** (`hierarchy_locator.py`) with
   anchor-based positioning.
9. **Integrate locator output into the existing `HierarchyNode`
   structure**, so `hierarchy_chunking.py` continues to work without
   changes.
10. **Add chunked Sonnet path** in `hierarchy_sonnet.py` with stitching
    logic.
11. **Refactor table extraction to per-page targeted Textract.**
    Replace the existing whole-doc `StartDocumentAnalysis` with sync
    `AnalyzeDocument` calls per Scout-flagged table page (parallelized
    with `ThreadPoolExecutor`). Keep the existing `_table_block_to_grid()`
    and `_grid_to_markdown()` helpers — only the orchestration changes.
    Use Scout descriptions as the embedding summary (replace separate
    Nova Lite summary calls).
12. **Remove `hierarchy_review.py`** from the pipeline (delete the
    integration in `pdf.py`; keep the file for now in case we need to
    restore it).
13. **RAG Lambda image-attachment integration.** In
    `src/rag_lambda/graph/retrieval.py`, detect retrieved chunks where
    `asset_content_type` starts with `image/`, fetch the PNG from S3
    (with authorization), and add it as an additional `content` block
    on the Bedrock Converse `messages` alongside the chunk's text.
    Graceful degradation: if the answer model is text-only, skip the
    image attachment and continue with description-only context.
14. **End-to-end sandbox test** with IRC 100-page sample (including a
    figure-targeted question that requires the model to read the
    rendered page).

Testing on the full 1109-page IRC is **deferred** — the chunked Sonnet
path is included in the implementation so the code path exists, but
validating it against a real >100-page document is future work, gated
on the 100-page sample passing first.

## 7. Verification plan

1. **Unit tests** (`tests/`):
   - `test_join_pages_with_markers.py` — offset math.
   - `test_hierarchy_locator.py` — body_anchor positioning across all
     four confidence tiers; cross-reference disambiguation; page-banner
     disambiguation; headings-followed-by-headings.
   - `test_hierarchy_sonnet_chunking.py` — chunk splitting, page-marker
     preservation, dedup-by-label, orphan handling.
   - `test_document_ingestion_triage.py` — update for Llama Scout
     response shape (existing test).

2. **Sandbox integration test (100-page IRC):**
   - Confirm no Bedrock timeouts.
   - Confirm `document_hierarchy.json` has ≥240 valid nodes including
     R303.10.
   - Confirm chunks include R303 with R303.10 in its hierarchy index.
   - Ask heating question through RAG; confirm
     `cited_hierarchy_node.hierarchy_node_id == "R303.10"`.

3. **Asset linking validation:**
   - Confirm Textract was called only on pages Scout flagged as
     containing tables (~15 pages for the IRC sample, not all 100).
   - Confirm tables on page 93 link to `R302.1` (deepest parent).
   - Confirm both R302.1 tables have 4-column grids with all cell
     values intact (including `0 hours`, `NA`) and footnote markers
     preserved as Unicode superscripts (`ᵃ`, `ᵇ`, `c`).
   - Confirm figure on page 81 links to `R301.2.2.1`.
   - Confirm the figure chunk's `asset_location` points at
     `kb_artifacts/{document_id}/pages/page_81.png` and the file
     exists.
   - Ask a figure-targeted question through the RAG Lambda (e.g.,
     "what seismic design category is Florida in?"); confirm the RAG
     Lambda attaches the page-81 PNG to the Bedrock Converse call and
     the answer references the seismic map's visual content.
   - Confirm table/figure descriptions in chunk metadata match the
     Scout descriptions exactly.

4. **Citation quality:**
   - Run the validation question set
     (`data/validation_questions.csv`).
   - Compare retrieved sources before/after this implementation.
   - Confirm `cited_hierarchy_node` resolves to deepest subsection on
     ≥80% of queries (vs ~0% in the current broken pipeline).

**Out of scope for this pass:** validating the chunked Sonnet path on
the full 1109-page IRC. The chunked code path is included in the
implementation so it's exercised by unit tests on synthetic inputs, but
running it against a real >100-page document is deferred until the
100-page sample is fully validated.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Llama 4 Scout becomes unavailable on Bedrock | Model ID is config-driven; fall back to Claude Haiku 4.5 or Pixtral Large |
| Sonnet 4.6 returns malformed JSON | Defensive JSON repair (already in `hierarchy_review.py`); fall back to deterministic regex builder |
| Document exceeds Sonnet's 1M context | Chunked path activates above `pdf_hierarchy_single_call_max_pages`; rolling 80-page chunks with running hierarchy |
| Chunked-path overlap produces duplicate nodes | Dedup-by-label after stitching keeps the first occurrence |
| Subsequent chunk emits an orphan node | Configurable handling (`promote` to root by default, or `drop`) with logged orphan rate |
| Locator finds a cross-reference instead of the real heading | Body-anchor approach: anchor text doesn't appear after cross-references |
| Locator finds a page-header banner instead of the real heading | Body anchor disambiguates same way; running banners don't have anchor text after them |
| Sonnet emits a non-unique body_anchor | Page scoping + walk-back regex handles common cases; pathological case drops node with `anchor_orphan` tier |
| body_anchor not found in joined text | Returns `anchor_not_found`; node dropped + logged; per-tier counts monitored |
| Sonnet's page_start drifts ± 1 from actual heading position | Anchor search uses ±1 window, then full-doc fallback returning `anchored_full_doc` tier |
| Running hierarchy in subsequent chunks grows too large | ~12-25K tokens for 1000-node hierarchy is fine; if pathological, future enhancement limits to last N chapters |
| Parallel Bedrock calls hit account throttling | Start with 6 workers; monitor `ThrottlingException` and back off |
| Scout output has LaTeX formatting artifacts | No-LaTeX prompt instruction added (validated); Unicode-only post-process if any leak |
| TOC pages produce large output (page 22 used 6K tokens) | 8192-token cap with headroom |
| Aurora paused during write (sandbox auto-pause) | Existing retry logic; consider keep-warm ping at job start |

## 9. What's eliminated

- **Nova Lite triage** (replaced by Llama 4 Scout — no content filter on
  regulated text).
- **`hierarchy_review.py` Sonnet review pass** (Sonnet is now the
  primary hierarchy builder via direct call).
- **`_SECTION_CODE_BUILDING_RE` and per-profile regex variants** (Sonnet
  handles structure detection globally).
- **The "merge model headings into regex boundaries" step** from the
  earlier plan (Sonnet IS the heading source — regex is fallback only).
- **`parser_profiles.py` regex selection** — profiles become metadata-
  only tags for downstream display.
- **Separate Nova Lite calls** for table and figure summaries — Scout's
  per-page descriptions are reused.

## 10. Decisions captured

1. **Embedded PDF text is the source of truth for chunks.** Model text
   is for gap detection and is stored as a reference artifact at
   `kb_raw/{document_id}/triage/model_extracted_text.json`.

2. **Llama 4 Scout is the universal triage model.** At $0.25/100 pages
   it's cheap enough for any document type, and the content-filter
   issue that disqualified Nova Lite doesn't apply.

3. **Sonnet 4.6 is the universal hierarchy builder.** Single call for
   small documents, chunked rolling-context for large ones. No per-
   profile regex tuning.

4. **`body_anchor` is the locator primitive.** A unique 40-80 char
   snippet from anywhere after each heading. The walk-back algorithm
   handles all disambiguation cases (cross-references, banners,
   adjacent headings) without special-case rules.

5. **One coordinate space.** `joined_text` + `page_offsets` are built
   once by `join_pages_with_markers()` and shared by the Sonnet input,
   the locator search, and the chunker. Citation char-offsets align
   automatically.

6. **80-page Sonnet chunks with 1-page overlap** for documents over
   `pdf_hierarchy_single_call_max_pages`. Configurable per environment.

7. **Tables and figures are first-class chunks** with descriptions from
   Scout. **Textract is invoked per-page only on Scout-flagged table
   pages** (sync `AnalyzeDocument` with `FeatureTypes=["TABLES"]`),
   parallelized with `ThreadPoolExecutor`. This keeps Textract cost
   bounded to ~$0.23 / 100 pages (15% table-page rate) instead of $1.50
   for whole-doc Textract. Validation on page 93 showed Textract grids
   preserve all 4 columns, all cell values, and footnote markers as
   Unicode superscripts — quality Scout cannot match on complex
   multi-column tables. **Figures use Scout's description as the
   embedding text and the full rendered page PNG as the artifact** —
   the RAG layer attaches the page image to the answer model's
   multimodal context when a figure chunk is retrieved. No cropping.

8. **The legacy deterministic regex hierarchy builder is retained as a
   defensive fallback only** — never the primary path. Triggered when
   Sonnet fails (model unavailable, malformed JSON, validation
   failure).

## 11. Open questions

1. **What mismatch threshold should trigger the per-page text swap in
   Step 5?** Start with existing `pdf_triage_text_mismatch_threshold =
   0.50`. Validate on IRC and at least one non-code PDF. Tune based on
   observed false-positive rate (pages with legitimate gaps that
   shouldn't swap).

2. **Should we keep Textract for figure bounding boxes?** Initial
   implementation can use page-render fallback for figures (the whole
   page as the figure image) if Textract figure bbox accuracy is
   unreliable on building-code documents. Validate empirically.

3. **Should the chunked Sonnet path always include the full running
   hierarchy, or only the most recent N chapters?** For 1000+ node
   hierarchies on very long documents, the prompt grows. Defer
   optimization until we hit a real document that pushes the limit.

4. **Should table/figure asset chunks include the raw markdown grid /
   image in the embedding text, or only the description?** Initial
   plan: include both (description first, then markdown grid). Embedding
   quality should not suffer because the description dominates the
   semantic content; the grid helps the answer model when the chunk is
   retrieved.
