---
name: demo
description: Launch Alfred and present a visual executive demo of recent work — what was built, what it looks like, and what it means for the product
disable-model-invocation: true
---

# Demo — Executive Review

Launch Alfred and present a CTO-level walkthrough of recent development work. Focus on capability, product impact, and system health — not implementation details.

**Audience:** Director / CTO. They care about: what can Alfred do now, how does it look, does it work, what's the strategic impact. They do NOT care about: code structure, test counts, refactoring, internal abstractions.

## Steps

### 1. Scope the Demo

Determine what was recently built:

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "master" ]; then
  git log --oneline master..HEAD
else
  git log --oneline -10
fi
```

Read any referenced spec docs (`docs/superpowers/specs/`) to understand the PRODUCT intent. Prepare a one-sentence elevator pitch of what this sprint delivered. Think in terms of capabilities, not tasks.

### 2. Boot the System

```bash
cd /Users/anirudhlath/code/private/alfred/alfred

# Infrastructure
redis-cli ping 2>/dev/null | grep -q PONG || bash scripts/dev-up.sh
pgrep -x mosquitto >/dev/null || bash scripts/dev-up.sh

# Services
uv run python -m runner &
RUNNER_PID=$!
sleep 4

# Verify
curl -sf http://localhost:8081/health >/dev/null && echo "System: READY" || echo "System: STARTING..."
```

If the system won't start, say so plainly and stop. Don't debug during the demo.

### 3. Validate Quietly

```bash
uv run python -m pytest -x -q 2>&1 | tail -3
```

Note the test count for the status slide. Don't show test output.

### 4. Demo What Changed (THIS IS THE CORE — don't skip it)

**CRITICAL:** Demo the ACTUAL RECENT WORK, not pre-existing features. Step 1 identified what was built — this step proves it works. The demo must show the NEW capabilities in action.

**Classify each change** into one of these demo strategies:

#### A. Frontend/UI changes → Visual Walkthrough (Playwright)
Use the Playwright MCP to drive the browser. Screenshot each new UI element or flow.

#### B. Backend/pipeline changes → Live Proof via CLI
Write a short Python script that exercises the new capability against the running system and prints human-readable output. Run it with `uv run python -c "..."` or as a temp script. Examples:
- **Memory/decay changes:** Query Redis to show entries with retrieval stats, trigger a Librarian cycle, show before/after state
- **New API endpoints:** `curl` the endpoint and show the response
- **Event processing:** Publish a test event to a Redis stream and show it being consumed
- **Pattern detection:** Seed episodic entries and trigger consolidation to show routine candidates appearing
- **Notification changes:** Trigger a notification and show it arriving via WebSocket or in Redis

Format CLI output as a code block with a caption explaining what the viewer is seeing. The output should be self-explanatory — timestamps, IDs, state changes.

#### C. Behavioral/intelligence changes → Conversation Proof
If the change affects how Alfred responds (new tools, context assembly, memory recall), demonstrate it via a live conversation in the UI. Type a prompt that specifically exercises the new behavior. Screenshot the response.

**For EVERY demo item:**
- State what you're about to prove: *"Let me show that the decay formula actually preserves important memories..."*
- Show the proof (screenshot or CLI output)
- One-line caption for the audience

**DO NOT** fall back to demoing pre-existing features (settings, onboarding, voice button) unless they were specifically changed in this sprint.

### 5. System Context (brief)

After demoing what's new, optionally show 1-2 existing capabilities for context — but ONLY if they help frame the new work. Keep this under 2 minutes. Don't walk through every screen.

### 6. Executive Summary

Present this at the end. This is what they'll remember.

```markdown
## Sprint Delivery

**[One sentence: what Alfred can do now that it couldn't before]**

### Capabilities Delivered
- [Capability 1 — framed as user/business value]
- [Capability 2]
- [Capability 3 if applicable]

### Architecture Decisions
- [Key technical choice and WHY it matters for the product — e.g., "OS keychain for credentials means zero plaintext secrets on disk, compliant with our security posture"]
- [Another if relevant]

### System Health
- Test coverage: [N] tests passing
- Services: [all running / any issues]
- Tech debt: [any known gaps, or "clean"]

### What's Next
- [Next 1-2 priorities from the backlog, framed as capabilities]
```

### 7. Wrap Up

Ask if they want to keep the system running or shut down.

```bash
kill $RUNNER_PID 2>/dev/null
```

## Tone Guidelines

- **Confident, not defensive.** Present what works. If something doesn't work, note it and move on.
- **Capabilities, not features.** "Alfred can now securely connect to external services" not "We added a keyring wrapper with async APIs."
- **Strategic, not tactical.** "This positions us for the integration marketplace" not "We added 4 REST endpoints."
- **Proof over narration.** Every claim must be backed by a screenshot, CLI output, or live conversation. Don't describe what something does — show it working.
- **Demo what's new, not what exists.** The audience wants to see what changed this sprint. Pre-existing features are context, not the main event.
- **Backend work deserves demos too.** If the sprint was pipeline/engine work, write scripts that prove the behavior. A Redis query showing decay scores is more compelling than a screenshot of an unchanged chat UI.
- **No jargon.** Say "settings page" not "CRUD endpoints." Say "secure storage" not "keyring backend."
