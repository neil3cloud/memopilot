-- Remove legacy coding-agent tables after retrieval-first pivot.
DROP TABLE IF EXISTS patch_attempts;
DROP TABLE IF EXISTS evidence_sources;
DROP TABLE IF EXISTS task_patterns;
DROP TABLE IF EXISTS investigation_sessions;
