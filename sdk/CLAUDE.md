# alfred-sdk

Publishable Python package. The ONLY coupling between Alfred and external apps.

- Must work standalone — no imports from alfred core, bus, or domains
- Keep dependencies minimal
- Core: AlfredClient, BaseFeature, @tool, telemetry decorators
- BaseFeature + @tool is the ONLY way to define tools — auto-extracts metadata from docstrings + type hints
- AlfredClient.discover_features() scans a package for BaseFeature subclasses and registers their tools
- AlfredClient.dispatch() routes to bound methods on feature instances
- Not published to PyPI — container builds install from source path
