-- MemoPilot workspace scoping updates
-- Version: 9

ALTER TABLE memory_items ADD COLUMN workspace_root TEXT;
ALTER TABLE evidence_sources ADD COLUMN workspace_root TEXT;
ALTER TABLE document_chunks ADD COLUMN workspace_root TEXT;
ALTER TABLE task_runs ADD COLUMN workspace_root TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_items_workspace_root
ON memory_items(workspace_root, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_evidence_sources_workspace_root
ON evidence_sources(workspace_root, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_document_chunks_workspace_root
ON document_chunks(workspace_root, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_runs_workspace_root
ON task_runs(workspace_root, created_at DESC);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (9, NULL);
