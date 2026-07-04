-- Track externally ingested AI session transcripts to prevent duplicate writeback.
CREATE TABLE IF NOT EXISTS ingested_sessions (
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL DEFAULT '',
    transcript_path TEXT,
    facts_count INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, session_id, workspace_root)
);

CREATE INDEX IF NOT EXISTS idx_ingested_sessions_workspace
    ON ingested_sessions(workspace_root, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_ingested_sessions_source_time
    ON ingested_sessions(source, ingested_at DESC);
