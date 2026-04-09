# D5: Episodic Hot-Storage Search

## Summary
Only cold SQLite search exists. No Redis Stream search for recent episodic memories.

## Context
Recent memories (hot store) should be searchable via RediSearch vector index, not just cold store.

## Acceptance Criteria
- Vector search over hot store (RedisVectorStore)
- Unified search API returns results from both hot and cold
- Results ranked by relevance across stores
