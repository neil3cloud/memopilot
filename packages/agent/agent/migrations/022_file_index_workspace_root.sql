-- Add workspace_root to file_index for multi-workspace scoping
-- Version: 10

ALTER TABLE file_index ADD COLUMN workspace_root TEXT;

CREATE INDEX IF NOT EXISTS idx_file_index_workspace_root
ON file_index(workspace_root, file_path);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (22, NULL);
