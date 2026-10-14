"""Stand-in for `claude -p` in the source-teach tests (no model call).
FAKE_CLAUDE=ok: answer with a brief citing the first long span of the unit file
named in the prompt. FAKE_CLAUDE=fail: exit 1 with no JSON. FAKE_CLAUDE=bad:
a brief whose only cite has an anchor not in the span (rejected)."""
import json
import os
import re
import sys

mode = os.environ.get("FAKE_CLAUDE", "ok")
prompt = sys.stdin.read()
if mode == "fail":
    sys.stderr.write("fake claude: failure\n")
    sys.exit(1)
m = re.search(r"unit file (\S+?\.md)", prompt)
lines = open(m.group(1), encoding="utf-8").read().split("\n") if m else []
sid, words = None, []
for ln in lines:
    mm = re.match(r"\[(\S+)\] (.*)", ln)
    if mm and len(re.findall(r"[A-Za-z]{2,}", mm.group(2))) >= 8 and not re.search(r"\d", mm.group(2)):
        sid, words = mm.group(1), mm.group(2).split()[:8]
        break
anchor = " ".join(words) if mode == "ok" else "this anchor is not anywhere in the span text"
cite = f'[[cite:{sid} "{anchor}"]]'
brief = (f"## Core claims\n- A claim. {cite}\n\n## Quiz seeds\n1. A question? — Answer: yes {cite}\n")
print(json.dumps({"result": brief, "session_id": "fake", "total_cost_usd": 0, "is_error": False}))
