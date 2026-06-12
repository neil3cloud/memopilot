-- MemoPilot Initial Schema Migration
-- Version: 1
-- Description: Core tables for memory, files, symbols, rules, skills, tasks, AI calls, patches

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    embedding_dim INTEGER,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO schema_version (version, embedding_dim) VALUES (1, NULL);

-- Core memory items
CREATE TABLE memory_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    source_path TEXT,
    source_hash TEXT,
    trust_level INTEGER NOT NULL CHECK (trust_level BETWEEN 1 AND 5),
    tags_json TEXT CHECK (json_valid(tags_json) OR tags_json IS NULL),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    stale INTEGER NOT NULL DEFAULT 0
);

-- Full-text search on memory items
CREATE VIRTUAL TABLE memory_fts USING fts5(
    title,
    body,
    tags_json,
    content='memory_items',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER memory_items_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_fts(rowid, title, body, tags_json)
    VALUES (new.rowid, new.title, new.body, new.tags_json);
END;

CREATE TRIGGER memory_items_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, body, tags_json)
    VALUES ('delete', old.rowid, old.title, old.body, old.tags_json);
END;

CREATE TRIGGER memory_items_au AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, body, tags_json)
    VALUES ('delete', old.rowid, old.title, old.body, old.tags_json);
    INSERT INTO memory_fts(rowid, title, body, tags_json)
    VALUES (new.rowid, new.title, new.body, new.tags_json);
END;

-- File index
CREATE TABLE file_index (
    file_path TEXT PRIMARY KEY,
    language TEXT,
    content_hash TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
    summary_id TEXT,
    stale INTEGER NOT NULL DEFAULT 0
);

-- Symbols (classes, functions, methods, imports)
CREATE TABLE symbols (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    summary TEXT,
    content_hash TEXT NOT NULL
);

-- Rules
CREATE TABLE rules (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    source TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    priority INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Skills
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applies_when TEXT NOT NULL,
    rules_json TEXT NOT NULL CHECK (json_valid(rules_json)),
    tools_json TEXT CHECK (json_valid(tools_json) OR tools_json IS NULL),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Task runs
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
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- AI calls
CREATE TABLE ai_calls (
    id TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL REFERENCES task_runs(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost REAL,
    actual_cost REAL,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    context_pack_hash TEXT,
    purpose TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Patch attempts
CREATE TABLE patch_attempts (
    id TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL REFERENCES task_runs(id),
    patch_path TEXT NOT NULL,
    files_changed_json TEXT NOT NULL CHECK (json_valid(files_changed_json)),
    risk_level TEXT,
    rule_compliance_score REAL,
    approved INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    validation_status TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Rule conflicts
CREATE TABLE rule_conflicts (
    id TEXT PRIMARY KEY,
    task_run_id TEXT REFERENCES task_runs(id),
    rule_a TEXT NOT NULL,
    rule_b TEXT NOT NULL,
    resolution TEXT NOT NULL,
    requires_user_attention INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- MCP calls
CREATE TABLE mcp_calls (
    id TEXT PRIMARY KEY,
    task_run_id TEXT REFERENCES task_runs(id),
    server_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input_json TEXT CHECK (json_valid(input_json) OR input_json IS NULL),
    result_summary TEXT,
    result_tokens INTEGER,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'blocked', 'cancelled')),
    blocked_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Evidence sources
CREATE TABLE evidence_sources (
    id TEXT PRIMARY KEY,
    task_run_id TEXT REFERENCES task_runs(id),
    source_type TEXT NOT NULL,
    source_path TEXT,
    source_url TEXT,
    trust_level INTEGER NOT NULL CHECK (trust_level BETWEEN 1 AND 5),
    extraction_method TEXT,
    extracted_findings_json TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Document chunks
CREATE TABLE document_chunks (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    trust_level INTEGER NOT NULL CHECK (trust_level BETWEEN 1 AND 5),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Context pack versions
CREATE TABLE context_pack_versions (
    id TEXT PRIMARY KEY,
    task_run_id TEXT REFERENCES task_runs(id),
    pack_path TEXT NOT NULL,
    pack_hash TEXT NOT NULL,
    token_estimate INTEGER,
    selected_model TEXT,
    template_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Workspace profile
CREATE TABLE workspace_profile (
    id TEXT PRIMARY KEY,
    profile_yaml TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Provider capabilities
CREATE TABLE provider_capabilities (
    model_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    max_context_tokens INTEGER,
    supports_tool_calling INTEGER NOT NULL DEFAULT 0,
    supports_json_mode INTEGER NOT NULL DEFAULT 0,
    estimated_cost_per_1m_input REAL DEFAULT 0,
    estimated_cost_per_1m_output REAL DEFAULT 0,
    privacy_level TEXT NOT NULL,
    allowed_task_types_json TEXT,
    denied_task_types_json TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX idx_symbols_file_path ON symbols(file_path);
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_ai_calls_task_run ON ai_calls(task_run_id);
CREATE INDEX idx_patch_attempts_task_run ON patch_attempts(task_run_id);
CREATE INDEX idx_file_index_stale ON file_index(stale);
CREATE INDEX idx_memory_items_trust ON memory_items(trust_level);
CREATE INDEX idx_memory_items_stale ON memory_items(stale);
CREATE INDEX idx_mcp_calls_task_run ON mcp_calls(task_run_id);
CREATE INDEX idx_evidence_sources_task_run ON evidence_sources(task_run_id);
CREATE INDEX idx_document_chunks_source ON document_chunks(source_path);
