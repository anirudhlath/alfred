# alfred-sdk

Publishable Python package. The ONLY coupling between Alfred and external apps.

- Must work standalone — no imports from alfred core, bus, or domains
- Keep dependencies minimal
- Core: AlfredClient, @mcp_tool, @publish, @subscribe, telemetry decorators
