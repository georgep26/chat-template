# Knowledge Base Metadata Strategy Research

Created: 2026-05-05  
Last updated: 2026-05-09  
Status: Research and option analysis; see `2026-05-09_metadata_implementation_plan.md` for the chosen implementation path.  
Scope: Bedrock Knowledge Bases document metadata strategy research for the `chat-template` RAG backend.

Implementation note: Since this research was first written, the RAG Lambda now passes retrieved source metadata into the answer prompt, exposes metadata for cited sources in `ChatResponse.sources`, and uses deterministic citation normalization so only cited sources are returned and persisted with the visible assistant turn. The remaining major work is ingestion-side metadata generation, validation, and richer retrieval filtering.

## Goals

Build a metadata strategy that improves retrieval quality, filtering, source display, and future data authorization without overcomplicating the current ingestion path.

The near-term target is to keep using Bedrock Knowledge Bases with S3 data sources and Aurora/RDS pgvector, while adding a repeatable metadata layer around source documents. The long-term target is to support richer document and chunk metadata when we need page-aware citations, section-aware filtering, or automated metadata extraction from document contents.

## Current Repo State

The current Knowledge Base infrastructure is defined in `infra/resources/knowledge_base_template.yaml`.

Current characteristics:

- Bedrock Knowledge Base storage is Aurora PostgreSQL/RDS with pgvector.
- Source documents come from an S3 data source.
- The data source uses fixed-size chunking with `MaxTokens: 512` and `OverlapPercentage: 20`.
- The RDS field mapping stores metadata in the `metadata` JSON/JSONB field.
- `src/rag_lambda/main.py` uses `src/rag_lambda/graph/retrieval.py` as the active retrieval path.
- `src/rag_lambda/graph/retrieval.py` supports metadata filters from `retrieval_filters`, but only builds equality filters.
- `src/rag_lambda/graph/retrieval.py` now formats retrieved chunks as numbered source blocks that include all returned `doc.metadata` values plus the chunk text before the answer LLM is invoked.
- Graph source dictionaries now preserve `retrieval_source_number`, `document_id`, `source_type`, `score`, `chunk`, and full `metadata`.
- `src/rag_lambda/graph/prompts.py` instructs the answer LLM to use source metadata for grounding and to cite source blocks with internal sentinel markers like `<[1]>`.
- `src/rag_lambda/citations.py` normalizes sentinel citations after answer generation, renumbers cited sources by first use, and filters out unused retrieved sources.
- `src/rag_lambda/api/models.py` now exposes cited source fields: `citation_number`, `document_id`, `source_type`, `score`, `chunk`, and `metadata`.
- `docs/frontend_interface.md` and the frontend source mapping now expect cited sources only and can display `citation_number` plus optional source metadata.
- The older `src/rag_lambda/retrieval.py` helper also preserves metadata dynamically, but it is still not the active path used by `main.py`.

Important implication: the active runtime path no longer drops metadata for cited sources. The next bottleneck is metadata quality at ingestion time. If Bedrock only returns sparse file/source metadata, the frontend and LLM will still have sparse source context even though the backend can now preserve and expose richer fields.

## Recent Repo Changes Since 2026-05-05

Recent git history and current branch work added several foundations that affect this research:

- Backend-generated UUID conversations, conversation ownership checks, and `request_id` support now make chat sessions durable and safer to resume.
- `chat_turn_events` and `TraceCollector` now persist backend trace events separately from visible chat history.
- The answer node now receives prior visible conversation history, so follow-up answers can use previous user/assistant turns.
- The answer prompt can be debug-logged locally with the fully formatted prompt, including source metadata and conversation history.
- Retrieval context now includes metadata-rich numbered source blocks rather than chunk text alone.
- Answer citations now use model-facing sentinel markers (`<[N]>`) and deterministic post-processing converts them to user-facing `[N]` citations.
- The API returns only cited sources. Retrieved-but-unused chunks no longer appear in `ChatResponse.sources` and their ids are not persisted as visible turn source ids.
- The frontend source UI has been updated to display the backend-provided citation number and optional metadata for any number of returned cited sources.

These changes do not replace the metadata ingestion research. They make the runtime ready to benefit from better `.metadata.json` sidecars and future chunk-level metadata.

## Design Principles for the Next Phases

1. Use a small standard metadata core for every file, regardless of document type.
2. Allow arbitrary custom metadata fields, but validate field names, value types, size, and safety.
3. Separate file-level metadata from chunk-level metadata. File-level sidecars are the first ingestion mechanism; chunk-level metadata becomes necessary for precise page/section citations and document highlighting.
4. Treat metadata as both retrieval infrastructure and user-facing citation context.
5. Preserve enough source location data now to support secure raw-document viewing later.
6. Make the ingestion pipeline profile-aware, not hardcoded to building code documents.
7. Keep table-of-contents/catalog generation in the ingestion pipeline so the agent can plan retrieval before semantic search.
8. Build early phases so they can support multiple document sets, multiple data sources, and eventually multiple knowledge bases.
9. Design the ingestion workflow so a later frontend upload path can reuse the same metadata validation, catalog update, and Knowledge Base sync steps.

## Standard and Flexible Metadata Model

Every source file should have a standard metadata core. This gives the RAG backend, frontend, ingestion tools, and future authorization layer a stable contract.

### Standard file-level fields

Use these canonical field names in sidecars and normalized source metadata:

| Field | Type | Required | Level | Purpose |
| --- | --- | --- | --- | --- |
| `metadata_schema_version` | string | Yes | file | Allows future metadata migrations. |
| `source_id` | string | Yes | file | Stable project-level source identifier, preferably derived from S3 key or content hash. |
| `filename` | string | Yes | file | Original filename. |
| `uploaded_at` | string | Yes | file | ISO date/time when the source was uploaded or registered. |
| `location` | string | Yes | file | Canonical source URI, usually `s3://bucket/key`. |
| `document_type` | string | Yes | file | Broad type such as `building_code`, `zoning_code`, `policy`, `manual`, `presentation`, `report`, `faq`, or `unknown`. |
| `source_status` | string | Yes | file | `current`, `superseded`, `draft`, `archived`, or `unknown`. |
| `access_tier` | string | Yes | file | `public`, `internal`, `restricted`; future authorization hook. |
| `ingested_at` | string | No | file | ISO date/time when this version was ingested into a knowledge base. |

Notes:

- The user-facing phrase "upload date" maps to canonical `uploaded_at`.
- `location` should be retained even if Bedrock also returns a location object. It is the app's canonical pointer to the raw source.
- `source_uri` can be accepted as a legacy/alias field, but `location` should be the canonical metadata key going forward.

### Flexible custom fields

The metadata strategy should support arbitrary fields such as:

- `page_number`
- `section`
- `chapter`
- `version_number`
- `publish_date`
- `jurisdiction`
- `code_family`
- `edition_year`
- `custom_metadata_field`

Recommended rule: arbitrary fields are allowed if they pass validation:

- snake_case key name;
- scalar, list of scalars, or small JSON object;
- no secrets, account IDs, ARNs, JWTs, or credentials;
- field value remains inside Bedrock sidecar size limits;
- value type is stable for that field across documents;
- optional registration in a metadata field registry if the field is used for filters or frontend display.

### Metadata field registry

Add a field registry before the metadata generator becomes too flexible. This can start as a checked-in YAML/JSON file:

```yaml
fields:
  page_number:
    type: number
    level: chunk
    display: true
    filterable: true
  section:
    type: string
    level: chunk
    display: true
    filterable: true
  version_number:
    type: string
    level: file
    display: true
    filterable: true
  custom_metadata_field:
    type: string
    level: file
    display: false
    filterable: false
```

This registry prevents unbounded metadata sprawl while still allowing each deployment to add domain-specific fields.

## Agentic RAG Features This Metadata Strategy Should Enable

The metadata plan should lay foundations for several future capabilities:

- source cards that show chapter, section, page, filename, and other relevant metadata;
- citation hover previews that show the cited chunk and highlighted supporting text;
- source click-through into a secure raw-document viewer at the relevant page/section;
- adaptive chunking by document type, such as section-based chunking for building codes;
- OCR or multimodal parsing when documents contain scanned text, charts, figures, images, or tables;
- retrieval planning from a generated catalog/table of contents before semantic search;
- hybrid retrieval where the agent can combine semantic search with direct document/section lookup;
- multiple knowledge bases or data sources with a routing step that chooses where to search;
- data authorization filters derived from user identity and metadata.
- frontend drag-and-drop document upload that stores user documents in S3 and triggers a controlled ingestion workflow.
- frontend Library view that lists currently available documents and organizes them by extracted metadata.

## Bedrock Metadata Research Summary

### What Bedrock returns at query time

The Bedrock `Retrieve` response returns an array of retrieval results. Each result can include:

- `content`: the retrieved source chunk;
- `metadata`: metadata attributes as key/value JSON;
- `location`: the URI or URL of the source document;
- `score`: retrieval relevance score.

For S3 sources, the source file URI is available through the result `location`. Bedrock can also return metadata fields from associated `.metadata.json` files.

### Bedrock-managed metadata fields

Bedrock can provide some internal metadata fields, but the exact fields depend on data source type, parser, vector store, modality, and ingestion configuration.

Fields observed or documented across Bedrock KB modes include:

- `x-amz-bedrock-kb-source-uri`
- `x-amz-bedrock-kb-data-source-id`
- `x-amz-bedrock-kb-chunk-id`
- `x-amz-bedrock-kb-source-file-mime-type`
- `x-amz-bedrock-kb-source-file-modality`
- multimodal timestamp fields for audio/video chunks
- `x-amz-bedrock-kb-document-page-number` for specific PDF/vector-store cases

For page numbers, the AWS docs are narrower than the general expectation: for PDF documents, Bedrock documents page number generation specifically when using Amazon OpenSearch Serverless as the vector store, and page numbers are not supported when no chunking is selected. This repo currently uses Aurora/RDS pgvector, so we should not assume automatic page numbers will be available or reliable in our current architecture.

### File-level sidecar metadata

For S3 data sources, Bedrock supports sidecar metadata files:

- Source file: `fileName.extension`
- Metadata file: `fileName.extension.metadata.json`
- The metadata file must be stored in the same S3 folder/location as the source file.
- The metadata file must be under the Bedrock metadata file size limit, currently documented as 10 KB.
- Metadata values can be used for retrieval filters.
- For Aurora/RDS vector stores, AWS recommends storing metadata in a single custom metadata column and indexing that column. Our template already maps metadata to a single `metadata` field, which aligns with this guidance.

Sidecar metadata is best suited for document-level metadata: document type, jurisdiction, code family, edition, effective date, owner, access tier, source system, and similar values that apply to the whole file.

### CSV-specific metadata

CSV files have a different metadata mode. A `fileName.csv.metadata.json` can define:

- which CSV column is the content field;
- which columns should be included or excluded as metadata fields.

Bedrock currently supports one content field for CSV ingestion. Metadata columns are associated with chunks and treated as string values. This is useful if we later ingest tabular datasets, but it should not drive the first PDF/document strategy.

### Direct ingestion and custom data sources

Bedrock also supports direct ingestion through `IngestKnowledgeBaseDocuments`. For custom data sources, metadata can be provided inline. For S3 data sources, content and metadata are provided from S3 locations.

This could become useful if we want a fully custom pipeline that writes individual logical documents into a custom data source, but it is more infrastructure and operational surface than we need for the first metadata iteration.

### Custom transformation Lambda

Bedrock supports a custom transformation Lambda in the ingestion pipeline. The Lambda can run after Bedrock parsing/chunking and can add chunk-level metadata. AWS documents that chunk-level metadata takes precedence over file-level metadata if keys collide.

This is the right tool if we need metadata that differs by chunk, such as:

- page number;
- page range;
- chapter;
- section;
- heading path;
- table/figure labels;
- extracted code section identifiers.

Tradeoff: using a custom transformation Lambda disables Bedrock's metadata-only optimization for resyncs, increases deployment complexity, and requires an intermediate S3 storage location plus Lambda permissions.

### Parsing, chunking, and multimodal considerations

Bedrock Knowledge Bases supports several text chunking modes, including fixed-size, default, hierarchical, semantic, and no chunking. The current repo uses fixed-size chunking. AWS notes that when parsed content is produced by advanced parsers, Bedrock may respect logical boundaries such as pages or sections during chunking, but we should still validate this with our own corpus and vector store before relying on it for page/section citations.

For multimodal documents, the default parser only outputs text. AWS recommends Bedrock Data Automation or a foundation-model parser when PDFs include figures, charts, tables, or images. Those parser options can extract multimodal elements and store them as files in S3 for retrieval/source attribution, but they add cost and configuration. This matters for building-code PDFs with tables/figures and for future slide-heavy corpora.

### Retrieval filtering

Bedrock retrieval filters are richer than the current backend helper. The API supports a union of filter operators such as equality, inequality, comparison, inclusion, and logical groups. We should keep the request shape simple until the metadata schema is stable, then extend the backend filter builder to support the operators that are useful and supported by the selected vector store.

## Recommended Strategy

Use a staged strategy:

1. Keep the implemented runtime metadata plumbing in place: retrieval source blocks include metadata, answers cite sources, and the API returns metadata for cited sources only.
2. Start ingestion work with generated file-level `.metadata.json` sidecars.
3. Add schema validation and controlled vocabularies so sidecars are reliable and safe.
4. Add richer retrieval filter support once metadata fields are stable.
5. Only add a Bedrock custom transformation Lambda if file-level metadata is not enough.

This keeps the first implementation simple and compatible with the current S3 + Aurora/RDS architecture, while preserving a path to chunk-level metadata later.

## Pilot Metadata Model for Building-Code Corpus

The standard metadata core above is the durable contract. The first pilot corpus can add building-code-specific fields, but these should live in the same field registry so the repo does not become hardcoded to building codes.

Use snake_case metadata keys. Keep sidecar metadata small, stable, and filter-oriented. Avoid storing long summaries in metadata because Bedrock sidecar files are size-limited and metadata is meant for filtering, display, routing, and citation context rather than full text storage.

### Required pilot fields beyond the standard core

| Field | Type | Level | Purpose |
| --- | --- | --- | --- |
| `title` | string | file | Human-readable document title. |
| `jurisdiction` | string | file | Human-readable jurisdiction name when applicable. |
| `jurisdiction_state` | string | file | State or region code when applicable. |
| `extraction_method` | string | file | `manual`, `filename`, `regex`, `pdf_properties`, `toc_parser`, `llm_assisted`, or combined value. |
| `extraction_confidence` | number | file | 0-1 confidence score for generated metadata. |

### Recommended pilot optional fields

| Field | Type | Level | Purpose |
| --- | --- | --- | --- |
| `code_family` | string | file | `irc`, `ibc`, `ifc`, `ipc`, `imc`, `nec`, `local_zoning`, etc. |
| `edition_year` | number | file | Useful for filtering current vs older code editions. |
| `version_number` | string | file | Optional publisher or internal version identifier. |
| `effective_date` | string | file | Prefer `YYYY-MM-DD` when known. |
| `publish_date` | string | file | Prefer `YYYY-MM-DD` when known. |
| `publisher` | string | file | ICC, city, county, state agency, etc. |
| `tags` | string list | file | Small controlled vocabulary; avoid free-form tag sprawl. |
| `metadata_warnings` | string list | file | Short warning codes, not long explanations. |

### Future chunk-level fields

These should not be required in the first file-level sidecar implementation. Add them through pre-splitting, Bedrock custom transformation Lambda, or a later custom ingestion path when source precision becomes important enough.

| Field | Type | Level | Purpose |
| --- | --- | --- | --- |
| `page_number` | number | chunk | Citation display and page filtering. |
| `page_start` | number | chunk | Page range start. |
| `page_end` | number | chunk | Page range end. |
| `chapter` | string | chunk | Code/document chapter. |
| `section` | string | chunk | Code section such as `R401.1`. |
| `heading_path` | string | chunk | Hierarchical heading trail. |
| `content_kind` | string | chunk | `text`, `table`, `figure`, `appendix`, `definition`, etc. |
| `quote_start_char` | number | chunk | Optional offset into normalized chunk text for highlighted evidence. |
| `quote_end_char` | number | chunk | Optional offset into normalized chunk text for highlighted evidence. |
| `asset_uri` | string | chunk | Pointer to extracted table/image/figure asset when applicable. |
| `asset_caption` | string | chunk | Caption or generated description for non-text assets. |

## Metadata Generation Options

### Option A: Manual sidecar files

Humans write `.metadata.json` files next to each source file.

Pros:

- Lowest implementation cost.
- Directly supported by Bedrock.
- Good for small document sets.

Cons:

- Easy to drift.
- Hard to scale.
- Inconsistent metadata quality.
- No extraction confidence or validation unless we build tooling anyway.

Recommendation: useful for early tests only, not the durable strategy.

### Option B: Pre-ingestion metadata generator

Create a repo-managed tool that scans local source documents or S3 keys, extracts metadata, validates against a schema, and writes `.metadata.json` sidecars before `StartIngestionJob`.

Likely inputs:

- source file path or S3 URI;
- S3 key naming convention;
- existing metadata manifest, if present;
- PDF document properties;
- first pages / title page text;
- table of contents text;
- optional LLM extraction for ambiguous fields.

Likely outputs:

- `fileName.extension.metadata.json` beside each source file;
- a local manifest/report for review;
- warnings for low-confidence fields;
- a diff-like summary before upload/sync.

Pros:

- Keeps Bedrock's native ingestion path.
- Works with current S3 data source and Aurora/RDS vector store.
- Allows metadata-only updates to benefit from Bedrock's optimization when no content files change and no custom transformation Lambda is used.
- Easier to test locally.
- Keeps metadata generation deterministic and auditable.

Cons:

- File-level only unless we pre-split documents by page/section.
- Page-level metadata requires either pre-splitting or a later chunk transformation.

Recommendation: use this as the first implementation path.

### Option C: Pre-split documents before Bedrock ingestion

Split documents into smaller files before upload, such as one file per chapter, section, or page, then generate file-level metadata for each split file.

Pros:

- Can represent page/chapter/section as file-level metadata.
- Avoids Bedrock custom transformation Lambda.
- Makes citations easier to display.

Cons:

- More source artifacts to manage.
- Can lose whole-document context unless split strategy is careful.
- Requires preserving links to the original source document.

Recommendation: consider for code books and long PDFs if page/section citation quality becomes a priority.

### Option D: Bedrock custom transformation Lambda

Configure a post-chunking custom transformation Lambda in the Bedrock data source ingestion pipeline to add chunk-level metadata.

Pros:

- Best fit for true chunk-level metadata.
- Can annotate Bedrock-produced chunks after native parsing/chunking.
- Supports page, section, heading, and content-kind metadata when extractable.

Cons:

- Additional Lambda, permissions, intermediate S3 storage, deployment config, and test coverage.
- Disables Bedrock metadata-only sync optimization.
- Harder to debug than a pre-ingestion generator.
- Must be added through `VectorIngestionConfiguration`; may require replacing or updating the data source.

Recommendation: defer until a later phase, after we prove the file-level schema and know which chunk-level fields matter.

### Option E: Fully custom data source / direct ingestion

Use Bedrock custom data sources and `IngestKnowledgeBaseDocuments` to push documents and metadata directly.

Pros:

- Maximum control over document identifiers and metadata.
- Good for streaming or non-S3 source systems.

Cons:

- More custom orchestration.
- Less aligned with the current S3 bucket deployment.

Recommendation: not needed yet.

### Option F: Multimodal parser plus asset metadata

Use Bedrock parsing options, Bedrock Data Automation, Textract, or a foundation-model parser to extract text, tables, figures, and image descriptions before indexing.

Pros:

- Better fit for PDFs with tables, diagrams, plots, screenshots, scanned pages, or slides.
- Bedrock Knowledge Bases can use Bedrock Data Automation or foundation models as parsers for multimodal data, and can extract figures/charts/tables/images into an S3 destination for retrieval/source attribution.
- Textract remains a useful specialized option for OCR, forms, and tables when we want explicit document-analysis control outside Bedrock KB ingestion.

Cons:

- Higher cost and more configuration than the default text parser.
- Parser choice applies broadly to PDFs in a data source, so mixed text-only and multimodal corpora may need separate data sources or knowledge bases.
- Visual evidence needs more frontend and backend structure than plain chunk text.

Recommendation: not part of the first implementation, but plan for it by reserving metadata fields such as `content_kind`, `asset_uri`, `asset_caption`, and `asset_page_number`.

## Phased Implementation Roadmap

The goal is not to implement every feature at once. The early phases should create stable contracts so later work on chunking, visual assets, hybrid search, and raw document viewing can reuse the same source identifiers and metadata shape.

### Phase 0: Runtime metadata and citation foundation - implemented

Current completed foundation:

- Retrieved source blocks passed to the answer LLM include metadata and chunk text.
- Graph source dictionaries preserve full returned metadata under `metadata`.
- The answer prompt tells the LLM to use metadata when grounding responses.
- Model-facing sentinel citations use `<[N]>`, then deterministic post-processing converts them to user-facing `[N]`.
- Citation post-processing filters the API response to cited sources only and renumbers sources by first citation use.
- `Source` responses include `citation_number`, `document_id`, `source_type`, `score`, `chunk`, and `metadata`.
- The frontend source UI can display any number of returned cited sources.

Remaining Phase 0 cleanup:

- Normalize common display fields such as `title`, `filename`, `location`, `page_number`, and `section` after the ingestion metadata schema is stable.
- Decide whether any internal-only metadata fields should be hidden from the frontend.
- Add integration tests against realistic Bedrock metadata payloads once the first sidecars are generated.

### Phase 1: Metadata schema, registry, and validation

Create a metadata contract before writing ingestion automation.

Deliverables:

- `docs/knowledge_base_metadata.md` with human-readable schema guidance.
- A machine-readable schema, likely under `config/metadata/knowledge_base_metadata.schema.json`.
- A metadata field registry, likely under `config/metadata/metadata_fields.yaml`.
- Controlled vocabularies for `document_type`, `source_status`, `access_tier`, `code_family`, and `content_kind`.
- A validation command that runs locally and in CI before metadata files are uploaded.

Validation rules:

- Required standard fields must be present.
- Metadata sidecar file size must stay under Bedrock's 10 KB limit.
- Dates must be normalized.
- Controlled vocabulary values must be recognized.
- Long free-form fields should be rejected.
- Field types must be stable across documents.
- No secrets, account IDs, ARNs, JWTs, credentials, or private URLs should be allowed in metadata.
- Unknown custom fields can be allowed only when they pass key/type/size/safety checks; fields used for filters or frontend display should be registered.

First implementation target: schema, registry, and validator only. Do not add LLM extraction until the validator can reject unsafe or malformed output.

### Phase 2: File-level metadata generator and source catalog MVP

Build a generator that can be run manually before Knowledge Base sync.

Potential command shape:

```bash
./scripts/kb_metadata/generate_metadata.sh sandbox --source-prefix kb_sources/
./scripts/kb_metadata/validate_metadata.sh sandbox --source-prefix kb_sources/
```

Candidate extraction stages:

1. S3 key parser: derive environment, source collection, document type, filename, and `location`.
2. File parser: derive PDF title, page count, author/publisher, creation/modification dates where available.
3. Text parser: extract title page, jurisdiction, edition year, code family, effective date, publish date, and table-of-contents hints.
4. Optional OCR triage: flag scanned/low-text PDFs for a later OCR/multimodal path instead of pretending the text parser succeeded.
5. Optional LLM extractor: use a constrained JSON schema only for low-confidence or unclassified files.
6. Review manifest: write low-confidence rows and warnings to a review file before upload.

Outputs:

- `fileName.extension.metadata.json` beside each source file using Bedrock's expected sidecar format.
- `metadata_manifest.jsonl` with one row per source file, including warnings and extraction confidence.
- `kb_catalog.json` as the first version of a human/agent-readable source catalog.
- `kb_summary.md` as a lightweight narrative summary of known document types, source collections, and observed metadata patterns.

Example sidecar shape:

```json
{
  "metadataAttributes": {
    "metadata_schema_version": {
      "value": {
        "type": "STRING",
        "stringValue": "1.0"
      },
      "includeForEmbedding": false
    },
    "filename": {
      "value": {
        "type": "STRING",
        "stringValue": "example-building-code.pdf"
      },
      "includeForEmbedding": true
    },
    "location": {
      "value": {
        "type": "STRING",
        "stringValue": "s3://example-bucket/kb_sources/example-building-code.pdf"
      },
      "includeForEmbedding": false
    },
    "title": {
      "value": {
        "type": "STRING",
        "stringValue": "Example Building Code"
      },
      "includeForEmbedding": true
    },
    "edition_year": {
      "value": {
        "type": "NUMBER",
        "numberValue": 2021
      },
      "includeForEmbedding": true
    }
  }
}
```

We should test `includeForEmbedding` behavior explicitly. Default recommendation: include descriptive fields such as title, filename, document type, code family, jurisdiction, and edition year for embedding; exclude operational fields such as schema version, extraction method, access tier, source status, location, and warnings.

### Phase 3: Wire metadata into deployment and ingestion workflow

Update deployment/runbooks so metadata generation is part of the Knowledge Base content workflow, not hidden manual work.

Deliverables:

- Add a documented pre-sync step to Knowledge Base deployment docs.
- Consider adding an optional `--generate-metadata` flag to `scripts/deploy/deploy_knowledge_base.sh`.
- Do not generate metadata during every infrastructure deploy by default until the generator is stable.
- Add a `metadata_strategy` section to environment config if needed.
- Store the generated catalog/summary in S3 with the corpus so ingestion and runtime tools can use the same source inventory.

Possible config shape:

```yaml
knowledge_base_metadata:
  enabled: true
  schema_version: "1.0"
  source_prefix: "kb_sources/"
  catalog_key: "kb_sources/_catalog/kb_catalog.json"
  summary_key: "kb_sources/_catalog/kb_summary.md"
  generator:
    mode: "local"
    llm_assisted: false
    review_required_below_confidence: 0.8
```

### Phase 4: Document profiles and adaptive chunking strategy

Add document-type profiles so ingestion decisions are not hardcoded to building code.

Profile examples:

- `building_code`: prefer chapter/section/page metadata, table-of-contents extraction, section-aware chunks, and precise citation display.
- `policy`: prefer title, owner, effective date, policy number, department, and revision status.
- `presentation`: prefer slide number, deck title, image/table extraction, speaker notes, and figure summaries.
- `faq`: prefer question/answer pairs and category metadata.
- `report`: prefer report date, author, section headings, charts, tables, and executive summary.

Implementation paths to compare for building-code PDFs:

1. Keep Bedrock fixed-size chunking and file-level sidecars.
   - Lowest complexity.
   - Good for metadata display and broad filters.
   - Weak for exact page/section citations.
2. Pre-split source documents by section, chapter, or page before Bedrock ingestion.
   - Represents page/section as file-level metadata.
   - Avoids custom transformation Lambda.
   - Requires preserving `original_location` and `source_id` so split artifacts still point back to the raw document.
3. Use Bedrock custom transformation Lambda after native parsing/chunking.
   - Best fit for true chunk-level metadata.
   - Adds Lambda, intermediate S3 storage, permissions, tests, and data-source configuration complexity.
4. Build a fully custom parser/direct ingestion path.
   - Maximum control.
   - Most operational surface.
   - Defer until Bedrock-managed ingestion blocks a required feature.

Recommendation: use file-level sidecars first, then try pre-splitting for long building-code PDFs before adding a custom transformation Lambda.

### Phase 5: Source evidence model and frontend citation experience

Extend the backend source shape from "chunk plus metadata" toward an evidence model that can support hover previews, source cards, and raw-document navigation.

Target source evidence shape:

```json
{
  "citation_number": 1,
  "document_id": "stable-source-id",
  "source_type": "building_code",
  "score": 0.78,
  "chunk": "... full retrieved chunk ...",
  "evidence_text": "Risers shall be vertical or sloped...",
  "evidence_highlights": [
    {
      "text": "Risers shall be vertical or sloped...",
      "start_char": 120,
      "end_char": 168
    }
  ],
  "metadata": {
    "title": "Example Building Code",
    "filename": "example-building-code.pdf",
    "location": "s3://bucket/kb_sources/example-building-code.pdf",
    "chapter": "3",
    "section": "R311.7.5.1",
    "page_number": 52
  }
}
```

Implementation options:

- Prompt-only evidence: ask the answer LLM to quote the relevant passage and cite it. Lowest cost, but evidence offsets are not deterministic.
- Deterministic quote matching: after answer generation, match cited quoted text back into the cited chunk and compute offsets. Good first path for hover highlighting.
- Separate evidence extraction node: run a small post-answer step that selects exact evidence spans from cited chunks. More reliable, more latency/cost.
- Precomputed chunk spans: store sentence/paragraph offsets at ingestion time. Best long-term path for deterministic highlights and raw document mapping.

Recommendation: start with deterministic quote matching against cited chunks. Add a separate evidence extraction node only if the answer LLM does not reliably quote source text.

Frontend stages:

- Stage 1: show cited source cards with citation number, title/filename, metadata, and chunk excerpt.
- Stage 2: hover over `[1]` or source card to show the cited chunk and highlight `evidence_text`.
- Stage 3: click citation/source to open a raw-document viewer at page/section.
- Stage 4: highlight the exact passage in the raw document when page coordinates or text-layer offsets are available.

### Phase 5A: Frontend Library view from metadata catalog

The frontend should eventually include a Library view that shows the documents currently available to the user in the knowledge base. This view should be powered by the same metadata catalog generated during ingestion.

Library view goals:

- show available documents before the user asks a question;
- organize documents by metadata such as `document_type`, `source_status`, `jurisdiction`, `code_family`, `edition_year`, `publish_date`, `uploaded_at`, and `access_tier`;
- support search/filter/sort over registered displayable metadata fields;
- show ingestion status for newly uploaded documents, such as `processing`, `ready`, or `failed`;
- distinguish curated reference documents from user-uploaded documents if both are available;
- expose friendly titles and filenames, not raw S3 paths;
- provide a path to open authorized raw-document views when that feature exists.

Backend/API implications:

- Add a catalog/list endpoint once `kb_catalog.json` or a database-backed source catalog exists.
- Filter the Library response server-side by the authenticated user's authorization.
- Return normalized display metadata rather than raw Bedrock metadata only.
- Include source status and ingestion timestamps so the UI can explain why a document is or is not searchable yet.
- Keep the Library contract aligned with `ChatResponse.sources` so source cards and Library rows display the same core metadata.

Implementation options:

- Static catalog file API: backend reads `kb_catalog.json` from S3 and returns authorized rows. Simple first implementation.
- Database-backed source catalog: ingestion workflow writes source rows into Aurora/DynamoDB for faster filtering/status updates. Better once frontend uploads and async processing exist.
- Bedrock data source inspection: query Bedrock where possible. Useful for operations, but less suitable as the main app contract because app-specific metadata, authorization, and upload status still need a local source catalog.

Recommendation: use the generated catalog as the first Library backing store. Move to a database-backed source catalog when upload status, per-user visibility, or large corpus filtering require it.

### Phase 6: Secure raw document access and document viewer foundation

Keep `location` available now so raw-document access can be added later without changing source identifiers.

Access options:

1. Backend-generated S3 presigned URL.
   - Simple and good for early authenticated viewing.
   - Time-limited URL grants direct object access.
   - Less ideal for repeated/high-volume document viewing and fine-grained viewer controls.
2. CloudFront signed URL or signed cookie.
   - Better for production document delivery and caching.
   - Supports stronger private-content patterns and custom policies.
   - Requires distribution/key-group setup.
3. Backend proxy endpoint.
   - Maximum authorization control.
   - Higher backend cost/latency and more streaming/range-request complexity.

Recommendation: plan for CloudFront signed URLs long-term. Use backend-generated S3 presigned URLs only for early internal testing if needed.

Viewer considerations:

- PDF.js can jump to a page with URL hash parameters such as `#page=2`.
- Exact text highlighting needs either reliable PDF text-layer search, text offsets mapped to extracted text, or precomputed page coordinates.
- For scanned PDFs, image-only pages, and diagrams, highlights may need bounding boxes from OCR/parser output rather than text offsets.
- Do not expose raw S3 URIs to users as clickable links; treat `location` as internal source identity and generate short-lived access URLs server-side.

### Phase 7: Visual, table, and multimodal ingestion

The building-code test corpus includes images and tables, and future corpora may include slide decks, scanned documents, plots, and screenshots. Text embeddings alone will miss some of this information.

Implementation options:

1. Default Bedrock text parser only.
   - Cheapest and simplest.
   - Fine for text-heavy PDFs.
   - Weak for tables, figures, plots, and scanned pages.
2. OCR/table extraction with Textract before Bedrock ingestion.
   - Strong control over tables/forms and scanned text.
   - Produces structured artifacts we can store beside the source.
   - Requires custom pipeline and mapping back to source pages.
3. Bedrock Data Automation or foundation-model parser for Bedrock KB.
   - Better managed path for figures, charts, tables, and images.
   - Can extract multimodal elements and store them in S3 for retrieval/source attribution.
   - Cost and parser selection must be managed carefully.
4. Dual artifact strategy.
   - Store text chunks and visual assets separately.
   - Embed text summaries/captions of visual assets.
   - Preserve `asset_uri`, `asset_page_number`, `asset_caption`, and `content_kind`.
   - Allow answer LLM to inspect the raw image/table later when a retrieved summary is insufficient.
5. Multimodal embeddings.
   - Useful when image similarity itself matters.
   - Adds model/vector-store considerations and retrieval routing complexity.

Recommendation: add OCR/multimodal triage in Phase 2, but defer full visual ingestion. The first multimodal implementation should use a dual artifact strategy: text summary/caption for fast retrieval plus an S3 pointer to the raw image/table for later LLM inspection.

### Phase 8: Catalog, retrieval planning, hybrid search, and multiple knowledge bases

Use ingestion outputs to build a catalog that a future retrieval-planning node can inspect before semantic search.

Catalog content:

- knowledge base id and data source id;
- source collections and document types;
- each source's `source_id`, title, filename, location, status, access tier, and domain metadata;
- known chapter/section/page inventory when available;
- document profile used during ingestion;
- available metadata filters and field types;
- known visual/table assets by source and page;
- update timestamp and ingestion job id.
- normalized display fields for the frontend Library view.

Retrieval planning options:

1. Static pre-filtering from request metadata.
   - Simple: apply user-specified filters and server-side authorization filters.
2. LLM planner reads `kb_summary.md`.
   - Lets the agent decide which collections/documents/filters are likely relevant.
   - Needs guardrails so generated filters use registered fields/operators only.
3. Direct section/document lookup.
   - If the user asks for "Section R311.7.5.1", retrieve that section directly from a structured section index instead of relying only on semantic search.
4. Hybrid search.
   - Combine semantic search with keyword/BM25 or structured lookup.
   - Useful for exact code section names, dates, document numbers, and citations.
5. Multiple KB/data-source routing.
   - Keep separate KBs for meaningfully different corpora, such as building codes vs local project documents.
   - Add a routing step that chooses one or more KBs/data sources, then merges cited-source candidates.

Recommendation: do not build hybrid search first. Build the catalog and field registry now so a later planner can safely choose filters, direct lookups, or KB routes.

### Phase 9: Frontend document upload and ingestion automation

Later, users should be able to drag and drop new documents in the frontend. This should use the same ingestion contracts defined in earlier phases instead of becoming a separate upload-only path.

Target flow:

1. Frontend requests an authorized upload target from the backend.
2. Backend validates the user's permission to add documents to the selected collection, then returns a short-lived S3 upload URL or an upload session.
3. Frontend uploads the document to a controlled S3 prefix, such as `uploads/pending/{user_id}/{upload_id}/`.
4. S3 event, EventBridge event, or explicit backend completion call starts an ingestion workflow.
5. Workflow runs file validation, malware/content checks if needed, metadata generation, OCR/multimodal triage, sidecar creation, catalog update, and Knowledge Base sync.
6. Workflow records ingestion status so the frontend can show `uploading`, `processing`, `ready`, or `failed`.
7. Once the document is ready, the source appears in the catalog and can be used by retrieval filters, source cards, and citations.

Implementation options:

- Simple backend-triggered sync: the upload completion endpoint starts metadata generation and `StartIngestionJob`. Lowest complexity, but less resilient for long-running processing.
- S3 event plus Lambda/Step Functions: S3 upload triggers a workflow that validates, enriches, and syncs the document. Better for production and long-running OCR/multimodal work.
- Queue-based ingestion: upload completion writes an ingestion job to SQS, and workers process jobs asynchronously. Good when many users may upload files.
- Separate user-upload knowledge base: keep user documents in a separate KB/data source from curated reference documents. Cleaner authorization boundary, but requires retrieval routing and result merging.

Recommended later direction: use a controlled S3 prefix, async ingestion job records, and Step Functions or SQS once OCR/multimodal processing becomes part of the path. Do not let uploaded files go directly into a production retrieval corpus without validation, metadata generation, and authorization metadata.

Important foundations from earlier phases:

- `source_id`, `uploaded_at`, `location`, `source_status`, and `access_tier` become required for uploaded documents.
- Metadata validation must reject unsafe fields and secrets before sidecars are written.
- The catalog must support pending/failed sources, not only successfully ingested sources.
- Retrieval must apply server-side authorization filters so users only retrieve uploaded documents they are allowed to access.
- Raw document viewing must generate short-lived authorized URLs rather than exposing durable S3 paths.

## Data Authorization Considerations

Metadata filters are not a replacement for application authorization. The backend must decide which filters apply to the authenticated user and then pass those filters to Bedrock.

Recommended future fields:

- `access_tier`
- `allowed_groups`
- `jurisdiction`
- `tenant_id` if multi-tenant use emerges

Do not trust user-supplied `retrieval_filters` blindly once any restricted data exists. Merge user filters with server-side authorization filters derived from JWT claims or backend user policy.

Raw document access has the same rule. A cited source may include `location`, but the frontend should not receive a durable raw S3 URL. The backend should authorize the user for the source, then mint a short-lived S3 presigned URL, CloudFront signed URL, signed cookie, or stream through a backend proxy depending on the deployment stage.

## Quality and Evaluation Notes

Add metadata-specific evals before relying on the new schema.

Evaluation dimensions:

- metadata extraction accuracy;
- filter correctness;
- retrieval precision after filtering;
- citation display quality, including whether cited source metadata is useful to users;
- evidence quote quality and whether the cited quote is actually present in the cited chunk;
- source card quality for page/chapter/section display;
- catalog accuracy for document routing and table-of-contents lookups;
- raw document access authorization checks;
- visual/table extraction coverage once multimodal ingestion starts;
- regression against unfiltered retrieval;
- no-secret/no-account-ID metadata validation.

Suggested tests:

- Known document title query returns expected `source_id`.
- Known section query returns the expected `section` once section metadata exists.
- `document_type` filter excludes unrelated document types.
- `source_status=current` excludes superseded docs.
- `edition_year >= X` returns expected code edition.
- Source response includes `citation_number`, `document_id`, `score`, `chunk`, and metadata.
- Answers with no source citations return `sources: []`.
- Answers with non-contiguous retrieval citations return sources renumbered by first citation use.
- Quoted evidence can be matched back to the cited chunk before the frontend highlights it.
- Source metadata includes `location`, but generated frontend document links are short-lived and authorized.
- Catalog generation reflects newly added and removed source documents.
- Metadata generator refuses sidecars over 10 KB.

## Open Questions

- What document corpus should be the first metadata pilot: building codes, zoning docs, town documents, or a mixed sample?
- Do we need page-level citations in the first user-facing version, or is section/title metadata sufficient for the first release?
- Should source documents be renamed into a canonical S3 key structure before metadata generation?
- Should metadata sidecars be committed to Git, generated locally, or stored only in S3?
- Do we want LLM-assisted extraction in the first implementation, or deterministic extraction only?
- Which fields should be included for embedding versus kept filter/display-only?
- Should the first catalog be stored only as generated S3 artifacts, or also checked into a non-secret fixture directory for local testing?
- Should building-code documents be pre-split by section before we add a Bedrock custom transformation Lambda?
- Which raw document access pattern should be used first: S3 presigned URLs, CloudFront signed URLs, or backend proxy?
- Should visual/table extraction start with Textract, Bedrock Data Automation, or Bedrock foundation-model parsing?
- How should a future planner choose between multiple knowledge bases versus multiple data sources in one knowledge base?
- Should user-uploaded documents live in the same Knowledge Base/data source as curated reference documents, or in a separate upload-specific KB/data source?
- What ingestion status model should the frontend poll or subscribe to for uploaded documents?
- Should the Library view be backed first by generated `kb_catalog.json`, or should we introduce a database-backed source catalog before user uploads?

## Recommended First Decision

Adopt Option B: a pre-ingestion metadata generator that writes Bedrock-compatible `.metadata.json` sidecars.

Runtime metadata preservation is already in place for cited sources, so the next concrete decision is Phase 1 and Phase 2:

1. Define the sidecar metadata schema and controlled vocabularies.
2. Define the metadata field registry and decide which fields are displayable/filterable.
3. Choose the first pilot corpus, likely building-code PDFs because they stress section/page citations.
4. Build a validator before building the generator.
5. Generate sidecars, `metadata_manifest.jsonl`, `kb_catalog.json`, and `kb_summary.md` for the pilot corpus.
6. Run a Knowledge Base ingestion job.
7. Use the existing prompt-debug and cited-source response path to inspect whether the returned metadata is useful.

Defer custom transformation Lambda until we have evidence that file-level metadata plus source normalization is insufficient.

## Research Sources

- AWS Bedrock `Retrieve` API response shape: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Retrieve.html
- Query a knowledge base and retrieve data: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-retrieve.html
- Configure metadata filtering and PDF page-number caveat: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-config.html
- S3 data source metadata sidecar format: https://docs.aws.amazon.com/bedrock/latest/userguide/s3-data-source-connector.html
- CSV metadata configuration: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-metadata.html
- Bedrock content chunking strategies: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html
- Bedrock parsing options for multimodal data: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-advanced-parsing.html
- Bedrock Data Automation concepts: https://docs.aws.amazon.com/bedrock/latest/userguide/bda-how-it-works.html
- Sync and metadata-only resync behavior: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html
- Direct ingestion with metadata: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-direct-ingestion-add.html
- Custom transformation Lambda for chunk-level metadata: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-custom-transformation.html
- Bedrock retrieval filter API: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_RetrievalFilter.html
- S3 presigned URLs for private object access: https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html
- CloudFront signed URLs for private content: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-signed-urls.html
- PDF.js viewer page navigation options: https://github.com/mozilla/pdf.js/wiki/Viewer-options
