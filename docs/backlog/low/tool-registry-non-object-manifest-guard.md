# ToolRegistry raises on a non-object JSON manifest

## Summary
`core/reflex/tool_registry.py::ToolRegistry.get_tools()` does:

```python
try:
    manifest: dict[str, Any] = json.loads(manifest_str)
except json.JSONDecodeError:
    logger.error("Invalid JSON in registry for service '%s'", service_name)
    continue

# Parse features
for feature in manifest.get("features", []):
```

If a registry entry's value is *valid* JSON that isn't a JSON object — e.g. `true`,
`123`, or `["ok"]` — `json.loads` succeeds (no `JSONDecodeError`) and `manifest` is
typed `dict[str, Any]` but is not actually a dict at runtime. The next line calls
`manifest.get("features", [])`, which raises `AttributeError` on a bool/int/list. That's
past the `except json.JSONDecodeError` catch, so `get_tools()` — called on every Reflex
prompt build — crashes instead of skipping the malformed entry.

## Context
`core/channels/service_credentials.py::_parse_manifest` already has the right pattern
for this exact class of bug (guard `isinstance(decoded, dict)` before calling `.get()`
on JSON-decoded input) — `ToolRegistry.get_tools()` needs the same discipline. A
malformed or corrupted `alfred:tool_registry` hash entry (e.g. written by a buggy or
malicious sovereign service) should be skipped and logged, not take down Reflex's tool
discovery for every registered service. Found during schema review 2026-07-16; latent
since the tool registry was introduced (pre-existing, not caused by this batch).

## Acceptance Criteria
- `ToolRegistry.get_tools()` guards against a non-dict decoded JSON manifest (same
  `isinstance(decoded, dict)` check as `_parse_manifest`) and logs + skips that service
  entry instead of raising.
- Covered by a test where `alfred:tool_registry` contains one malformed entry (JSON
  scalar/array, e.g. `"123"` or `"[]"`) alongside one valid manifest, and `get_tools()`
  returns the valid manifest's tools without raising.
