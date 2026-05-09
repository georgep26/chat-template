# Knowledge Base Metadata

This document defines the application-owned metadata contract for documents and
chunks in the chat-template knowledge-base ingestion pipeline.

The application catalog and S3 artifacts are the durable source of truth.
Bedrock Knowledge Bases and the vector table are retrieval indexes generated
from those artifacts, not the canonical document registry.

## Storage Layers

Each ingested document should preserve three layers:

1. Raw document in S3 under `kb_raw/{document_id}/`.
2. App-owned derivative artifacts in S3 under `kb_index/` and `kb_artifacts/`.
3. Bedrock-generated vector rows produced from `kb_index/`.

The Knowledge Base inclusion prefix should point at `kb_index/`. Do not index
`kb_raw/`, `kb_landing/`, or `kb_artifacts/` by default.

## Identifiers

| Field | Scope | Purpose |
| --- | --- | --- |
| `landing_s3_uri` | Ingestion job/document | Temporary processing identifier before `document_id` exists. |
| `document_id` | Document/chunk | Stable app-owned id for the original uploaded document. |
| `chunk_id` | Chunk | Stable app-owned id for a citable/retrievable slice of a document. |
| `location` | Document/chunk | S3 URI for the current row. For documents this points to the raw file; for chunks this points to the chunk artifact. |
| `source_location` | Document/chunk | S3 URI for the raw source document. |

`document_id` should be generated only after initial metadata extraction has
completed. During early processing, use `landing_s3_uri` as the unique id.

## Document Metadata

Required document-level fields:

| Field | Type | Notes |
| --- | --- | --- |
| `metadata_schema_version` | string | Current version is `1.0`. |
| `record_type` | string | `document`. |
| `document_id` | string | App-owned document id. |
| `document_title` | string | Human-readable title. |
| `document_type` | string | `pdf`, `word_doc`, `image`, `web_page`, `csv`, `excel`, `powerpoint`, or `unknown`. |
| `filename` | string | Original filename. |
| `tenant_id` | string | Current implementation uses this as the owner/user namespace. |
| `collection_id` | string | Logical collection/library grouping. |
| `location` | string | Raw document S3 URI. |
| `source_location` | string | Raw document S3 URI. Same as `location` for document rows. |
| `source_status` | string | `landing`, `processing`, `ready`, `failed`, `archived`, or `deleted`. |
| `access_tier` | string | `restricted`, `tenant`, or `public`. Default to `restricted`. |

Common optional fields:

| Field | Type | Notes |
| --- | --- | --- |
| `document_description` | string | Short catalog description for the agent and Library view. |
| `landing_s3_uri` | string | Original landing-zone URI. |
| `uploaded_at` | timestamp | Source object upload time if available. |
| `ingested_at` | timestamp | Time metadata was written. |
| `publish_date` | date/string | Source document publication date when known. |
| `version_label` | string | Human-readable version or revision label. |
| `jurisdiction` | string | Useful for building-code and policy corpora. |
| `code_family` | string | Example: `IRC`, `IBC`, `local_amendment`. |
| `processing` | object | Extraction status, warnings, confidence, and route-specific details. |
| `custom_metadata` | object | Flexible user/domain-specific fields. |

## Chunk Metadata

Chunk metadata is written beside each indexable artifact as a Bedrock sidecar
file, for example:

```text
kb_index/{document_id}/sections/{chunk_id}.md
kb_index/{document_id}/sections/{chunk_id}.md.metadata.json
```

Required chunk-level fields:

| Field | Type | Notes |
| --- | --- | --- |
| `metadata_schema_version` | string | Current version is `1.0`. |
| `record_type` | string | `chunk`. |
| `document_id` | string | Parent document id. |
| `chunk_id` | string | Stable app-owned chunk id. |
| `chunk_type` | string | `section`, `page`, `table_summary`, `figure_summary`, `image_summary`, or `fallback_text`. |
| `tenant_id` | string | Same authorization namespace as the document. |
| `collection_id` | string | Same collection as the document. |
| `location` | string | S3 URI for the chunk artifact. |
| `source_location` | string | S3 URI for the raw document. |
| `access_tier` | string | Copied from document-level access unless overridden. |

Common optional chunk fields:

| Field | Type | Notes |
| --- | --- | --- |
| `document_title` | string | Denormalized for retrieval filtering/display. |
| `section` | string | Section identifier or heading, such as `R311.7.5.1`. |
| `chapter` | string | Chapter identifier or title. |
| `page_start` | integer | First source page covered by the chunk. |
| `page_end` | integer | Last source page covered by the chunk. |
| `chunk_index` | integer | Stable ordering within the document. |
| `token_count` | integer | Token count used for chunking validation. |
| `char_count` | integer | Character count of the artifact text. |
| `content_type` | string | Artifact content type, such as `text/markdown`. |
| `custom_metadata` | object | Flexible domain metadata. |

## Authorization

The agent should only see catalog rows, table-of-contents summaries, retrieval
results, and direct artifact fetches that the authenticated user is authorized
to access.

For now, `tenant_id` means the user/owner namespace. Later multi-tenant support
can widen that concept, but backend authorization should still filter before
the agent receives document options.

Uploaded documents should default to `access_tier: restricted` until explicit
grants or collection-level rules are applied.

## Catalog Database Tables

The Aurora catalog schema is deployment-managed in `sql/document_catalog_schema.sql`.

Tables:

- `document_catalog`: one row per original document.
- `document_chunks`: one row per durable chunk/artifact.
- `document_ingestion_jobs`: ingestion status and error tracking.
- `document_access_grants`: explicit document access assignments.

The Library view should read from `document_catalog` and `document_chunks`, not
from the Bedrock vector table.

## Config

Ingestion tuning values live under `ingestion` in `config/<env>/app_config.yaml`.
Do not hide default ingestion behavior in Lambda code; add tunable values to
app config with comments.

Current Phase 1 PDF tuning fields:

- `pdf_text_probe_pages`
- `pdf_text_available_min_chars`
- `pdf_always_ocr`
- `pdf_front_page_sample_count`
- `pdf_random_page_sample_count`
- `pdf_default_chunk_tokens`
- `pdf_default_chunk_overlap`
- `pdf_embedding_chunk_token_limit_titan_v2`

