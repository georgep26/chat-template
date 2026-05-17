# Hierarchical Chunking Strategy Options

Created: 2026-05-16  
Status: Draft decision report  
Scope: Chunking strategy for structured technical documents, using `data/2021_International_Residential_Code-2_first_100_pages.pdf` as the first concrete test case.

## Decision Pressure

The current design question is not simply "large chunks or small chunks." A chunk currently has to serve several different jobs:

1. **Semantic retrieval unit** — what gets embedded and returned by vector search.
2. **Answer-context unit** — what text is placed into the answer LLM's context window.
3. **Direct-fetch unit** — what another agent or the frontend can fetch by id.
4. **Citation/evidence unit** — the exact chapter/section/sub-section/page shown to the user as support for a factual claim.

Those jobs want different sizes. Retrieval often works better with enough surrounding context. Citations need the smallest verifiable location. Direct fetch wants stable, human-meaningful ids. The main recommendation is to stop treating one physical chunk as all four things.

## Current Context

Relevant current implementation:

- `docs/development_plans/2026-05-09_metadata_implementation_plan.md` selects the derivative-corpus approach: original PDFs in `kb_raw/`, indexable derivative text in `kb_index/`, non-text artifacts in `kb_artifacts/`, app-owned catalog rows in Aurora, and Bedrock KB indexing only the derivative text artifacts.
- `config/*/app_config.yaml` already exposes PDF ingestion knobs such as `pdf_default_chunk_tokens`, `pdf_embedding_chunk_token_limit_titan_v2`, `pdf_embedding_chunk_char_limit_titan_v2`, `pdf_section_chunking_enabled`, `pdf_section_min_confidence`, `pdf_section_min_boundaries`, and `pdf_section_min_chars`.
- `src/document_ingestion_lambda/file_type_processors/section_chunking.py` can detect section-code headings and emit section chunks with metadata including `chapter`, `section`, `heading_path`, page range, token estimate, and stable `chunk_id`.
- `src/rag_lambda/graph/retrieval.py` now places retrieved source metadata and chunk text into `<source>` blocks before the answer model runs.
- `src/rag_lambda/citations.py` verifies `<cite source="N">QUOTE</cite>` text against retrieved chunks and returns only cited sources.
- `docs/development_plans/2026-05-16_subsection_citation_resolution_plan.md` already proposes resolving cited quotes to the deepest sub-section by storing an interior `section_index` on each chunk.

## IRC Sample Observations

The 100-page IRC sample is a strong stress test because it has:

- clear hierarchy: part, chapter, section, sub-section, sub-sub-section;
- two-column layout;
- long table of contents pages;
- very large parent sections;
- short leaf sections;
- technical tables and figures that are answer-critical;
- citations where practitioners expect exact section labels such as `R303.10`, not only `R303`.

Concrete examples from the sample:

- `SECTION R301 DESIGN CRITERIA` starts on PDF page 60 and `SECTION R302` starts on PDF page 92. R301 is therefore about 33 PDF pages. After stripping repeated headers/footer boilerplate, earlier local extraction measured roughly 11,756 words and roughly 15.7k-17.4k estimated tokens. That is too large to use as one embedding chunk for Titan's configured 8,192-token budget.
- `SECTION R303 LIGHT, VENTILATION AND HEATING` is much smaller and fits comfortably as an answer-context chunk, but the heating answer lives specifically in `R303.10 Required heating`. Showing only `R303` in the citation is technically broad and not precise enough for code users.
- `R301` contains many tables and figures. Those should not be treated only as incidental text inside one giant section. Tables and figures need their own chunk ids/assets/summaries, while still being linked to the nearest section path.
- A simple heading regex over raw extracted PDF text can find many real section codes, but it also sees false positives from licensing text, references such as "Section 1613.2", table of contents lines, and two-column layout artifacts. The ingestion strategy needs structure validation, not regex alone.

## Core Model: Four Separate Units

Use four related but distinct units in the metadata model.

| Unit | Purpose | Typical size | Example |
| --- | --- | --- | --- |
| `document` | Original uploaded source and Library row. | Whole file | 2021 IRC PDF |
| `hierarchy_node` | Logical hierarchy node for navigation and citation resolution. | Any heading depth | `R303.10 Required heating` |
| `retrieval_chunk` | Text artifact embedded in Bedrock and returned by semantic search. | Usually 500-3,000 tokens; hard cap below embed limit | `R303` or a grouped child range |
| `evidence_span` | Exact cited quote/span inside a retrieval chunk or source asset. | Sentence/paragraph/table row | 68°F requirement sentence |

The important shift: a citation can point to a `hierarchy_node` and `evidence_span` even when the retrieved `chunk_id` is a broader parent chunk.

## Options

### Option A: Fixed-size chunks only

Split every document into fixed-size text chunks, such as 1,000 tokens with 20% overlap.

Pros:

- Simple and general.
- Works for documents with no reliable hierarchy.
- Low implementation risk.

Cons:

- Weak citations. A chunk boundary rarely aligns with a section, table, figure, or page.
- Duplicate overlap can cause confusing citations.
- Direct-fetch by section is hard because section ids are not first-class.
- Poor fit for code books, manuals, contracts, and standards.

Best use:

- Fallback path for unstructured documents after hierarchy detection fails.

### Option B: Page-level chunks

Create one chunk per page, splitting oversized pages if necessary.

Pros:

- Page metadata is straightforward.
- Good for PDF viewer navigation.
- Useful fallback for slide decks, scanned files, and image-heavy documents.

Cons:

- Page boundaries rarely match logical sections.
- Two-column technical PDFs can split related material awkwardly.
- A single page may contain several small sections or table fragments.
- Citations are less useful than section citations for code books.

Best use:

- Fallback or secondary artifact layer. Keep page artifacts for viewer/highlight support, but do not make pages the main semantic unit for structured code documents.

### Option C: Parent-section chunks

Chunk at major section boundaries, such as `R301`, `R302`, `R303`.

Pros:

- Human-readable and aligned with a document's visible structure.
- Good context for answer generation when parent sections are modest in size.
- Works well for direct fetch by broad section.

Cons:

- Parent sections are uneven. R303 is manageable; R301 is far too large.
- Citation precision is poor unless we add a sub-section/evidence index.
- Large sections with many tables and figures become noisy retrieval units.

Best use:

- Only when a parent section is within the configured target range and is semantically coherent.

### Option D: Deepest leaf chunks

Chunk at the lowest detected heading level, such as `R303.10`, `R311.7.5.2`, or equivalent leaf sections.

Pros:

- Natural citation precision.
- Good direct-fetch ids for exact section references.
- Embeddings are tightly focused on specific topics.

Cons:

- Many leaf sections are too small for robust semantic retrieval.
- Some answers need surrounding exceptions, definitions, parent scope, or sibling provisions.
- Chunk count grows quickly.
- Technical tables/figures may be separated from the text that explains them.

Best use:

- Direct lookup by known section id.
- Optional child-index embeddings if we implement parent-child retrieval.
- Not recommended as the only answer-context chunking strategy.

### Option E: Adaptive hierarchy chunks

Build the full hierarchy tree, then choose retrieval chunks based on size and coherence:

- use the deepest node that is large enough to stand alone;
- merge small sibling nodes under a shared parent until the group reaches a useful minimum;
- split large nodes by child nodes first, then paragraphs if needed;
- always keep citation metadata for every deeper node inside the chosen chunk.

Pros:

- Generalizes beyond IRC to manuals, policies, contracts, reports, and standards.
- Avoids giant parent chunks like R301.
- Avoids tiny sentence-sized leaf chunks as primary retrieval units.
- Preserves direct-fetch and citation precision through the hierarchy tree.

Cons:

- More implementation complexity than fixed/page chunks.
- Requires clear heuristics and test fixtures for each document profile.
- Needs robust heading validation and false-positive handling.

Best use:

- Primary long-term strategy for structured technical documents.

### Option F: Parent-child retrieval

Embed smaller child chunks but return a larger parent chunk to the answer LLM. For example, embed `R303.10` or its summary, but when it retrieves, include `R303` or a configured parent/context window in the prompt.

Pros:

- Good retrieval recall from focused child embeddings.
- Good answer context from parent/surrounding text.
- Lets citations resolve to the child node while answer generation sees context.

Cons:

- More moving parts: child id, parent id, returned context id, evidence id.
- More storage/index metadata.
- Requires deduplication when many children retrieve the same parent.

Best use:

- Later enhancement after adaptive hierarchy chunks are working.
- Especially useful for documents where leaf sections are small but parent context is important.

### Option G: Dual index: coarse and fine

Create two embedding layers: one coarse index for broad retrieval and one fine index for exact section/table/figure retrieval.

Pros:

- Maximum flexibility.
- Can support broad topic queries and exact section lookup.
- Useful for agentic workflows that plan retrieval in multiple passes.

Cons:

- Higher cost and operational complexity.
- More deduplication and source ranking complexity.
- Harder to explain which layer produced the final answer.

Best use:

- Future scale-up after single-index adaptive chunks expose concrete gaps.

## Recommended Direction

Use **adaptive hierarchy chunks with a separate citation/evidence index**.

Short version:

1. Build and store the full document hierarchy tree.
2. Select retrieval chunks adaptively from that tree.
3. Store a fine-grained `section_index` or `hierarchy_index` inside each retrieval chunk.
4. Verify answer quotes against retrieved chunk text.
5. Resolve the verified quote offset to the deepest containing hierarchy node.
6. Return citation metadata for the deepest hierarchy node, not just the retrieval chunk.
7. Treat tables and figures as first-class child chunks/assets linked into the same hierarchy tree.

This gives us the best balance:

- retrieval has enough context;
- citations are specific;
- direct fetch works by `chunk_id`, `section`, or future `hierarchy_node_id`;
- the frontend can show source cards, hover previews, and later raw-document highlights;
- the approach generalizes to other hierarchical document families.

## Proposed Heuristics

Add these as configurable values under `ingestion` after we test the defaults:

```yaml
pdf_hierarchy_chunking:
  enabled: true
  target_min_tokens: 300
  target_max_tokens: 3000
  hard_max_tokens: 7000
  merge_small_leaf_nodes: true
  split_large_nodes_by_children: true
  keep_page_artifacts: true
  store_hierarchy_index: true
```

Heuristic rules:

1. Detect candidate hierarchy from TOC, body headings, and sampled pages.
2. Validate candidates against the body text. Reject licensing/order numbers, TOC dot leaders, cross-reference-only lines, and headings that do not repeat consistently.
3. Build a `structure_tree` with ids, titles, heading paths, page ranges, char offsets, parent ids, and child ids.
4. For every hierarchy node, estimate token count.
5. If a node is between `target_min_tokens` and `target_max_tokens`, it is a good retrieval chunk candidate.
6. If a node is below `target_min_tokens`, merge it with adjacent siblings under the same parent until the group is useful, unless the node is likely to be directly cited often.
7. If a node is above `target_max_tokens`, split by child nodes. If children are still too large, split by paragraphs/tables with deterministic part ids.
8. Never exceed `hard_max_tokens` for an embedding artifact.
9. Preserve child hierarchy metadata even when children are merged into one retrieval chunk.
10. Create table and figure chunks as child units with `parent_hierarchy_node_id`, `page_start`, `caption`, `asset_location`, and summary text.

## Metadata Shape

Add hierarchy information without breaking the current chunk metadata.

Retrieval chunk metadata should keep:

```json
{
  "chunk_id": "..._section_group_r303",
  "chunk_type": "section",
  "chapter": "R303",
  "section": "R303",
  "heading_path": "R303",
  "page_start": 98,
  "page_end": 99
}
```

Add chunk-local hierarchy index:

```json
{
  "hierarchy_index": [
    {
      "hierarchy_node_id": "R303",
      "label": "R303",
      "title": "LIGHT, VENTILATION AND HEATING",
      "heading_path": ["R303"],
      "depth": 1,
      "char_start": 0,
      "char_end": 8397,
      "page_start": 98,
      "page_end": 99
    },
    {
      "hierarchy_node_id": "R303.10",
      "label": "R303.10",
      "title": "Required heating",
      "heading_path": ["R303", "R303.10"],
      "depth": 2,
      "char_start": 7889,
      "char_end": 8397,
      "page_start": 99,
      "page_end": 99
    }
  ]
}
```

Citation response metadata should identify the cited evidence separately from the retrieved chunk:

```json
{
  "citation_number": 1,
  "chunk_id": "..._section_group_r303",
  "cited_hierarchy_node": {
    "hierarchy_node_id": "R303.10",
    "label": "R303.10",
    "title": "Required heating",
    "heading_path": ["R303", "R303.10"],
    "page_start": 99,
    "page_end": 99
  },
  "cited_quotes": [
    "Where the winter design temperature in Table R301.2 is below 60°F..."
  ]
}
```

The final answer LLM can still generate the quote and citation, but the backend should determine the final cited section from verified quote offsets, not from an LLM-provided section id.

## Table and Figure Handling

Tables and figures should not be hidden inside text chunks only.

For each table/figure:

- generate a separate `chunk_id`;
- set `chunk_type` to `table` or `figure`;
- link to `parent_hierarchy_node_id`;
- preserve `caption` from the source when available;
- store generated description/summary for embedding;
- store raw/structured artifact in `kb_artifacts/`;
- include page and source-location metadata;
- allow direct fetch of the raw table/figure asset after authorization.

For IRC, this matters most in R301 because many requirements are conveyed by tables and figures. A query about seismic design categories, wind speed, snow load, or exterior wall ratings may need a table/figure chunk more than a text section chunk.

## Retrieval Flow Implications

The agentic retrieval flow should use hierarchy in two places:

1. **Before retrieval:** provide an authorization-filtered catalog/index summary. The model can decide whether to semantic search, direct-fetch a known section, or both.
2. **After retrieval:** provide retrieved chunks with metadata plus their local hierarchy index. If the answer LLM cites a quote, the backend resolves the quote to the deepest hierarchy node.

For exact references such as "What does R303.10 require?", direct lookup should fetch that hierarchy node or its containing retrieval chunk instead of relying only on semantic search.

For vague questions such as "heating requirements for dwelling areas", semantic search can retrieve the chunk, and citation resolution can still return `R303.10`.

## Implementation Path

### Phase 1: Hierarchy index on existing section chunks

Goal: improve citation precision without changing retrieval behavior too much.

Tasks:

1. Update heading detection to detect all valid sub-headings inside a chosen retrieval chunk.
2. Store a `hierarchy_index` or `section_index` in chunk `custom_metadata` and, if small enough, in the catalog row metadata.
3. Record verified quote offsets in `src/rag_lambda/citations.py`.
4. Resolve each verified quote to the deepest containing hierarchy node.
5. Extend the `Source` API model to return `cited_hierarchy_node` or `cited_subsections`.
6. Update frontend source cards later to display the cited hierarchy label and title.

This phase solves the R303.10 citation problem.

### Phase 2: Adaptive hierarchy chunk selection

Goal: avoid both giant parent chunks and tiny leaf chunks.

Tasks:

1. Build a full `structure_tree` artifact for each structured document.
2. Add configurable min/target/max token thresholds.
3. Select retrieval chunks from the hierarchy tree using the heuristics above.
4. Split oversize sections by child nodes before falling back to paragraph splitting.
5. Merge undersized siblings when they are not good standalone retrieval units.
6. Re-ingest the IRC sample and inspect chunk distribution for R301, R302, and R303.

This phase addresses R301-sized sections.

### Phase 3: Direct hierarchy lookup

Goal: support agentic workflows and MCP-style direct fetch.

Tasks:

1. Add a catalog lookup path by `document_id`, `section`, `heading_path`, or `hierarchy_node_id`.
2. Return the containing authorized retrieval chunk plus the precise hierarchy-node slice when available.
3. Deduplicate direct-fetched chunks against semantic retrieval results.
4. Expose the same primitives through the Knowledge API Lambda.

### Phase 4: Table/figure integration

Goal: make visual and tabular technical evidence citable.

Tasks:

1. Link extracted table/figure chunks to `parent_hierarchy_node_id`.
2. Include table/figure ids in the document hierarchy tree.
3. Allow citations to point to table rows, captions, or generated summaries where evidence is verified.
4. Preserve raw artifact locations for future raw-document viewing.

## Near-term Recommendation

For the next implementation step, do not switch directly to deepest-leaf chunking.

Instead:

1. Implement the `section_index` / `hierarchy_index` on current section chunks.
2. Update citation normalization to return the deepest verified cited hierarchy node.
3. Re-ingest the 100-page IRC sample.
4. Run the heating question again and confirm the source card can show `R303.10 Required heating`.
5. Then implement adaptive chunk selection and use R301 as the main validation case.

This sequence gives us an immediate citation win while preserving the retrieval behavior that is already mostly working.

## Open Questions

1. Should `hierarchy_index` be included in Bedrock sidecar metadata, catalog metadata only, or both?
   - Recommendation: catalog/custom metadata first. Bedrock metadata fields are better for filtering/display, while large nested indexes can become awkward in sidecars.
2. Should we store raw char offsets, normalized char offsets, or both?
   - Recommendation: store raw offsets at ingestion. Compute normalized quote-match offsets at cite time and map back if needed.
3. What is the minimum useful retrieval chunk size?
   - Starting point: 300 tokens, but validate on IRC, a policy document, and a report-style PDF.
4. What is the preferred max retrieval chunk size?
   - Starting point: 3,000 target max and 7,000 hard max, leaving room for metadata and multiple sources in the answer prompt.
5. How should page coordinates be handled for raw PDF highlighting?
   - Defer exact coordinates. Preserve page ranges and char offsets now; add OCR/text-layer bounding boxes later.
