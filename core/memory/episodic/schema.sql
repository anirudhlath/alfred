-- Cold storage for episodic memory entries (beyond 7-day hot window).

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);

CREATE TABLE IF NOT EXISTS episodic_entries (
    id          TEXT PRIMARY KEY,
    timestamp   REAL NOT NULL,     -- Unix timestamp (float)
    source      TEXT NOT NULL,     -- "conversation", "system1_action", "trigger", "integration"
    summary     TEXT NOT NULL,
    entities    TEXT NOT NULL,     -- JSON array of entity strings
    valence     TEXT NOT NULL,     -- "positive", "negative", "neutral"
    embedding   BLOB              -- Raw float32 bytes from sentence-transformer
);

CREATE INDEX IF NOT EXISTS idx_episodic_timestamp ON episodic_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_episodic_source ON episodic_entries(source);
