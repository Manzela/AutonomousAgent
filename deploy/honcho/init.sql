-- Honcho bootstrap: ensures pgvector extension is enabled when Honcho first starts.
-- Honcho's own migrations create the rest of the schema.
CREATE EXTENSION IF NOT EXISTS vector;
