-- Remove stale retention_config entry for memory_usage_events (table never created).
DELETE FROM retention_config WHERE table_name = 'memory_usage_events';
