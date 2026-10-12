#!/bin/bash
# PreToolUse guard for the librarian agent (decision 15): Read, Grep and Glob may
# only touch the current source's store ($SCHOLA_STORE). Anything else is denied.
# Exit 2 blocks the call; the reason on stderr goes back to the model.
exec python3 -I -c '
import json, os, re, sys
root = os.environ.get("SCHOLA_STORE")
if not root:
    sys.stderr.write("librarian: no SCHOLA_STORE set; file access denied\n"); sys.exit(2)
root = os.path.realpath(root)
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.stderr.write("librarian: malformed hook input; denied\n"); sys.exit(2)
tool, inp = d.get("tool_name"), d.get("tool_input") or {}
cwd = d.get("cwd") or os.getcwd()
paths = []
if tool == "Read":
    paths.append(inp.get("file_path") or "")
elif tool in ("Grep", "Glob"):
    paths.append(inp.get("path") or cwd)
    pat = inp.get("pattern") or ""
    if tool == "Glob" and (pat.startswith(("/", "~")) or ".." in pat.split("/")):
        paths.append(pat.split("*")[0] or "/")
    g = inp.get("glob") or ""
    if g.startswith(("/", "~")) or ".." in g.split("/"):
        paths.append(g.split("*")[0] or "/")
else:
    sys.stderr.write(f"librarian: tool {tool} not allowed\n"); sys.exit(2)
for pat in (inp.get("pattern") if tool == "Glob" else None, inp.get("glob")):
    # brace alternatives can smuggle absolute or parent paths: {/etc/*,x}
    if pat and "{" in pat and re.search(r"[{,]\s*[/~]|\.\.", pat):
        sys.stderr.write("librarian: brace pattern with a path is not allowed\n"); sys.exit(2)
for p in paths:
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(p)))
    if full != root and not full.startswith(root + os.sep):
        sys.stderr.write(f"librarian: {p} is outside the source store; denied\n"); sys.exit(2)
sys.exit(0)
'
