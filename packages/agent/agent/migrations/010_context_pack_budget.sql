-- Context pack budget and transparency fields
-- Version: 10

ALTER TABLE context_pack_versions ADD COLUMN budget_summary_json TEXT;
ALTER TABLE context_pack_versions ADD COLUMN stale_exclusion_count INTEGER DEFAULT 0;
ALTER TABLE context_pack_versions ADD COLUMN included_items_json TEXT;
ALTER TABLE context_pack_versions ADD COLUMN excluded_items_json TEXT;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (10, NULL);
