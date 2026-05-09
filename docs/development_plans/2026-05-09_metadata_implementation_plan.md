# Metadata Implementation Plan

Created: 2026-05-09  
Status: Proposed implementation path  
Scope: Concrete metadata, artifact, catalog, retrieval, and authorization implementation plan for the `chat-template` RAG backend.

This plan turns the broader research in `2026-05-05_knowledge_base_metadata_strategy_research.md` into a specific implementation path. The research document should remain the place for alternatives and background. This document should track the route we intend to build.

## Chosen Direction

Use Option C from the research document as the backbone: a preprocessed derivative corpus with stable app-owned document and chunk identifiers.

Core decision:

1. Upload each original document to an S3 landing zone.
2. Store each original document once in S3 as the durable source of truth.
3. Generate derivative artifacts from the original document:
   - section/page text chunks;
   - table markdown/JSON artifacts;
   - image/figure assets;
   - image/table summaries for text retrieval where useful.
4. Store derivative artifacts in S3 under controlled prefixes.
5. Store an app-owned document catalog in Aurora so the backend can perform fast lookup, authorization, Library listing, and ingestion status checks.
6. Index only the derivative text artifacts into Bedrock Knowledge Bases.
7. Use Bedrock semantic/hybrid retrieval for open-ended questions.
8. Use the app catalog plus S3 artifact fetches for direct section/page/image lookup when the agent already knows what it wants to read.

Important distinction: the Bedrock vector table stores chunk text in `bedrock_integration.bedrock_kb.chunks`, but that table should not be our durable source catalog. Bedrock owns ingestion into that table, and row ids/chunk boundaries can change after resync, parser changes, or chunking changes. We can query it as an optimization, but the stable application contract should be the catalog plus S3 artifacts.

## Terminology and Identifier Decisions

Use `document_id` as the canonical app-owned id for the original uploaded document. The earlier research doc used `source_id` for this concept; implementation should prefer `document_id` going forward.

Recommended identifiers:

| Identifier | Example | Purpose |
| --- | --- | --- |
| `landing_s3_uri` | `s3://bucket/kb_landing/tenant/collection/raw-file.pdf` | Temporary ingestion-stage id before basic metadata is extracted. |
| `document_id` | `irc_building_code_2021_rev_1_a8f43c2d9b` | Stable original document id. |
| `chunk_id` | `irc_building_code_2021_rev_1_a8f43c2d9b_section_r311` | Stable id for any citable/retrievable slice of a document, including text, pages, tables, figures, and images. |
| `source_location` | `s3://bucket/kb_raw/.../original.pdf` | Internal pointer to source-of-truth file. |
| `location` | `s3://bucket/kb_index/.../section.md` | Internal pointer to the current artifact record. For document rows, this can point to the raw source document. For chunk rows, this points to the chunk artifact. |

`document_id` generation should use a readable prefix plus a generated random suffix for each document upload:

```text
{document_type}_{short_title_or_code_family}_{edition_or_year}_{version_or_revision}_{random_suffix}
```

Example:

```text
irc_building_code_2021_rev_1_a8f43c2d9b
```

The suffix does not need to be derived from content or metadata. Its job is to make the upload id unique. Generate it with a random UUID/ULID-style value or a shortened cryptographically random token. Metadata fields such as title, document type, edition/version, and publish date should remain editable without changing `document_id`.

Ingestion-stage rule: do not require the final `document_id` before initial metadata extraction has completed. During the landing and initial metadata-processing stages, use the landing S3 URI as the unique processing identifier. The workflow should keep the file in the landing zone while it identifies file type, extracts basic metadata, decides whether OCR/multimodal processing is needed, detects hierarchy, and selects the chunking strategy. After those initial scans provide enough metadata context, mint the final `document_id`, copy the raw file into `kb_raw/{document_id}/`, generate chunks/assets under the durable document layout, and write catalog rows with both `landing_s3_uri` and `document_id`.

Do not use Bedrock vector row ids as public API ids. Use app-owned ids in metadata and catalog rows, then map Bedrock retrieval results back to those ids.

## Target S3 Layout

Keep raw documents, indexable text, and non-text assets separate so the Knowledge Base does not accidentally ingest duplicate or unsupported artifacts.

```text
kb_landing/{tenant_id}/{collection_id}/{filename}

kb_raw/{document_id}/original/{filename}
kb_raw/{document_id}/document_metadata.json

kb_index/{document_id}/sections/{chunk_id}.md
kb_index/{document_id}/sections/{chunk_id}.md.metadata.json
kb_index/{document_id}/pages/{chunk_id}.md
kb_index/{document_id}/pages/{chunk_id}.md.metadata.json
kb_index/{document_id}/tables/{chunk_id}.summary.md
kb_index/{document_id}/tables/{chunk_id}.summary.md.metadata.json
kb_index/{document_id}/figures/{chunk_id}.summary.md
kb_index/{document_id}/figures/{chunk_id}.summary.md.metadata.json

kb_artifacts/{document_id}/tables/{chunk_id}.md
kb_artifacts/{document_id}/tables/{chunk_id}.json
kb_artifacts/{document_id}/figures/{chunk_id}.png
kb_artifacts/{document_id}/figures/{chunk_id}.json

kb_catalog/{environment}/document_catalog.json
kb_catalog/{environment}/kb_summary.md
kb_catalog/{environment}/metadata_manifest.jsonl
```

Knowledge Base inclusion prefix should point at `kb_index/`, not `kb_raw/`, `kb_landing/`, or `kb_artifacts/`.

Reason: the KB should index clean text artifacts and sidecar metadata. Raw PDFs and extracted image assets should stay available for authorized fetch/viewer flows without being duplicated into the semantic index.

## Ingestion Flow

Initial ingestion can be developer-only, but the flow should be compatible with future frontend drag-and-drop uploads.

Target flow:

1. A document is uploaded to an S3 landing zone.
2. An ingestion Lambda or Step Functions workflow is triggered for that object.
3. The ingestion workflow uses the landing S3 URI as the temporary processing id.
4. The workflow identifies file type while the source remains in the landing zone.
5. The workflow runs initial metadata extraction for that file type while the source remains in the landing zone.
6. The workflow runs OCR/multimodal triage when applicable.
7. The workflow detects hierarchy and chooses the chunking strategy.
8. The workflow mints the final `document_id` from extracted metadata plus a random suffix.
9. The raw document is copied into `kb_raw/{document_id}/original/`.
10. The workflow generates derivative text chunks/assets into `kb_index/` and `kb_artifacts/` using the chosen chunking strategy.
11. The workflow writes Bedrock sidecar metadata files next to each `kb_index/` artifact.
12. The workflow writes or updates catalog rows in Aurora.
13. The workflow starts or schedules a Bedrock Knowledge Base ingestion job.
14. The workflow records ingestion status for the Library view and future upload progress UI.

For all document types, preserve three layers:

1. Raw document in S3.
2. Text/chunked/artifact representation used for embeddings and direct lookup.
3. Vector DB rows generated by Bedrock ingestion.

The raw document and app catalog are the durable source of truth. The vector DB is a retrieval index.

## File-Type Extraction Routes

### PDF

PDF ingestion should do more than blindly extract text.

Steps:

1. Determine whether text is available in the PDF or OCR is required.
2. Support a configurable `always_ocr` setting for corpora where OCR quality is preferred over embedded text extraction.
3. If embedded text is available, extract text first.
4. Use a multimodal model, such as Nova Lite, to inspect:
   - the first `front_page_sample_count` pages, default 5;
   - `random_page_sample_count` random pages, default 5.
5. Ask the model whether embedded text extraction is sufficient or whether OCR should be run.
6. Identify whether the document has hierarchy:
   - table of contents;
   - chapters;
   - sections;
   - numbered headings;
   - page-level organization.
7. Choose the smallest reasonable chunk that:
   - fits within the configured embedding token limit;
   - is useful as a standalone information unit;
   - preserves chapter/section/page metadata.
8. Run deterministic token checks for each proposed chunk.
9. If a logical section exceeds the configured token limit, split it into deterministic part chunks while retaining the same section metadata.
10. If no logical hierarchy exists, use standard chunking, initially `1000` tokens with `20%` overlap, and preserve page numbers where possible.

Config fields to introduce:

```yaml
metadata_ingestion:
  pdf:
    always_ocr: false
    front_page_sample_count: 5
    random_page_sample_count: 5
    default_chunk_tokens: 1000
    default_chunk_overlap: 0.2
    embedding_chunk_token_limit:
      amazon.titan-embed-text-v2: 8192
```

### Word Documents

Word document ingestion should share the hierarchy and chunking logic used for PDFs after text/image extraction.

Initial open item: choose the extraction library or service for `.docx` text, embedded images, headings, and tables.

Expected route:

1. Extract text, headings, tables, and embedded images.
2. Identify hierarchy.
3. Generate chunks using the same token-limit and standalone-section rules as PDF ingestion.
4. Store extracted images/tables as artifacts when useful.

### Image Files

For `png`, `jpg`, `jpeg`, and similar image files:

1. Store the raw image in `kb_raw/` or `kb_artifacts/`.
2. Use a multimodal model, such as Nova Lite, to generate a grounded description.
3. Store the description as a text artifact in `kb_index/`.
4. Embed the description in the vector DB through Bedrock KB ingestion.
5. Preserve the raw image URI in metadata so the answer workflow can read the image directly when needed.

The LLM-generated description is for retrieval. It should not replace the raw image as the source of truth.

### HTML

HTML ingestion is a placeholder for now.

Expected future route:

- preserve source URL if available;
- strip navigation/boilerplate;
- extract title, headings, body text, tables, and images;
- generate chunks by heading structure when possible.

### CSV and Tabular Files

CSV ingestion should use Bedrock Knowledge Bases tabular/CSV capabilities where they fit, but this needs more research before implementation.

Expected future route:

- identify content columns and metadata columns;
- preserve raw CSV in S3;
- generate summaries or row/section artifacts where direct table lookup is needed;
- decide whether some datasets should be queried directly instead of embedded.

### PowerPoint and Slide Decks

PowerPoint is not in the first implementation, but the architecture should support it.

Expected future route:

- extract slide text;
- extract speaker notes;
- extract images/charts/tables;
- generate one text summary artifact per slide;
- preserve raw slide/page image assets for multimodal inspection.

## Metadata Model

Use the standard metadata core from the research document, updated here with implementation names.

### Document-Level Metadata

Default fields for each document upload:

| Field | Purpose |
| --- | --- |
| `document_id` | Stable id for the original document. |
| `document_title` | Human-readable title. |
| `document_description` | Short description for the agent catalog and Library. |
| `location` | S3 URI for the raw/original document when used on document rows. |
| `document_type` | File/content type such as `pdf`, `word_doc`, `image`, `web_page`, `csv`, `excel`, or `powerpoint`. |
| `filename` | Original filename. |
| `uploaded_at` | ISO timestamp when uploaded or registered. |
| `source_status` | `pending`, `processing`, `ready`, `failed`, `current`, `superseded`, `draft`, or `archived`, depending on workflow stage and corpus semantics. |
| `access_tier` | Coarse visibility tier such as `public`, `internal`, or `restricted`. |
| `allowed_groups` | List of groups allowed to access the document. This captures the working-note idea of access being tied to groups. |
| `tenant_id` | Tenant/account boundary if multi-tenant use emerges. |
| `collection_id` | Collection or corpus grouping. |

### Chunk-Level Metadata

A chunk is any slice/section of a document. This includes text sections, pages, chapters, tables, figures, and images.

Default fields for each chunk:

| Field | Purpose |
| --- | --- |
| `document_id` | Links the chunk back to `document_catalog`. |
| `chunk_id` | Stable id for the chunk. Can also identify figures/tables/images when they are retrievable units. |
| `source_location` | S3 URI of the original document. |
| `location` | S3 URI of the chunk artifact itself. |
| `chunk_type` | `text`, `figure`, `image`, `table`, `page`, `chapter`, `section`, etc. |
| `chunk_sequence` | Deterministic ordering within the document. |
| `token_estimate` | Estimated token count for the chunk text/summary. |

Custom metadata can vary by document type.

PDF/Word text chunk fields:

- `page_start`
- `page_end`
- `chapter`
- `section`
- `heading_path`

Image/figure/table chunk fields:

- `description`: LLM-generated description used for retrieval;
- `caption`: raw caption text from the source document, not invented by the LLM;
- `page_start`
- `page_end`
- `asset_location`
- `summary_location`
- `extraction_method`
- `extraction_confidence`

Authorization metadata should be propagated from document rows to chunk rows so Bedrock retrieval filters can exclude unauthorized chunks before they reach the agent.

## Catalog Tables

Use Aurora as the app-owned catalog store. Keep generated JSON files for debugging, review, and portability, but do not make the frontend or agent depend on reading a giant catalog file directly.

### `document_catalog`

The `document_catalog` table powers both:

- the agent's authorized document catalog tool;
- the frontend Library view.

The agent should receive an authorization-filtered summary of this table, not the full global catalog. The initial full catalog retrieval for the agent should include only:

- `document_id`
- `document_title`
- `document_description`

Full proposed table:

```sql
CREATE TABLE document_catalog (
    document_id TEXT PRIMARY KEY,
    landing_s3_uri TEXT,
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    document_title TEXT NOT NULL,
    document_description TEXT,
    filename TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_status TEXT NOT NULL,
    access_tier TEXT NOT NULL,
    location TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

### `document_chunks`

```sql
CREATE TABLE document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document_catalog(document_id),
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    source_location TEXT NOT NULL,
    location TEXT NOT NULL,
    title TEXT,
    chapter TEXT,
    section TEXT,
    heading_path TEXT,
    page_start INTEGER,
    page_end INTEGER,
    chunk_sequence INTEGER NOT NULL,
    token_estimate INTEGER,
    asset_location TEXT,
    summary_location TEXT,
    description TEXT,
    caption TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

For text chunks, `asset_location`, `summary_location`, `description`, and `caption` can be null. For image, figure, and table chunks, `chunk_id` is still the canonical id. `location` should point to the text artifact used for indexing when one exists; `asset_location` can point to the raw extracted image/table artifact.

### `document_ingestion_jobs`

```sql
CREATE TABLE document_ingestion_jobs (
    ingestion_job_id TEXT PRIMARY KEY,
    landing_s3_uri TEXT,
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    document_id TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error_code TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

### `document_access_grants`

```sql
CREATE TABLE document_access_grants (
    grant_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document_catalog(document_id),
    principal_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The first implementation can simplify this schema, but it should not remove the core concept: app-owned catalog rows are the durable authorization and navigation layer.

## Bedrock Knowledge Base Ingestion Strategy

Index derivative text artifacts from `kb_index/`.

Recommended first chunking strategy:

- Generate section-sized or page-sized markdown files.
- Keep each file below the configured embedding token limit.
- Prefer "no chunking" or a chunking strategy that preserves each generated file as closely as possible.
- If a section is too large, split it deterministically into part files:
  - `section-r311-7-5-1-part-001.md`
  - `section-r311-7-5-1-part-002.md`
  - metadata includes the same `section` plus `chunk_sequence`.

Why this matters: if we want "one vector result = one known section or section part", we should not upload large files and then rely on Bedrock fixed-size chunking to preserve our boundaries.

Each `kb_index` file gets a Bedrock sidecar `.metadata.json` with:

- stable `document_id` and `chunk_id`;
- authorization fields;
- display fields;
- page/section fields;
- `location` pointing at the indexed text artifact;
- `source_location` pointing at the raw source document.

## Direct Artifact Retrieval from S3

The backend should support direct artifact fetches for agent workflows, but only after catalog lookup and authorization.

Example use cases:

- User asks about a known code section and the planner finds that section in the catalog.
- The model reads a table of contents and decides it needs a specific section.
- A retrieved image summary indicates that the actual figure may be answer-critical.
- The frontend opens a cited source and requests the source text or raw document viewer.

Backend flow:

1. Agent or frontend asks for a `document_id` or `chunk_id`.
2. Backend loads the catalog or chunk row.
3. Backend checks user authorization against tenant, collection, access tier, allowed groups, owner, and explicit grants.
4. Backend validates the S3 URI belongs to an allowed prefix from the catalog.
5. Backend fetches the artifact from S3.
6. Backend enforces size/type limits before adding content to agent context or returning it to the frontend.

Recommended limits:

- Text artifacts should be sized so one artifact can be safely added to model context.
- Large text artifacts should be summarized or split before ingestion.
- Images should be fetched only when the agent explicitly needs visual detail.
- Raw PDFs should not be loaded into agent context by default.

S3 is the durable artifact store. The catalog is the index. The backend is the authorization and fetch boundary.

## Agent Retrieval Modes

The RAG Lambda should eventually support multiple retrieval modes.

### Semantic Retrieval

Current/default path:

- User asks a question.
- Backend applies server-side authorization filters.
- Bedrock retrieves relevant chunks from the Knowledge Base.
- Backend post-filters retrieved chunks against `document_catalog`/`document_chunks`.
- Answer LLM receives only authorized chunks and metadata.
- Citation post-processing returns only cited sources.

### Catalog-Planned Retrieval

Future planner path:

- Backend loads an authorization-filtered document catalog summary.
- The initial agent-facing catalog includes only `document_id`, `document_title`, and `document_description`.
- Agent decides whether to use semantic retrieval, direct lookup, or both.
- If it selects a known section/page/document, backend fetches that artifact directly from S3 or catalog-backed storage.

### Direct Lookup

For exact references:

- query by `section`, `chapter`, `page_number`, `document_id`, or `chunk_id`;
- fetch the specific text/table/image artifact;
- add it to answer context with citation metadata.

### Hybrid Retrieval

Later path:

- use Bedrock hybrid search where available;
- use Aurora full-text search over app-owned chunk rows or Bedrock `chunks` as an optimization;
- merge semantic results with exact section/table-of-contents matches;
- deduplicate by `chunk_id`.

## Authorization Strategy

The agent should never see the global catalog or global table of contents. It should only see a backend-generated, authorization-filtered view for the current user.

Authorization must apply to all paths:

- Library view;
- agent document catalog and table of contents;
- Bedrock retrieval filters;
- direct catalog lookup;
- direct S3 artifact fetch;
- raw document viewer URLs;
- source cards and citation metadata.

Recommended authorization model:

1. Authenticate the user with Cognito.
2. Resolve user authorization from claims and backend policy:
   - `tenant_id`;
   - group memberships;
   - collection memberships;
   - explicit document grants;
   - ownership for user-uploaded documents.
3. Build an authorized document scope.
4. Filter Library/catalog responses with that scope.
5. Inject metadata filters into Bedrock retrieval using coarse fields such as `tenant_id`, `collection_id`, `access_tier`, and `allowed_groups`.
6. Post-filter Bedrock results against `document_catalog` and `document_chunks` before sending chunks to the answer LLM.
7. Deny direct S3 artifact fetches unless the catalog row is authorized.

Important security note: Bedrock metadata filters are a useful enforcement layer, but they should not be the only enforcement layer. The backend should also validate returned `document_id`/`chunk_id` values against the app-owned catalog before sending retrieved chunks to the model or frontend.

If per-document ACLs become too large to express cleanly as Bedrock filters, prefer one of these approaches:

- keep coarse filters in Bedrock and post-filter strictly in the backend;
- split highly restricted corpora into separate data sources or Knowledge Bases;
- use direct catalog/DB lookup for strict-access documents;
- route user-uploaded or tenant-private documents through separate KBs/data sources if needed.

## Library View Strategy

The Library view should be backed by `document_catalog`, not by Bedrock directly.

Library API should return only authorized document rows:

```json
{
  "documents": [
    {
      "document_id": "irc_building_code_2021_rev_1_a8f43c2d9b",
      "document_title": "International Residential Code for One and Two Family Dwellings",
      "document_description": "2021 IRC building code reference document.",
      "filename": "2021-irc.pdf",
      "document_type": "pdf",
      "source_status": "current",
      "access_tier": "internal",
      "jurisdiction": "South Dakota",
      "code_family": "irc",
      "edition_year": 2021,
      "uploaded_at": "2026-05-09T12:00:00Z",
      "ingested_at": "2026-05-09T12:15:00Z"
    }
  ]
}
```

The Library should organize and filter by registered display metadata:

- document type;
- collection;
- source status;
- jurisdiction;
- code family;
- edition year;
- publish date;
- upload date;
- access tier;
- ingestion status.

## Librarian Agent

A later "librarian" agent can continuously improve the catalog and derivative artifacts after initial ingestion.

Possible responsibilities:

- detect documents with weak metadata;
- detect pages where embedded PDF text extraction missed visible text;
- schedule OCR repair jobs;
- identify broken or missing chunk/page mappings;
- improve table/image descriptions;
- detect duplicate or superseded documents;
- recommend catalog cleanup;
- re-run preprocessing during low-cost compute windows when appropriate.

This should not be required for the first implementation. The first implementation should produce deterministic artifacts and record enough warnings/confidence scores for a future librarian process to act on.

## User Upload Strategy

Frontend drag-and-drop upload should reuse this same pipeline.

Later target flow:

1. Frontend asks backend for an upload session.
2. Backend authorizes upload into a tenant/collection.
3. Backend returns a short-lived S3 upload URL.
4. Frontend uploads the raw file to a landing or pending S3 prefix.
5. Backend or S3 event starts an ingestion job.
6. Ingestion job validates the file, generates metadata, creates derivative artifacts, writes catalog rows, writes Bedrock sidecars, and starts/schedules Knowledge Base sync.
7. Frontend polls or subscribes to ingestion status.
8. Once ready, the uploaded document appears in Library and retrieval.

Uploaded documents must default to restricted visibility until metadata, status, and ACLs are explicitly assigned.

## Implementation Phases

### Phase 1: Schema and Config

Deliverables:

- `docs/knowledge_base_metadata.md`
- `config/metadata/knowledge_base_metadata.schema.json`
- `config/metadata/metadata_fields.yaml`
- initial SQL schema for `document_catalog`, `document_chunks`, `document_ingestion_jobs`, and `document_access_grants`
- deployment script update to apply catalog schema
- ingestion config for OCR, page sampling, chunk token limits, default chunk size, and overlap

### Phase 2: Deterministic Preprocessing Pilot

Deliverables:

- local/scripted preprocessor for a small building-code corpus;
- canonical `document_id` and S3 key generation;
- deterministic PDF text extraction;
- OCR-needed detection scaffold;
- table of contents extraction;
- hierarchy detection;
- generated markdown section/page artifacts;
- generated sidecar metadata;
- generated `metadata_manifest.jsonl`, `document_catalog.json`, and `kb_summary.md`;
- catalog DB inserts.

Do not use LLM extraction as the only extraction path first. Add LLM-assisted metadata/hierarchy decisions after deterministic parsing and validation work.

### Phase 3: File-Type Routing and Multimodal Sampling

Deliverables:

- ingestion Lambda/Step Functions workflow;
- file type router;
- PDF multimodal sampling for OCR-quality decision;
- image description generation path;
- placeholders for Word, HTML, CSV, Excel, and PowerPoint routes;
- warning/confidence metadata for every route.

### Phase 4: Knowledge Base Ingestion Update

Deliverables:

- point Knowledge Base ingestion at `kb_index/`;
- test no-chunking or boundary-preserving chunking with section/page artifacts;
- verify Bedrock returns `document_id`, `chunk_id`, `location`, `source_location`, section/page metadata, and authorization metadata;
- enable/index metadata filtering support in Aurora as needed.

### Phase 5: Backend Catalog and Library API

Deliverables:

- catalog query service;
- Library list endpoint;
- authorization-filtered document rows;
- initial agent catalog tool returning only `document_id`, `document_title`, and `document_description`;
- basic filters/sorts for display metadata;
- frontend interface documentation update.

### Phase 6: Retrieval Planner and Direct Artifact Fetch

Deliverables:

- backend artifact fetch service with authorization checks;
- direct lookup by `chunk_id`, `section`, and `document_id`;
- optional table-of-contents context for the agent;
- answer prompt support for direct-fetched artifacts;
- trace events that distinguish semantic retrieval from direct lookup.

### Phase 7: Evidence and Source UX

Deliverables:

- cited evidence text;
- deterministic quote matching against cited chunks;
- source cards with section/page metadata;
- hover previews using highlighted evidence;
- raw document open action that generates authorized short-lived URLs.

### Phase 8: Visual/Table Artifacts

Deliverables:

- table extraction into markdown/JSON artifacts;
- image/figure extraction into S3 assets;
- generated summaries for image/table retrieval;
- direct image/table fetch path for answer LLM inspection;
- table/image chunk metadata in source cards and Library details where useful.

### Phase 9: Librarian Agent

Deliverables:

- catalog quality scan job;
- OCR repair candidate detection;
- metadata cleanup suggestions;
- optional reprocessing queue;
- reporting for documents/chunks with low confidence or missing structure.

### Phase 10: User Uploads

Deliverables:

- upload session endpoint;
- landing/pending S3 upload prefix;
- async ingestion job status;
- upload-specific access defaults;
- Library integration for pending/processing/ready/failed states.

## Decisions Answered

| Question | Decision |
| --- | --- |
| Should Option C be the implementation backbone? | Yes. Use preprocessed derivative text and non-text chunks plus app-owned catalog. |
| Should the canonical id be `source_id` or `document_id`? | Use `document_id` for original documents. Treat `source_id` as legacy/research wording if encountered. |
| Should raw PDFs be indexed into the KB? | No by default. Store raw PDFs in `kb_raw/`; index clean text artifacts from `kb_index/`. |
| Should direct S3 pulls be allowed? | Yes, but only through backend catalog lookup, authorization, URI validation, and size/type limits. |
| Should the Bedrock vector table be our catalog? | No. It can be queried as an optimization, but catalog/document identity belongs to the app. |
| Should Library use Bedrock as source of truth? | No. Library should use app-owned `document_catalog`. |
| Should access control be applied before the agent sees the index? | Yes. The agent only gets authorized catalog/TOC/retrieval results. |
| Should user uploads go through the same metadata path? | Yes. Uploads should create raw files, derivative artifacts, catalog rows, sidecars, and KB sync jobs. |
| Should LLM-assisted metadata extraction be first? | No. Start deterministic; add LLM assistance after validation exists. |
| Should a librarian agent be required in the first release? | No. Record confidence/warnings now so a later librarian can repair issues. |

## Near-Term Next Steps

1. Write `docs/knowledge_base_metadata.md` with the standard document/chunk metadata contract.
2. Create metadata schema and field registry files.
3. Add Aurora catalog schema SQL using `document_catalog` and `document_chunks`.
4. Build a deterministic preprocessor for one building-code pilot document.
5. Generate `kb_index/` artifacts and sidecars.
6. Update Knowledge Base ingestion to index only `kb_index/`.
7. Add catalog-backed Library endpoint after catalog rows are available.
8. Add the initial agent catalog tool that returns authorized `document_id`, `document_title`, and `document_description`.
