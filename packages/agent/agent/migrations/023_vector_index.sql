-- Vector index support for semantic search
-- Requires: sqlite-vec extension for vector operations
-- Version: 23

-- Vector storage for symbol embeddings and memory items
CREATE TABLE IF NOT EXISTS vectors (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,  -- 'symbol', 'memory_item', 'context_pack'
    entity_id TEXT NOT NULL,
    embedding BLOB NOT NULL,  -- Vector bytes (sqlite-vec format)
    dimension INTEGER NOT NULL,
    model TEXT NOT NULL,  -- e.g., 'ollama:nomic-embed-text', 'anthropic:text-embedding-3-small'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, model)
);

-- Index for efficient lookup by entity
CREATE INDEX IF NOT EXISTS idx_vectors_entity
ON vectors(entity_type, entity_id);

-- Index for created_at (for cleanup/backfill)
CREATE INDEX IF NOT EXISTS idx_vectors_created_at
ON vectors(created_at);

-- Track vector indexing status per workspace
CREATE TABLE IF NOT EXISTS vector_index_status (
    workspace_root TEXT PRIMARY KEY,
    symbols_indexed INTEGER DEFAULT 0,
    memory_items_indexed INTEGER DEFAULT 0,
    last_indexed_at DATETIME,
    last_indexed_model TEXT,
    embedding_dimension INTEGER
);

-- Vector configuration (capped to one row)
CREATE TABLE IF NOT EXISTS vector_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled BOOLEAN DEFAULT 1,
    preferred_model TEXT DEFAULT 'ollama:nomic-embed-text',
    embedding_dimension INTEGER DEFAULT 768,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Initialize config if not exists
INSERT OR IGNORE INTO vector_config (id, enabled, preferred_model, embedding_dimension)
VALUES (1, 1, 'ollama:nomic-embed-text', 768);

-- Update schema version
DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (23, 768);
