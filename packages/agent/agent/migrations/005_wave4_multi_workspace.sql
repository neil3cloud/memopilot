-- MemoPilot Wave 4 schema additions
-- Version: 5

CREATE TABLE IF NOT EXISTS workspace_roots (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_workspace_roots_active
ON workspace_roots(active, updated_at DESC);
