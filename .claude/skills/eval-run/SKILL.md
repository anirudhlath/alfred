---
name: eval-run
description: Run evals against the Reflex Engine SLM with optional repeat count, backend selection, and tag filtering
disable-model-invocation: true
---

# Eval Run

Run the evals suite and display results. Accepts optional arguments.

## Usage

- `/eval-run` — run all scenarios with defaults (Ollama, gpt-oss:20b)
- `/eval-run -n 5` — run 5 times with aggregate pass rates
- `/eval-run --backend lmstudio` — use LM Studio instead of Ollama
- `/eval-run --tag lighting` — filter by tag
- `/eval-run --scenario evals/scenarios/home/tv_on_dims_lights.yaml` — single scenario

## Steps

1. Verify Ollama is reachable (or LM Studio if `--backend lmstudio`):
   ```bash
   curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && echo "Ollama: OK" || echo "Ollama: UNREACHABLE"
   ```

2. Run the evals with the user's arguments (default: `--model gpt-oss:20b`):
   ```bash
   cd /Users/anirudhlath/code/private/alfred/alfred
   uv run python -m evals run --model gpt-oss:20b $ARGS
   ```

3. Display the full output to the user.

4. If the run produced new data in `evals/runs/`, briefly summarize: total scenarios, pass/partial/fail counts, and any regressions vs. the previous run.
