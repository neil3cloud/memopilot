-- Cost guard savings tracking
-- Version: 12

ALTER TABLE ai_calls ADD COLUMN hypothetical_frontier_cost REAL;

DELETE FROM schema_version;
INSERT INTO schema_version (version, embedding_dim) VALUES (12, NULL);
