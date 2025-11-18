-- ============================================================================
-- Embeddings Table Setup for AWS Bedrock Knowledge Base
-- ============================================================================
-- This script sets up pgvector extension and creates the table structure
-- required for AWS Bedrock Knowledge Base with Aurora PostgreSQL.
--
-- Prerequisites:
-- - Aurora PostgreSQL 15.7 (as specified in light_db_template.yaml)
-- - Database: chat_template_db (as specified in knowledge_base_template.yaml)
--
-- Table Structure:
-- - Schema: bedrock_integration
-- - Table: bedrock_kb
-- - Fields match the FieldMapping in knowledge_base_template.yaml:
--   * id: Primary key
--   * embedding: Vector column (1024 dimensions for amazon.titan-embed-text-v2)
--   * chunks: Text content
--   * metadata: JSON metadata
-- ============================================================================

-- Enable pgvector extension
-- Note: pgvector must be available in your Aurora PostgreSQL instance
CREATE EXTENSION IF NOT EXISTS vector;

-- Create the schema if it doesn't exist
CREATE SCHEMA IF NOT EXISTS bedrock_integration;

-- Create the bedrock_kb table with all required fields
CREATE TABLE IF NOT EXISTS bedrock_integration.bedrock_kb (
    id uuid PRIMARY KEY,
    embedding vector(1024),  -- 1024 dimensions for amazon.titan-embed-text-v2 (default model)
    chunks TEXT NOT NULL,
    metadata JSONB
);

-- Create an index on the vector column for efficient similarity search
-- Using HNSW index for better performance on large datasets
-- Note: HNSW requires pgvector 0.5.0+ and may not be available in all Aurora versions
-- This index is REQUIRED by AWS Bedrock Knowledge Base
CREATE INDEX IF NOT EXISTS bedrock_kb_embedding_idx 
ON bedrock_integration.bedrock_kb 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Alternative: If HNSW is not available, use ivfflat index instead
-- Note: Bedrock Knowledge Base requires an index on the embedding column
-- If HNSW fails, uncomment the following and comment out the above:
-- CREATE INDEX IF NOT EXISTS bedrock_kb_embedding_idx 
-- ON bedrock_integration.bedrock_kb 
-- USING ivfflat (embedding vector_cosine_ops)
-- WITH (lists = 100);

-- Create an index on metadata for faster filtering queries
-- CREATE INDEX IF NOT EXISTS bedrock_kb_metadata_idx 
-- ON bedrock_integration.bedrock_kb 
-- USING GIN (metadata);

-- Create a GIN index on the chunks column for full-text search
-- This is required by AWS Bedrock Knowledge Base
CREATE INDEX IF NOT EXISTS bedrock_kb_chunks_idx 
ON bedrock_integration.bedrock_kb 
USING gin (to_tsvector('english', chunks));

-- Grant necessary permissions (adjust as needed for your setup)
-- The Bedrock Knowledge Base service role will need access to this table
-- This is typically handled via the IAM role and RDS Data API, but you may
-- need to grant permissions if using direct database connections

-- Example: Grant permissions to a specific role (adjust role name as needed)
-- GRANT USAGE ON SCHEMA bedrock_integration TO <your_role>;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON bedrock_integration.bedrock_kb TO <your_role>;

-- ============================================================================
-- Notes:
-- ============================================================================
-- 1. Vector Dimension:
--    - amazon.titan-embed-text-v1: 1536 dimensions
--    - amazon.titan-embed-text-v2: 1024 dimensions (default)
--    - cohere.embed-english-v3: 1024 dimensions
--    - cohere.embed-multilingual-v3: 1024 dimensions
--    If using a different model, adjust the vector dimension accordingly.
--
-- 2. Index Type:
--    - HNSW (Hierarchical Navigable Small World) is preferred for better
--      performance but requires pgvector 0.5.0+
--    - IVFFlat is an alternative that works with older pgvector versions
--
-- 3. Index Parameters:
--    - HNSW: m (number of connections) and ef_construction (search width)
--    - IVFFlat: lists (number of clusters)
--    Adjust these based on your data size and query patterns.
--
-- 4. This script is idempotent - it can be run multiple times safely.
-- ============================================================================

