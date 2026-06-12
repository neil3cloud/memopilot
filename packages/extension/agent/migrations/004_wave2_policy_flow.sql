-- MemoPilot Wave 2 schema additions
-- Version: 4

CREATE TABLE IF NOT EXISTS policy_packs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    enforcement_mode TEXT NOT NULL DEFAULT 'enforce',
    rules_json TEXT NOT NULL CHECK (json_valid(rules_json)),
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS policy_pack_versions (
    id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL REFERENCES policy_packs(id),
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL CHECK (json_valid(content_json)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_pack_versions_unique
ON policy_pack_versions(pack_id, version);

CREATE INDEX IF NOT EXISTS idx_policy_packs_active
ON policy_packs(active, updated_at DESC);

CREATE TABLE IF NOT EXISTS local_flows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    steps_json TEXT NOT NULL CHECK (json_valid(steps_json)),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS local_flow_runs (
    id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES local_flows(id),
    task_text TEXT NOT NULL,
    input_json TEXT NOT NULL CHECK (json_valid(input_json)),
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    status TEXT NOT NULL CHECK (status IN ('completed', 'blocked', 'failed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_local_flow_runs_flow_id
ON local_flow_runs(flow_id, created_at DESC);
