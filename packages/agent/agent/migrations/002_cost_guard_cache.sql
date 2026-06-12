-- MemoPilot Group 1 Cost Guard + Response Cache
-- Version: 2

CREATE TABLE IF NOT EXISTS response_cache (
    context_pack_hash TEXT PRIMARY KEY,
    response_text TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    estimated_cost REAL NOT NULL DEFAULT 0,
    actual_cost REAL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id TEXT PRIMARY KEY,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('spend', 'save')),
    amount REAL NOT NULL,
    source TEXT NOT NULL,
    reference_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cost_ledger_entry_type ON cost_ledger(entry_type);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_created_at ON cost_ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_response_cache_last_hit ON response_cache(last_hit_at);
