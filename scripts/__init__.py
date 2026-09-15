"""Runs first for every `python -m scripts.X` entry point.

On Windows, redirected output falls back to cp1252, and non-ASCII
characters like the arrow printed by scripts.run raise UnicodeEncodeError.
"""

import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
