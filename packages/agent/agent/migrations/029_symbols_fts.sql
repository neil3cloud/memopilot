-- Full-text search on symbols, for FTS5-ranked relevance scoring during
-- symbol-level context assembly. Mirrors the memory_fts pattern: symbols.id
-- is TEXT PRIMARY KEY, so content_rowid binds to SQLite's implicit integer
-- rowid, not the id column — queries must join back via rowid.
CREATE VIRTUAL TABLE symbols_fts USING fts5(
    name,
    signature,
    summary,
    content='symbols',
    content_rowid='rowid'
);

CREATE TRIGGER symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, signature, summary)
    VALUES (new.rowid, new.name, new.signature, new.summary);
END;

CREATE TRIGGER symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, signature, summary)
    VALUES ('delete', old.rowid, old.name, old.signature, old.summary);
END;

CREATE TRIGGER symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, signature, summary)
    VALUES ('delete', old.rowid, old.name, old.signature, old.summary);
    INSERT INTO symbols_fts(rowid, name, signature, summary)
    VALUES (new.rowid, new.name, new.signature, new.summary);
END;

-- Backfill existing rows (this migration may run against a populated DB).
INSERT INTO symbols_fts(rowid, name, signature, summary)
SELECT rowid, name, signature, summary FROM symbols;
