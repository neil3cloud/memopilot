-- Memory manager usage tracking and decay
-- Version: 15

ALTER TABLE memory_items ADD COLUMN last_used_at TEXT;
ALTER TABLE memory_items ADD COLUMN usage_count INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_memory_items_last_used
    ON memory_items(last_used_at);

CREATE INDEX IF NOT EXISTS idx_memory_items_status_created
    ON memory_items(memory_status, created_at);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (15, NULL);
