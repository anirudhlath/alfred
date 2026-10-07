import os

# Inspect reads its console display from this on first use; tests never want a TUI.
os.environ.setdefault("INSPECT_DISPLAY", "none")
# Each eval otherwise binds Inspect's control server (uvicorn on a unix socket, plus a
# discovery file); a test eval needs neither.
os.environ.setdefault("INSPECT_EVAL_CTL_SERVER", "false")
