"""Stand-in for `claude -p` as the fact-checker judge (no model call).
FAKE_FC is a JSON object {"<substring of the statement>": "<mode>"}; the first key found in
the prompt's <statement> block picks the mode, else FAKE_FC_DEFAULT (default "supported").
Modes: supported | partial | unsupported (plain JSON), fenced:<verdict> (```json block),
fail (exit 1, no JSON), error (is_error true), garbage (no verdict in the result).
FAKE_FC_LOG, if set, gets one JSON line per call: argv, cwd, SCHOLA_STORE, MD_LOG_LIVE, prompt."""
import json
import os
import re
import sys

prompt = sys.stdin.read()
log = os.environ.get("FAKE_FC_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "store": os.environ.get("SCHOLA_STORE"),
                            "md_log_live": os.environ.get("MD_LOG_LIVE"), "prompt": prompt}) + "\n")
m = re.search(r"<statement>\n(.*?)\n</statement>", prompt, re.S)
stmt = m.group(1) if m else ""
mode = os.environ.get("FAKE_FC_DEFAULT", "supported")
for k, v in json.loads(os.environ.get("FAKE_FC") or "{}").items():
    if k in stmt:
        mode = v
        break
if mode == "fail":
    sys.stderr.write("fake fact-checker: failure\n")
    sys.exit(1)
verdict = json.dumps({"verdict": mode.split(":")[-1], "reason": f"fake judge: {mode}"})
result = {"garbage": "I cannot decide.", "error": "boom"}.get(mode, verdict)
if mode.startswith("fenced:"):
    result = f"```json\n{verdict}\n```"
print(json.dumps({"result": result, "session_id": "fake", "total_cost_usd": 0, "is_error": mode == "error"}))
