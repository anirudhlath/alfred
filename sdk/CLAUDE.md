# alfred-sdk

Publishable Python package. The ONLY coupling between Alfred and external apps.

- Must work standalone — no imports from alfred core, bus, or domains
- Keep dependencies minimal
- Core: AlfredClient, @mcp_tool, @publish, @subscribe, telemetry decorators
- @mcp_tool wraps with a SYNC wrapper — AlfredClient.dispatch() uses inspect.isawaitable() to handle both sync and async tool functions
- Not published to PyPI — container builds install from source path
