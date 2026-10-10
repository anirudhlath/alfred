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
