-- Document catalog schema for knowledge-base ingestion.
-- These tables are the app-owned source of truth for Library, authorization,
-- direct artifact lookup, and ingestion status. Bedrock vector rows are an
-- index generated from this catalog and the S3 artifacts.

CREATE TABLE IF NOT EXISTS document_catalog (
    document_id VARCHAR(255) PRIMARY KEY,
    tenant_id VARCHAR(255) NOT NULL,
    collection_id VARCHAR(255) NOT NULL,
    metadata_schema_version VARCHAR(40) NOT NULL DEFAULT '1.0',
    document_title TEXT NOT NULL,
    document_description TEXT,
    document_type VARCHAR(80) NOT NULL,
    filename TEXT NOT NULL,
    landing_s3_uri TEXT,
    source_location TEXT NOT NULL,
    location TEXT NOT NULL,
    source_status VARCHAR(40) NOT NULL DEFAULT 'processing',
    access_tier VARCHAR(40) NOT NULL DEFAULT 'restricted',
    publish_date DATE,
    version_label VARCHAR(255),
    jurisdiction VARCHAR(255),
    code_family VARCHAR(255),
    uploaded_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    processing JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id VARCHAR(255) PRIMARY KEY,
    document_id VARCHAR(255) NOT NULL REFERENCES document_catalog(document_id) ON DELETE CASCADE,
    tenant_id VARCHAR(255) NOT NULL,
    collection_id VARCHAR(255) NOT NULL,
    metadata_schema_version VARCHAR(40) NOT NULL DEFAULT '1.0',
    chunk_type VARCHAR(80) NOT NULL,
    chunk_index INTEGER,
    document_title TEXT,
    title TEXT,
    chapter VARCHAR(255),
    section VARCHAR(255),
    page_start INTEGER,
    page_end INTEGER,
    location TEXT NOT NULL,
    source_location TEXT NOT NULL,
    content_type VARCHAR(120) NOT NULL DEFAULT 'text/markdown',
    token_count INTEGER,
    char_count INTEGER,
    embedding_model_id VARCHAR(255),
    access_tier VARCHAR(40) NOT NULL DEFAULT 'restricted',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    processing JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_ingestion_jobs (
    job_id UUID PRIMARY KEY,
    landing_s3_uri TEXT NOT NULL,
    document_id VARCHAR(255) REFERENCES document_catalog(document_id) ON DELETE SET NULL,
    tenant_id VARCHAR(255) NOT NULL,
    collection_id VARCHAR(255) NOT NULL,
    document_type VARCHAR(80),
    filename TEXT,
    status VARCHAR(40) NOT NULL DEFAULT 'processing',
    stage VARCHAR(120),
    input_event JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_access_grants (
    grant_id UUID PRIMARY KEY,
    document_id VARCHAR(255) NOT NULL REFERENCES document_catalog(document_id) ON DELETE CASCADE,
    tenant_id VARCHAR(255) NOT NULL,
    principal_type VARCHAR(40) NOT NULL,
    principal_id VARCHAR(255) NOT NULL,
    access_level VARCHAR(40) NOT NULL DEFAULT 'read',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_document_catalog_tenant_collection_status
ON document_catalog (tenant_id, collection_id, source_status, document_id);

CREATE INDEX IF NOT EXISTS idx_document_catalog_type_status
ON document_catalog (document_type, source_status);

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_order
ON document_chunks (document_id, chunk_index, chunk_id);

CREATE INDEX IF NOT EXISTS idx_document_chunks_tenant_collection
ON document_chunks (tenant_id, collection_id, document_id);

CREATE INDEX IF NOT EXISTS idx_document_chunks_section
ON document_chunks (document_id, section);

CREATE INDEX IF NOT EXISTS idx_document_ingestion_jobs_status
ON document_ingestion_jobs (tenant_id, collection_id, status, started_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_document_ingestion_jobs_landing_s3_uri
ON document_ingestion_jobs (landing_s3_uri);

CREATE INDEX IF NOT EXISTS idx_document_access_grants_principal
ON document_access_grants (tenant_id, principal_type, principal_id, access_level);

CREATE UNIQUE INDEX IF NOT EXISTS idx_document_access_grants_unique_principal
ON document_access_grants (document_id, principal_type, principal_id);
