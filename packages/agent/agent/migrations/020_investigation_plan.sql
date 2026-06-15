-- Investigation-to-plan loop support
-- Depends on migration 019

ALTER TABLE patch_attempts ADD COLUMN acceptance_targets_json TEXT
    CHECK (json_valid(acceptance_targets_json) OR acceptance_targets_json IS NULL);

-- task_runs already includes investigation_session_id in the current schema lineage.
-- Recreate the supporting index to keep the column discoverable across upgrade paths.
CREATE INDEX IF NOT EXISTS idx_task_runs_session ON task_runs(investigation_session_id);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (20, NULL);
