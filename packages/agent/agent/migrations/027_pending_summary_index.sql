-- Partial index for _summarize_pending_symbols(): speeds up the recurring
-- "WHERE summary IS NULL AND kind IN ('function','class')" scan, joined
-- against file_index.file_path, from a full table scan to an index lookup.
CREATE INDEX IF NOT EXISTS idx_symbols_pending_summary
ON symbols(file_path, kind)
WHERE summary IS NULL;
