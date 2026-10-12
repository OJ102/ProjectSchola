"""Spike 0c: run the librarian on one unit, verify, retry once, cache.

Prototype of source-brief-save.sh (design decisions 6, 15, 17): the librarian
only returns text; this script confines it, writes the "Where" line itself,
verifies, retries once, and caches. A brief that still fails after the retry
is cached only as `partial`: its verified items, with the dropped ones listed.

usage: python3 spikes/brief_run.py <storedir> <unit.md> <outdir> [--model sonnet]
       python3 spikes/brief_run.py --finalize <storedir> <unit.md> <attempt.md> <outdir>   (no model call)
Needs <unit.md>.ids next to the unit file (written by spikes/unit.py).
Writes <outdir>/<unit>.md, <unit>.verify.json, <unit>.run.json.

Confinement (decision 15): the unit file is copied into <store>/units/, the
librarian runs with cwd = the store, SCHOLA_STORE set, and a session settings
file that installs hooks/source-librarian-guard.sh as a PreToolUse hook on
Read|Grep|Glob and denies Bash, Write, Edit, web tools and secret paths.
(Hooks in agent frontmatter did not fire under `claude -p --agent` in 2.1.296,
so the runner owns the hook.) The librarian prompt lives in
spikes/librarian-prompt.md and is passed as an inline --agents definition; it
is not shipped as a plugin agent (decision 25).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import source_verify as sv  # noqa: E402

GUARD = os.path.join(ROOT, "hooks", "source-librarian-guard.sh")
PROMPT = os.path.join(HERE, "librarian-prompt.md")
DENY = ["Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.config/**)", "Read(~/.gnupg/**)", "Read(**/.env)",
        "Read(**/.env.*)", "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "mcp__*"]


def settings_file():
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"hooks": {"PreToolUse": [{"matcher": ".*",
                                         "hooks": [{"type": "command", "command": GUARD}]}]},
               "permissions": {"deny": DENY}}, f)
    f.close()
    return f.name


def claude(prompt, store, resume=None, model=None):
    # decision 25: the librarian is not a plugin agent; it exists only as this
    # inline --agents definition, so nothing can delegate to it unconfined
    agent = {"librarian": {"description": "Source-mode librarian (runner-only).",
                           "prompt": open(PROMPT, encoding="utf-8").read(),
                           "tools": ["Read", "Grep", "Glob"], "model": "sonnet"}}
    cmd = ["claude", "-p", "--plugin-dir", ROOT, "--agents", json.dumps(agent), "--agent", "librarian",
           "--settings", settings_file(), "--output-format", "json",
           # pin the toolset and permission mode so neither the agent file nor
           # the user's defaults or MCP servers can widen it
           "--tools", "Read,Grep,Glob", "--strict-mcp-config", "--permission-mode", "default",
           "--allowedTools", "Read", "Grep", "Glob"]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    t0 = time.time()
    env = dict(os.environ, SCHOLA_STORE=os.path.realpath(store))
    # prompt on stdin: --allowedTools is variadic and would swallow it
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=store, timeout=900, env=env)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        sys.exit(f"claude failed: {r.stderr[-800:]} {r.stdout[-800:]}")
    u = d.get("usage", {})
    d["_tokens"] = {"input": u.get("input_tokens", 0), "cache_create": u.get("cache_creation_input_tokens", 0),
                    "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0),
                    "turns": d.get("num_turns"), "cost_usd": d.get("total_cost_usd"),
                    "seconds": round(time.time() - t0, 1)}
    return d


def with_where(brief, where):
    """Replace any librarian-written Where section with the code-generated one."""
    lines, out, skip = brief.strip("\n").split("\n"), [], False
    for ln in lines:
        if re.match(r"^##\s+Where\s*$", ln.strip(), re.I):
            skip = True
            continue
        if skip and ln.startswith("#"):
            skip = False
        if not skip:
            out.append(ln)
    while out and not out[0].strip():
        out.pop(0)
    return f"## Where\n{where}\n\n" + "\n".join(out) + "\n"


def partial(text, rep):
    """Keep only verified items: an item survives if it has at least one valid
    cite and no failed cite (failed cites are not silently removed, the item is
    dropped). Lines with structural failures are dropped. Headings and Where stay."""
    bad_lines = {f["line"] for f in rep["failures"] if f.get("line")}
    keep, dropped = [], []
    for n, ln in enumerate(text.split("\n"), 1):
        if n in bad_lines:
            dropped.append(ln)
        else:
            keep.append(ln)
    out = "<!-- status: partial; dropped %d item(s) that failed verification -->\n" % len(dropped) + "\n".join(keep)
    return out, dropped


def finalize(store, unit, text, outdir, attempts, runs, where):
    name = os.path.splitext(os.path.basename(unit))[0]
    ids = unit + ".ids"
    rep = sv.verify(store, None, [ids], where=where, text=text)
    status, dropped = "verified", []
    if not rep["pass"]:
        text, dropped = partial(text, rep)
        rep = sv.verify(store, None, [ids], where=where, text=re.sub(r"^<!--.*?-->\n", "", text))
        status = "partial" if rep["pass"] else "rejected"
    if status == "rejected":
        text = ""  # never cache a brief that fails, even after stripping
    open(os.path.join(outdir, f"{name}.md"), "w").write(text)
    rep.update({"status": status, "dropped": dropped})
    json.dump(rep, open(os.path.join(outdir, f"{name}.verify.json"), "w"), indent=1, ensure_ascii=False)
    summary = {"unit": name, "where": where, "status": status, "attempts": attempts,
               "final_cites": rep["cites"], "dropped_items": len(dropped),
               "brief_chars": len(text), "brief_tokens_est": len(text) // 4,
               "unit_tokens_est": len(open(unit, encoding="utf-8").read()) // 4,
               "calls": [r["_tokens"] for r in runs]}
    json.dump(summary, open(os.path.join(outdir, f"{name}.run.json"), "w"), indent=1, ensure_ascii=False)
    return summary


def main():
    a = sys.argv[1:]
    if a and a[0] == "--finalize":
        store, unit, attempt, outdir = a[1:5]
        where = open(unit, encoding="utf-8").readline().replace("# Unit source:", "").strip()
        text = with_where(open(attempt, encoding="utf-8").read(), where)
        print(json.dumps(finalize(store, unit, text, outdir, [], [], where), indent=1, ensure_ascii=False))
        return
    store, unit, outdir = a[0], a[1], a[2]
    model = a[a.index("--model") + 1] if "--model" in a else None
    os.makedirs(outdir, exist_ok=True)
    store = os.path.realpath(store)
    name = os.path.splitext(os.path.basename(unit))[0]
    # the unit file must live inside the store, the only place the librarian can read
    os.makedirs(os.path.join(store, "units"), exist_ok=True)
    inside = os.path.join(store, "units", os.path.basename(unit))
    if os.path.realpath(unit) != inside:
        shutil.copy(unit, inside)
    where = open(unit, encoding="utf-8").readline().replace("# Unit source:", "").strip()
    units = sorted(os.path.splitext(f)[0] for f in os.listdir(os.path.join(store, "units")) if f.endswith(".md")
                   and f != os.path.basename(unit))
    prompt = (f"Write the unit brief for the unit file {inside}. Follow your brief format exactly. "
              f"Unit ids you may name as 'Earlier unit:': {', '.join(units) or '(none)'}. "
              f"Output only the brief.")
    runs = [claude(prompt, store, model=model)]
    attempts = []
    text = with_where(runs[-1].get("result", ""), where)
    open(os.path.join(outdir, f"{name}.attempt1.md"), "w").write(text)
    rep = sv.verify(store, None, [unit + ".ids"], where=where, text=text)
    attempts.append({"pass": rep["pass"], "cites": rep["cites"], "ok": rep["ok"], "failures": rep["failures"]})
    if not rep["pass"]:
        reasons = "\n".join(f"- line {f.get('line')}: {f.get('id', '')} {f['reason']}" for f in rep["failures"])
        fix = ("The citation checker rejected these lines of your brief:\n" + reasons +
               "\nFix them: copy anchors verbatim from one line of the unit file, use that line's span id, "
               "cite every item, keep the exact section headings, one line per item, and never write page "
               "numbers or timestamps. Output the whole corrected brief only.")
        runs.append(claude(fix, store, resume=runs[0].get("session_id"), model=model))
        text = with_where(runs[-1].get("result", ""), where)
        open(os.path.join(outdir, f"{name}.attempt2.md"), "w").write(text)
        rep = sv.verify(store, None, [unit + ".ids"], where=where, text=text)
        attempts.append({"pass": rep["pass"], "cites": rep["cites"], "ok": rep["ok"], "failures": rep["failures"]})
    print(json.dumps(finalize(store, unit, text, outdir, attempts, runs, where), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
