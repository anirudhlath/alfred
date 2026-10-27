# Task 3 report

Status: DONE_WITH_CONCERNS (the optional strict check of existing test files retains two baseline typing errors; no new typing errors).

## Implemented

- Added 25 goldens: guest_boundary has seven shipped identity/personal-data cases and two pending enforcement cases; critical_actions has eleven shipped cases; privacy has five shipped cases.
- Guest cases cover synthetic web and registered/unregistered Signal identities, sir catalog/lamp controls, withheld memory/calendar/confirmation/attention tools, personal-data requests, and a sir-to-guest session after a fictional private medical disclosure.
- Critical cases cover lock unlock, garage open and alarm disarm before-consent interception plus explicit-ID approval/execution; lock cases additionally cover refusal, duplicate approval, wrong ID, innocuous status and natural yes. Concrete HA execution/count/state and pending snapshots score outcomes. Entity/name/area alternatives use target matchers rather than exact model argument wording.
- Explicit-ID fixture turns select the observed earlier pending ID and require the real model confirmation path; no approval endpoint is called. Natural yes injects no ID. Refusal requires no execution without inventing a cancellation tool.
- Added fixed fictional apple_calendar username/password/caldav_url fields with loopback port 9. Existing apartment entities and services are retained.
- Mapped shipped identity/privacy/critical evidence to suites; guest enforcement stays pending, guest-choice capture maps to onboarding tests/not_llm. Critical PRD status stays planned and documentation explains existing code versus product status.
- Extended golden validation with dynamically read internal manifests and registered adapter capabilities, catalog/pending-selector name checking, world target checking and privacy-negative evidence checks covering every seeded credential, every recorded request and missing role evidence.
- Updated docs/evals.md with check syntax, evidence/failure behavior, pending fixture semantics, suite counts and current gaps. Removed slice 3 from the remaining list.

## TDD evidence

RED before fixture/guard implementation:

`uv run pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py -q -k 'slice_three or service_guard_rejects or target_guard_rejects'`

Expected failures: suites did not exist; privacy was still pending_suites; existing service guard saw only the misspelled pending home tool and missed memory/integration/confirmation catalog names and the pending selector. Initial target test exposed the missing helper; a subsequent minimal empty helper made this a real assertion failure rather than a NameError.

`uv run pytest tests/evals/test_goldens_load.py -q -k target_guard_rejects`

Expected assertion RED: `assert [] == ['llm_tool_args: target lock.frnot_door']`. Logs: /tmp/task3-red.log and /tmp/task3-target-red.log.

GREEN after implementation:

`uv run pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py -q`

`68 passed in 1.37s`. Log: /tmp/task3-goldens-final.log.

## Verification

- `uv run pytest tests/evals -q`: **837 passed, 1 skipped in 82.82s**. Log: /tmp/task3-evals-final.log. No model or stack run performed.
- `uv run ruff check tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py`: **All checks passed!**
- `uv run ruff format --check tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py`: **2 files already formatted**.
- `git diff --check`: **passed**.
- `uv run mypy --strict tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py`: **two existing errors**, at current lines 317 (`PydanticDescriptorProxy[ModelValidatorDecoratorInfo]` not callable for direct `_coherent()` validation) and 690 (`BaseModel` has no `within_s`). No new errors.
- Verified baseline by `git show 7127dda:tests/evals/test_goldens_load.py > /tmp/task3-base-goldens.py` then `uv run mypy --strict /tmp/task3-base-goldens.py`: the same errors at original lines 169 and 512. Logs: /tmp/task3-mypy.log, /tmp/task3-mypy-base.log. Root separately owns the full production mypy/gates.

## Files changed

- New YAML files under evals/suites/guest_boundary/, critical_actions/ and privacy/.
- evals/coverage.yaml; evals/harness/worlds/apartment.yaml; docs/evals.md.
- tests/evals/test_goldens_load.py; tests/evals/test_prd_coverage.py.
- This report.

## Self-review and limits

- Confirmed actual home-service tool schema uses `target`; its registered target_service is `home-service`, not `home`. Corrected these before final checks using the sibling home-service source provided by the controller (640a73f).
- Preserved all existing world entities/services and did not change production code or other workers' files.
- Broadened target argument matching to valid entity/name/area forms, retaining actual HA entity outcomes.
- Strengthened the same-session identity transition to detect private conversation leakage, not merely missing tools.
- All requested local eval verification passed. The optional test-file strict mypy failures predate this change and are reported rather than silently fixed outside scope.
- Natural yes and the identity transition intentionally expose potential product gaps; no real model outcome is claimed. Guest enforcement remains planned. Root owns real suite runs, full repository gates, review, publishing and any baseline decision.

## Review fix: lock-status false positive (base 9e17b13)

- Replaced the `locked` substring needle in critical_actions/lock_status_query.yaml with the word-boundary regex `\blocked\b`, so an incorrect `unlocked` reply cannot satisfy it.
- Added a focused four-case regression: two natural locked replies pass, two unlocked replies fail.
- RED: `uv run pytest tests/evals/test_goldens_load.py -q -k critical_lock_status_reply`: **2 failed, 2 passed, 60 deselected in 0.87s**. Both unlocked replies incorrectly returned True before the fixture correction. Log: /tmp/task3-status-red.log.
- GREEN: `uv run pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py -q`: **72 passed in 1.28s**. Log: /tmp/task3-status-green.log.
- `uv run ruff check tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py`: **All checks passed!**
- `uv run ruff format --check tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py`: **2 files already formatted**.
- `git diff --check`: **passed**.
- Only the fixture, golden validation tests and this report changed. No production edits or repeat full gates; controller's full pytest run covers the previously committed tree.

## Full-suite catalog investigation and fix (base 90139c2)

Controller full pytest exposed one failure in golden tool validation (4065 passed, 15 skipped): all five calendar tool names became unknown in the full process, although eval-only runs passed. Controller log: /tmp/alfred-slice3-full-tests.log.

Root cause investigation: existing integration/settings test fixtures clear IntegrationRegistry._registry and leave test adapters registered. Full collection has already imported the real adapter modules. `_integration_tools()` imported those cached modules, whose registration decorators cannot run again, then read the cleared mutable registry. The validation catalog therefore lost real calendar capabilities.

Confirmed the collection/global-state difference with:

`uv run pytest tests/core/integrations/test_apple_calendar.py tests/core/integrations/test_registry_keyring.py tests/evals/test_goldens_load.py::test_every_golden_names_real_services_and_tools -q`

Before fix: **1 failed, 33 passed in 2.03s**, with the same five unknown calendar tool names. Without prior calendar collection, registry-keyring tests plus the guard passed (8 tests), confirming cached imports are the differentiator. Logs: /tmp/task3-catalog-reproduce-cached.log and /tmp/task3-catalog-reproduce.log.

Added a regression that first imports adapter modules, replaces the registry with an empty dictionary, clears the catalog cache and verifies the real calendar's dynamically derived capability names remain present. It also asserts the emptied registry remains unchanged. RED: `uv run pytest tests/evals/test_goldens_load.py -q -k integration_catalog_survives`: **1 failed, 64 deselected in 0.77s**, actual catalog was frozenset(). Log: /tmp/task3-catalog-red.log.

Single fix: dynamically discover integration modules, inspect their locally declared concrete Integration subclasses and read capability declarations from those classes. Tool names are still derived from the real adapters, with no hardcoded catalog and no weakened golden checks. The helper no longer depends on process-global registration contents.

GREEN verification:

- `uv run pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py -q`: **73 passed in 1.35s**. Log: /tmp/task3-catalog-green.log.
- Exact minimal reproduction command above: **34 passed in 1.98s**. Log: /tmp/task3-catalog-reproduce-green.log.
- Owned Ruff check: **All checks passed!**
- Owned Ruff format check: **2 files already formatted**.
- `git diff --check`: **passed**.
- Optional strict mypy of the two owned test files: the same two verified baseline errors remain at current lines 319 and 708; no new errors. Log: /tmp/task3-catalog-mypy.log.

Only tests/evals/test_goldens_load.py and this report changed. No production edits. Controller owns the post-fix full repository rerun and real suites.

## Final review fix: negated status and evidence documentation (base 60af121)

A word-boundary `locked` needle alone still accepted the false answer “The front door is not locked.” Prepared the functional patch and regression under /tmp while the controller's first live run was active; did not mutate in-flight goldens. Applied only after explicit controller clearance at live-run completion.

- Added a narrow negative needle for explicit `not locked` and ASCII/curly `isn't locked` forms. Retained the affirmative word-boundary check.
- Added the existing faithfulness judge, with the actual locked fixture as reference, to assess accurate status across natural phrasing and contradictory/unknown claims. No production changes or broad natural-language parser.
- Extended the focused reply regression to seven real check evaluations: locked replies pass; unlocked, not locked and both contraction forms fail. No tautological rubric-string tests.
- Corrected docs/evals.md: missing/ambiguous pending selection renders the fixed eval-nonexistent-pending-action ID, records transcript diagnostics and continues to product-result checks. Catalog checks inspect recorded names and malformed entries may be omitted by the existing extractor; only unreadable pending records carry the described local evidence-error guarantee. Upstream normally validates malformed request bodies.

RED before applying the fixture change:

`uv run python -m pytest /tmp/task3-status-semantic/test_status_regression.py -q`

**3 failed, 4 passed in 0.90s**: the three explicit negations were incorrectly accepted by the old fixture. Log: /tmp/task3-status-negation-red.log.

GREEN after applying:

- `uv run pytest tests/evals/test_goldens_load.py -q -k critical_lock_status_reply`: **7 passed, 61 deselected in 0.81s**. Log: /tmp/task3-status-negation-green.log.
- `uv run pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py -q`: **76 passed in 1.33s** on the final source. Log: /tmp/task3-status-final-goldens.log. Ruff initially flagged a literal curly apostrophe; replaced it with the equivalent Unicode escape and reran the checks.
- Owned Ruff check: **All checks passed!** after that source-literal correction.
- Owned Ruff format check: **2 files already formatted**.
- `git diff --check`: **passed**.

Evidence limit: these unit tests prove deterministic reply checks reject the concrete false statements. They do not evaluate the model-backed semantic judge's accuracy. The controller's final three-epoch live run will exercise the added faithfulness criterion; the first live run used the prior scoring. No new full pytest or production gates were run for this fixture/documentation correction, as directed.
