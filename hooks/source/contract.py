"""Adapter contract check: does a store satisfy the shared schema?

usage: python3 contract.py <store-dir> [--verify <path to a source_verify.py>]

Every adapter's only contract is to produce the shared store files. This
checks them, whatever the source type:
- source.json is complete and well-formed;
- spans.jsonl: unique ids of the form <segment>.<n>, a locator on every span,
  norm/sha1 consistent with the text, no literal citation syntax ([[ or ]]),
  1-based page indices;
- every span appears, with its id, on one line of its segment file;
- outline entries have ranges, and their ranges cover every span;
- units cover every span exactly once, in order, within 3-8k tokens unless
  marked undersized for a short section or a short source;
- labels.json (paged sources) has one 1-based entry per segment;
- assets listed in assets/index.json exist inside assets/;
- an anchor taken from every span round-trips through the anchor matcher
  (srcnorm.find_tokens, or the citation gate given with --verify).

Output: one "ok <check>" or "FAIL <check> :: <detail>" line per check (the
format tests/spikes.sh tallies). Exit status 1 if anything failed.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srcnorm import find_tokens, norm  # noqa: E402
from units import MAX_TOKENS, MIN_TOKENS  # noqa: E402

ID_RE = re.compile(r"^([A-Za-z0-9:_-]+)\.(\d+)$")


class Checker:
    def __init__(self, name):
        self.name = name
        self.lines = []
        self.failed = False

    def check(self, what, problems):
        problems = [p for p in problems if p]
        if problems:
            self.failed = True
            more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
            self.lines.append(f"FAIL contract[{self.name}]: {what} :: " + "; ".join(map(str, problems[:5])) + more)
        else:
            self.lines.append(f"ok contract[{self.name}]: {what}")


def anchor_of(text):
    """An anchor a librarian could quote: the whole span if short, else a
    run of 6-25 leading words that ends on a plain word boundary."""
    words = text.split()
    if len(words) <= 12:
        return text
    for k in list(range(12, 5, -1)) + list(range(13, min(25, len(words)) + 1)):
        cand = " ".join(words[:k])
        if re.search(r"[A-Za-z]$", words[k - 1]) and find_tokens(cand, text) is not None:
            return cand
    return " ".join(words[:12])


def load_verifier(path):
    spec = importlib.util.spec_from_file_location("source_verify_ext", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    spec.loader.exec_module(mod)
    return mod


def run(store, verify_path=None):
    c = Checker(os.path.basename(os.path.normpath(store)))
    j = lambda *p: os.path.join(store, *p)

    def load(name):
        try:
            with open(j(name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            return e

    sj = load("source.json")
    if isinstance(sj, Exception):
        c.check("source.json readable", [str(sj)])
        return c
    c.check("source.json complete", [
        None if sj.get("status") == "complete" else "status is not complete",
        None if re.fullmatch(r"[0-9a-f]{64}", str(sj.get("sha256", ""))) else "bad sha256",
        *[None if sj.get(k) else f"missing {k}" for k in ("type", "adapter", "origin", "title", "ingest_version")]])
    c.check(".gitignore ignores everything", [None if os.path.isfile(j(".gitignore")) and
                                              "*" in open(j(".gitignore")).read().split() else "missing or not '*'"])

    try:
        spans = [json.loads(l) for l in open(j("spans.jsonl"), encoding="utf-8") if l.strip()]
    except (OSError, ValueError) as e:
        c.check("spans.jsonl readable", [str(e)])
        return c
    ids = [s.get("id") for s in spans]
    dup = sorted({i for i in ids if ids.count(i) > 1}) if len(set(ids)) != len(ids) else []
    c.check("span ids unique", [f"duplicate {d}" for d in dup[:5]])
    probs = []
    for s in spans:
        m = ID_RE.match(str(s.get("id")))
        if not m or m.group(1) != s.get("segment"):
            probs.append(f"{s.get('id')}: id is not <segment>.<n>")
    c.check("span ids are <segment>.<n>", probs)
    c.check("every span has a locator", [f"{s.get('id')}" for s in spans
                                         if not isinstance(s.get("locator"), str) or not s["locator"].strip()])
    c.check("no citation syntax in span text", [s["id"] for s in spans if "[[" in s.get("text", "")
                                                or "]]" in s.get("text", "")])
    probs = []
    for s in spans:
        if s.get("norm") != norm(s.get("text", "")):
            probs.append(f"{s['id']}: norm stale")
        elif s.get("sha1") != hashlib.sha1(s["norm"].encode()).hexdigest():
            probs.append(f"{s['id']}: sha1 mismatch")
    c.check("norm and sha1 match the text", probs)
    c.check("page indices are 1-based", [s["id"] for s in spans
                                         if "pdf_index" in s and (not isinstance(s["pdf_index"], int) or s["pdf_index"] < 1)])

    idx = load(os.path.join("segments", "index.json"))
    if isinstance(idx, Exception):
        c.check("segments/index.json readable", [str(idx)])
        idx = []
    seg_ids = [x.get("id") for x in idx]
    probs = []
    seg_lines = {}
    for sid in seg_ids:
        p = j("segments", f"{sid}.md")
        if not os.path.isfile(p) or os.path.islink(p):
            probs.append(f"missing segments/{sid}.md")
            continue
        seg_lines[sid] = set(open(p, encoding="utf-8").read().splitlines())
    for s in spans:
        if s.get("segment") not in seg_lines:
            probs.append(f"{s['id']}: segment {s.get('segment')} not indexed")
            continue
        line = f"[{s['id']}] " + s["text"].replace("\n", " ⏎ ")
        if line not in seg_lines[s["segment"]]:
            probs.append(f"{s['id']}: not on its own line in the segment file")
    c.check("segment files hold every span as one [id] line", probs)
    c.check("segment indices are 1-based", [x.get("id") for x in idx if "pdf_index" in x
                                            and (not isinstance(x["pdf_index"], int) or x["pdf_index"] < 1)])

    order = {sid: k for k, sid in enumerate(ids)}
    ol = load("outline.json")
    if isinstance(ol, Exception) or not isinstance(ol, list) or not ol:
        c.check("outline.json present and non-empty", ["missing or empty"])
    else:
        probs, covered = [], [False] * len(spans)
        for o in ol:
            if not o.get("title") or not isinstance(o.get("depth"), int) or o["depth"] < 1:
                probs.append(f"{o.get('id')}: bad title/depth")
            if o.get("pdf_index") is not None and (not isinstance(o["pdf_index"], int) or o["pdf_index"] < 1):
                probs.append(f"{o.get('id')}: pdf_index not 1-based")
            a, b = order.get(o.get("span_start")), order.get(o.get("span_end"))
            if a is not None and b is not None and a <= b:
                for k in range(a, b + 1):
                    covered[k] = True
        c.check("outline entries well-formed", probs)
        c.check("outline ranges cover every span", [ids[k] for k, v in enumerate(covered) if not v])

    units = load("units.json")
    if isinstance(units, Exception) or not isinstance(units, list):
        c.check("units.json readable", [str(units)])
    else:
        probs, pos = [], 0
        total = sum(len(s["text"]) for s in spans) // 4
        for u in units:
            a, b = order.get(u.get("span_start")), order.get(u.get("span_end"))
            if a is None or b is None:
                probs.append(f"{u.get('id')}: unknown span range")
                continue
            if a != pos:
                probs.append(f"{u['id']}: starts at span {a}, expected {pos} (gap or overlap)")
            pos = b + 1
        if spans and pos != len(spans):
            probs.append(f"units end at span {pos} of {len(spans)}")
        c.check("units cover every span once, in order", probs)
        probs = []
        for u in units:
            t = u.get("tokens", 0)
            if MIN_TOKENS <= t <= MAX_TOKENS:
                continue
            why = u.get("undersized")
            if t < MIN_TOKENS and why in ("section", "whole_source"):
                if why == "whole_source" and total >= MIN_TOKENS:
                    probs.append(f"{u['id']}: whole_source but the source has {total} tokens")
                continue
            probs.append(f"{u['id']}: {t} tokens" + (f" ({why})" if why else ""))
        c.check(f"unit sizes within {MIN_TOKENS}-{MAX_TOKENS} tokens", probs)

    if os.path.exists(j("labels.json")):
        lj = load("labels.json")
        if isinstance(lj, Exception):
            c.check("labels.json readable", [str(lj)])
        else:
            pages = lj.get("pages", [])
            probs = [None if [p.get("pdf_index") for p in pages] == list(range(1, len(pages) + 1))
                     else "pdf_index not 1..N",
                     None if len(pages) == len(idx) else f"{len(pages)} labels for {len(idx)} segments"]
            probs += [f"label_to_pdf[{k}]" for k, v in lj.get("label_to_pdf", {}).items()
                      if not all(isinstance(x, int) and 1 <= x <= len(pages) for x in v)]
            probs += [f"PDF {p['pdf_index']}: empty label" for p in pages if not str(p.get("label") or "").strip()]
            c.check("labels.json: one 1-based label per page", probs)

    ai = load(os.path.join("assets", "index.json"))
    if not isinstance(ai, Exception):
        probs = []
        adir = os.path.realpath(j("assets"))
        for a in ai:
            p = os.path.realpath(os.path.join(adir, str(a.get("file", ""))))
            if not p.startswith(adir + os.sep) or not os.path.isfile(p) or os.path.islink(os.path.join(adir, a["file"])):
                probs.append(f"{a.get('id')}: missing or outside assets/")
        c.check("assets exist inside assets/", probs)

    probs = []
    if verify_path:
        v = load_verifier(verify_path)
        by_id = {s["id"]: s for s in spans}
        for s in spans:
            st, why, _ = v.check_cite(s["id"], anchor_of(s["text"]), by_id, ids, None)
            if st != "ok":
                probs.append(f"{s['id']}: {why}")
        c.check("an anchor from every span passes the citation gate", probs)
    else:
        for s in spans:
            if find_tokens(anchor_of(s["text"]), s["text"]) is None:
                probs.append(s["id"])
        c.check("an anchor from every span round-trips", probs)
    return c


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    vp = a[a.index("--verify") + 1] if "--verify" in a else None
    c = run(a[0], vp)
    print("\n".join(c.lines))
    sys.exit(1 if c.failed else 0)


if __name__ == "__main__":
    main()
