CREATE TABLE IF NOT EXISTS task_patterns (
    id TEXT PRIMARY KEY,
    pattern_type TEXT NOT NULL,
    context_path TEXT,
    details_json TEXT,
    suggestion TEXT,
    workspace_root TEXT,
    surfaced_at TEXT NOT NULL DEFAULT (datetime('now')),
    dismissed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_patterns_workspace ON task_patterns(workspace_root);
CREATE INDEX IF NOT EXISTS idx_task_patterns_type ON task_patterns(pattern_type);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (21, NULL);
