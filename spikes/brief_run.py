"""Spike 0c: run the librarian on one unit, verify, retry once, cache.

Prototype of source-brief-save.sh (design decision 6): the librarian only
returns text; this script writes, verifies and caches the brief.

usage: python3 spikes/brief_run.py <storedir> <unit.md> <outdir> [--model sonnet]
Needs <unit.md>.ids next to the unit file (written by spikes/unit.py).
Writes <outdir>/<unit>.md (verified brief), <unit>.verify.json, <unit>.run.json.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import source_verify as sv  # noqa: E402


def claude(prompt, resume=None, model=None):
    cmd = ["claude", "-p", "--plugin-dir", ROOT, "--agent", "learn-voice-teacher:librarian",
           "--output-format", "json", "--allowedTools", "Read,Grep,Glob"]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    t0 = time.time()
    # prompt on stdin: --allowedTools is variadic and would swallow it
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=ROOT, timeout=900)
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


def strip_failed(text, rep):
    """Second failure: drop failed cites; a claim left with no valid cite is
    marked [unverified] so it is never taught as 'the source says'."""
    bad = {}
    for f in rep["failures"]:
        bad.setdefault(f.get("line"), []).append(f)
    lines = text.split("\n")
    for ln, fs in bad.items():
        if not ln:
            continue
        line = lines[ln - 1]
        for c in rep["detail"]:
            if c["line"] == ln and c["status"] != "ok":
                line = line.replace(f'[[cite:{c["id"]} "{c["anchor"]}"]]', "")
        if not sv.CITE.search(line) and re.match(r"^\s*(?:[-*+]|\d+[.)])\s", line):
            line = line.rstrip() + " [unverified]"
        lines[ln - 1] = line
    return "\n".join(lines)


def main():
    a = sys.argv[1:]
    store, unit, outdir = a[0], a[1], a[2]
    model = a[a.index("--model") + 1] if "--model" in a else None
    os.makedirs(outdir, exist_ok=True)
    name = os.path.splitext(os.path.basename(unit))[0]
    ids = unit + ".ids"
    where = open(unit, encoding="utf-8").readline().replace("# Unit source:", "").strip()
    prompt = (f"Write the unit brief for the unit file {os.path.abspath(unit)} "
              f"(outline path: {where}). Follow your brief format exactly. Output only the brief.")
    runs = [claude(prompt, model=model)]
    brief = runs[-1].get("result", "")
    path = os.path.join(outdir, f"{name}.attempt1.md")
    open(path, "w").write(brief)
    rep = sv.verify(store, path, [ids])
    attempts = [{"pass": rep["pass"], "cites": rep["cites"], "ok": rep["ok"], "failures": rep["failures"]}]
    if not rep["pass"]:
        reasons = "\n".join(f"- line {f.get('line')}: {f.get('id', '')} {f['reason']}" for f in rep["failures"])
        fix = ("The citation checker rejected these lines of your brief:\n" + reasons +
               "\nFix them: copy anchors verbatim from one line of the unit file, use that line's span id, "
               "cite every claim, and never write page numbers or timestamps. Output the whole corrected brief only.")
        runs.append(claude(fix, resume=runs[0].get("session_id"), model=model))
        brief = runs[-1].get("result", "")
        path = os.path.join(outdir, f"{name}.attempt2.md")
        open(path, "w").write(brief)
        rep = sv.verify(store, path, [ids])
        attempts.append({"pass": rep["pass"], "cites": rep["cites"], "ok": rep["ok"], "failures": rep["failures"]})
        if not rep["pass"]:
            brief = strip_failed(brief, rep)
    final = os.path.join(outdir, f"{name}.md")
    open(final, "w").write(brief)
    final_rep = sv.verify(store, final, [ids])
    json.dump(final_rep, open(os.path.join(outdir, f"{name}.verify.json"), "w"), indent=1, ensure_ascii=False)
    summary = {"unit": name, "where": where, "attempts": attempts,
               "final_pass": final_rep["pass"], "final_cites": final_rep["cites"],
               "unverified_lines": brief.count("[unverified]"),
               "brief_chars": len(brief), "brief_tokens_est": len(brief) // 4,
               "unit_tokens_est": len(open(unit, encoding="utf-8").read()) // 4,
               "calls": [r["_tokens"] for r in runs]}
    json.dump(summary, open(os.path.join(outdir, f"{name}.run.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
