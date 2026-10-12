"""Spike 0c helper: cut one teachable unit out of a span store.

usage: python3 spikes/unit.py <storedir> <selector> <out.md>
  selector: an outline title (PDF: bookmark title, exact or case-insensitive
            prefix), "segments:ch13" / "segments:ch13-ch14", or "all".

Writes <out.md> (the librarian's input: one line per span, "[id] text") and
<out.md>.ids (allowed span ids, one per line). Prints the token estimate.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srcnorm import norm  # noqa: E402


def load(store):
    spans = [json.loads(l) for l in open(os.path.join(store, "spans.jsonl"))]
    ol_path = os.path.join(store, "outline.json")
    outline = json.load(open(ol_path)) if os.path.exists(ol_path) else []
    return spans, outline


def pdf_unit(spans, outline, title):
    idx = next((i for i, o in enumerate(outline) if o["title"] == title), None)
    if idx is None:
        idx = next((i for i, o in enumerate(outline) if o["title"].lower().startswith(title.lower())), None)
    if idx is None:
        sys.exit(f"no outline item {title!r}")
    o = outline[idx]
    nxt = next((x for x in outline[idx + 1:] if x["depth"] <= o["depth"]), None)
    start = o["pdf_index"]  # 1-based
    end = nxt["pdf_index"] if nxt else 10 ** 9
    seg = lambda s: int(s["segment"][1:])
    sel = [s for s in spans if start <= seg(s) <= end]
    # trim to the heading spans on the boundary pages
    t0 = next((k for k, s in enumerate(sel) if seg(s) == start and s["norm"] == norm(o["title"])), 0)
    sel = sel[t0:]
    if nxt:
        t1 = next((k for k, s in enumerate(sel) if seg(s) == end and s["norm"] == norm(nxt["title"])), None)
        if t1 is None:
            sel = [s for s in sel if seg(s) < end]
        else:
            sel = sel[:t1]
    path = []
    for x in outline[:idx + 1]:
        path = path[:x["depth"] - 1] + [x["title"]]
    return sel, " › ".join(path), f"pp. {sel[0]['locator']}–{sel[-1]['locator']}"


def main():
    store, selector, out = sys.argv[1:4]
    spans, outline = load(store)
    if selector == "all":
        sel, where = spans, "whole source"
    elif selector.startswith("segments:"):
        a, _, b = selector[9:].partition("-")
        b = b or a
        segs = sorted({s["segment"] for s in spans})
        keep = set(segs[segs.index(a):segs.index(b) + 1])
        sel = [s for s in spans if s["segment"] in keep]
        where = sel[0]["locator"].split(" ¶")[0]
    else:
        sel, where, rng = pdf_unit(spans, outline, selector)
        where += f" ({rng})"
    if sel and sel[0].get("start") is not None:
        where = f"{sel[0]['locator']}–{sel[-1]['locator']}"
    body = "\n".join(f"[{s['id']}] {s['text']}" for s in sel)
    with open(out, "w") as f:
        f.write(f"# Unit source: {where}\n\n{body}\n")
    with open(out + ".ids", "w") as f:
        f.write("\n".join(s["id"] for s in sel) + "\n")
    chars = len(body)
    print(json.dumps({"where": where, "spans": len(sel), "chars": chars, "tokens_est": chars // 4,
                      "first": sel[0]["id"], "last": sel[-1]["id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
