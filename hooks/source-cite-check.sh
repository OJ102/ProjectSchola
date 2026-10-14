#!/bin/bash
# Stop/SessionEnd hook: check and rewrite tutor citations in Obsidian session logs.
# All logic lives in hooks/source/cite_check.py; see its docstring.
# python3 -I: the source directory and the caller's environment never inject modules.
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/source/cite_check.py" "$@"
