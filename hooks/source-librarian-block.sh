#!/bin/bash
# PreToolUse on Agent/Task: the librarian subagent may not be delegated to from a
# session. It runs only through the brief runner (source-brief-save.sh), which
# confines it to one source store (decision 15).
python3 -I -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
t = str((d.get("tool_input") or {}).get("subagent_type") or "")
if t.split(":")[-1].strip().lower() == "librarian":
    sys.stderr.write("the librarian runs only via the source brief runner; do not delegate to it\n")
    sys.exit(2)
'
