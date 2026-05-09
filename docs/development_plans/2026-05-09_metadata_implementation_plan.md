# Metadata Implementation Plan

Created: 2026-05-09  
Status: Proposed implementation path  
Scope: Concrete metadata, artifact, catalog, retrieval, and authorization implementation plan for the `chat-template` RAG backend.

This plan turns the broader research in `2026-05-05_knowledge_base_metadata_strategy_research.md` into a specific implementation path. The research document should remain the place for alternatives and background. This document should track the route we intend to build.

## Chosen Direction

Use Option C from the research document as the backbone: a preprocessed derivative corpus with stable app-owned source, chunk, and asset identifiers.

Core decision:

1. Store each original document once in S3 as the durable source of truth.
2. Generate derivative artifacts from the original document:
   - section/page text chunks;
   - table markdown/JSON artifacts;
   - image/figure assets;
   - optional image/table summaries for text retrieval.
3. Store derivative artifacts in S3 under controlled prefixes.
4. Store an app-owned source catalog in Aurora so the backend can perform fast lookup, authorization, Library listing, and ingestion status checks.
5. Index only the derivative text artifacts into Bedrock Knowledge Bases.
6. Use Bedrock semantic/hybrid retrieval for open-ended questions.
7. Use the app catalog plus S3 artifact fetches for direct section/page/image lookup when the agent already knows what it wants to read.

Important distinction: the Bedrock vector table stores chunk text in `bedrock_integration.bedrock_kb.chunks`, but that table should not be our durable source catalog. Bedrock owns ingestion into that table, and row ids/chunk boundaries can change after resync, parser changes, or chunking changes. We can query it as an optimization, but the stable application contract should be the catalog plus S3 artifacts.

## Target S3 Layout

Keep raw documents, indexable text, and non-text assets separate so the Knowledge Base does not accidentally ingest duplicate or unsupported artifacts.

```text
kb_raw/{source_id}/original/{filename}
kb_raw/{source_id}/source_metadata.json

kb_index/{source_id}/sections/{chunk_id}.md
kb_index/{source_id}/sections/{chunk_id}.md.metadata.json
kb_index/{source_id}/pages/{chunk_id}.md
kb_index/{source_id}/pages/{chunk_id}.md.metadata.json
kb_index/{source_id}/tables/{asset_id}.summary.md
kb_index/{source_id}/tables/{asset_id}.summary.md.metadata.json
kb_index/{source_id}/figures/{asset_id}.summary.md
kb_index/{source_id}/figures/{asset_id}.summary.md.metadata.json

kb_artifacts/{source_id}/tables/{asset_id}.md
kb_artifacts/{source_id}/tables/{asset_id}.json
kb_artifacts/{source_id}/figures/{asset_id}.png
kb_artifacts/{source_id}/figures/{asset_id}.json

kb_catalog/{environment}/kb_catalog.json
kb_catalog/{environment}/kb_summary.md
kb_catalog/{environment}/metadata_manifest.jsonl
```

Knowledge Base inclusion prefix should point at `kb_index/`, not `kb_raw/` or `kb_artifacts/`.

Reason: the KB should index clean text artifacts and sidecar metadata. Raw PDFs and extracted image assets should stay available for authorized fetch/viewer flows without being duplicated into the semantic index.

## Stable Identifiers

Every source and artifact needs a stable app-owned id before Bedrock ingestion.

Recommended ids:

| Identifier | Example | Purpose |
| --- | --- | --- |
| `source_id` | `irc-2021-sd` | Stable original document id. |
| `chunk_id` | `irc-2021-sd-section-r311-7-5-1` | Stable indexable text artifact id. |
| `asset_id` | `irc-2021-sd-page-52-figure-01` | Stable image/table/figure artifact id. |
| `original_location` | `s3://bucket/kb_raw/.../original.pdf` | Internal pointer to source-of-truth file. |
| `location` | `s3://bucket/kb_index/.../section.md` | Internal pointer to the indexed artifact. |

Do not use Bedrock vector row ids as public API ids. Use app-owned ids in metadata and catalog rows, then map Bedrock retrieval results back to those ids.

## Metadata Model

Use the standard metadata core from the research document for every original source and derivative artifact:

- `metadata_schema_version`
- `source_id`
- `filename`
- `uploaded_at`
- `location`
- `document_type`
- `source_status`
- `access_tier`
- `ingested_at`

Derivative text artifacts should also include:

- `chunk_id`
- `original_location`
- `content_kind`
- `chunk_strategy`
- `chunk_sequence`
- `page_start`
- `page_end`
- `chapter`
- `section`
- `heading_path`

Visual/table assets should include:

- `asset_id`
- `source_id`
- `original_location`
- `asset_location`
- `summary_location`
- `content_kind`
- `page_number`
- `caption`
- `extraction_method`
- `extraction_confidence`

Authorization metadata should be present on every source and propagated to all derivative artifacts:

- `tenant_id`
- `collection_id`
- `access_tier`
- `allowed_groups`
- `owner_user_id`
- `visibility`

## Catalog Tables

Use Aurora as the app-owned catalog store. Keep generated JSON files for debugging, review, and portability, but do not make the frontend or agent depend on reading a giant catalog file directly.

Proposed tables:

```sql
CREATE TABLE kb_sources (
    source_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_status TEXT NOT NULL,
    access_tier TEXT NOT NULL,
    original_location TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE kb_chunks (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES kb_sources(source_id),
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    content_kind TEXT NOT NULL,
    location TEXT NOT NULL,
    title TEXT,
    chapter TEXT,
    section TEXT,
    heading_path TEXT,
    page_start INTEGER,
    page_end INTEGER,
    chunk_sequence INTEGER NOT NULL,
    token_estimate INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE kb_assets (
    asset_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES kb_sources(source_id),
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    content_kind TEXT NOT NULL,
    asset_location TEXT NOT NULL,
    summary_location TEXT,
    page_number INTEGER,
    caption TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE kb_ingestion_jobs (
    ingestion_job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    source_id TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error_code TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE kb_source_access_grants (
    grant_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES kb_sources(source_id),
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
- Keep each file below an agreed token limit.
- Prefer "no chunking" or a chunking strategy that preserves each generated file as closely as possible.
- If a section is too large, split it deterministically into part files:
  - `section-r311-7-5-1-part-001.md`
  - `section-r311-7-5-1-part-002.md`
  - metadata includes the same `section` plus `chunk_sequence`.

Why this matters: if we want "one vector result = one known section or section part", we should not upload large files and then rely on Bedrock fixed-size chunking to preserve our boundaries.

Each `kb_index` file gets a Bedrock sidecar `.metadata.json` with:

- stable source/chunk ids;
- authorization fields;
- display fields;
- page/section fields;
- `location` pointing at the indexed text artifact;
- `original_location` pointing at the raw source document.

## Direct Artifact Retrieval from S3

The backend should support direct artifact fetches for agent workflows, but only after catalog lookup and authorization.

Example use cases:

- User asks about a known code section and the planner finds that section in the catalog.
- The model reads a table of contents and decides it needs a specific section.
- A retrieved image summary indicates that the actual figure may be answer-critical.
- The frontend opens a cited source and requests the source text or raw document viewer.

Backend flow:

1. Agent or frontend asks for a `source_id`, `chunk_id`, or `asset_id`.
2. Backend loads the catalog row.
3. Backend checks user authorization against tenant, collection, access tier, group, owner, and explicit grants.
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

### Semantic retrieval

Current/default path:

- User asks a question.
- Backend applies server-side authorization filters.
- Bedrock retrieves relevant chunks from the Knowledge Base.
- Answer LLM receives retrieved chunks and metadata.
- Citation post-processing returns only cited sources.

### Catalog-planned retrieval

Future planner path:

- Backend loads an authorization-filtered `kb_summary` or table of contents.
- Agent decides whether to use semantic retrieval, direct lookup, or both.
- If it selects a known section/page/source, backend fetches that artifact directly from S3 or catalog-backed storage.

### Direct lookup

For exact references:

- query by `section`, `chapter`, `page_number`, `source_id`, `chunk_id`, or `asset_id`;
- fetch the specific text/table/image artifact;
- add it to answer context with citation metadata.

### Hybrid retrieval

Later path:

- use Bedrock hybrid search where available;
- use Aurora full-text search over app-owned chunk rows or Bedrock `chunks` as an optimization;
- merge semantic results with exact section/table-of-contents matches;
- deduplicate by `chunk_id`.

## Authorization Strategy

The agent should never see the global catalog or global table of contents. It should only see a backend-generated, authorization-filtered view for the current user.

Authorization must apply to all paths:

- Library view;
- agent table of contents;
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
   - explicit source grants;
   - ownership for user-uploaded documents.
3. Build an authorized source scope.
4. Filter Library/catalog responses with that scope.
5. Inject metadata filters into Bedrock retrieval using coarse fields such as `tenant_id`, `collection_id`, `access_tier`, and `allowed_groups`.
6. Post-filter Bedrock results against the catalog before sending chunks to the answer LLM.
7. Deny direct S3 artifact fetches unless the catalog row is authorized.

Important security note: Bedrock metadata filters are a useful enforcement layer, but they should not be the only enforcement layer. The backend should also validate returned `source_id`/`chunk_id` values against the app-owned catalog before sending retrieved chunks to the model or frontend.

If per-document ACLs become too large to express cleanly as Bedrock filters, prefer one of these approaches:

- keep coarse filters in Bedrock and post-filter strictly in the backend;
- split highly restricted corpora into separate data sources or Knowledge Bases;
- use direct catalog/DB lookup for strict-access documents;
- route user-uploaded or tenant-private documents through separate KBs/data sources if needed.

## Library View Strategy

The Library view should be backed by `kb_sources`, not by Bedrock directly.

Library API should return only authorized source rows:

```json
{
  "sources": [
    {
      "source_id": "irc-2021-sd",
      "title": "2021 IRC - South Dakota",
      "filename": "2021-irc-sd.pdf",
      "document_type": "building_code",
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

## User Upload Strategy

Frontend drag-and-drop upload should reuse this same pipeline.

Later target flow:

1. Frontend asks backend for an upload session.
2. Backend authorizes upload into a tenant/collection.
3. Backend returns a short-lived S3 upload URL.
4. Frontend uploads the raw file to a pending S3 prefix.
5. Backend or S3 event starts an ingestion job.
6. Ingestion job validates the file, generates metadata, creates derivative artifacts, writes catalog rows, writes Bedrock sidecars, and starts/schedules Knowledge Base sync.
7. Frontend polls or subscribes to ingestion status.
8. Once ready, the uploaded document appears in Library and retrieval.

Uploaded documents must default to restricted visibility until metadata, status, and ACLs are explicitly assigned.

## Implementation Phases

### Phase 1: Schema and config

Deliverables:

- `docs/knowledge_base_metadata.md`
- `config/metadata/knowledge_base_metadata.schema.json`
- `config/metadata/metadata_fields.yaml`
- initial SQL schema for `kb_sources`, `kb_chunks`, `kb_assets`, `kb_ingestion_jobs`, and `kb_source_access_grants`
- deployment script update to apply catalog schema

### Phase 2: Deterministic preprocessing pilot

Deliverables:

- local/scripted preprocessor for a small building-code corpus;
- canonical S3 key generation;
- deterministic section/page text extraction where feasible;
- table of contents extraction;
- generated markdown section/page artifacts;
- generated sidecar metadata;
- generated `metadata_manifest.jsonl`, `kb_catalog.json`, and `kb_summary.md`;
- catalog DB inserts.

Do not use LLM extraction first. Add it after deterministic parsing and validation work.

### Phase 3: Knowledge Base ingestion update

Deliverables:

- point Knowledge Base ingestion at `kb_index/`;
- test no-chunking or boundary-preserving chunking with section/page artifacts;
- verify Bedrock returns `source_id`, `chunk_id`, `location`, `original_location`, section/page metadata, and authorization metadata;
- enable/index metadata filtering support in Aurora as needed.

### Phase 4: Backend catalog and Library API

Deliverables:

- catalog query service;
- Library list endpoint;
- authorization-filtered source rows;
- basic filters/sorts for display metadata;
- frontend interface documentation update.

### Phase 5: Retrieval planner and direct artifact fetch

Deliverables:

- backend artifact fetch service with authorization checks;
- direct lookup by `chunk_id`, `section`, and `source_id`;
- optional table-of-contents context for the agent;
- answer prompt support for direct-fetched artifacts;
- trace events that distinguish semantic retrieval from direct lookup.

### Phase 6: Evidence and source UX

Deliverables:

- cited evidence text;
- deterministic quote matching against cited chunks;
- source cards with section/page metadata;
- hover previews using highlighted evidence;
- raw document open action that generates authorized short-lived URLs.

### Phase 7: Visual/table artifacts

Deliverables:

- table extraction into markdown/JSON artifacts;
- image/figure extraction into S3 assets;
- generated summaries for image/table retrieval;
- direct image/table fetch path for answer LLM inspection;
- asset metadata in source cards and Library details where useful.

### Phase 8: User uploads

Deliverables:

- upload session endpoint;
- pending S3 upload prefix;
- async ingestion job status;
- upload-specific access defaults;
- Library integration for pending/processing/ready/failed states.

## Decisions Answered

| Question | Decision |
| --- | --- |
| Should Option C be the implementation backbone? | Yes. Use preprocessed derivative text/assets plus app-owned catalog. |
| Should raw PDFs be indexed into the KB? | No by default. Store raw PDFs in `kb_raw/`; index clean text artifacts from `kb_index/`. |
| Should direct S3 pulls be allowed? | Yes, but only through backend catalog lookup, authorization, URI validation, and size/type limits. |
| Should the Bedrock vector table be our catalog? | No. It can be queried as an optimization, but catalog/source identity belongs to the app. |
| Should Library use Bedrock as source of truth? | No. Library should use app-owned `kb_sources`. |
| Should access control be applied before the agent sees the index? | Yes. The agent only gets authorized catalog/TOC/retrieval results. |
| Should user uploads go through the same metadata path? | Yes. Uploads should create raw files, derivative artifacts, catalog rows, sidecars, and KB sync jobs. |
| Should LLM-assisted metadata extraction be first? | No. Start deterministic; add LLM extraction after validation exists. |

## Near-Term Next Steps

1. Write `docs/knowledge_base_metadata.md` with the standard metadata contract.
2. Create metadata schema and field registry files.
3. Add Aurora catalog schema SQL.
4. Build a deterministic preprocessor for one building-code pilot document.
5. Generate `kb_index/` artifacts and sidecars.
6. Update Knowledge Base ingestion to index only `kb_index/`.
7. Add catalog-backed Library endpoint after catalog rows are available.
