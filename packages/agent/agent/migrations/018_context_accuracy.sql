-- Context accuracy improvements: structural graph, commit history, quality metrics
-- Depends on migration 017

-- ─────────────────────────────────────────────────────────────────────────────
-- Layer 3: Symbol relationship graph (call graph + import graph)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS symbol_relationships (
    id TEXT PRIMARY KEY,
    from_symbol_id TEXT NOT NULL,
    to_symbol_id TEXT,                -- NULL when target is external / not yet indexed
    to_symbol_name TEXT NOT NULL,
    to_file_path TEXT,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN (
            'calls', 'imports', 'inherits',
            'implements', 'instantiates', 'references', 'overrides'
        )),
    workspace_root TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (from_symbol_id) REFERENCES symbols(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symrel_from   ON symbol_relationships(from_symbol_id);
CREATE INDEX IF NOT EXISTS idx_symrel_to     ON symbol_relationships(to_symbol_name);
CREATE INDEX IF NOT EXISTS idx_symrel_type   ON symbol_relationships(relation_type);
CREATE INDEX IF NOT EXISTS idx_symrel_tofile ON symbol_relationships(to_file_path);

-- ─────────────────────────────────────────────────────────────────────────────
-- Layer 4: Git commit history
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commit_history (
    id TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    commit_message TEXT NOT NULL,
    commit_summary TEXT,            -- lightweight condensed summary (optional)
    author_name TEXT,
    committed_at TEXT NOT NULL,
    files_changed_json TEXT,        -- JSON list of affected file paths
    workspace_root TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_commits_sha
    ON commit_history(commit_sha, workspace_root);
CREATE INDEX IF NOT EXISTS idx_commits_workspace
    ON commit_history(workspace_root);
CREATE INDEX IF NOT EXISTS idx_commits_date
    ON commit_history(committed_at DESC);

-- Full-text search over commit messages and summaries
CREATE VIRTUAL TABLE IF NOT EXISTS commit_fts USING fts5(
    commit_message,
    commit_summary,
    content='commit_history',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS commit_fts_insert
    AFTER INSERT ON commit_history BEGIN
        INSERT INTO commit_fts(rowid, commit_message, commit_summary)
        VALUES (new.rowid, new.commit_message, coalesce(new.commit_summary, ''));
    END;

CREATE TRIGGER IF NOT EXISTS commit_fts_delete
    AFTER DELETE ON commit_history BEGIN
        INSERT INTO commit_fts(commit_fts, rowid, commit_message, commit_summary)
        VALUES ('delete', old.rowid, old.commit_message, coalesce(old.commit_summary, ''));
    END;

CREATE TRIGGER IF NOT EXISTS commit_fts_update
    AFTER UPDATE ON commit_history BEGIN
        INSERT INTO commit_fts(commit_fts, rowid, commit_message, commit_summary)
        VALUES ('delete', old.rowid, old.commit_message, coalesce(old.commit_summary, ''));
        INSERT INTO commit_fts(rowid, commit_message, commit_summary)
        VALUES (new.rowid, new.commit_message, coalesce(new.commit_summary, ''));
    END;

-- File-to-commit mapping (one row per file per commit)
CREATE TABLE IF NOT EXISTS commit_file_changes (
    id TEXT PRIMARY KEY,
    commit_id TEXT NOT NULL REFERENCES commit_history(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    change_type TEXT NOT NULL
        CHECK (change_type IN ('added', 'modified', 'deleted', 'renamed')),
    lines_added INTEGER DEFAULT 0,
    lines_deleted INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cfc_file   ON commit_file_changes(file_path);
CREATE INDEX IF NOT EXISTS idx_cfc_commit ON commit_file_changes(commit_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Quality metrics: per-task context quality tracking
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE task_runs ADD COLUMN quality_score REAL;
ALTER TABLE task_runs ADD COLUMN quality_verdict TEXT;
ALTER TABLE task_runs ADD COLUMN has_callers INTEGER DEFAULT 0;
ALTER TABLE task_runs ADD COLUMN has_recent_history INTEGER DEFAULT 0;
ALTER TABLE task_runs ADD COLUMN dedup_savings_pct REAL DEFAULT 0.0;
ALTER TABLE task_runs ADD COLUMN stale_exclusion_pct REAL DEFAULT 0.0;
ALTER TABLE task_runs ADD COLUMN graph_expansion_files INTEGER DEFAULT 0;

-- ─────────────────────────────────────────────────────────────────────────────
-- Rejection learning: capture why patches were rejected
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE patch_attempts ADD COLUMN rejection_reason TEXT;
ALTER TABLE patch_attempts ADD COLUMN rejection_category TEXT
    CHECK (rejection_category IN (
        'wrong_approach', 'missed_business_rule', 'wrong_file',
        'broke_existing_behavior', 'incomplete', 'other'
    ) OR rejection_category IS NULL);

CREATE INDEX IF NOT EXISTS idx_patch_attempts_rejection
    ON patch_attempts(rejection_category)
    WHERE rejection_category IS NOT NULL;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (18, NULL);
