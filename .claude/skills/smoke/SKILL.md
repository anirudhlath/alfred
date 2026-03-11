---
name: smoke
description: Run the end-to-end smoke test with prerequisite checks
disable-model-invocation: true
---

# Smoke Test

Run the Alfred smoke test after verifying all infrastructure is up.

## Steps

1. Check prerequisites:
   ```bash
   # Redis
   redis-cli ping 2>/dev/null | grep -q PONG && echo "Redis: OK" || echo "Redis: DOWN — run 'bash scripts/dev-up.sh'"

   # Mosquitto
   pgrep -x mosquitto >/dev/null && echo "Mosquitto: OK" || echo "Mosquitto: DOWN — run 'bash scripts/dev-up.sh'"

   # home-service
   curl -sf http://localhost:8000/health >/dev/null 2>&1 && echo "home-service: OK" || echo "home-service: DOWN — start it first"
   ```

2. If any prerequisite is down, report which ones and stop. Do NOT proceed without all three.

3. If all up, run the smoke test:
   ```bash
   cd /Users/anirudhlath/code/private/alfred/alfred
   bash scripts/smoke-test.sh
   ```

4. Display the full output. Highlight any failures.
