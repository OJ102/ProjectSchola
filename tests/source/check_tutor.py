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

# -----------------------------------------------------------------------------
# 5. Every quoted run before a cite is checked (not only one right before the token)
# -----------------------------------------------------------------------------
QUOTE_CASES = {
    "word_between": ('It says "the moon alone causes every tide" in the book [[cite:p0010.1]].', False),
    "two_quotes_one_fake": ('It says "the moon alone causes every tide" and "driven by the gravitational pull" [[cite:p0010.1]].', False),
    "single_quoted_fake": ("It says 'the moon alone causes every tide' [[cite:p0010.1]].", False),
    "single_curly_fake": ("It says ‘the moon alone causes every tide’ [[cite:p0010.1]].", False),
    "guillemets_fake": ("It says «the moon alone causes every tide» [[cite:p0010.1]].", False),
    "two_quotes_real": ('Both "driven by the gravitational pull" and "to a lesser degree" appear [[cite:p0010.1]].', True),
    "single_quoted_real": ("It's 'driven by the gravitational pull of the moon', don't forget [[cite:p0010.1]].", True),
    "quote_owned_by_earlier_cite": ('First "when the sun and moon align" [[cite:p0010.2]] then tides [[cite:p0010.1]].', True),
    "quote_after_token": ('As the book puts it [[cite:p0010.1]]: "the moon alone causes every tide".', False),
    "bad_cite_before_absorbs_nothing": ('"the moon alone causes every tide" [[cite:p9999.9]][[cite:p0010.1]]', False),
    "german_quotes": ("„the moon alone causes every tide“ [[cite:p0010.1]]", False),
    "cjk_quotes": ("「the moon alone causes every tide」 [[cite:p0010.1]]", False),
    "fullwidth_quotes": ("＂the moon alone causes every tide＂ [[cite:p0010.1]]", False),
    "mixed_curly_straight": ('“the moon alone causes every tide" [[cite:p0010.1]]', False),
    "angle_single": ("‹the moon alone causes every tide› [[cite:p0010.1]]", False),
    "prime_marks": ("″the moon alone causes every tide″ [[cite:p0010.1]]", False),
    "german_real": ("„driven by the gravitational pull of the moon“ [[cite:p0010.1]]", True),
    "removed_cite_then_unrelated_cite": ('"the moon alone causes every tide" [[cite:p9999.9]] and later tides [[cite:p0010.1]].', True),
}
for name, (txt, good) in QUOTE_CASES.items():
    r_q = subprocess.run(["bash", cite_check_sh, STORE_DIR, brief_path, "--text", txt], capture_output=True, text=True)
    out_q = r_q.stdout
    if good:
        o(f"tutor quotes: {name} keeps its locator", "(p. 10)" in out_q and "quote not found" not in out_q, out_q.strip())
    else:
        o(f"tutor quotes: {name} is not given a locator", "(p. 10)" not in out_q and "quote not found" in out_q, out_q.strip())

# -----------------------------------------------------------------------------
# 6. Stop hook fails closed: non-UTF-8 log, and any error inside the check
# -----------------------------------------------------------------------------
with open(session_log_path, "wb") as f:
    f.write(f"---\nsession: {session_id}\ntopic: Ocean Tides\n---\n\n**Tutor:** caf\xe9 latin1 byte, moon [[cite:p0010.1]] and [[cite:p9999.9]].\n".encode("latin-1"))
r_b = subprocess.run(["bash", cite_check_sh], input=hook_input, capture_output=True, text=True, env=hook_env)
log_b = open(session_log_path, encoding="utf-8", errors="replace").read()
o("hook: non-UTF-8 byte in the log is checked, not skipped",
  "[[cite:" not in log_b and "(p. 10)" in log_b and "(citation removed)" in log_b, f"rc={r_b.returncode} {log_b[-200:]} {r_b.stderr[-200:]}")

spans_path = os.path.join(tides_store, "spans.jsonl")
good_spans = open(spans_path, encoding="utf-8").read()
with open(spans_path, "a", encoding="utf-8") as f:
    f.write("{not json\n")
with open(session_log_path, "w", encoding="utf-8") as f:
    f.write(initial_session_body)
r_c = subprocess.run(["bash", cite_check_sh], input=hook_input, capture_output=True, text=True, env=hook_env)
log_c = open(session_log_path, encoding="utf-8").read()
o("hook: an error inside the check still strips [[cite: tokens (fail closed) and says why",
  r_c.returncode == 0 and "[[cite:" not in log_c and "cite check failed" in log_c and "cite check failed" in r_c.stderr,
  f"rc={r_c.returncode} log={log_c[-200:]} err={r_c.stderr[-200:]}")
with open(spans_path, "w", encoding="utf-8") as f:
    f.write(good_spans)

# Lock held by another live process (not handed over): fail closed, never leave tokens
lock_path = os.path.join(state_root, f"{session_id}.lock")
ps = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getppid())], capture_output=True, text=True).stdout.strip()
os.symlink(f"{os.getppid()} {ps}", lock_path)
with open(session_log_path, "w", encoding="utf-8") as f:
    f.write(initial_session_body)
r_l = subprocess.run(["bash", cite_check_sh], input=hook_input, capture_output=True, text=True,
                     env=dict(hook_env, MD_LOG_LOCK_HELD="1 not-the-owner"), timeout=60)
log_l = open(session_log_path, encoding="utf-8").read()
o("hook: lock not obtainable -> tokens still stripped", "[[cite:" not in log_l and "LockError" in r_l.stderr,
  f"{log_l[-200:]} {r_l.stderr[-200:]}")
os.unlink(lock_path)

# -----------------------------------------------------------------------------
# 7. Ordering: the cite check runs inside md-log-stop-hook.sh, after its render, under its lock
# -----------------------------------------------------------------------------
hooks_json = json.load(open(os.path.join(HOOKS_DIR, "hooks.json"), encoding="utf-8"))
parallel = [ev for ev, groups in hooks_json["hooks"].items() for g in groups for h in g.get("hooks", [])
            if "source-cite-check" in " ".join(h.get("args", []) + [h.get("command", "")])]
o("hooks.json: no cite-check hook running in parallel with md-log-stop-hook.sh", not parallel, parallel)

md_tr = os.path.join(hook_work, "md_transcript.jsonl")
md_sid = "test-session-md-0002"
with open(md_tr, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "timestamp": "2026-10-14T10:00:00Z", "message": {"role": "user",
            "content": f"<command-name>/teach</command-name>\n<command-args>tides Unit brief: {tides_brief}</command-args>"}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text",
            "text": "Tides follow the moon [[cite:p0010.1]]. It says \"the moon alone causes every tide\" [[cite:p0010.1]]. Invented [[cite:p9999.9]]."}]}}) + "\n")
md_input = json.dumps({"transcript_path": md_tr, "session_id": md_sid})
r_md = subprocess.run(["bash", os.path.join(HOOKS_DIR, "md-log-stop-hook.sh")], input=md_input,
                      capture_output=True, text=True, env=hook_env, timeout=120)
md_logs = [os.path.join(vault_root, "sessions", n) for n in os.listdir(os.path.join(vault_root, "sessions"))
           if md_sid in open(os.path.join(vault_root, "sessions", n), encoding="utf-8", errors="replace").read()]
md_text = open(md_logs[0], encoding="utf-8").read() if md_logs else ""
o("md-log render + cite check: no raw [[cite: in the note, locators and removals applied",
  bool(md_text) and "[[cite:" not in md_text and "(p. 10)" in md_text and "quote not found" in md_text
  and "(citation removed)" in md_text, f"rc={r_md.returncode} {md_text[-300:]} {r_md.stderr[-200:]}")
o("md-log: lock released after the cite check", not os.path.lexists(os.path.join(state_root, f"{md_sid}.lock")))
r_md2 = subprocess.run(["bash", os.path.join(HOOKS_DIR, "md-log-stop-hook.sh")], input=md_input,
                       capture_output=True, text=True, env=hook_env, timeout=120)
md_prog = json.load(open(store_progress_path, encoding="utf-8"))
drift = json.load(open(os.path.join(tides_store, "drift.json"), encoding="utf-8"))
o("md-log re-render: drift for a session is not counted twice", drift.get(md_sid) == 2, drift)
o("md-log: progress.json total is the sum over sessions", md_prog.get("drift_flagged_cites") == sum(drift.values()), (md_prog, drift))

# A session that only used where/fastpath/review is still a source session
for sub_cmd in ("where", "fastpath", "review"):
    tr_w = os.path.join(hook_work, f"tr_{sub_cmd}.jsonl")
    sid_w = f"test-session-{sub_cmd}"
    with open(tr_w, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "teach me tides"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash",
                "input": {"command": f"bash /p/hooks/source-brief.sh {sub_cmd} tides u001"}}]}}) + "\n")
    lp = os.path.join(vault_root, "sessions", f"2026-10-14 {sub_cmd}.md")
    with open(lp, "w", encoding="utf-8") as f:
        f.write(f"---\nsession: {sid_w}\n---\nTutor: \"the moon alone causes every tide\" [[cite:p0010.1]] ok [[cite:p0010.1]]\n")
    subprocess.run(["bash", cite_check_sh], input=json.dumps({"transcript_path": tr_w, "session_id": sid_w}),
                   capture_output=True, text=True, env=hook_env)
    lt = open(lp, encoding="utf-8").read()
    o(f"hook: session using only `{sub_cmd}` is checked", "[[cite:" not in lt and "quote not found" in lt and "(p. 10)" in lt, lt)

# No source found, but the note holds citation tokens: they are removed
tr_n = os.path.join(hook_work, "tr_nosrc.jsonl")
with open(tr_n, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "explain tides"}}) + "\n")
lp = os.path.join(vault_root, "sessions", "2026-10-14 nosrc.md")
with open(lp, "w", encoding="utf-8") as f:
    f.write("---\nsession: test-session-nosrc\n---\nTutor: \"made up\" [[cite:p0010.1]] and a [[wikilink]].\n")
subprocess.run(["bash", cite_check_sh], input=json.dumps({"transcript_path": tr_n, "session_id": "test-session-nosrc"}),
               capture_output=True, text=True, env=hook_env)
lt = open(lp, encoding="utf-8").read()
o("hook: tokens in a session with no detectable source are removed", "[[cite:" not in lt and "no source found" in lt, lt)
lp2 = os.path.join(vault_root, "sessions", "2026-10-14 plain.md")
with open(lp2, "w", encoding="utf-8") as f:
    f.write("---\nsession: test-session-plain\n---\nA plain note with a [[wikilink]].\n")
subprocess.run(["bash", cite_check_sh], input=json.dumps({"transcript_path": tr_n, "session_id": "test-session-plain"}),
               capture_output=True, text=True, env=hook_env)
o("hook: a non-source note without tokens is left untouched", "[[wikilink]]" in open(lp2, encoding="utf-8").read())

# The vault never holds a raw token, even mid-render: the check runs on the temp file before the move
import threading  # noqa: E402
seen, stop = [], threading.Event()


def poll():
    d = os.path.join(vault_root, "sessions")
    while not stop.is_set():
        for n in os.listdir(d):
            if n.endswith(".md"):
                try:
                    if "[[cite:" in open(os.path.join(d, n), encoding="utf-8", errors="replace").read():
                        seen.append(n)
                except OSError:
                    pass


th = threading.Thread(target=poll)
th.start()
for _ in range(15):
    subprocess.run(["bash", os.path.join(HOOKS_DIR, "md-log-stop-hook.sh")], input=md_input,
                   capture_output=True, text=True, env=hook_env, timeout=120)
stop.set()
th.join()
o("md-log: no raw [[cite: token is ever visible in the vault during renders", not seen, sorted(set(seen)))
