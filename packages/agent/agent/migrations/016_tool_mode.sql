-- Tool mode support: caller tracking, tool sessions, and tool call events

-- task_runs: add source tracking for tool-mode calls
ALTER TABLE task_runs ADD COLUMN source TEXT DEFAULT 'memopilot_ui';

-- task_runs: flag whether patch governance was available
ALTER TABLE task_runs ADD COLUMN patch_governance_available INTEGER NOT NULL DEFAULT 1;

-- tool_mode_sessions: track active tool mode integrations
CREATE TABLE IF NOT EXISTS tool_mode_sessions (
    id TEXT PRIMARY KEY,
    caller TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    first_call_at TEXT NOT NULL,
    last_call_at TEXT NOT NULL,
    total_calls INTEGER NOT NULL DEFAULT 0,
    total_context_tokens_returned INTEGER NOT NULL DEFAULT 0,
    total_redacted_values INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_tool_mode_sessions_caller
    ON tool_mode_sessions(caller, active);

-- tool_call_events: per-call record for auditing
CREATE TABLE IF NOT EXISTS tool_call_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES tool_mode_sessions(id),
    task_run_id TEXT,
    tool_name TEXT NOT NULL,
    caller TEXT NOT NULL,
    context_pack_hash TEXT,
    output_tokens INTEGER DEFAULT 0,
    stale_exclusion_count INTEGER DEFAULT 0,
    redacted_values INTEGER DEFAULT 0,
    patch_review_triggered INTEGER DEFAULT 0,
    writeback_triggered INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_call_events_session
    ON tool_call_events(session_id);

CREATE INDEX IF NOT EXISTS idx_tool_call_events_created
    ON tool_call_events(created_at);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (16, NULL);
