-- Migration from schema v1 to v2: add significance, semantic_key, vec0 tables

ALTER TABLE episodic_entries ADD COLUMN significance TEXT DEFAULT '{}';
ALTER TABLE episodic_entries ADD COLUMN semantic_key TEXT DEFAULT '';
ALTER TABLE episodic_entries ADD COLUMN compressed_into TEXT DEFAULT NULL;

-- vec0 virtual tables for KNN search
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodic_semantic USING vec0(embedding float[768]);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodic_content USING vec0(embedding float[768]);

UPDATE schema_version SET version = 2 WHERE version = 1;
