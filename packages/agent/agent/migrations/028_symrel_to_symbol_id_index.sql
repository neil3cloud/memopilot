-- get_callers() runs a recursive CTE filtering
-- "sr.to_symbol_id = ? AND sr.relation_type = 'calls'" at every depth level,
-- on every /v1/context/assemble request (via find_callers_not_in_context).
-- No existing index covers to_symbol_id, forcing a full table scan of
-- symbol_relationships on the primary user-facing retrieval path.
CREATE INDEX IF NOT EXISTS idx_symrel_to_symbol_id
ON symbol_relationships(to_symbol_id, relation_type);
