-- Model routing outcome tracking
-- Version: 11

ALTER TABLE task_runs ADD COLUMN routing_escalation_source TEXT;
ALTER TABLE task_runs ADD COLUMN routing_base_tier TEXT;
ALTER TABLE task_runs ADD COLUMN model_override INTEGER DEFAULT 0;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (11, NULL);
