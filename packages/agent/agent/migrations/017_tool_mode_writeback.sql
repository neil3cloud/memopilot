-- Tool mode writeback support

PRAGMA foreign_keys = OFF;
PRAGMA legacy_alter_table = ON;

ALTER TABLE task_runs RENAME TO task_runs_old_017;

CREATE TABLE task_runs (
    id TEXT PRIMARY KEY,
    user_request TEXT NOT NULL,
    task_type TEXT,
    mode TEXT,
    risk_level TEXT,
    active_rules_json TEXT CHECK (json_valid(active_rules_json) OR active_rules_json IS NULL),
    active_skills_json TEXT CHECK (json_valid(active_skills_json) OR active_skills_json IS NULL),
    context_pack_path TEXT,
    selected_model TEXT,
    estimated_cost REAL,
    actual_cost REAL,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'success', 'failed', 'cancelled',
        'completed', 'blocked', 'awaiting_writeback',
        'completed_via_writeback', 'writeback_dismissed'
    )),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    investigation_session_id TEXT REFERENCES investigation_sessions(id),
    workspace_root TEXT,
    routing_escalation_source TEXT,
    routing_base_tier TEXT,
    model_override INTEGER DEFAULT 0,
    source TEXT DEFAULT 'memopilot_ui',
    patch_governance_available INTEGER NOT NULL DEFAULT 1,
    outcome_summary TEXT,
    outcome_status TEXT,
    writeback_id TEXT,
    writeback_dismissed INTEGER DEFAULT 0
);

INSERT INTO task_runs (
    id, user_request, task_type, mode, risk_level,
    active_rules_json, active_skills_json, context_pack_path,
    selected_model, estimated_cost, actual_cost, status,
    created_at, updated_at, investigation_session_id, workspace_root,
    routing_escalation_source, routing_base_tier, model_override,
    source, patch_governance_available, outcome_summary, outcome_status,
    writeback_id, writeback_dismissed
)
SELECT
    id, user_request, task_type, mode, risk_level,
    active_rules_json, active_skills_json, context_pack_path,
    selected_model, estimated_cost, actual_cost, status,
    created_at, updated_at, investigation_session_id, workspace_root,
    routing_escalation_source, routing_base_tier, model_override,
    source, patch_governance_available, NULL, NULL, NULL, 0
FROM task_runs_old_017;

DROP TABLE task_runs_old_017;

PRAGMA legacy_alter_table = OFF;
PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_task_runs_session ON task_runs(investigation_session_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_workspace_root ON task_runs(workspace_root, created_at DESC);

CREATE TABLE IF NOT EXISTS tool_mode_writebacks (
    id TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    caller TEXT NOT NULL,
    git_diff_hash TEXT NOT NULL,
    diff_files_json TEXT,
    proposals_count INTEGER NOT NULL DEFAULT 0,
    blocked_content_count INTEGER NOT NULL DEFAULT 0,
    outcome_summary TEXT NOT NULL,
    outcome_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_run_id) REFERENCES task_runs(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_writebacks_diff_hash
    ON tool_mode_writebacks(git_diff_hash, task_run_id);

CREATE INDEX IF NOT EXISTS idx_writebacks_task_run
    ON tool_mode_writebacks(task_run_id);

ALTER TABLE memory_items ADD COLUMN writeback_id TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_items_writeback
    ON memory_items(writeback_id);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (17, NULL);
