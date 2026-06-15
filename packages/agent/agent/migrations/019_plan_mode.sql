-- Plan mode: store plans as decision memory items with plan tracking on task_runs
-- Depends on migration 018

-- ─────────────────────────────────────────────────────────────────────────────
-- Plan tracking: link task_runs to plans and track plan source on patch_attempts
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE task_runs ADD COLUMN plan_memory_id TEXT;
ALTER TABLE patch_attempts ADD COLUMN plan_memory_id TEXT;

-- Index for finding tasks that used a specific plan
CREATE INDEX IF NOT EXISTS idx_task_runs_plan
    ON task_runs(plan_memory_id)
    WHERE plan_memory_id IS NOT NULL;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (19, NULL);
