-- Validation baseline and auto-retry tracking
-- Version: 13

ALTER TABLE patch_attempts ADD COLUMN baseline_validation_json TEXT;
ALTER TABLE patch_attempts ADD COLUMN pre_existing_failures_json TEXT;
ALTER TABLE patch_attempts ADD COLUMN new_failures_json TEXT;
ALTER TABLE patch_attempts ADD COLUMN fixed_by_patch_json TEXT;
ALTER TABLE patch_attempts ADD COLUMN retry_count INTEGER DEFAULT 0;
ALTER TABLE patch_attempts ADD COLUMN auto_retry_stopped_reason TEXT;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (13, NULL);
