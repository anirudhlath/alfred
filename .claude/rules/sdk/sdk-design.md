---
paths:
  - "sdk/**"
---

# SDK Design Rules

- alfred-sdk is a publishable Python package — keep dependencies minimal
- It is the ONLY coupling between Alfred and external apps
- Core exports: AlfredClient, @mcp_tool, @publish, @subscribe, telemetry decorators
- Apps install it as an optional dependency
- The SDK must work standalone — no imports from alfred core, bus, or domains
- Registration via client.register() announces tool manifests to Redis registry
- MCP transport is HTTP (JSON-RPC) between networked containers
