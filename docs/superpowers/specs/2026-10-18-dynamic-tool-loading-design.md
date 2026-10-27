# Dynamic tool loading for System 2 — design

Part of the Pydantic AI rework ([#317](https://github.com/anirudhlath/alfred/issues/317)), the PR after full
session history. Extends `docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md`, which has no
component for it.

## Why

Every System 2 model request carries every tool. A production request measured on 2026-10-15 sent 121
tools, 59,977 JSON characters, of which home-service's 99 generated tools were 49,512. That is roughly
13k of the request's 18–25k input tokens, paid again on every model request of every turn.

[anirudhlath/alfred-home-service#32](https://github.com/anirudhlath/alfred-home-service/issues/32) removes
the home tools that should not exist. This design lowers the cost of the tools that remain, and of every
integration added later: the model sees the tools it uses on most turns, and finds the rest by searching.

## Decisions

### 1. What stays loaded, and what is deferred

The rule comes from registry metadata, never from tool names.

| tools | today | loaded |
|---|---|---|
| In-process toolsets: triggers, memory, attention, integrations | 22 | always |
| Registry tools whose `audience` is `reflex` (lights, switches, media players) | 9, ~7.6k chars | always |
| Every other registry tool, `home_call_service` included | 89, ~40k chars | on demand |

The reflex-audience tools are the house's everyday verbs: they serve the commonest commands and keep them at
two model requests. `RegistryToolset._build` sets `ToolDefinition.defer_loading` for every tool whose audience
is not `reflex`. `ToolInfo` already carries the audience that home-service registers.

### 2. How the model finds a deferred tool

Pydantic AI 2.55's `ToolSearch` capability supplies this. On our chat-completions models (OpenRouter in
production, vLLM in evals) it is always the framework-side fallback, a local function tool `search_tools`:

- It takes `{"queries": [...]}` and returns the matching tool names.
- Each matched tool's schema is sent from the next request on.
- Calling a deferred tool that has not been found is refused with a retry prompt telling the model to search
  first.

Alfred adds `ToolSearch` to System 2's capabilities explicitly:

- **`strategy="keywords"`, pinned.** The default strategy is documented as liable to change. The keyword
  search is noisy on home-service's bare descriptions (for example `light.turn_on`), but it found the right
  tool within its ten results for every non-reflex probe query. With the reflex verbs loaded, searches are
  for climate, vacuum, scripts and the like, where the probe ranked the right tool first.
- **`max_results=10`.**
- **Alfred's own `tool_description`**, which is generic and names no domains. It says that the listed tools
  are already loaded, that other smart-home tools are found by describing what they do, and that one call can
  carry several queries. The framework's default text talks about "capability ids", which is wrong here.

A custom strategy (bge-m3 embeddings from the shared embedding server, ranked by cosine) is the follow-up if
evals show misses. It plugs in as `ToolSearch(strategy=fn)` without other changes. File it as an issue.

### 3. Discovery across turns

Discovery is re-derived from message history on every request, and the search exchange round-trips through
the session store as typed parts (probed). So a tool found in turn 1 is still loaded in turn 2 of the same
session.

**Compaction:**
- `SlidingWindowCompaction` (60 messages, keep 40) can drop the search exchange. The tool is then unloaded and
  the model searches again: one extra request, in a session long enough to compact. Accepted.
- `ClearToolResults` never blanks a search result, but each search counts as one of its kept pairs. Raise
  `TOOL_RESULTS_KEPT` from 3 to 4, so a search doesn't displace a real tool result.

**Identity:** a turn whose speaker differs from the stored session starts with no history, so it inherits no
discoveries. Whatever a session holds, the search corpus is the turn's scoped toolset. A guest's search can
reveal only benign tools, and the gate refuses anything outside the corpus.

### 4. Request limit

A turn that needs a deferred tool now takes at least three model requests: search, call, answer.
`REQUEST_LIMIT` stays at 10 unless measured otherwise. The eval run reports requests per turn for each golden,
on this branch and on master. The limit is raised only if a golden's turn comes within two requests of it
because of searching.

Raising it blindly would let the tool loops already seen in evals (a turn calling an unreachable calendar
until the limit) burn more requests.

### 5. Parallel tool calls

Pydantic AI runs the tool calls of one model response concurrently. Alfred keeps that: no `sequential` flag.

The effect ledger (`core/conscious/effects.py`) is safe under concurrency. Each call's `RPUSH` returns its own
index, and settling writes only that index. A new engine-level test pins the multi-step path through the real
engine, the real `RecordEffects` and a fakeredis ledger:

1. The turn searches.
2. It calls two side-effecting registry tools in one response.
3. It answers.

The test asserts:
- both actions are routed;
- both ledger entries are settled at distinct indices;
- `actions_taken` lists both;
- a redelivery of the same request acts on neither again.

### 6. Prompts

No prompt changes in this PR. The `search_tools` description carries the guidance, and the production prompts
live in Langfuse.

If evals show the model calling deferred tools without searching (each refusal costs a request), a routing line
goes into the System 2 defaults and the Langfuse prompt in a follow-up.

### 7. Prompt caching

Each discovery inserts tools into the tool list, which would break a provider's cached prefix at that point.
Alfred sets no cache options today, so nothing is lost. Post-deploy verification reads both input tokens and
cached tokens.

## Verification

**Unit tests:**
- the deferral rule (a reflex-audience tool is visible, any other registry tool is deferred);
- a guest's search reveals no elevated tool;
- a tool discovered in turn 1 is sent in turn 2 after the history round trip;
- the parallel side-effect test from §5.

**Evals:**
- `home_control` and `conversation`, 5 epochs, this branch against master, at most 4 requests in flight;
- no golden's pass rate drops beyond run-to-run noise;
- the scorecards, the requests per turn and the System 2 input tokens per request go to #317.

**After deploy:** a production turn needing a deferred tool, read back from Langfuse, showing the search, the
call and the input tokens of each request, against the 2026-10-15 measurement. Post it to #317.

## Out of scope

- Trimming the home-service surface (alfred-home-service#32).
- Embedding search (the follow-up issue in §2).
- Provider prompt caching.
- Native tool search, which needs a different model class than OpenRouter's chat completions.
- Deferring integrations, which are 11 small tools today.
