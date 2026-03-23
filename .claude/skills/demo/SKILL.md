---
name: demo
description: Start Alfred, demonstrate recent changes visually in the browser, and present a non-technical summary of what was built and its impact
disable-model-invocation: true
---

# Demo — Show Me What You Built

Launch Alfred and visually demonstrate recent work. The user is not interested in code details — they want to see features working and understand impact.

## Steps

### 1. Detect What Changed

Figure out what was recently built. Check the current branch and compare against master:

```bash
cd /Users/anirudhlath/code/private/alfred/alfred

# If on a feature branch, diff against master
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "master" ]; then
  git log --oneline master..HEAD
else
  # On master, show recent commits since last tag/merge
  git log --oneline -10
fi
```

Read the commit messages and any referenced spec/plan docs to understand WHAT was built (not HOW). Summarize in 2-3 plain-English bullet points for later.

### 2. Check Infrastructure

```bash
redis-cli ping 2>/dev/null | grep -q PONG && echo "Redis: OK" || echo "Redis: DOWN"
pgrep -x mosquitto >/dev/null && echo "Mosquitto: OK" || echo "Mosquitto: DOWN"
```

If infrastructure is down, run `bash scripts/dev-up.sh` and verify again. If it still fails, stop and tell the user what's missing.

### 3. Start Alfred Services

Start the unified runner in the background. Wait for services to be ready.

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
uv run python -m runner &
RUNNER_PID=$!
sleep 3
```

Check the web channel is responding:
```bash
curl -sf http://localhost:8081/health | jq .
```

If health check fails, wait a few more seconds and retry. If still down after 10s, show logs and stop.

### 4. Run Tests Quietly

Run the test suite in the background to confirm everything works. Don't show output unless something fails.

```bash
uv run python -m pytest -x -q 2>&1 | tail -3
```

If tests fail, mention it briefly but continue with the demo — the user wants to see features, not test output.

### 5. Open Browser and Demo

Use the Playwright MCP to open the browser and walk through the changes visually.

**Always start here:**
1. Navigate to `http://localhost:8081`
2. Take a screenshot — show the main Alfred PWA

**Then demo based on what changed.** Match recent commits to these demo paths:

#### If changes touched `web/settings.*` or `core/integrations/` or `shared/secrets.py`:
- Click the gear icon in the header to go to Settings
- Take a screenshot showing the integration cards
- Point out which integrations are available and their status
- Try filling in a test credential and clicking Save (use dummy data)
- Click Test Connection on an integration
- Take a screenshot of the result

#### If changes touched `web/index.html` or `web/app.js` (onboarding):
- Clear localStorage to trigger onboarding: run JS `localStorage.removeItem('alfred_onboarded')` then reload
- Take screenshots walking through each onboarding step
- Show the skip button
- Complete the wizard

#### If changes touched `core/notifications/` or notification adapters:
- Show the chat interface
- Point out notification rendering if visible
- Explain what notification channels are configured

#### If changes touched `core/conscious/` or `core/reflex/`:
- Type a test message in the chat (e.g., "Good evening, Alfred")
- Wait for response
- Take a screenshot of the conversation

#### If changes touched `core/voice/` or STT/TTS:
- Note that voice is available (microphone button visible)
- Explain what changed in voice processing

**For ANY demo:** Take screenshots at each interesting point. The user wants to SEE the features.

### 6. Present the Summary

After the visual walkthrough, present a clean non-technical summary. Format:

```
## What's New

[2-3 bullet points in plain English describing features, not code]

## What You Just Saw

[Reference the screenshots — "The settings page now lets you securely connect your calendar and financial accounts without editing config files"]

## Impact

[What this means for the user's daily experience with Alfred — e.g., "Alfred can now pull your calendar events and portfolio data into morning briefings"]

## Status

- Tests: [X passed / Y failed]
- Services: [running / issues]
```

### 7. Cleanup

Ask the user if they want to keep Alfred running or shut it down.

If shutting down:
```bash
kill $RUNNER_PID 2>/dev/null
```

## Notes

- This skill is for the USER, not for developers. Avoid jargon.
- Screenshots are worth more than explanations.
- If something doesn't work during the demo, note it calmly and move on — don't debug live.
- The goal is: "Here's what Alfred can do now that it couldn't before."
