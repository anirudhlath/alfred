---
paths:
  - "core/memory/**"
---

# Memory System Rules

- Preference files use Markdown with YAML frontmatter
- Frontmatter fields: domain, updated, confidence (manual|inferred|librarian)
- Files are read-only at runtime — only the Librarian or humans edit them
- Scratchpad writes are serialized: components push to Redis List, a single async writer drains to scratchpad.md
- Scratchpad entries are timestamped and tagged with source component
