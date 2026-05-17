# Hierarchical Chunking Detailed Implementation Plan

Created: 2026-05-16  
Status: Final implementation plan  
Supersedes draft: `hierachical_chunking_refinement.md`  
Related:

- `2026-05-05_knowledge_base_metadata_strategy_research.md`
- `2026-05-09_metadata_implementation_plan.md`
- `2026-05-16_hierarchical_chunking_strategy_options.md`
- `2026-05-16_subsection_citation_resolution_plan.md`

## Goal

Implement a hierarchy-aware ingestion and retrieval path that supports:

- strong semantic retrieval;
- precise citations to the deepest useful document node;
- direct fetch by section, chunk, table, figure, or page;
- agent-facing document index and table-of-contents context;
- future support for non-code documents, multimodal documents, and frontend document upload.

The first concrete validation target is the first-100-pages IRC sample, but the implementation must remain general enough for policies, manuals, reports, contracts, presentations, and internal company documents.

## Non-goals for this implementation

- Do not create a fully normalized Aurora hierarchy table in the first version.
- Do not build frontend document upload yet.
- Do not build a PDF viewer with highlighted source text yet.
- Do not require Sonnet for every document once deterministic hierarchy generation is reliable.
- Do not use chunk boundaries as the only citation boundary.

## Core Design

Separate these four concepts:

| Concept | Purpose | Stored where |
| --- | --- | --- |
| `document` | Original uploaded source and catalog row. | `document_catalog`, `kb_raw/` |
| `hierarchy_node` | Logical node such as chapter, section, subsection, table, figure, or page. | `kb_raw/{document_id}/document_hierarchy.json` |
| `retrieval_chunk` | Text or summary artifact embedded into Bedrock Knowledge Bases. | `document_chunks`, `kb_index/` |
| `evidence_span` | Verified quote/span used in an answer. | RAG response + chat history source payload |

Retrieval chunks should optimize semantic retrieval and context size. Citation precision should come from verified quote offsets plus hierarchy metadata.

## Current Codebase State

### Ingestion entrypoint

Current file: `src/document_ingestion_lambda/main.py`

- `main(event)` reads hydrated `APP_CONFIG_PATH` with `utils.config.read_config`.
- `DocumentIngestionService` receives the `ingestion` config dictionary directly.
- `DocumentIngestionService.process_object()`:
  - validates the object is under `landing_prefix`;
  - resolves `tenant_id` and `collection_id` from the S3 landing key;
  - starts a `document_ingestion_jobs` row through `DocumentCatalogStore`;
  - routes PDFs to `PdfProcessor`;
  - writes `document_catalog` and `document_chunks` rows;
  - completes or fails the ingestion job.
- Non-PDF branches already exist and raise `NotImplementedError`.

Keep this entrypoint style. Do not add an ingestion-wide config wrapper class. New code should read values from the existing `ingestion_config` dictionary.

### PDF processor

Current file: `src/document_ingestion_lambda/file_type_processors/pdf.py`

`PdfProcessor.process()` currently does this:

1. Download landing PDF to local temp.
2. Run `scan_pdf()` to collect basic text availability and PDF metadata.
3. Run `_run_triage()`.
4. Generate `document_id`.
5. Copy raw source into `kb_raw/{document_id}/original/`.
6. Extract text through `_extract_text()`.
7. Build chunks through `_build_chunk_artifacts()`.
8. Run table and figure extraction through `_extract_assets()`.
9. Write chunk markdown and Bedrock sidecars to S3.
10. Write `document_metadata.json`.
11. Return catalog rows to the ingestion service.

This is the right ownership boundary. The processor should continue to own PDF orchestration, but the steps need to be reordered and split into smaller modules.

### Current triage

Current file: `src/document_ingestion_lambda/triage.py`

The current triage code returns document-level decisions:

- `ocr_required`
- `has_hierarchy`
- `hierarchy_kind`
- `toc_pages`
- `table_pages`
- `figure_pages`

This conflicts with the refined design. The multimodal triage model should observe pages and extract structured page-level facts. It should not decide whether OCR is required and should not be the authoritative hierarchy generator.

### Current section chunking

Current file: `src/document_ingestion_lambda/file_type_processors/section_chunking.py`

The current code:

- detects section boundaries with regular expressions;
- skips pages flagged as TOC;
- emits one chunk per detected section;
- falls back to page chunks when triage confidence is too low;
- stores only the section-level boundary in chunk metadata.

This is a useful starting point, but it needs to become hierarchy-aware. It should stop treating section chunking as the final representation of document structure.

### Table and figure extraction

Current files:

- `src/document_ingestion_lambda/file_type_processors/table_extraction.py`
- `src/document_ingestion_lambda/file_type_processors/figure_extraction.py`

The code already creates separate table and figure chunks/assets using Textract and Nova Lite summaries. It does not yet link table/figure chunks to a parent `hierarchy_node_id`.

### Catalog and read APIs

Current files:

- `src/document_ingestion_lambda/catalog_store.py`
- `src/library_api_lambda/main.py`
- `src/knowledge_api_lambda/main.py`
- `src/knowledge/chunk_read.py`
- `src/knowledge/document_read.py`
- `src/knowledge/artifact_fetch.py`

The catalog can already store flexible JSON metadata:

- document extra metadata is stored in `document_catalog.metadata`;
- chunk extra metadata is stored in `document_chunks.metadata`;
- chunk content is fetched from `kb_index/` through the Knowledge API;
- raw document presigned URLs are already available through `/documents/{document_id}/raw-url`.

For v1, use this flexible metadata and S3 hierarchy artifacts instead of adding a new hierarchy table.

### RAG retrieval and citation processing

Current files:

- `src/knowledge/vector_search.py`
- `src/rag_lambda/graph/retrieval.py`
- `src/rag_lambda/citations.py`
- `src/rag_lambda/api/models.py`

Current retrieval:

- calls Bedrock Knowledge Bases through `knowledge.vector_search.search`;
- flattens Bedrock `source_metadata`;
- formats all available metadata into `<metadata>` blocks;
- sends chunk text to the answer LLM.

Current citations:

- parses `<cite source="N">QUOTE</cite>`;
- verifies the quote exists in the retrieved chunk after whitespace and soft-wrap normalization;
- supports fallback `<[N]>` markers;
- renumbers citations by first use;
- returns only sources cited in the answer.

The missing pieces are:

- source enrichment from the app-owned chunk catalog;
- quote offset capture;
- hierarchy-node resolution from verified quote offsets;
- response models for `cited_hierarchy_node` and `evidence_spans`.

## Target S3 Layout

Keep the current prefixes and add hierarchy artifacts under `kb_raw/`.

```text
kb_landing/{tenant_id}/{collection_id}/{filename}

kb_raw/{document_id}/original/{filename}
kb_raw/{document_id}/document_metadata.json
kb_raw/{document_id}/document_hierarchy.json
kb_raw/{document_id}/hierarchy_indexes/{chunk_id}.hierarchy_index.json
kb_raw/{document_id}/triage/page_observations.json
kb_raw/{document_id}/triage/text_quality_report.json

kb_index/{document_id}/sections/{chunk_id}.md
kb_index/{document_id}/sections/{chunk_id}.md.metadata.json
kb_index/{document_id}/pages/{chunk_id}.md
kb_index/{document_id}/pages/{chunk_id}.md.metadata.json
kb_index/{document_id}/tables/{chunk_id}.summary.md
kb_index/{document_id}/tables/{chunk_id}.summary.md.metadata.json
kb_index/{document_id}/figures/{chunk_id}.summary.md
kb_index/{document_id}/figures/{chunk_id}.summary.md.metadata.json

kb_artifacts/{document_id}/tables/{chunk_id}.json
kb_artifacts/{document_id}/figures/{chunk_id}.png
```

## Target Data Contracts

### Page triage observation

Replace decision-oriented triage output with page observations.

```python
@dataclass
class HeadingCandidate:
    label: str
    title: str | None
    raw_text: str
    heading_pattern_type: str | None
    confidence: float | None

@dataclass
class PageTableObservation:
    caption: str | None
    title: str | None
    description: str | None
    bbox: dict[str, float] | None
    confidence: float | None

@dataclass
class PageFigureObservation:
    caption: str | None
    title: str | None
    description: str | None
    bbox: dict[str, float] | None
    confidence: float | None

@dataclass
class PageTriageObservation:
    page_number: int
    raw_visible_text: str
    likely_document_type: str | None
    heading_candidates: list[HeadingCandidate]
    appears_to_be_toc_page: bool
    toc_entries: list[dict[str, Any]]
    tables: list[PageTableObservation]
    figures: list[PageFigureObservation]
    notes: list[str]
    parse_error: bool = False
```

The model should not compare `raw_visible_text` to embedded PDF text. The backend performs that comparison.

### Triage result

```python
@dataclass
class PdfTriageResult:
    triage_status: str  # completed | skipped | failed
    page_observations: list[PageTriageObservation]
    sampled_pages: list[int]
    full_page_triage_enabled: bool
    warnings: list[str]
```

Keep `PdfTriageResult.skipped()` and `PdfTriageResult.failed()` helpers so `PdfProcessor` can degrade gracefully.

### Text quality report

Add a deterministic text-quality result.

```python
@dataclass
class PageTextQuality:
    page_number: int
    embedded_char_count: int
    visible_char_count: int
    token_overlap_ratio: float | None
    mismatch_ratio: float | None
    suspicious: bool
    reasons: list[str]

@dataclass
class TextQualityReport:
    triaged_page_count: int
    full_page_triage_enabled: bool
    suspicious_page_count: int
    suspicious_page_ratio: float
    pages_over_text_mismatch_threshold: list[int]
    ocr_recommended: bool
    textract_recommended: bool
    selected_pages_for_ocr: list[int]
    reason: str
    page_reports: list[PageTextQuality]
```

The initial comparison can use normalized token overlap. Do not require perfect exact text matching from Nova Lite. Treat Nova Lite visible text as a signal that large visible regions may be missing from the embedded text layer.

### Parser profile

Add a known parser-profile registry. Do not let the model return arbitrary regular expressions that the backend executes.

```python
@dataclass(frozen=True)
class ParserProfile:
    profile_id: str
    description: str
    heading_pattern_types: tuple[str, ...]
    default_heading_pattern_type: str
    level_rules: dict[str, Any]
    title_cleanup_rules: dict[str, Any]
    false_positive_rules: dict[str, Any]
    toc_rules: dict[str, Any]
    chunking_defaults: dict[str, Any]
    citation_display_fields: tuple[str, ...]
```

Initial profiles:

| Profile | Purpose |
| --- | --- |
| `building_code` | IRC/IBC/NFPA-style technical code sections like `R303.10`. |
| `numbered_manual` | Manuals and policies with numeric headings like `5.4.1`. |
| `prose_report` | Reports with prose headings and weak numbering. |
| `presentation` | Slide/page-first documents. |
| `plain_document` | Fallback profile for weak or absent hierarchy. |

Sonnet may suggest a new profile when the data does not fit known profiles, but the implementation should store that suggestion in processing metadata rather than auto-creating an active parser profile.

### Document hierarchy

```python
@dataclass
class HierarchyNode:
    hierarchy_node_id: str
    label: str | None
    title: str
    node_type: str  # document | chapter | section | subsection | table | figure | page
    parent_id: str | None
    children: list[str]
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    char_start: int | None
    char_end: int | None
    token_estimate: int | None
    source: str  # deterministic | sonnet_corrected | table_extraction | figure_extraction
    evidence: list[dict[str, Any]]

@dataclass
class DocumentHierarchy:
    document_id: str
    schema_version: str
    profile: str
    heading_pattern_type: str
    source_location: str
    root_nodes: list[str]
    nodes: list[HierarchyNode]
    generation: dict[str, Any]
```

If Sonnet review is enabled and returns a valid corrected hierarchy, mark nodes with `source="sonnet_corrected"` where appropriate and treat the corrected hierarchy as authoritative.

### Retrieval chunk hierarchy index

Each retrieval chunk should include a compact hierarchy index, stored either inline in catalog metadata or in an S3 sidecar.

```json
{
  "hierarchy_node_id": "R303",
  "hierarchy_index_location": "s3://bucket/kb_raw/document_id/hierarchy_indexes/chunk_id.hierarchy_index.json",
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
```

Use inline indexes for small chunks. Store larger indexes in S3 and keep only `hierarchy_index_location` plus summary scalar fields in Bedrock sidecars.

### Citation response additions

Extend `src/rag_lambda/api/models.py`.

```python
class CitedHierarchyNode(BaseModel):
    hierarchy_node_id: str
    label: Optional[str] = None
    title: str
    node_type: Optional[str] = None
    heading_path: List[str] = Field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None

class EvidenceSpan(BaseModel):
    quote_index: int
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    hierarchy_node_id: Optional[str] = None

class Source(BaseModel):
    # existing fields
    cited_hierarchy_node: Optional[CitedHierarchyNode] = None
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
```

Keep `cited_quotes` and `quote_verified` for compatibility.

## Config Changes

Update every environment config that supports ingestion, starting with `config/sandbox/app_config.yaml`.

Retain existing values where they still apply, but replace decision-oriented triage settings with observation-oriented settings.

```yaml
ingestion:
  # ---- Multimodal page triage ----
  pdf_triage_enabled: true
  pdf_triage_model_id: "amazon.nova-lite-v1:0"
  pdf_triage_region: "us-east-1"
  pdf_triage_temperature: 0.0
  pdf_triage_max_tokens_per_page: 2048
  pdf_full_page_triage_enabled: false # If true, run triage over every page and ignore front/random samples.
  pdf_front_page_sample_count: 30 # Used only when full-page triage is false.
  pdf_random_page_sample_count: 5 # Used only when full-page triage is false.
  pdf_triage_render_scale: 2.0

  # ---- Deterministic text quality check ----
  pdf_triage_text_mismatch_threshold: 0.50 # Per-page mismatch threshold between embedded text and model visible text.
  pdf_triage_document_text_mismatch_threshold: 0.30 # Document ratio that recommends OCR/Textract.
  pdf_text_quality_min_visible_chars: 100

  # ---- OCR/Textract fallback ----
  pdf_ocr_enabled: true
  pdf_always_ocr: false
  pdf_ocr_poll_interval_seconds: 5
  pdf_ocr_max_wait_seconds: 540

  # ---- Hierarchy generation ----
  pdf_hierarchy_enabled: true
  hierarchy_generation_mode: "deterministic" # deterministic
  hierarchy_review_model_mode: "auto" # auto | always | never
  hierarchy_review_model_id: "us.anthropic.claude-sonnet-4-6"
  hierarchy_review_region: "us-east-1"
  hierarchy_review_min_confidence: 0.80
  hierarchy_review_temperature: 0.0
  hierarchy_review_max_tokens: 8192

  # ---- Adaptive hierarchy chunking ----
  pdf_hierarchy_chunking_enabled: true
  pdf_hierarchy_target_min_tokens: 300
  pdf_hierarchy_target_max_tokens: 3000
  pdf_hierarchy_hard_max_tokens: 7000
  pdf_hierarchy_merge_small_leaf_nodes: true
  pdf_hierarchy_split_large_nodes_by_children: true
  pdf_hierarchy_store_index: true
  pdf_hierarchy_inline_index_max_nodes: 40
```

Deprecate these existing keys after migrating tests and code:

- `pdf_triage_ocr_threshold`
- `pdf_triage_hierarchy_threshold`
- `pdf_section_chunking_enabled`
- `pdf_section_min_confidence`
- `pdf_section_min_boundaries`
- `pdf_section_min_chars`

Do not remove deprecated keys until code and tests no longer reference them.

## Implementation Steps

### Phase 1: Refactor multimodal triage into observation extraction

Files:

- modify `src/document_ingestion_lambda/triage.py`
- update `tests/test_document_ingestion_triage.py`
- update `src/document_ingestion_lambda/file_type_processors/pdf.py`

Implementation:

1. Replace current decision schema in `_ask_model_about_page()` with an observation schema.
2. Ask the model to return:
   - raw visible text;
   - likely document type;
   - heading candidates with label/title/raw text/confidence;
   - TOC indicators and TOC entries when visible;
   - tables with captions/titles/descriptions;
   - figures with captions/titles/descriptions;
   - notes and uncertainty.
3. Add `full_page_triage_enabled` to `TriageConfig`.
4. Update `_select_sampled_pages()`:
   - when full page triage is true, return all pages;
   - otherwise return front pages plus deterministic random sample.
5. Remove model-driven OCR aggregation from triage.
6. Remove model-driven hierarchy decision from triage.
7. Keep summary helper `triage_summary_dict()`, but make it return:
   - status;
   - sampled pages;
   - full-page flag;
   - TOC pages;
   - table pages;
   - figure pages;
   - heading pattern counts;
   - warnings.
8. Persist the full page observations to:

```text
kb_raw/{document_id}/triage/page_observations.json
```

Acceptance criteria:

- Triage can be disabled and returns skipped.
- Full-page triage ignores sample counts.
- Parse errors on one page do not fail the full document.
- Unit tests stub Bedrock responses and assert parsed observations.
- No code path reads `triage.ocr_required` or `triage.has_hierarchy` after this phase.

### Phase 2: Add deterministic text-quality comparison

Files:

- add `src/document_ingestion_lambda/file_type_processors/text_quality.py`
- update `src/document_ingestion_lambda/file_type_processors/pdf.py`
- add `tests/test_pdf_text_quality.py`

Implementation:

1. Implement normalization helpers:
   - lowercase;
   - collapse whitespace;
   - repair soft-wrap hyphens;
   - strip punctuation for token-overlap checks.
2. For each triaged page, compare:
   - embedded PDF text from `PdfTextExtractionResult`;
   - triage `raw_visible_text`.
3. Compute:
   - embedded char count;
   - visible char count;
   - token overlap ratio;
   - mismatch ratio;
   - suspicious flag;
   - reasons.
4. Document-level OCR/Textract recommendation:
   - suspicious page when mismatch exceeds `pdf_triage_text_mismatch_threshold`;
   - document suspicious when suspicious page ratio exceeds `pdf_triage_document_text_mismatch_threshold`;
   - selected page OCR recommendation for isolated suspicious pages.
5. Write the report to:

```text
kb_raw/{document_id}/triage/text_quality_report.json
```

6. Update `PdfProcessor._extract_text()`:
   - stop accepting `triage` as the OCR decision source;
   - accept `text_quality_report`;
   - run OCR/Textract when `pdf_always_ocr` is true or the report recommends it;
   - keep embedded text when OCR is not recommended.

Acceptance criteria:

- Nova Lite does not decide OCR.
- Backend can recommend OCR based on deterministic comparison.
- The report explains which pages triggered the recommendation.
- Existing OCR tests are updated to use text-quality signals.

### Phase 3: Add parser profiles

Files:

- add `src/document_ingestion_lambda/file_type_processors/parser_profiles.py`
- add `tests/test_parser_profiles.py`

Implementation:

1. Define `ParserProfile` and profile registry.
2. Include built-in profiles:
   - `building_code`;
   - `numbered_manual`;
   - `prose_report`;
   - `presentation`;
   - `plain_document`.
3. Implement profile selection:
   - prefer explicit document metadata if provided in the future;
   - otherwise infer from triage heading candidates, filename/title, and TOC signals;
   - default to `plain_document`.
4. Restrict heading parser choices to known pattern types.
5. Store selected profile and profile confidence in document processing metadata.

Parser profile fields should include:

- allowed heading pattern types;
- title cleanup rules;
- false-positive rejection rules;
- level inference rules;
- TOC detection rules;
- default chunking thresholds;
- citation display fields.

Acceptance criteria:

- IRC sample selects `building_code`.
- A weakly structured document selects `plain_document`.
- Model-suggested unknown profiles are recorded as suggestions but not executed.

### Phase 4: Build deterministic document hierarchy

Files:

- add `src/document_ingestion_lambda/file_type_processors/hierarchy.py`
- update or replace parts of `section_chunking.py`
- add `tests/test_hierarchy_builder.py`

Implementation:

1. Build hierarchy candidates from:
   - triage heading candidates;
   - embedded or OCR text;
   - TOC entries;
   - table/figure captions when available.
2. Validate candidates against the selected parser profile.
3. Reject:
   - TOC dot-leader lines;
   - headers and footers;
   - cross-reference-only lines;
   - licensing/order/footer numbers;
   - headings that appear out of page order;
   - headings that cannot be found in authoritative text unless they are table/figure-only nodes.
4. Infer parentage:
   - `R303.10` belongs under `R303`;
   - `R311.7.5.1` belongs under `R311.7.5`;
   - chapter nodes own section roots when chapter headings are present.
5. Compute:
   - page ranges;
   - document-relative char offsets;
   - token estimates;
   - heading paths;
   - hierarchy confidence;
   - conflict diagnostics.
6. Produce a `DocumentHierarchy`.
7. Validate hierarchy before it can be used:
   - all parent ids exist;
   - page ranges are valid;
   - node ids are unique;
   - child ranges are inside parent ranges when possible;
   - node order is monotonic;
   - char offsets are non-overlapping among siblings where applicable.
8. Write:

```text
kb_raw/{document_id}/document_hierarchy.json
```

9. Store this pointer in `document_catalog.metadata`, for example:

```json
{
  "hierarchy_location": "s3://bucket/kb_raw/document_id/document_hierarchy.json",
  "hierarchy_profile": "building_code",
  "hierarchy_confidence": 0.91,
  "hierarchy_source": "deterministic",
  "hierarchy_available": true
}
```

Acceptance criteria:

- IRC sample produces nodes for `R303` and `R303.10`.
- `R303.10 Required heating` is a child of `R303`.
- TOC entries are not mistaken for body sections.
- The hierarchy artifact is written even if chunking later falls back to page chunks.

### Phase 5: Add optional Sonnet hierarchy review and correction

Files:

- add `src/document_ingestion_lambda/file_type_processors/hierarchy_review.py`
- update `PdfProcessor`
- add `tests/test_hierarchy_review.py`

Implementation:

1. Add config modes:
   - `always`;
   - `auto`;
   - `never`.
2. In `auto` mode, call Sonnet when:
   - deterministic hierarchy confidence is below threshold;
   - serious conflicts exist;
   - the selected profile is unknown or weak;
   - text-quality report is ambiguous;
   - development config explicitly keeps review enabled.
3. Send Sonnet:
   - selected parser profile;
   - deterministic hierarchy candidate;
   - page triage observations;
   - text-quality report;
   - TOC findings;
   - table/figure observation summaries.
4. Ask Sonnet to return strict JSON:
   - `review_status`;
   - `confidence`;
   - `recommended_action`;
   - `corrected_hierarchy`;
   - `issues`;
   - `profile_suggestion`;
   - `textract_recommendation`.
5. Validate Sonnet output with the same backend validator used for deterministic hierarchy.
6. If valid and review mode is enabled, make the Sonnet-corrected hierarchy the source of truth.
7. If invalid, log diagnostics, store review failure metadata, and fall back to deterministic hierarchy.

Important rule:

When Sonnet review is enabled and returns a valid correction payload, the Sonnet-corrected hierarchy is authoritative. The backend still validates it before writing artifacts.

Acceptance criteria:

- Stubbed Sonnet correction can add a missing `R303.10` node.
- Invalid Sonnet output does not break ingestion.
- Processing metadata records whether deterministic or Sonnet-corrected hierarchy was used.

### Phase 6: Implement adaptive hierarchy chunk planning

Files:

- update `src/document_ingestion_lambda/file_type_processors/section_chunking.py`
- optionally add `src/document_ingestion_lambda/file_type_processors/hierarchy_chunking.py`
- update `src/document_ingestion_lambda/file_type_processors/pdf.py`
- add `tests/test_hierarchy_chunking.py`

Recommended implementation:

- Keep legacy `build_section_chunk_artifacts()` until tests are migrated.
- Add new hierarchy-first chunk builder in `hierarchy_chunking.py`.
- Have `PdfProcessor._build_chunk_artifacts()` call the hierarchy-first builder when a valid hierarchy exists.

Chunk planner rules:

1. If no reliable hierarchy exists, use current page/fixed-size fallback.
2. Estimate tokens for each hierarchy node.
3. A node between `pdf_hierarchy_target_min_tokens` and `pdf_hierarchy_target_max_tokens` is a good retrieval chunk candidate.
4. Merge small sibling nodes when configured.
5. Split large nodes by child nodes first.
6. If child splitting still exceeds `pdf_hierarchy_hard_max_tokens`, split by paragraphs with deterministic part ids.
7. Generate a hierarchy index for every retrieval chunk.
8. Tables and figures remain separate chunks but link to nearest parent hierarchy nodes.

Chunk metadata additions:

```json
{
  "chunk_type": "section",
  "section": "R303",
  "heading_path": "CHAPTER 3/R303",
  "custom_metadata": {
    "retrieval_strategy": "adaptive_hierarchy",
    "hierarchy_node_id": "R303",
    "hierarchy_profile": "building_code",
    "hierarchy_index_location": "s3://bucket/kb_raw/document_id/hierarchy_indexes/chunk_id.hierarchy_index.json",
    "hierarchy_index_inline": []
  }
}
```

Bedrock sidecar metadata:

- Add scalar hierarchy fields to `build_bedrock_sidecar_metadata()`:
  - `hierarchy_node_id`;
  - `hierarchy_profile`;
  - `parent_hierarchy_node_id`;
  - `hierarchy_path`;
  - `hierarchy_index_location`.
- Do not put large nested `hierarchy_index_inline` arrays into Bedrock sidecars.
- Keep nested indexes in Aurora JSONB and S3 artifacts.

Acceptance criteria:

- IRC `R303` can remain a retrieval chunk while `R303.10` appears in its hierarchy index.
- Large sections such as `R301` are split or grouped according to configured limits.
- Every chunk has enough metadata to resolve citations to hierarchy nodes.

### Phase 7: Link tables and figures to hierarchy nodes

Files:

- update `table_extraction.py`
- update `figure_extraction.py`
- update `PdfProcessor._extract_assets()`
- add `tests/test_table_extraction.py` coverage
- add `tests/test_figure_extraction.py` coverage

Implementation:

1. Pass the final `DocumentHierarchy` into table/figure extraction.
2. For each table or figure:
   - use page number and caption proximity to find the nearest parent hierarchy node;
   - set `parent_hierarchy_node_id`;
   - include `heading_path`;
   - include caption/title/description.
3. Add table and figure nodes to `document_hierarchy.json` when useful.
4. Keep table/figure summary chunks separate from text section chunks.
5. Include short table/figure pointers in nearby text chunks only when they improve context without bloating embeddings.

Acceptance criteria:

- A table on page 93 links to the closest section node.
- A figure/map on page 81 links to the closest section or page node.
- Table/figure chunks can still be retrieved independently.

### Phase 8: Reorder `PdfProcessor.process()`

File:

- `src/document_ingestion_lambda/file_type_processors/pdf.py`

Target order:

1. Download PDF.
2. Scan PDF for basic metadata and page count.
3. Run page triage observations if enabled.
4. Extract enough metadata to generate `document_id`.
   - Until `document_id` exists, use the landing S3 URI as the ingestion job identifier.
5. Copy raw PDF to `kb_raw/{document_id}/original/`.
6. Extract embedded PDF text.
7. Run deterministic text-quality comparison.
8. Run OCR/Textract when config and text-quality report recommend it.
9. Select parser profile.
10. Build deterministic hierarchy candidate.
11. Optionally run Sonnet review/correction.
12. Write `document_hierarchy.json`.
13. Build adaptive hierarchy chunks or fallback chunks.
14. Extract table and figure chunks/assets and link them to hierarchy.
15. Write chunk markdown, sidecar metadata, hierarchy-index artifacts, triage artifacts, and document metadata.
16. Return catalog document and chunk rows.

Processing metadata should capture every major decision:

```json
{
  "triage": {},
  "text_quality": {},
  "ocr": {},
  "parser_profile": {},
  "hierarchy": {
    "status": "complete",
    "source": "sonnet_corrected",
    "hierarchy_location": "s3://..."
  },
  "chunking": {
    "strategy": "adaptive_hierarchy",
    "chunk_count": 42
  },
  "table_extraction": {},
  "figure_extraction": {}
}
```

Acceptance criteria:

- Ingestion still succeeds when triage fails.
- Ingestion still succeeds when Sonnet review fails.
- Document metadata includes hierarchy pointers and processing diagnostics.

### Phase 9: Add source enrichment for RAG retrieval

Files:

- add `src/knowledge/source_enrichment.py`
- update `src/knowledge/chunk_read.py`
- update `src/rag_lambda/graph/retrieval.py`
- optionally update `src/knowledge_api_lambda/main.py`
- add `tests/test_source_enrichment.py`
- update `tests/test_graph_retrieval.py`

Reason:

Bedrock Knowledge Bases returns sidecar metadata, but nested hierarchy indexes should live in the app catalog/S3 to avoid Bedrock metadata bloat. The RAG answer prompt needs enriched hierarchy metadata.

Implementation:

1. Add a batch read method to `ChunkReadStore`:

```python
def get_chunks_by_ids(
    self,
    *,
    chunk_ids: list[str],
    tenant_id: str,
    access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
) -> dict[str, dict[str, Any]]:
```

2. Add `knowledge.source_enrichment.enrich_vector_results()`:
   - input: `VectorSearchResult` list, config, tenant id;
   - fetch matching `document_chunks` rows by `chunk_id`;
   - merge catalog metadata JSON into retrieval metadata;
   - preserve Bedrock score and retrieval location;
   - never widen authorization.
3. Use enrichment in `rag_lambda/graph/retrieval.py` before formatting context.
4. Keep Knowledge API `/search` basic at first, or add the same enrichment there behind a config flag.
5. If `hierarchy_index_inline` is absent and `hierarchy_index_location` is present, optionally fetch the small S3 JSON artifact if the RAG Lambda has S3 permission.

RAG prompt formatting change:

- Keep current `<source>` block.
- Add `<hierarchy_index>` only when available.

Example:

```xml
<hierarchy_index>
- R303 LIGHT, VENTILATION AND HEATING, pages 98-99, chars 0-8397
- R303.10 Required heating, page 99, chars 7889-8397
</hierarchy_index>
```

Acceptance criteria:

- The answer model sees `R303.10 Required heating` in the source block for the heating question.
- Retrieval still works if catalog enrichment is unavailable.
- No unauthorized chunk metadata is merged into the prompt.

### Phase 10: Resolve citations to hierarchy nodes

Files:

- update `src/rag_lambda/citations.py`
- update `src/rag_lambda/api/models.py`
- update `tests/test_citations_quotes.py`
- add focused tests for hierarchy resolution

Implementation:

1. Replace boolean `_quote_is_in_chunk()` with a match result:

```python
@dataclass(frozen=True)
class QuoteMatch:
    verified: bool
    char_start: int | None = None
    char_end: int | None = None
```

2. Implement normalized text with source offset mapping:
   - repair soft-wrap hyphens;
   - collapse whitespace;
   - map normalized character spans back to original chunk offsets.
3. When a `<cite>` quote verifies:
   - record the original chunk offset;
   - append `cited_quotes`;
   - append `evidence_spans`;
   - resolve deepest matching hierarchy node from `hierarchy_index_inline`.
4. If multiple quotes cite different hierarchy nodes from one source:
   - keep one `evidence_span` per quote;
   - choose `cited_hierarchy_node` as the deepest node for the first verified quote, or add a list later if frontend needs it.
5. For fallback `<[N]>` citations without quotes:
   - renumber the citation;
   - include source only if cited;
   - do not invent `evidence_spans`.
6. Keep existing first-use source renumbering behavior.

Acceptance criteria:

- The heating answer source resolves from chunk-level `R303` to `R303.10 Required heating`.
- Unverified quotes are dropped as today.
- Bare citations still work.
- Chat history can store and rehydrate sources with the new fields because source payloads are already JSON.

### Phase 11: Add Knowledge API hierarchy and direct fetch routes

Files:

- update `src/knowledge_api_lambda/main.py`
- update `src/knowledge_api_lambda/api/models.py`
- add `src/knowledge/hierarchy_read.py`
- add `src/knowledge/hierarchy_fetch.py`
- update `tests/test_knowledge_api_lambda.py`

Routes:

```text
GET /documents/{document_id}/hierarchy?depth=3
GET /documents/{document_id}/hierarchy-nodes/{hierarchy_node_id}
```

Implementation:

1. Authenticate exactly like existing routes.
2. Resolve tenant id exactly like existing routes.
3. Fetch authorized document through `DocumentReadStore`.
4. Read `hierarchy_location` from `document_catalog.metadata`.
5. Fetch hierarchy JSON from S3 with strict bucket/prefix validation:
   - expected bucket must match configured document bucket;
   - allowed prefix should be `kb_raw/`;
   - enforce a max JSON byte size.
6. For `/hierarchy`:
   - return bounded tree or flat node list;
   - apply `depth`;
   - include summary fields only.
7. For `/hierarchy-nodes/{hierarchy_node_id}`:
   - find node by id;
   - identify containing chunk from hierarchy index or chunk catalog;
   - fetch chunk content with existing `fetch_chunk_content`;
   - slice content when offsets are available;
   - return bounded content plus source metadata.
8. Keep `/chunks/{chunk_id}` and `/documents/{document_id}/raw-url` unchanged.

Response model examples:

```python
class HierarchyNodeSummary(BaseModel):
    hierarchy_node_id: str
    label: Optional[str] = None
    title: str
    node_type: str
    heading_path: List[str] = Field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    token_estimate: Optional[int] = None
    children_available: bool = False

class GetDocumentHierarchyResponse(BaseModel):
    document_id: str
    document_title: str
    profile: Optional[str] = None
    nodes: List[HierarchyNodeSummary]

class GetHierarchyNodeResponse(BaseModel):
    document_id: str
    hierarchy_node_id: str
    label: Optional[str] = None
    title: str
    heading_path: List[str] = Field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    containing_chunk_id: Optional[str] = None
    content_type: str
    content: str
    source_location: str
```

Acceptance criteria:

- Unauthorized documents return 404 like existing routes.
- The frontend can request a hierarchy summary for Library details.
- Agents can request direct section content without semantic search.

### Phase 12: Update Library API document index metadata

Files:

- update `src/library_api_lambda/api/models.py`
- update `src/library_api_lambda/catalog_read_store.py` only if query columns need changing
- update frontend later after backend is stable

Implementation:

The current Library API already returns `metadata`. For v1, avoid schema churn and expose hierarchy-related fields in `metadata`:

```json
{
  "hierarchy_available": true,
  "hierarchy_profile": "building_code",
  "hierarchy_location": "s3://...",
  "available_lookup_modes": [
    "semantic_search",
    "chunk_fetch",
    "hierarchy_fetch",
    "raw_document_url"
  ]
}
```

Later, if frontend usage stabilizes, promote selected fields to typed `CatalogDocument` properties.

Acceptance criteria:

- The Library view can identify which documents have hierarchy/TOC available.
- Existing document list behavior does not break.

### Phase 13: Update answer prompt guidance

Files:

- update `src/rag_lambda/graph/prompts.py`
- update `tests/test_graph_prompts.py`

Implementation:

1. Explain that sources may include:
   - metadata;
   - hierarchy index;
   - chunk text.
2. Instruct the model to cite using existing quote-carrying format:

```xml
<cite source="N">exact quote from source chunk</cite>
```

3. Instruct it not to invent section labels. It can mention labels that appear in metadata or source text.
4. Keep backend citation normalization as source of truth.

Acceptance criteria:

- Prompt renders with hierarchy index when present.
- Model output remains compatible with existing citation parser.

## Detailed PDF Ingestion Flow

The final PDF flow should look like this:

```text
S3 landing object
  -> DocumentIngestionService
  -> PdfProcessor
    -> scan_pdf()
    -> multimodal_triage()
    -> generate document_id after scan/triage/basic metadata
    -> copy raw PDF to kb_raw
    -> extract embedded text
    -> compare text quality
    -> run OCR/Textract if recommended
    -> select parser profile
    -> build deterministic hierarchy
    -> optional Sonnet review/correction
    -> validate final hierarchy
    -> write document_hierarchy.json
    -> plan adaptive retrieval chunks
    -> build text chunk artifacts + hierarchy indexes
    -> extract table/figure chunks and link to hierarchy
    -> write S3 artifacts and sidecars
    -> write document metadata
    -> return document/chunk catalog rows
```

## Detailed RAG Retrieval Flow

```text
user question
  -> retrieve_node
    -> Bedrock Knowledge Base search
    -> catalog source enrichment by chunk_id
    -> prompt context with metadata + hierarchy_index + chunk text
  -> answer node
    -> model emits quote-carrying citations
  -> citation normalization
    -> verify quote
    -> record quote offset
    -> resolve deepest hierarchy_node
    -> return only cited sources
  -> ChatResponse + persisted chat history
```

## Detailed Direct Fetch Flow

```text
frontend or agent asks for document hierarchy / node
  -> Knowledge API
    -> authenticate
    -> authorize through document_catalog
    -> fetch hierarchy artifact from kb_raw
    -> fetch containing chunk or raw URL when needed
    -> return bounded response
```

## Test Plan

Use the repo conda environment for tests.

```bash
conda run -n chat-template-env pytest tests/test_document_ingestion_triage.py
conda run -n chat-template-env pytest tests/test_section_chunking.py
conda run -n chat-template-env pytest tests/test_graph_retrieval.py
conda run -n chat-template-env pytest tests/test_citations_quotes.py
conda run -n chat-template-env pytest tests/test_knowledge_api_lambda.py
```

Add or update tests:

| Area | Test file |
| --- | --- |
| Page triage observations and full-page selection | `tests/test_document_ingestion_triage.py` |
| Text-quality mismatch thresholds | `tests/test_pdf_text_quality.py` |
| Parser profile selection | `tests/test_parser_profiles.py` |
| Deterministic hierarchy builder | `tests/test_hierarchy_builder.py` |
| Sonnet review validation/fallback | `tests/test_hierarchy_review.py` |
| Adaptive chunk planning | `tests/test_hierarchy_chunking.py` |
| Table/figure hierarchy linking | `tests/test_table_extraction.py`, `tests/test_figure_extraction.py` |
| Source enrichment | `tests/test_source_enrichment.py` |
| Citation offset and hierarchy resolution | `tests/test_citations_quotes.py` |
| Knowledge API hierarchy routes | `tests/test_knowledge_api_lambda.py` |
| Prompt formatting | `tests/test_graph_prompts.py`, `tests/test_graph_retrieval.py` |

## Sandbox Validation Plan

After implementation:

1. Hydrate sandbox config.
2. Deploy with the sandbox AWS profile:

```bash
AWS_PROFILE=adte_sandbox_management_account ./scripts/deploy/deploy_document_ingestion_lambda.sh sandbox --tag latest
AWS_PROFILE=adte_sandbox_management_account ./scripts/deploy/deploy_rag_lambda.sh sandbox --tag latest
AWS_PROFILE=adte_sandbox_management_account ./scripts/deploy/deploy_knowledge_api_lambda.sh sandbox --tag latest
AWS_PROFILE=adte_sandbox_management_account ./scripts/deploy/deploy_library_api_lambda.sh sandbox --tag latest
```

3. Upload the first-100-pages IRC sample to `kb_landing/sandbox/default/`.
4. Confirm ingestion writes:
   - `document_hierarchy.json`;
   - page observations;
   - text-quality report;
   - hierarchy indexes;
   - updated `document_catalog` row;
   - updated `document_chunks` rows.
5. Start Bedrock KB sync if not automatically triggered by the current deployment path.
6. Ask through the frontend:
   - `How warm do you need to keep dwelling areas?`
7. Confirm:
   - retrieved context contains `R303.10 Required heating`;
   - answer cites the 68 deg F text;
   - response source has `cited_hierarchy_node.hierarchy_node_id == "R303.10"`;
   - source card can display page 99 and `R303.10 Required heating`.
8. Test a larger section such as `R301` to validate chunk splitting.
9. Test the Knowledge API hierarchy route for the ingested document.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Nova Lite visible text is incomplete or not exact. | Use it only for drift detection and page observations, never as authoritative text. |
| Sonnet returns invalid hierarchy corrections. | Validate corrected hierarchy before accepting it; fall back to deterministic hierarchy. |
| Bedrock sidecar metadata cannot carry large nested indexes cleanly. | Store nested hierarchy indexes in Aurora JSONB and S3; put only scalar pointers in Bedrock sidecars. |
| Chunk char offsets drift after markdown headers are added. | Define offsets relative to the exact chunk body used for citation verification, not the markdown header. |
| OCR/Textract adds latency and cost. | Run only when deterministic text-quality report recommends it, except when explicitly forced by config. |
| Direct hierarchy fetch exposes unauthorized data. | Always authorize through catalog rows before fetching S3 artifacts. |
| Large documents create huge hierarchy artifacts. | Enforce max artifact sizes and bounded API responses; return child nodes on demand. |
| Parser profiles become too model-dependent. | Keep known parser profiles in code/config; store model profile suggestions for human review. |

## Recommended Implementation Order

1. Refactor triage to page observations.
2. Add text-quality comparison.
3. Add parser profiles.
4. Add deterministic hierarchy builder and artifact writer.
5. Add optional Sonnet hierarchy review/correction.
6. Add adaptive hierarchy chunking and hierarchy indexes.
7. Link tables and figures to hierarchy.
8. Add RAG source enrichment.
9. Add citation offset and hierarchy-node resolution.
10. Add Knowledge API hierarchy/direct-fetch routes.
11. Update Library metadata and frontend-facing docs.
12. Run sandbox end-to-end validation.

This order preserves a working ingestion path throughout the change. The first useful checkpoint is after steps 1 through 6: the IRC sample should ingest into hierarchy-aware chunks even before RAG citation resolution is updated.

## Documentation Updates After Implementation

Update these docs once the code lands:

- `docs/frontend_interface.md`
  - Add `cited_hierarchy_node`, `evidence_spans`, hierarchy routes, and Library metadata fields.
- `docs/aws_naming_convention.md`
  - Add new S3 artifact locations if naming conventions need examples.
- `docs/development_plans/2026-05-09_metadata_implementation_plan.md`
  - Mark implemented phases and link to this final plan.
- `docs/working_files/`
  - Put ingestion test reports and debugging notes here, not in permanent development plans.
- `README.md`
  - Add high-level summary of ingestion/retrieval capabilities after the implementation is stable.

## Definition of Done

The implementation is complete when:

- the 100-page IRC sample ingests successfully in sandbox;
- `document_hierarchy.json` is written and references `R303.10`;
- retrieval context for the heating question includes hierarchy metadata;
- the answer response cites only used sources;
- the cited source resolves to `R303.10 Required heating`;
- Knowledge API can return the document hierarchy and direct node content after auth;
- tests cover triage, hierarchy, chunking, enrichment, citation resolution, and Knowledge API routes;
- frontend-facing documentation describes the new response shape.
