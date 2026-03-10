---
paths:
  - "sdk/**"
---

# SDK Design Rules

- alfred-sdk is a publishable Python package — keep dependencies minimal
- It is the ONLY coupling between Alfred and external apps
- Core exports: AlfredClient, BaseFeature, @tool, telemetry decorators
- BaseFeature + @tool is the ONLY way to define tools — auto-extracts names, descriptions, and parameters from Python code
- AlfredClient.discover_features() scans a package for BaseFeature subclasses and registers tools
- Apps install it as an optional dependency
- The SDK must work standalone — no imports from alfred core, bus, or domains
- Registration via client.register() announces feature manifests to Redis registry
- Unregistration via client.unregister() on graceful shutdown (HDEL)
- MCP transport is HTTP (JSON-RPC) between networked containers
