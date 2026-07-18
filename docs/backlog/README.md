# Backlog Conventions

Two layers:

- **GitHub Issues are canonical for mirrored tickets.** Closing the issue = done;
  delete the local file in the closing PR (or note it). Labels: `priority: <tier>`,
  `epic: <name>`, `agent-ready` (scoped tightly enough to hand to `@claude` directly).
  Milestone per upcoming release — zero open issues on the milestone means scope
  complete and the QA pass can start.
- **Local files (`docs/backlog/<tier>/*.md`) are canonical for sensitive tickets** —
  anything marked 🔒 (coordinated disclosure: live exposures, personal data, unpatched
  vulnerabilities). These are never mirrored to Issues until remediated, and epic files
  in `docs/backlog/epics/` stay local.

New non-sensitive work: file an Issue directly (templates enforce structure); a local
file is optional. New sensitive work: local file only, marked 🔒.
