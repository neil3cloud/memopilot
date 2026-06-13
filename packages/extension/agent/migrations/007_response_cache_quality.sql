-- Response cache quality filters
-- Version: 7

ALTER TABLE response_cache
ADD COLUMN response_status TEXT NOT NULL DEFAULT 'success'
CHECK (response_status IN ('pending', 'running', 'success', 'failed', 'cancelled'));

UPDATE response_cache
SET response_status = 'success'
WHERE response_status IS NULL OR TRIM(response_status) = '';

CREATE INDEX IF NOT EXISTS idx_response_cache_status ON response_cache(response_status);

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (7, NULL);
