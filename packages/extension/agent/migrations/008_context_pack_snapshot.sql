-- Context pack snapshot and backup readiness
-- Version: 8

ALTER TABLE context_pack_versions
ADD COLUMN pack_content_snapshot TEXT;

UPDATE context_pack_versions
SET pack_content_snapshot = NULL
WHERE pack_content_snapshot IS NULL;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (8, NULL);
