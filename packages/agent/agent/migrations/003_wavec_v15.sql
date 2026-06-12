-- MemoPilot Wave C (v1.5) schema additions
-- Version: 3

CREATE TABLE IF NOT EXISTS skill_store_versions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    conflict INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_store_versions_unique_version
ON skill_store_versions(skill_id, version);

CREATE INDEX IF NOT EXISTS idx_skill_store_versions_name
ON skill_store_versions(name, created_at DESC);

CREATE TABLE IF NOT EXISTS optimizer_runs (
    id TEXT PRIMARY KEY,
    task_text TEXT NOT NULL,
    suggested_tools_json TEXT NOT NULL,
    suggested_skills_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_optimizer_runs_created_at
ON optimizer_runs(created_at DESC);
