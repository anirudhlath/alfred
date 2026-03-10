# Research Protocol

After completing ANY implementation task that touches telemetry-producing code:

1. Check research/data/ for new or updated CSVs
2. If new data exists:
   - Update or create research/daily/{YYYY-MM-DD}.md with summary statistics
   - Compute p50/p95/p99 latencies where applicable
   - Note token usage, event throughput, and anomalies
3. If a milestone is reached (new capability proven, latency target hit):
   - Create or update an experiment log in research/experiments/EXP-NNN-*.md
   - Use the format: Hypothesis, Method, Results, Analysis
4. Weekly: review research/paper/ sections and flag which need updating

## Data Format
- CSVs in research/data/: append-only, never overwrite
- Daily notes in research/daily/: auto-generated Markdown
- Experiment logs: formal structure (hypothesis → results → analysis)
