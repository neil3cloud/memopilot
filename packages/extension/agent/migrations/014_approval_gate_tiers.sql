-- Approval gate tiered model
-- Version: 14

ALTER TABLE patch_attempts ADD COLUMN approval_tier TEXT;
ALTER TABLE patch_attempts ADD COLUMN scroll_gate_cleared INTEGER DEFAULT 0;
ALTER TABLE patch_attempts ADD COLUMN type_confirm_required INTEGER DEFAULT 0;
ALTER TABLE patch_attempts ADD COLUMN type_confirm_completed INTEGER DEFAULT 0;
ALTER TABLE patch_attempts ADD COLUMN compliance_warnings_dismissed_json TEXT;
ALTER TABLE patch_attempts ADD COLUMN compliance_actions_triggered_json TEXT;
ALTER TABLE patch_attempts ADD COLUMN ranked_files_json TEXT;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (14, NULL);
