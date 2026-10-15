#!/bin/bash
# Check and rewrite tutor citations in Obsidian session logs (md-log-stop-hook.sh runs the check
# itself, under its session lock, after each render; this wrapper is for direct use and tests).
# All logic lives in hooks/source/cite_check.py; see its docstring.
# python3 -I: the source directory and the caller's environment never inject modules.
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/source/cite_check.py" "$@"
