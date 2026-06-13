-- MemoPilot schema remediation migration
-- Version: 6
-- Description: Add remediation constraints, governance fields, retention tables, and relation tables

CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_version_unique ON schema_version(version);

CREATE TRIGGER IF NOT EXISTS validate_rules_scope
BEFORE INSERT ON rules
WHEN new.scope NOT IN ('global', 'workspace', 'task', 'safety', 'inferred', 'imported')
BEGIN
    SELECT RAISE(ABORT, 'Invalid rules.scope value');
END;

CREATE TRIGGER IF NOT EXISTS validate_rules_scope_update
BEFORE UPDATE ON rules
WHEN new.scope NOT IN ('global', 'workspace', 'task', 'safety', 'inferred', 'imported')
BEGIN
    SELECT RAISE(ABORT, 'Invalid rules.scope value');
END;

UPDATE memory_items SET trust_level = (6 - trust_level) WHERE trust_level BETWEEN 1 AND 5;
UPDATE evidence_sources SET trust_level = (6 - trust_level) WHERE trust_level BETWEEN 1 AND 5;
UPDATE document_chunks SET trust_level = (6 - trust_level) WHERE trust_level BETWEEN 1 AND 5;

CREATE TABLE IF NOT EXISTS memory_relations (
    id TEXT PRIMARY KEY,
    from_memory_id TEXT NOT NULL REFERENCES memory_items(id),
    to_memory_id TEXT NOT NULL REFERENCES memory_items(id),
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'supersedes', 'supports', 'contradicts',
        'derived_from', 'related_to', 'tested_by'
    )),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_relations_from ON memory_relations(from_memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_relations_to ON memory_relations(to_memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_relations_type ON memory_relations(relation_type);

ALTER TABLE memory_items ADD COLUMN memory_class TEXT DEFAULT 'fact';
ALTER TABLE memory_items ADD COLUMN memory_status TEXT DEFAULT 'discovered';
ALTER TABLE memory_items ADD COLUMN visibility_scope TEXT DEFAULT 'workspace';
ALTER TABLE memory_items ADD COLUMN reusable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_items ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_items ADD COLUMN use_policy_json TEXT;
ALTER TABLE memory_items ADD COLUMN provenance_json TEXT;

CREATE TRIGGER IF NOT EXISTS validate_memory_status
BEFORE INSERT ON memory_items
WHEN new.memory_status NOT IN (
    'discovered','evidence_only','pending_review','confirmed',
    'restricted','stale','disputed','superseded','rejected'
)
BEGIN
    SELECT RAISE(ABORT, 'Invalid memory_status value');
END;

CREATE TRIGGER IF NOT EXISTS validate_memory_status_update
BEFORE UPDATE ON memory_items
WHEN new.memory_status IS NOT NULL AND new.memory_status NOT IN (
    'discovered','evidence_only','pending_review','confirmed',
    'restricted','stale','disputed','superseded','rejected'
)
BEGIN
    SELECT RAISE(ABORT, 'Invalid memory_status value');
END;

CREATE TABLE IF NOT EXISTS retention_config (
    table_name TEXT PRIMARY KEY,
    max_rows INTEGER NOT NULL,
    max_days INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR REPLACE INTO retention_config (table_name, max_rows, max_days) VALUES
    ('recall_traces', 500, 90),
    ('memory_usage_events', 2000, 90),
    ('audit_events', 5000, 180);

CREATE TABLE IF NOT EXISTS recall_traces (
    id TEXT PRIMARY KEY,
    context_pack_hash TEXT NOT NULL,
    request_json TEXT,
    included_memory_ids_json TEXT,
    excluded_memory_ids_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_recall_traces_created ON recall_traces(created_at);
CREATE INDEX IF NOT EXISTS idx_recall_traces_hash ON recall_traces(context_pack_hash);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events(created_at);

CREATE TABLE IF NOT EXISTS memory_artifacts (
    id TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL REFERENCES task_runs(id),
    artifact_type TEXT NOT NULL CHECK (artifact_type IN (
        'patch_diff', 'raw_transcript', 'validation_output',
        'context_pack', 'screenshot', 'log_file', 'other'
    )),
    artifact_path TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    size_bytes INTEGER,
    blocked_reason TEXT NOT NULL,
    redacted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_artifacts_task ON memory_artifacts(task_run_id);

CREATE TABLE IF NOT EXISTS investigation_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    mode TEXT NOT NULL DEFAULT 'investigation',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'patch_generated', 'closed', 'abandoned')),
    workspace_root TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

ALTER TABLE evidence_sources ADD COLUMN investigation_session_id TEXT REFERENCES investigation_sessions(id);
ALTER TABLE task_runs ADD COLUMN investigation_session_id TEXT REFERENCES investigation_sessions(id);

CREATE INDEX IF NOT EXISTS idx_evidence_sources_session ON evidence_sources(investigation_session_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_session ON task_runs(investigation_session_id);

ALTER TABLE document_chunks ADD COLUMN memory_id TEXT REFERENCES memory_items(id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_memory ON document_chunks(memory_id);

ALTER TABLE workspace_profile ADD COLUMN is_cache INTEGER NOT NULL DEFAULT 1;
ALTER TABLE workspace_profile ADD COLUMN synced_from_yaml_at TEXT;

INSERT INTO memory_fts(memory_fts) VALUES('rebuild');

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (6, NULL);
