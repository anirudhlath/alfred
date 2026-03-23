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

### 4. Visual Walkthrough

Use the Playwright MCP to drive the browser. This is the core of the demo — show, don't tell.

**Open Alfred:**
1. Navigate to `http://localhost:8081`
2. Take a screenshot of the main interface

**Then walk through what's new.** Match recent work to these demo flows:

**Settings / Integrations / Credentials:**
- Navigate to Settings (gear icon)
- Screenshot the integration cards — highlight that each service self-describes its credential requirements
- Show the save/test/clear workflow
- Key message: *"Users can now connect external services through a settings UI instead of editing config files. Credential storage uses the OS keychain — no plaintext secrets."*

**Onboarding:**
- Clear onboarding state: `localStorage.removeItem('alfred_onboarded')`, reload
- Walk through the wizard with screenshots at each step
- Show the skip button — demonstrate graceful defaults
- Key message: *"First-run experience is self-service. Users can skip any step and Alfred starts with sensible defaults, learning preferences over time through the Librarian."*

**Conversation / Intelligence:**
- Type a message in the chat (e.g., "Good morning, Alfred — what's my day look like?")
- Wait for response, screenshot
- Key message: *"The Conscious Engine assembles context from memory, integrations, and calendar to deliver personalized briefings."*

**Notifications:**
- If notification UI is visible, screenshot it
- Key message: *"Alfred proactively notifies through Signal, voice, or the web interface — respecting DND schedules and priority routing."*

**Voice:**
- Show the microphone button is present
- Key message: *"Voice input via Whisper, voice output via Piper TTS — both run locally, no cloud dependency."*

**For every screenshot:** Add a one-line caption explaining what the viewer is seeing. Frame it as a capability, not a feature list.

### 5. Executive Summary

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

### 6. Wrap Up

Ask if they want to keep the system running or shut down.

```bash
kill $RUNNER_PID 2>/dev/null
```

## Tone Guidelines

- **Confident, not defensive.** Present what works. If something doesn't work, note it and move on.
- **Capabilities, not features.** "Alfred can now securely connect to external services" not "We added a keyring wrapper with async APIs."
- **Strategic, not tactical.** "This positions us for the integration marketplace" not "We added 4 REST endpoints."
- **Visuals over words.** Every screenshot replaces a paragraph of explanation.
- **No jargon.** Say "settings page" not "CRUD endpoints." Say "secure storage" not "keyring backend."
