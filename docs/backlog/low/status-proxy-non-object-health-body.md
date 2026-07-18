# Status proxy 500s on a non-object /health JSON body

## Summary
`_service_status` in `core/channels/web_server.py` (the `GET
/api/integrations/{name}/status` service branch) does:

```python
try:
    resp = await app.state.http.get(health_url)
    payload: dict[str, Any] = resp.json()
except (httpx.HTTPError, ValueError) as exc:
    return {"name": name, "healthy": False, "detail": {"error": str(exc)}}
return {
    "name": name,
    "healthy": service_payload_healthy(resp.status_code, payload),
    ...
}
```

If a service's `/health` returns a *valid* JSON body that isn't a JSON object — e.g.
`true`, `123`, or `["ok"]` — `resp.json()` succeeds (no `ValueError`) and `payload` is
typed `dict[str, Any]` but is not actually a dict at runtime. `service_payload_healthy`
then calls `payload.get("status")`, which raises `AttributeError` on a bool/int/list.
That's past the `(httpx.HTTPError, ValueError)` catch, so the request 500s instead of
reporting an unhealthy service.

## Context
`core/channels/service_credentials.py::_parse_manifest` already has the right pattern
for this exact class of bug (guard `isinstance(decoded, dict)` before calling `.get()`
on JSON-decoded input) — the status proxy path needs the same discipline. A
misbehaving or misconfigured third-party `/health` endpoint should degrade to
`healthy: false`, not take down the admin/settings request.

## Acceptance Criteria
- `_service_status` (or `service_payload_healthy`) guards against a non-dict decoded
  JSON body and returns `{"healthy": False, "detail": {"error": ...}}` instead of
  raising.
- Covered by a test where the mocked `/health` handler returns `httpx.Response(200,
  json=123)` (or similar non-object body) and the status endpoint responds 200 with
  `healthy: False` instead of 500.
