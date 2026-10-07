import os

# Inspect reads its console display from this on first use; tests never want a TUI.
os.environ.setdefault("INSPECT_DISPLAY", "none")
