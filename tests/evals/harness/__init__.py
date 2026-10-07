import os

from inspect_ai.util import display_type

# Tests never want a display. Pin it now: eval_async pins "plain" unless a display is
# already set, and Inspect reads INSPECT_DISPLAY only when asked for one.
os.environ["INSPECT_DISPLAY"] = "none"
display_type()
# Each eval otherwise binds Inspect's control server (uvicorn on a unix socket, plus a
# discovery file); a test eval needs neither.
os.environ.setdefault("INSPECT_EVAL_CTL_SERVER", "false")
