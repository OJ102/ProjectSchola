"""Spike 0d: end-to-end dry run of one unit, with the main-context token count.

usage: python3 spikes/dryrun.py <storedir> <brief.md> <outdir> [--model sonnet] [--analyze]

Drives a headless teaching session (claude -p, then --resume per turn) with
scripted learner turns, then reads the session transcript and measures:
  - source-derived tokens that entered the MAIN context (tool results from
    Read/Grep/Glob/Agent over the store, briefs or units), budget <= 10k;
  - total context per turn (input + cache tokens) and cost;
  - tutor citations: ids not in the brief or a lookup are flagged (prototype
    of the Stop-hook check), the rest rendered to locators.
"""
import glob
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

BUDGET = 10_000
TUTOR_CITE = re.compile(r"\[\[cite:([^\s\]\"]+)(?:\s+\"[^\"]*\")?\]\]")


def turns_for(brief_path, store):
    seg = os.path.abspath(os.path.join(store, "segments"))
    return [
        f"/teach Teach me this unit from my source. Unit brief: {os.path.abspath(brief_path)} . "
        f"Store segments dir (for lookups only): {seg} . Keep each reply short, one idea at a time.",
        "I think pre-training just teaches the model to predict the next word, and post-training makes it chat nicely? Not sure why that needs two steps.",
        "Where does it say how much compute post-training uses compared to pre-training?",
        "Ok. Quiz me on one thing from this unit.",
        "My answer: the reward model is trained on comparisons, because people are more consistent comparing two answers than scoring one.",
    ]


def claude(prompt, session=None, model=None, first=False):
    cmd = ["claude", "-p", "--plugin-dir", ROOT, "--output-format", "json",
           "--append-system-prompt", open(os.path.join(HERE, "teach-source-draft.md")).read(),
           "--allowedTools", "Read,Grep,Glob,Skill"]
    if model:
        cmd += ["--model", model]
    if session and not first:
        cmd += ["--resume", session]
    elif session:
        cmd += ["--session-id", session]
    t0 = time.time()
    # keep the plugin's Stop-hook session log out of the real vault
    env = dict(os.environ, OBSIDIAN_VAULT_PATH=os.path.join(HERE, "out", "vault"))
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=ROOT, timeout=900, env=env)
    d = json.loads(r.stdout)
    d["_seconds"] = round(time.time() - t0, 1)
    return d


def transcript(session):
    paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session}.jsonl"))
    if not paths:
        return []
    return [json.loads(l) for l in open(paths[0]) if l.strip()]


def source_derived(entries, store, brief):
    """Tool results in the main context that carry source text."""
    store_abs = os.path.abspath(store)
    uses, by_id = [], {}
    for e in entries:
        msg = e.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if c.get("type") == "tool_use":
                by_id[c["id"]] = c
            if c.get("type") == "tool_result":
                use = by_id.get(c.get("tool_use_id"), {})
                inp = json.dumps(use.get("input", {}))
                body = c.get("content")
                if isinstance(body, list):
                    body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                body = body or ""
                is_src = (store_abs in inp or os.path.abspath(brief) in inp or "/spikes/out/" in inp)
                uses.append({"tool": use.get("name"), "input": inp[:160], "chars": len(body),
                             "tokens_est": len(body) // 4, "source_derived": is_src})
    return uses


def main():
    a = sys.argv[1:]
    store, brief, outdir = a[0], a[1], a[2]
    model = a[a.index("--model") + 1] if "--model" in a else None
    os.makedirs(outdir, exist_ok=True)
    import uuid
    session = str(uuid.uuid4())
    turns, replies = [], []
    if "--analyze" in a:  # re-measure a recorded run without new model calls
        old = json.load(open(os.path.join(outdir, "dryrun.json")))
        session, turns = old["session"], old["turns"]
        replies = json.load(open(os.path.join(outdir, "replies.json")))
    for k, t in enumerate([] if replies else turns_for(brief, store)):
        d = claude(t, session=session, model=model, first=(k == 0))
        u = d.get("usage", {})
        turns.append({"turn": k + 1, "context_in": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                      "output": u.get("output_tokens", 0), "cost_usd": d.get("total_cost_usd"),
                      "seconds": d["_seconds"], "num_turns": d.get("num_turns")})
        replies.append({"user": t, "tutor": d.get("result", "")})
    json.dump(replies, open(os.path.join(outdir, "replies.json"), "w"), indent=1, ensure_ascii=False)
    entries = transcript(session)
    uses = source_derived(entries, store, brief)
    src_tokens = sum(u["tokens_est"] for u in uses if u["source_derived"])
    # tutor citation check (prototype of source-cite-check.sh)
    brief_ids = {sid for sid, _ in sv.CITE.findall(open(brief).read())}
    lookup_ids = set()
    spans, _ = sv.load_spans(store)
    for e in entries:
        msg = e.get("message") or {}
        content = msg.get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_result":
                body = c.get("content")
                body = "".join(x.get("text", "") for x in body if isinstance(x, dict)) if isinstance(body, list) else (body or "")
                lookup_ids |= {m for m in re.findall(r"\b(p\d{4}\.\d+|ch\d{2}\.\d+|t\d{2}:\d{2}:\d{2})\b", body) if m in spans}
    allowed = brief_ids | lookup_ids
    cites, flagged, quote_bad = [], [], []
    for r in replies:
        for m in TUTOR_CITE.finditer(r["tutor"]):
            sid = m.group(1)
            (cites if sid in allowed else flagged).append(sid)
            # a quotation right before a cite must come from the cited span
            before = r["tutor"][max(0, m.start() - 300):m.start()]
            q = re.findall(r'["\u201c]([^"\u201d\]\n]{12,200})["\u201d]', before.split("\n\n")[-1])
            if q and sid in spans and sv.norm(q[-1]).strip(" .") not in spans[sid]["norm"]:
                # deterministic repair: the span that really holds the quote
                nq = sv.norm(q[-1]).strip(" .")
                home = next((k for k, v in spans.items() if nq in v["norm"]), None)
                quote_bad.append({"id": sid, "quote": q[-1][:80], "repoint_to": home})
    rendered = []
    for r in replies:
        rendered.append(TUTOR_CITE.sub(lambda m: f"({sv.locator_text(store, spans[m.group(1)])})" if m.group(1) in allowed and m.group(1) in spans else "(citation removed)", r["tutor"]))
    rep = {"session": session, "turns": turns, "source_tool_results": uses,
           "source_derived_tokens_main": src_tokens, "budget": BUDGET, "within_budget": src_tokens <= BUDGET,
           "tutor_cites": len(cites), "tutor_cites_flagged": flagged, "tutor_quote_mismatch": quote_bad,
           # total_cost_usd from a resumed session is cumulative
           "total_cost_usd": round(max(t["cost_usd"] or 0 for t in turns), 4)}
    json.dump(rep, open(os.path.join(outdir, "dryrun.json"), "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(outdir, "dryrun-log.md"), "w") as f:
        for r, rr in zip(replies, rendered):
            f.write(f"**Learner:** {r['user']}\n\n**Tutor:** {rr}\n\n---\n\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "source_tool_results"}, indent=1))
    for u in uses:
        print(f"  {u['tool']:<6} src={u['source_derived']!s:<5} {u['tokens_est']:>6} tok  {u['input'][:110]}")


if __name__ == "__main__":
    main()
