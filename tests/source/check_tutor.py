#!/usr/bin/env python3
"""Tutor citation checking and Stop-hook regression tests (tester).

usage: check_tutor.py <hooks-dir> <workdir>
Builds a synthetic store + brief, feeds crafted replies to cite_check.py --analyze,
tests direct text filter modes, and tests the Stop/SessionEnd hook mode with
mock transcripts and Obsidian session logs.
"""
import json
import os
import subprocess
import sys

HOOKS_DIR, WORK_DIR = sys.argv[1], sys.argv[2]
STORE_DIR = os.path.join(WORK_DIR, "store")
DR_DIR = os.path.join(WORK_DIR, "dr")
os.makedirs(STORE_DIR, exist_ok=True)
os.makedirs(DR_DIR, exist_ok=True)

sys.path.insert(0, os.path.join(HOOKS_DIR, "source"))
from srcnorm import norm  # noqa: E402

def o(name, good, detail=""):
    print(f"ok {name}" if good else f"FAIL {name} :: " + str(detail).replace("\n", " "))

# -----------------------------------------------------------------------------
# 1. Synthetic store & brief setup
# -----------------------------------------------------------------------------
SPANS = {
    "p0010.1": "Tides are driven by the gravitational pull of the moon and, to a lesser degree, the sun.",
    "p0010.2": "Spring tides happen when the sun and moon align and their pulls add up together.",
    "p0011.1": "Neap tides happen when the sun and moon sit at right angles to each other.",
    "p0050.1": "Ocean currents carry heat from the equator toward the poles over many years.",
}

with open(os.path.join(STORE_DIR, "spans.jsonl"), "w", encoding="utf-8") as f:
    for sid, t in SPANS.items():
        f.write(json.dumps({
            "id": sid,
            "segment": sid.split(".")[0],
            "locator": sid[1:5].lstrip("0"),
            "kind": "para",
            "text": t,
            "norm": norm(t),
        }) + "\n")

# Store metadata files
with open(os.path.join(STORE_DIR, "labels.json"), "w", encoding="utf-8") as f:
    json.dump({"pages": [{"pdf_index": 10, "label": "10"}, {"pdf_index": 11, "label": "11"}, {"pdf_index": 50, "label": "50"}]}, f)

with open(os.path.join(STORE_DIR, "outline.json"), "w", encoding="utf-8") as f:
    json.dump([{"id": "c1", "title": "Tides", "depth": 1, "pdf_index": 10}], f)

brief_path = os.path.join(WORK_DIR, "brief.md")
with open(brief_path, "w", encoding="utf-8") as f:
    f.write(
        "# B\n\n## Core claims\n"
        "- Moon drives tides [[cite:p0010.1 \"driven by the gravitational pull of the moon\"]]\n"
        "- Spring tides [[cite:p0010.2 \"when the sun and moon align and their pulls\"]]\n"
        "- Neap tides [[cite:p0011.1 \"when the sun and moon sit at right angles\"]]\n"
    )

# -----------------------------------------------------------------------------
# 2. CLI mode with crafted replies (--analyze)
# -----------------------------------------------------------------------------
REPLIES = {
    "ok_plain": "The moon is the main driver [[cite:p0010.1]].",
    "ok_quote": "The book says “Tides are driven by the gravitational pull of the moon” [[cite:p0010.1]].",
    "forged_id_in_store": "Currents move heat [[cite:p0050.1]].",
    "forged_id_unknown": "Something invented [[cite:p9999.9]].",
    "quote_wrong_span": "It says \"when the sun and moon sit at right angles\" [[cite:p0010.2]].",
    "quote_fabricated": "It says \"the moon alone causes every tide on earth\" [[cite:p0010.1]].",
    "quote_with_brackets": "It says \"the moon alone ]] causes every tide on earth\" [[cite:p0010.1]].",
    "quote_with_newline": "It says \"the moon alone causes\nevery tide on earth\" [[cite:p0010.1]].",
    "anchor_forged_in_cite": "Tides [[cite:p0010.1 \"the sun alone causes every tide on earth\"]].",
    "neutralize_tokens": "Neutralize [[cite:live and ]] in text.",
}

order = list(REPLIES)
with open(os.path.join(DR_DIR, "replies.json"), "w", encoding="utf-8") as f:
    json.dump([{"user": k, "tutor": v} for k, v in REPLIES.items()], f)

with open(os.path.join(DR_DIR, "dryrun.json"), "w", encoding="utf-8") as f:
    json.dump({"session": "00000000-tester-no-such-session", "turns": [{"cost_usd": 0}]}, f)

cite_check_sh = os.path.join(HOOKS_DIR, "source-cite-check.sh")
r = subprocess.run([
    "bash", cite_check_sh, STORE_DIR, brief_path, DR_DIR, "--analyze"
], capture_output=True, text=True)

o("tutor: dryrun --analyze runs on crafted replies", r.returncode == 0, (r.stderr or r.stdout)[-400:])

if r.returncode == 0:
    rep = json.load(open(os.path.join(DR_DIR, "dryrun.json"), encoding="utf-8"))
    log_content = open(os.path.join(DR_DIR, "dryrun-log.md"), encoding="utf-8").read()
    log = log_content.split("---")
    flag = rep.get("tutor_cites_flagged", [])
    mismatches = rep.get("tutor_quote_mismatch", [])
    qids = [(q.get("id"), q.get("quote", "")[:30], q.get("repoint_to")) for q in mismatches]

    def logpart(k):
        idx = order.index(k)
        return log[idx] if idx < len(log) else ""

    o("tutor: id in brief not flagged", "p0010.1" not in flag and "p0010.2" not in flag, f"flagged {flag}")
    o("tutor: id in store but not brief/lookup flagged", "p0050.1" in flag, f"flagged {flag}")
    o("tutor: unknown id flagged", "p9999.9" in flag, f"flagged {flag}")
    o(
        "tutor: forged ids render '(citation removed)'",
        "(citation removed)" in logpart("forged_id_in_store") and "(citation removed)" in logpart("forged_id_unknown"),
        logpart("forged_id_in_store")[-120:] + " / " + logpart("forged_id_unknown")[-120:],
    )
    o(
        "tutor: correct smart-quoted quote not a mismatch",
        not any("Tides are driven" in q.get("quote", "") for q in mismatches),
        str(qids),
    )
    o(
        "tutor: quote from other span re-pointed",
        any(q.get("id") == "p0010.2" and q.get("repoint_to") == "p0011.1" for q in mismatches),
        str(qids),
    )
    o(
        "tutor: fabricated quote caught (repoint_to None)",
        any(q.get("id") == "p0010.1" and "moon alone causes every" in q.get("quote", "") and q.get("repoint_to") is None for q in mismatches)
        and "(citation removed: quote not found in the source)" in logpart("quote_fabricated"),
        str(qids),
    )
    o(
        "tutor: fabricated quote containing ']]' caught",
        any("]]" in q.get("quote", "") or "moon alone ]]" in q.get("quote", "") for q in mismatches)
        or "(citation removed)" in logpart("quote_with_brackets"),
        f"mismatches {qids}; log: {logpart('quote_with_brackets').strip()[-160:]}",
    )
    o(
        "tutor: fabricated quote with a newline caught",
        any("causes" in q.get("quote", "") and "every" in q.get("quote", "") and "\n" in q.get("quote", "") for q in mismatches)
        or "(citation removed)" in logpart("quote_with_newline"),
        f"mismatches {qids}; log: {logpart('quote_with_newline').strip()[-160:]}",
    )
    o(
        "tutor: forged anchor inside [[cite:id \"...\"]] caught",
        any("sun alone" in q.get("quote", "") for q in mismatches) or "(citation removed)" in logpart("anchor_forged_in_cite"),
        f"mismatches {qids}; log: {logpart('anchor_forged_in_cite').strip()[-160:]}",
    )
    o(
        "tutor: neutralization of live [[cite: tokens",
        "[[cite:" not in log_content and "]]" not in log_content,
        "live tokens survived in dryrun-log.md",
    )
    o(
        "tutor: log renders re-pointed quote at its real span (p. 11)",
        "(p. 11)" in logpart("quote_wrong_span"),
        logpart("quote_wrong_span").strip()[-160:],
    )
    o(
        "tutor: log does not present a fabricated quote as cited",
        "(p. 10)" not in logpart("quote_fabricated"),
        logpart("quote_fabricated").strip()[-160:],
    )

# -----------------------------------------------------------------------------
# 3. Direct text CLI mode (--text and stdin pipe)
# -----------------------------------------------------------------------------
r_text = subprocess.run([
    "bash", cite_check_sh, STORE_DIR, brief_path, "--text", "Tides are driven [[cite:p0010.1]]."
], capture_output=True, text=True)
o("tutor text CLI: --text rewrites citation to locator", r_text.returncode == 0 and "(p. 10)" in r_text.stdout, r_text.stdout.strip())

r_pipe = subprocess.run([
    "bash", cite_check_sh, STORE_DIR, brief_path
], input="Moon drives [[cite:p0010.1]] and bad [[cite:p9999.9]].", capture_output=True, text=True)
o(
    "tutor text CLI: stdin pipe rewrites citation to locator",
    r_pipe.returncode == 0 and "(p. 10)" in r_pipe.stdout and "(citation removed)" in r_pipe.stdout,
    r_pipe.stdout.strip(),
)

# -----------------------------------------------------------------------------
# 4. Stop hook mode
# -----------------------------------------------------------------------------
hook_work = os.path.join(WORK_DIR, "hook_test")
src_root = os.path.join(hook_work, "src")
vault_root = os.path.join(hook_work, "vault")
state_root = os.path.join(hook_work, "state")
os.makedirs(os.path.join(vault_root, "sessions"), exist_ok=True)
os.makedirs(os.path.join(vault_root, "sources", "tides"), exist_ok=True)
os.makedirs(state_root, exist_ok=True)

# Create managed source store in src_root/tides
tides_store = os.path.join(src_root, "tides")
os.makedirs(os.path.join(tides_store, "briefs"), exist_ok=True)
with open(os.path.join(tides_store, "spans.jsonl"), "w", encoding="utf-8") as f:
    for sid, t in SPANS.items():
        f.write(json.dumps({
            "id": sid,
            "segment": sid.split(".")[0],
            "locator": sid[1:5].lstrip("0"),
            "kind": "para",
            "text": t,
            "norm": norm(t),
        }) + "\n")

with open(os.path.join(tides_store, "labels.json"), "w", encoding="utf-8") as f:
    json.dump({"pages": [{"pdf_index": 10, "label": "10"}, {"pdf_index": 11, "label": "11"}, {"pdf_index": 50, "label": "50"}]}, f)

with open(os.path.join(tides_store, "outline.json"), "w", encoding="utf-8") as f:
    json.dump([{"id": "c1", "title": "Tides", "depth": 1, "pdf_index": 10}], f)

# Brief in store
tides_brief = os.path.join(tides_store, "briefs", "u001.md")
with open(tides_brief, "w", encoding="utf-8") as f:
    f.write(
        "# Tides\n\n## Core claims\n"
        "- Moon [[cite:p0010.1 \"driven by the gravitational pull of the moon\"]]\n"
    )

# Vault progress.md
vault_progress_path = os.path.join(vault_root, "sources", "tides", "progress.md")
with open(vault_progress_path, "w", encoding="utf-8") as f:
    f.write(
        "---\nsource: tides\ntitle: Ocean Tides\nstatus: not started\n---\n\n"
        "# tides\n\nProgress for this source.\n"
    )

# Store progress.json
store_progress_path = os.path.join(tides_store, "progress.json")
with open(store_progress_path, "w", encoding="utf-8") as f:
    json.dump({"units_total": 1, "units_done": 0}, f, indent=1)

# Session log
session_id = "test-session-001"
session_log_path = os.path.join(vault_root, "sessions", "2026-10-14 Ocean Tides.md")
initial_session_body = (
    f"---\nsession: {session_id}\ntopic: Ocean Tides\n---\n\n"
    "**Learner:** What causes tides?\n\n"
    "**Tutor:** Tides are driven by the gravitational pull of the moon [[cite:p0010.1]]. "
    "Also currents move heat [[cite:p0050.1]]. "
    "And invented [[cite:p9999.9]].\n"
)
with open(session_log_path, "w", encoding="utf-8") as f:
    f.write(initial_session_body)

# Transcript
transcript_path = os.path.join(hook_work, "transcript.jsonl")
with open(transcript_path, "w", encoding="utf-8") as f:
    f.write(json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": f"<command-name>/teach</command-name><command-args>tides Unit brief: {tides_brief}</command-args>",
        },
    }) + "\n")

hook_env = dict(
    os.environ,
    SCHOLA_SOURCES_DIR=src_root,
    OBSIDIAN_VAULT_PATH=vault_root,
    MD_LOG_STATE_DIR=state_root,
)

# Test edge case: clean exit on empty stdin
r_empty = subprocess.run(["bash", cite_check_sh], input="", capture_output=True, text=True, env=hook_env)
o("hook: clean exit 0 on empty stdin", r_empty.returncode == 0, f"rc={r_empty.returncode}")

# Test edge case: clean exit on invalid json
r_badjson = subprocess.run(["bash", cite_check_sh], input="{bad-json", capture_output=True, text=True, env=hook_env)
o("hook: clean exit 0 on invalid JSON", r_badjson.returncode == 0, f"rc={r_badjson.returncode}")

# Test edge case: non-source session
non_source_tr = os.path.join(hook_work, "non_source_tr.jsonl")
with open(non_source_tr, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hello world"}}) + "\n")
r_nonsrc = subprocess.run(
    ["bash", cite_check_sh],
    input=json.dumps({"transcript_path": non_source_tr, "session_id": "other-session"}),
    capture_output=True,
    text=True,
    env=hook_env,
)
o("hook: clean exit 0 on non-source transcript", r_nonsrc.returncode == 0, f"rc={r_nonsrc.returncode}")

# Run Stop hook on our mock source session
hook_input = json.dumps({"transcript_path": transcript_path, "session_id": session_id})
r_hook = subprocess.run(
    ["bash", cite_check_sh],
    input=hook_input,
    capture_output=True,
    text=True,
    env=hook_env,
)
o("hook: exit 0 on valid session", r_hook.returncode == 0, (r_hook.stderr or r_hook.stdout)[-200:])

rewritten_log = open(session_log_path, encoding="utf-8").read()
o(
    "hook: rewrites session log with valid locator and removes unverified cites",
    "(p. 10)" in rewritten_log and rewritten_log.count("(citation removed)") == 2 and "[[cite:" not in rewritten_log,
    rewritten_log,
)
o(
    "hook: session log frontmatter preserved",
    rewritten_log.startswith(f"---\nsession: {session_id}\ntopic: Ocean Tides\n---"),
    rewritten_log[:100],
)

vault_prog_content = open(vault_progress_path, encoding="utf-8").read()
o(
    "hook: records drift in vault progress.md",
    "drift_flagged_cites: 2" in vault_prog_content,
    vault_prog_content[:200],
)

store_prog_json = json.load(open(store_progress_path, encoding="utf-8"))
o(
    "hook: records drift in store progress.json",
    store_prog_json.get("drift_flagged_cites") == 2,
    str(store_prog_json),
)

# Second turn with another unverified citation: verify incremental drift accumulation
with open(session_log_path, "a", encoding="utf-8") as f:
    f.write("\n**Tutor:** Here is another [[cite:p0050.1]].\n")

subprocess.run(
    ["bash", cite_check_sh],
    input=hook_input,
    capture_output=True,
    text=True,
    env=hook_env,
)
vault_prog_turn2 = open(vault_progress_path, encoding="utf-8").read()
store_prog_turn2 = json.load(open(store_progress_path, encoding="utf-8"))
o(
    "hook: increments drift_flagged_cites on subsequent unverified citations",
    "drift_flagged_cites: 3" in vault_prog_turn2 and store_prog_turn2.get("drift_flagged_cites") == 3,
    f"vault:\n{vault_prog_turn2[:200]}\nstore:\n{store_prog_turn2}",
)

# Missing session log test
missing_log_session = json.dumps({"transcript_path": transcript_path, "session_id": "no-such-log"})
r_nolog = subprocess.run(
    ["bash", cite_check_sh],
    input=missing_log_session,
    capture_output=True,
    text=True,
    env=hook_env,
)
o("hook: clean exit 0 when session log is absent", r_nolog.returncode == 0, f"rc={r_nolog.returncode}")
