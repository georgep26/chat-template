# Hierarchical Chunking Refinement

Created: 2026-05-16  
Status: Draft implementation refinement  
Related plan: `2026-05-16_hierarchical_chunking_strategy_options.md`  
Related implementation plan: `2026-05-09_metadata_implementation_plan.md`

## Goal

The goal is to build a chunking strategy that supports strong retrieval, strong citations, direct source fetches, and agent-facing document navigation without forcing one physical chunk size to solve every problem.

The recommended approach is hybrid:

- build a full logical hierarchy for structured documents;
- choose retrieval chunks adaptively from that hierarchy;
- store fine-grained hierarchy metadata inside each retrieval chunk;
- verify cited quotes against retrieved chunk text;
- resolve verified quotes to the deepest hierarchy node that contains the quote;
- keep tables and figures as first-class chunks/assets linked into the same hierarchy.

The 2021 IRC first-100-pages PDF is the initial test case because it has a clear hierarchy, long sections, short sub-sections, tables, figures, and code-style citations. The design should generalize to other technical standards, policies, manuals, reports, contracts, presentations, and internal company documents.

## Core Design Principle

Separate these concepts:

| Concept | Purpose | Example |
| --- | --- | --- |
| `document` | Original uploaded source and Library/catalog row. | 2021 IRC PDF |
| `hierarchy_node` | Logical section/chapter/table/figure node in the document hierarchy. | `R303.10 Required heating` |
| `retrieval_chunk` | Text artifact embedded into Bedrock and returned by semantic retrieval. | a grouped `R303` chunk |
| `evidence_span` | Exact quote, table row, or figure/caption span cited in an answer. | the 68°F heating sentence |

Chunking should optimize semantic retrieval and context size. Citation metadata should come from `hierarchy_node` and `evidence_span` metadata, not from the retrieval chunk boundary alone.

## Target Process Overview

The refined process has four main flows:

1. **Ingestion flow:** parse the document, detect hierarchy, create retrieval chunks/assets, write sidecars, and update the app-owned catalog.
2. **Retrieval flow:** search semantic chunks, enrich prompt context, answer with quoted citations, and deterministically resolve citations.
3. **Direct fetch flow:** fetch a known document, section, chunk, table, or figure by id after authorization.
4. **Agent index / table-of-contents flow:** provide agents an authorization-filtered document index so they can choose semantic retrieval, direct fetch, or both.

## Ingestion Flow

### 1. Read available text first

For PDFs, start with the embedded text layer when available. This is cheap, deterministic, and enough for many digitally generated PDFs.

Initial outputs:

- per-page extracted text;
- page count;
- PDF title/metadata when available;
- basic text quality metrics such as extracted character count and blank-page ratio.

The embedded text layer is not automatically trusted. It is compared against multimodal visible-text extraction before final chunking.

### 2. Run configurable multimodal triage

Use a cheap configurable model, currently Nova Lite in sandbox, to inspect rendered pages. The page selection mode should be explicit:

- If `pdf_full_page_triage_enabled` is `true`, run multimodal triage on every page in the document.
- If `pdf_full_page_triage_enabled` is `false`, run triage on the first `pdf_front_page_sample_count` pages plus `pdf_random_page_sample_count` additional random pages.
- When full-page triage is enabled, ignore `pdf_front_page_sample_count` and `pdf_random_page_sample_count`.

The model's job is extraction and page observation only. It should not compare its extracted text with the embedded PDF text layer, and it should not decide whether OCR or Textract is needed. The backend performs those comparisons deterministically after triage.

The model should extract:

- likely document type;
- raw visible page text, using the same style of extraction tested in `docs/working_files/llm_extraction_test`;
- heading candidates, including the visible heading label and title text;
- table of contents indicators, such as whether this page appears to be a TOC page;
- tables, including captions, visible table titles, and table descriptions suitable for later embedding;
- figures/images/maps/plots, including captions, visible figure titles, and figure descriptions suitable for later embedding;
- nearby headings or hierarchy context for tables and figures.

The raw text extracted by the multimodal model is not intended to become the authoritative text source. The goal is to use it as a low-cost visual observation that the backend can compare against the embedded PDF text layer. Nova Lite is cheap enough for sandbox experimentation and can extract enough visible text to identify pages where the embedded text layer may be incomplete.

Recommended config direction:

```yaml
ingestion:
  pdf_triage_enabled: true
  pdf_triage_model_id: "amazon.nova-lite-v1:0"
  # If true, run multimodal triage on every page and ignore the front/random sample counts.
  pdf_full_page_triage_enabled: false
  # Used only when full-page triage is false.
  pdf_front_page_sample_count: 30
  # Used only when full-page triage is false.
  pdf_random_page_sample_count: 5
  # Per-page text mismatch threshold between embedded PDF text and model-extracted visible text.
  pdf_triage_text_mismatch_threshold: 0.50
  # If this fraction of triaged pages exceed the mismatch threshold, backend recommends OCR/Textract.
  pdf_triage_document_text_mismatch_threshold: 0.30
  pdf_triage_hierarchy_threshold: 0.50
```

Example per-page triage output:

```json
{
  "page_number": 99,
  "raw_visible_text": "R303.10 Required heating. Where the winter design temperature in Table R301.2 is below 60°F...",
  "heading_candidates": [
    {
      "label": "R303.10",
      "title": "Required heating",
      "raw_text": "R303.10 Required heating.",
      "confidence": 0.92
    }
  ],
  "toc_indicators": {
    "appears_to_be_toc_page": false,
    "toc_entries": []
  },
  "tables": [],
  "figures": [],
  "notes": []
}
```

### 3. Validate text quality and choose OCR path

After multimodal triage, the backend compares the model-extracted visible text to the embedded PDF text. If the comparison indicates substantial visible text is missing from the embedded layer, run Textract or another OCR path. The decision should be based on measurable checks:

- disagreement between embedded text and model-extracted `raw_visible_text`;
- embedded text missing headings, table captions, or figure captions that the multimodal model extracted;
- low extracted character count relative to page count.

The OCR decision should not be made by the multimodal model. The model extracts visible text/headings/tables/figures/captions. The backend applies deterministic thresholds.

Recommended deterministic text-drift check:

1. Normalize the embedded PDF text for a page.
2. Normalize the multimodal model's `raw_visible_text` for the same page.
3. Compute a similarity score or mismatch ratio. A first implementation can use token-set overlap or normalized edit distance.
4. Mark a page as suspicious when the mismatch ratio is greater than `pdf_triage_text_mismatch_threshold`, initially `0.50`.
5. Mark the document as likely needing OCR/Textract when the fraction of suspicious triaged pages exceeds `pdf_triage_document_text_mismatch_threshold`, initially `0.30`, or when critical TOC/section/table pages are suspicious.

Interpretation:

- A single page with more than 50% mismatch usually means the embedded text layer missed a large visible region, has broken ordering, or lost table/figure/caption text.
- A majority of triaged pages with more than 50% mismatch suggests the whole PDF text layer is unreliable.
- If full-page triage is enabled, the document-level OCR decision can be based on all pages.
- If sample triage is used, the document-level decision should be treated as a confidence signal and passed to the main ingestion planner.

The main ingestion planner should receive a compact text-quality report:

```json
{
  "triaged_page_count": 100,
  "full_page_triage_enabled": true,
  "suspicious_page_count": 4,
  "suspicious_page_ratio": 0.04,
  "pages_over_text_mismatch_threshold": [7, 81, 93, 94],
  "thresholds": {
    "page_mismatch": 0.5,
    "document_mismatch": 0.3
  },
  "ocr_recommended": false,
  "textract_recommended": false,
  "reason": "Most pages have adequate embedded text; suspicious pages are table/figure-heavy and can be handled by table/figure extraction."
}
```

If `ocr_recommended` or `textract_recommended` is true, the hierarchy planner should use that backend-generated signal when selecting the ingestion plan. If deterministic confidence is low or model review is enabled, Sonnet can correct the proposed Textract scope for the full document, selected pages, or only table/figure-heavy pages depending on the mismatch pattern.

### 4. Build a document hierarchy plan

The default hierarchy plan should be deterministic. Nova Lite and similar multimodal models provide page-level observations, but the backend should aggregate and validate those observations with known parser profiles.

The multimodal model should not return arbitrary regular expressions that the backend executes directly. Instead, it should return constrained pattern classifications and examples:

```json
{
  "page_number": 99,
  "heading_pattern_type": "section_codes",
  "heading_candidates": [
    {
      "label": "R303.10",
      "title": "Required heating",
      "raw_text": "R303.10 Required heating.",
      "confidence": 0.92
    }
  ]
}
```

The backend selects from a known heading-parser registry:

| Pattern type | Parser behavior | Example |
| --- | --- | --- |
| `section_codes` | Alphanumeric section ids with dot-separated depth. | `R303.10 Required heating` |
| `chapter_heading` | Chapter/section prose headings. | `CHAPTER 3 BUILDING PLANNING` |
| `numbered_headings` | Numeric outline headings. | `5.4.1 Installation` |
| `page_only` | No reliable hierarchy; preserve page boundaries. | slide decks, scans |

Deterministic aggregation should:

1. Normalize heading candidates from triage, embedded text, OCR/Textract text when used, and table-of-contents entries.
2. Match candidates against the selected known parser profile.
3. Validate candidates against the authoritative extracted/OCR text.
4. Reject licensing/order numbers, dot-leader TOC lines, cross-reference-only lines, and page-footer/header artifacts.
5. Use id shape to infer hierarchy paths, for example `R303.10` -> `R303`, and `R311.7.5.1` -> `R311.7.5`.
6. Enforce page-order monotonicity and non-overlapping ranges.
7. Cross-check body headings against the table of contents when available.
8. Produce a hierarchy confidence score plus conflict diagnostics.

Example deterministic hierarchy aggregation output:

```json
{
  "hierarchy_detected": true,
  "document_profile": "building_code",
  "heading_pattern_type": "section_codes",
  "confidence": 0.91,
  "toc_pages": [10, 11, 12, 13],
  "detected_level_examples": {
    "chapter": ["CHAPTER 3 BUILDING PLANNING"],
    "section": ["R303 LIGHT, VENTILATION AND HEATING"],
    "subsection": ["R303.10 Required heating"]
  },
  "conflicts": [],
  "review_model_recommended": false
}
```

The deterministic hierarchy is the default hierarchy candidate. For now, keep Sonnet as a configurable correction step that compares the deterministic hierarchy against the Nova Lite triage observations and text-quality report. When Sonnet is enabled, the Sonnet-corrected hierarchy becomes the authoritative hierarchy used for ingestion. Once the deterministic builder is reliable for a profile, we can turn the Sonnet step off and run only the deterministic path.

Add a configurable model-review policy:

```yaml
ingestion:
  hierarchy_generation_mode: "deterministic" # deterministic
  hierarchy_review_model_mode: "auto" # auto | always | never
  hierarchy_review_model_id: "us.anthropic.claude-sonnet-4-6"
  hierarchy_review_min_confidence: 0.80
```

In `auto` mode:

- call Sonnet during early development when we want a second-pass correction of the deterministic hierarchy;
- later, skip Sonnet when deterministic aggregation confidence is at or above `hierarchy_review_min_confidence` and no serious conflicts exist;
- call Sonnet when hierarchy confidence is low, parser candidates conflict, OCR/Textract recommendations are ambiguous, or the document profile is unknown;
- use the deterministic hierarchy as the source of truth only when Sonnet is skipped or disabled;
- use the Sonnet-corrected hierarchy as the source of truth when Sonnet is enabled and returns a valid correction payload.

When model review is enabled, send the triage JSON, text-quality report, table-of-contents findings, and deterministically generated hierarchy candidate to Sonnet 4.6. The triage JSON should contain the page-level extracted text, heading candidates, table descriptions, figure descriptions, and table/figure captions. Sonnet should compare the deterministic hierarchy against those observations and return a corrected hierarchy plan when it sees missing nodes, false positives, wrong parentage, table/figure placement errors, or strategy issues. It should not execute arbitrary parsing rules or invent unsupported headings, but it can correct the hierarchy using the evidence it was given:

- Does a hierarchy exist?
- What hierarchy pattern is present?
- What are the likely levels? Example: chapter -> section -> sub-section -> sub-sub-section.
- Which pages are table-of-contents pages?
- Which known heading parser/profile should be used?
- Which document profile applies? Example: `building_code`, `policy`, `manual`, `report`, `presentation`.
- Is Textract/OCR necessary for the full document, selected pages, or not at all?
- Does the deterministic hierarchy appear to miss headings that Nova Lite observed?
- Does the deterministic hierarchy include false-positive headings that are likely headers, footers, TOC noise, captions, or cross-references?
- Are table and figure nodes linked to the right parent hierarchy nodes?
- Which chunking strategy should be used?

The expected output from Sonnet is a correction payload that becomes the source of truth when model review is enabled:

```json
{
  "review_status": "corrected",
  "confidence": 0.88,
  "recommended_action": "use_corrected_hierarchy",
  "corrected_hierarchy": {
    "profile": "building_code",
    "heading_pattern_type": "section_codes",
    "nodes": [
      {
        "hierarchy_node_id": "R302.1",
        "label": "R302.1",
        "title": "Exterior walls",
        "parent_id": "R302",
        "node_type": "subsection",
        "page_start": 92,
        "page_end": 93
      }
    ]
  },
  "issues": [
    {
      "severity": "warning",
      "page_number": 93,
      "message": "Two table captions were detected by triage and should be linked under R302.1."
    }
  ],
  "suggested_profile": "building_code",
  "textract_recommendation": "selected_pages_only"
}
```

The backend should still validate the Sonnet-corrected hierarchy before writing `document_hierarchy.json`: ids must match the selected parser profile, page ranges must be valid, parent ids must exist, and nodes should be traceable to triage headings, extracted text, TOC entries, or table/figure captions. This keeps the normal ingestion path cheaper and more reproducible when Sonnet is off, while allowing Sonnet to become authoritative when it is explicitly enabled. In production, known document profiles can run with `hierarchy_review_model_mode: "never"` once deterministic validation is trusted.

### 5. Store the full document hierarchy

Store a hierarchy artifact beside the raw document in S3:

```text
kb_raw/{document_id}/document_hierarchy.json
```

Example hierarchy artifact:

```json
{
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "schema_version": "1.0",
  "profile": "building_code",
  "source_location": "s3://bucket/kb_raw/irc_building_code_2021_rev_1_a8f43c2d9b/original/2021_International_Residential_Code-2_first_100_pages.pdf",
  "root_nodes": ["chapter_3"],
  "nodes": [
    {
      "hierarchy_node_id": "chapter_3",
      "label": "CHAPTER 3",
      "title": "BUILDING PLANNING",
      "node_type": "chapter",
      "parent_id": null,
      "children": ["R301", "R302", "R303"],
      "heading_path": ["CHAPTER 3"],
      "page_start": 60,
      "page_end": 100,
      "char_start": 0,
      "char_end": 137650,
      "token_estimate": 68825
    },
    {
      "hierarchy_node_id": "R303",
      "label": "R303",
      "title": "LIGHT, VENTILATION AND HEATING",
      "node_type": "section",
      "parent_id": "chapter_3",
      "children": ["R303.1", "R303.2", "R303.10"],
      "heading_path": ["CHAPTER 3", "R303"],
      "page_start": 98,
      "page_end": 99,
      "char_start": 129253,
      "char_end": 137650,
      "token_estimate": 4199
    },
    {
      "hierarchy_node_id": "R303.10",
      "label": "R303.10",
      "title": "Required heating",
      "node_type": "subsection",
      "parent_id": "R303",
      "children": [],
      "heading_path": ["CHAPTER 3", "R303", "R303.10"],
      "page_start": 99,
      "page_end": 99,
      "char_start": 137142,
      "char_end": 137650,
      "token_estimate": 254
    }
  ]
}
```

The exact offsets above are illustrative. The implementation should compute them from the normalized chunk text used to generate artifacts.

### 6. Choose retrieval chunks adaptively

Use the hierarchy tree to choose retrieval chunks. Do not default to the deepest leaf as the vector unit.

Recommended first heuristic:

```yaml
ingestion:
  pdf_hierarchy_chunking_enabled: true
  pdf_hierarchy_target_min_tokens: 300
  pdf_hierarchy_target_max_tokens: 3000
  pdf_hierarchy_hard_max_tokens: 7000
  pdf_hierarchy_merge_small_leaf_nodes: true
  pdf_hierarchy_split_large_nodes_by_children: true
  pdf_hierarchy_store_index: true
```

Rules:

1. If no reliable hierarchy exists, use fallback chunking: page chunks or fixed-size chunks with overlap.
2. If hierarchy exists, estimate tokens for every hierarchy node.
3. Nodes between target min/max are good retrieval chunk candidates.
4. Small sibling nodes can be merged into a grouped retrieval chunk, while preserving each child in `hierarchy_index`.
5. Large nodes are split by child nodes first.
6. If child-node splitting still exceeds the hard max, split by paragraphs with deterministic part ids.
7. Tables and figures become their own retrieval chunks/assets, linked to the nearest hierarchy node.

This avoids both failure modes:

- giant parent chunks, such as a single R301 chunk;
- tiny leaf chunks that are precise but poor for semantic retrieval.

### 7. Generate retrieval chunk artifacts

Retrieval chunks are markdown files under `kb_index/`, with adjacent Bedrock sidecar metadata.

Example retrieval chunk artifact path:

```text
kb_index/{document_id}/sections/{chunk_id}.md
kb_index/{document_id}/sections/{chunk_id}.md.metadata.json
```

Example markdown header:

```markdown
# R303 LIGHT, VENTILATION AND HEATING

- document_id: irc_building_code_2021_rev_1_a8f43c2d9b
- chunk_id: irc_building_code_2021_rev_1_a8f43c2d9b_section_r303
- source: s3://bucket/kb_raw/irc_building_code_2021_rev_1_a8f43c2d9b/original/2021_International_Residential_Code-2_first_100_pages.pdf
- page: 98-99
- section: R303
- heading_path: CHAPTER 3/R303

R303 LIGHT, VENTILATION AND HEATING
...
R303.10 Required heating. Where the winter design temperature...
```

Example catalog metadata for a text retrieval chunk:

```json
{
  "metadata_schema_version": "1.0",
  "record_type": "chunk",
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "chunk_id": "irc_building_code_2021_rev_1_a8f43c2d9b_section_r303",
  "tenant_id": "sandbox",
  "collection_id": "default",
  "document_title": "2021 International Residential Code",
  "title": "R303 LIGHT, VENTILATION AND HEATING",
  "document_type": "pdf",
  "filename": "2021_International_Residential_Code-2_first_100_pages.pdf",
  "location": "s3://bucket/kb_index/irc_building_code_2021_rev_1_a8f43c2d9b/sections/irc_building_code_2021_rev_1_a8f43c2d9b_section_r303.md",
  "source_location": "s3://bucket/kb_raw/irc_building_code_2021_rev_1_a8f43c2d9b/original/2021_International_Residential_Code-2_first_100_pages.pdf",
  "chunk_type": "section",
  "chunk_index": 37,
  "chapter": "CHAPTER 3",
  "section": "R303",
  "heading_path": "CHAPTER 3/R303",
  "page_start": 98,
  "page_end": 99,
  "content_type": "text/markdown",
  "token_count": 4199,
  "char_count": 8397,
  "access_tier": "public",
  "custom_metadata": {
    "retrieval_strategy": "adaptive_hierarchy",
    "hierarchy_node_id": "R303",
    "hierarchy_index_location": "s3://bucket/kb_raw/irc_building_code_2021_rev_1_a8f43c2d9b/hierarchy_indexes/irc_building_code_2021_rev_1_a8f43c2d9b_section_r303.hierarchy_index.json",
    "hierarchy_index_inline": [
      {
        "hierarchy_node_id": "R303",
        "label": "R303",
        "title": "LIGHT, VENTILATION AND HEATING",
        "node_type": "section",
        "heading_path": ["CHAPTER 3", "R303"],
        "depth": 2,
        "char_start": 0,
        "char_end": 8397,
        "page_start": 98,
        "page_end": 99
      },
      {
        "hierarchy_node_id": "R303.10",
        "label": "R303.10",
        "title": "Required heating",
        "node_type": "subsection",
        "heading_path": ["CHAPTER 3", "R303", "R303.10"],
        "depth": 3,
        "char_start": 7889,
        "char_end": 8397,
        "page_start": 99,
        "page_end": 99
      }
    ]
  }
}
```

Recommendation: keep a compact hierarchy index inline only when it is small. Store the full index in S3 and catalog `metadata` when it is large. Avoid putting a large nested hierarchy index into Bedrock sidecar metadata if it creates indexing/filtering noise.

### 8. Generate table and figure chunks

Tables and figures are separate chunks. Their summary markdown is indexed; the raw or structured asset is stored in `kb_artifacts/`.

Example table chunk metadata:

```json
{
  "metadata_schema_version": "1.0",
  "record_type": "chunk",
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "chunk_id": "irc_building_code_2021_rev_1_a8f43c2d9b_table_0093_01",
  "tenant_id": "sandbox",
  "collection_id": "default",
  "document_title": "2021 International Residential Code",
  "title": "TABLE R302.1(1) EXTERIOR WALLS",
  "chunk_type": "table",
  "chapter": "CHAPTER 3",
  "section": "R302.1",
  "heading_path": "CHAPTER 3/R302/R302.1",
  "page_start": 93,
  "page_end": 93,
  "location": "s3://bucket/kb_index/irc_building_code_2021_rev_1_a8f43c2d9b/tables/irc_building_code_2021_rev_1_a8f43c2d9b_table_0093_01.summary.md",
  "asset_location": "s3://bucket/kb_artifacts/irc_building_code_2021_rev_1_a8f43c2d9b/tables/irc_building_code_2021_rev_1_a8f43c2d9b_table_0093_01.json",
  "summary_location": "s3://bucket/kb_index/irc_building_code_2021_rev_1_a8f43c2d9b/tables/irc_building_code_2021_rev_1_a8f43c2d9b_table_0093_01.summary.md",
  "caption": "TABLE R302.1(1) EXTERIOR WALLS",
  "description": "Exterior wall fire-resistance and fire separation distance requirements for dwellings without automatic fire sprinklers.",
  "access_tier": "public",
  "custom_metadata": {
    "parent_hierarchy_node_id": "R302.1",
    "extraction_method": "textract_analyze_document_tables",
    "summary_model_id": "amazon.nova-lite-v1:0",
    "table_index_on_page": 1
  }
}
```

When a table/figure summary is retrieved, the answer path can fetch the raw table JSON or figure asset if needed and if the request is authorized.

### 9. Write catalog rows and start KB sync

The ingestion workflow writes:

- `document_catalog` row for the original document;
- `document_chunks` rows for retrieval chunks, tables, figures, pages, or other assets;
- `document_ingestion_jobs` status updates;
- Bedrock sidecar metadata next to each `kb_index/` artifact;
- a Bedrock Knowledge Base ingestion job when appropriate.

## Retrieval Flow

### 1. Build authorized retrieval scope

Before retrieval, resolve the user scope:

- `tenant_id`;
- groups;
- allowed collections;
- access tiers;
- explicit document grants when implemented.

Apply coarse Bedrock filters where possible and post-filter returned chunks against the app-owned catalog before any chunk text reaches the answer model.

### 2. Retrieve semantic chunks

Bedrock retrieves index artifacts from `kb_index/`. Returned source metadata should include at minimum:

- `document_id`;
- `chunk_id`;
- `chunk_type`;
- `document_title`;
- `title`;
- `section`;
- `heading_path`;
- `page_start`;
- `page_end`;
- `location`;
- `source_location`;
- authorization fields.

### 3. Enrich source context for the answer model

The answer prompt should include chunk text and enough metadata for the model to cite sources.

Example answer context block:

```xml
<source number="1">
source_number: 1
<metadata>
document_id: irc_building_code_2021_rev_1_a8f43c2d9b
chunk_id: irc_building_code_2021_rev_1_a8f43c2d9b_section_r303
document_title: 2021 International Residential Code
title: R303 LIGHT, VENTILATION AND HEATING
section: R303
heading_path: CHAPTER 3/R303
page_start: 98
page_end: 99
chunk_type: section
</metadata>
<hierarchy_index>
- R303 LIGHT, VENTILATION AND HEATING, pages 98-99, chars 0-8397
- R303.1 Habitable rooms, page 98, chars 180-1290
- R303.10 Required heating, page 99, chars 7889-8397
</hierarchy_index>
<chunk>
R303 LIGHT, VENTILATION AND HEATING
...
R303.10 Required heating. Where the winter design temperature in Table R301.2 is below 60°F...
</chunk>
</source>
```

The `hierarchy_index` helps the model orient itself, but it should not be trusted as the final citation authority. The backend still verifies the quoted text and resolves the cited hierarchy node deterministically.

### 4. Answer with quote-carrying citations

Preferred answer format remains:

```xml
<cite source="1">Where the winter design temperature in Table R301.2 is below 60°F (16°C), every dwelling unit shall be provided with heating facilities capable of maintaining a room temperature of not less than 68°F (20°C) ...</cite>
```

The answer model should cite factual statements and include the relevant quote when possible.

### 5. Normalize and resolve citations

Backend citation processing should:

1. Parse `<cite source="N">QUOTE</cite>`.
2. Verify the quote appears in the cited chunk after normalization.
3. Record the quote offset in the chunk.
4. Use `hierarchy_index` to find the deepest hierarchy node containing the quote.
5. Return only cited sources.
6. Renumber sources based on first use in the answer.

Example response source:

```json
{
  "citation_number": 1,
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "source_type": "section",
  "score": 0.82,
  "chunk": "R303 LIGHT, VENTILATION AND HEATING ...",
  "metadata": {
    "chunk_id": "irc_building_code_2021_rev_1_a8f43c2d9b_section_r303",
    "document_title": "2021 International Residential Code",
    "title": "R303 LIGHT, VENTILATION AND HEATING",
    "section": "R303",
    "heading_path": "CHAPTER 3/R303",
    "page_start": 98,
    "page_end": 99
  },
  "cited_quotes": [
    "Where the winter design temperature in Table R301.2 is below 60°F (16°C), every dwelling unit shall be provided with heating facilities capable of maintaining a room temperature of not less than 68°F (20°C)"
  ],
  "cited_hierarchy_node": {
    "hierarchy_node_id": "R303.10",
    "label": "R303.10",
    "title": "Required heating",
    "heading_path": ["CHAPTER 3", "R303", "R303.10"],
    "page_start": 99,
    "page_end": 99
  },
  "evidence_spans": [
    {
      "quote_index": 0,
      "char_start": 7889,
      "char_end": 8120,
      "page_start": 99,
      "page_end": 99
    }
  ]
}
```

## Direct Document / Chunk Fetch Flow

Direct fetch supports frontend source cards, raw-document viewers, and external agents that already know which section or chunk they need.

Supported lookup inputs:

- `document_id`;
- `chunk_id`;
- `hierarchy_node_id`, such as `R303.10`;
- `section`, such as `R303.10`;
- page range;
- table/figure chunk id.

Direct fetch must always go through backend authorization.

Flow:

1. Caller requests a document/chunk/section/table/figure.
2. Backend authenticates the user and resolves authorization scope.
3. Backend looks up `document_catalog`, `document_chunks`, and/or hierarchy artifact.
4. Backend verifies the requested node belongs to an authorized document and allowed S3 prefix.
5. Backend fetches the text artifact, hierarchy-node slice, table JSON, figure image, or generated access URL.
6. Backend returns bounded content suitable for model context or frontend display.

Example direct fetch request:

```json
{
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "hierarchy_node_id": "R303.10",
  "include_context": "parent",
  "include_raw_location": false
}
```

Example direct fetch response:

```json
{
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "hierarchy_node_id": "R303.10",
  "label": "R303.10",
  "title": "Required heating",
  "heading_path": ["CHAPTER 3", "R303", "R303.10"],
  "page_start": 99,
  "page_end": 99,
  "containing_chunk_id": "irc_building_code_2021_rev_1_a8f43c2d9b_section_r303",
  "content_type": "text/markdown",
  "content": "R303.10 Required heating. Where the winter design temperature in Table R301.2 is below 60°F ...",
  "source_location": "s3://bucket/kb_raw/irc_building_code_2021_rev_1_a8f43c2d9b/original/2021_International_Residential_Code-2_first_100_pages.pdf"
}
```

For frontend raw document viewing, the backend should eventually return a short-lived viewer URL rather than exposing raw S3 URIs directly.

## Agent Index / Table-of-Contents Context Flow

Agents should be able to see what sources are available without seeing unauthorized documents or loading entire documents into context.

The backend should provide an authorization-filtered document index. This can power:

- retrieval planning inside the RAG Lambda;
- a Knowledge API / MCP-style primitive;
- the frontend Library view;
- agent workflows that choose direct fetch before semantic search.

### Document index response

Initial document index rows should be compact:

```json
{
  "documents": [
    {
      "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
      "document_title": "2021 International Residential Code",
      "document_description": "Building code reference document, 100-page sample, includes Chapter 3 Building Planning.",
      "document_type": "pdf",
      "profile": "building_code",
      "source_status": "ready",
      "collections": ["default"],
      "available_lookup_modes": ["semantic_search", "section_fetch", "chunk_fetch", "table_fetch", "figure_fetch"]
    }
  ]
}
```

### Document table of contents response

When an agent asks for a document's TOC/hierarchy summary, return a bounded tree or flattened list:

```json
{
  "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
  "document_title": "2021 International Residential Code",
  "toc_depth": 3,
  "nodes": [
    {
      "hierarchy_node_id": "R301",
      "label": "R301",
      "title": "DESIGN CRITERIA",
      "page_start": 60,
      "page_end": 92,
      "token_estimate": 17000,
      "children_available": true,
      "summary": "Structural design criteria including wind, seismic, snow, flood, live load, dead load, and deflection requirements."
    },
    {
      "hierarchy_node_id": "R303.10",
      "label": "R303.10",
      "title": "Required heating",
      "page_start": 99,
      "page_end": 99,
      "token_estimate": 254,
      "children_available": false,
      "summary": "Heating facilities required to maintain not less than 68°F in habitable rooms under specified winter design temperature conditions."
    }
  ]
}
```

For large documents, return a top-level index first and let the agent request child nodes on demand. Do not inject a full giant TOC into every answer prompt by default.

### Agent planning flow

1. Agent receives compact authorized document index.
2. Agent rewrites/clarifies the question if needed.
3. Agent chooses one or more retrieval modes:
   - semantic search;
   - direct section fetch;
   - direct table/figure fetch;
   - page fetch;
   - hybrid retrieval.
4. Backend executes only authorized retrieval/fetch operations.
5. Answer model receives retrieved/fetched context with metadata.
6. Backend verifies citations and returns only cited sources.

## Fallback Strategy for Non-Hierarchical Documents

Not every document has a useful hierarchy. The system should support profiles:

- `building_code`: hierarchy-first, section-aware chunks, table/figure extraction.
- `policy`: heading-aware chunks, effective dates, policy owner, revision metadata.
- `manual`: chapter/section chunks, procedure steps, figures/tables.
- `report`: section headings, executive summary, tables/charts.
- `presentation`: slide-level chunks with image summaries and speaker notes.
- `plain_document`: page or fixed-size chunks with page metadata.

Fallback rules:

1. If no hierarchy is detected, use page-level chunks for PDFs where page citations matter.
2. If page-level chunks are too large, split by estimated tokens with page metadata preserved.
3. If page boundaries are not meaningful, use fixed-size chunks with overlap.
4. Always preserve source document id, page range when available, and raw source location.

## Near-Term Implementation Recommendation

Do not switch directly to deepest-leaf chunking.

Recommended next sequence:

1. Add `hierarchy_index` / `section_index` generation to current section chunks.
2. Store the index in chunk `custom_metadata` and a sidecar S3 artifact.
3. Update citation normalization to record quote offsets and resolve `cited_hierarchy_node`.
4. Extend the RAG API source model to return `cited_hierarchy_node` and `evidence_spans`.
5. Re-ingest the 100-page IRC sample.
6. Test the heating question and confirm the source can display `R303.10 Required heating`.
7. Implement adaptive hierarchy chunk selection and use `R301` as the main validation case.
8. Add direct fetch by `hierarchy_node_id` through the Knowledge API.
9. Add compact document index / TOC responses for agent planning.

This sequence gives us citation precision quickly while preserving current retrieval behavior long enough to compare before/after results.

## Open Design Questions

1. Should the canonical hierarchy artifact be stored only in S3 or also normalized into Aurora?
   - Recommendation: store full hierarchy JSON in S3 first; store searchable summary fields in Aurora. Normalize later if direct lookup/query performance requires it.
2. Should `hierarchy_index` be included in Bedrock sidecars?
   - Recommendation: include compact display/filter fields in Bedrock sidecars, but keep large nested indexes in app catalog/S3 metadata to avoid retrieval noise.
3. Should table and figure summaries be included in the text section chunk that contains them?
   - Recommendation: include a short pointer/caption in nearby text chunks, but index table/figure summaries separately with their own chunk ids.
4. Should the answer model be asked to cite the deepest section directly?
   - Recommendation: it can be prompted to use section labels when obvious, but backend deterministic quote resolution should be the source of truth.
5. What chunk size thresholds should we start with?
   - Recommendation: start with `target_min_tokens=300`, `target_max_tokens=3000`, and `hard_max_tokens=7000`, then tune from IRC plus at least one non-code PDF.
