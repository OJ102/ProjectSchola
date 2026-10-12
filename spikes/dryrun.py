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
TUTOR_CITE = re.compile(r'\[\[cite:([^\s\]"]+)(?:\s+"(.*?)")?\]\]', re.S)


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


QUOTE_BEFORE = re.compile(r'["\u201c]([^"\u201c\u201d]{8,600})["\u201d][\s.,;:)]*$')


def check_reply(text, spans, order, allowed, store):
    """Prototype of source-cite-check.sh for one tutor reply. Returns the
    rendered reply and a list of (kind, detail). A cite renders as its locator
    only if the id is allowed, any anchor inside it verifies, and a quotation
    right before it is found in that span. A quotation found in another span
    is re-pointed there; a quotation found nowhere removes the citation."""
    issues = []

    def sub(m):
        sid, anchor = m.group(1), m.group(2)
        if sid not in allowed or sid not in spans:
            issues.append(("flagged", sid))
            return "(citation removed)"
        if anchor is not None:
            st, why, _ = sv.check_cite(sid, anchor, spans, order, None)
            if st != "ok":
                issues.append(("quote", {"id": sid, "quote": anchor[:80], "repoint_to": None, "problem": f"forged anchor: {why}"}))
                return "(citation removed)"
        para = text[:m.start()].split("\n\n")[-1]
        q = QUOTE_BEFORE.search(para)
        if q and sv.find_tokens(q.group(1), spans[sid]["text"]) is None:
            home = next((k for k in ([i for i in order if i in allowed] + order)
                         if sv.find_tokens(q.group(1), spans[k]["text"]) is not None), None)
            if home:
                issues.append(("quote", {"id": sid, "quote": q.group(1)[:80], "repoint_to": home}))
                return f"({sv.locator_text(store, spans[home])})"
            issues.append(("quote", {"id": sid, "quote": q.group(1)[:80], "repoint_to": None,
                                     "problem": "quote not found in the source"}))
            return "(citation removed: quote not found in the source)"
        issues.append(("ok", sid))
        return f"({sv.locator_text(store, spans[sid])})"
    return sv.display_safe(TUTOR_CITE.sub(sub, text)), issues


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
    spans, order = sv.load_spans(store)
    for e in entries:
        msg = e.get("message") or {}
        content = msg.get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_result":
                body = c.get("content")
                body = "".join(x.get("text", "") for x in body if isinstance(x, dict)) if isinstance(body, list) else (body or "")
                lookup_ids |= {m for m in re.findall(r"\b(p\d{4}\.\d+|ch\d{2}\.\d+|t\d{2}:\d{2}:\d{2})\b", body) if m in spans}
    allowed = brief_ids | lookup_ids
    cites, flagged, quote_bad, rendered = [], [], [], []
    for r in replies:
        out, issues = check_reply(r["tutor"], spans, order, allowed, store)
        rendered.append(out)
        for k, sid in issues:
            {"ok": cites, "flagged": flagged}.get(k, quote_bad).append(sid)
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
