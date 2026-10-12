"""Prototype citation gate (spike 0c). Deterministic: no model calls.

usage: python3 spikes/source_verify.py <storedir> <brief.md> [--allow ids-file]... [--json out.json]
       python3 spikes/source_verify.py <storedir> <brief.md> --render   (print brief with locators)

A cite is [[cite:<span-id> "<anchor>"]]. Each cite must:
  - name a span id that exists in <storedir>/spans.jsonl,
  - name a span in the allowed set (the unit's ids file, plus prerequisites),
  - carry an anchor of >= 6 words or >= 30 chars and <= 25 words, not only
    stopwords; a span shorter than that must be quoted whole,
  - match the span text after normalization (exact substring), or, for noisy
    spans (video cues, OCR), a token similarity >= 0.9 against the span or the
    span joined with its neighbours (reported as match: fuzzy), with negations,
    numbers, units and comparatives matching exactly.
Every list item in a claim-bearing section must carry at least one valid cite.
Exit 0 when everything passes, 1 otherwise; the report lists each failure.
"""
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srcnorm import norm  # noqa: E402

CITE = re.compile(r'\[\[cite:([^\s\]"]+)\s+"(.*?)"\]\]')
BARE_CITE = re.compile(r"\[\[cite:[^\]]*\]\]")
CLAIM_SECTIONS = ("core claims", "definitions", "key figures", "equations", "examples", "quiz seeds")
NOISY_KINDS = {"cue", "ocr"}
MIN_WORDS, MIN_CHARS, MAX_WORDS = 6, 30, 25
FUZZY_MIN = 0.9
STOPWORDS = set("""a an and are as at be by for from has have he her his i in is it its of on or
our she so that the their them they this to was we were what when which who will with you your
there here then than these those been being do does did not no yes but if into out up down over""".split())
# In fuzzy mode these must match exactly: flipping one flips the claim
NEGATIONS = {"not", "no", "never", "n't", "without", "cannot", "can't", "won't", "don't", "doesn't",
             "isn't", "aren't", "wasn't", "weren't", "didn't", "none", "nor", "neither", "nothing"}
COMPARATIVES = {"more", "less", "fewer", "higher", "lower", "greater", "smaller", "larger", "before",
                "after", "above", "below", "increase", "increases", "decrease", "decreases", "most", "least"}
UNITS = {"%", "percent", "ms", "s", "sec", "seconds", "minutes", "hours", "kb", "mb", "gb", "tb", "k", "m", "b",
         "billion", "million", "thousand", "tokens", "km", "kg", "g", "cm", "mm", "x"}
# Locators the librarian must not write itself (code renders them)
SELF_LOCATOR = re.compile(r"\b(?:p\.|pp\.|page|pages)\s*\d+|\b\d{1,2}:\d{2}:\d{2}\b", re.I)


def load_spans(store):
    spans = [json.loads(l) for l in open(os.path.join(store, "spans.jsonl"), encoding="utf-8")]
    return {s["id"]: s for s in spans}, [s["id"] for s in spans]


def tokens(t):
    return re.findall(r"[a-z]+n't|\d+(?:[.,]\d+)*|%|[a-z]+", t)


def critical(tok):
    return tok in NEGATIONS or tok in COMPARATIVES or tok in UNITS or tok.endswith("n't") or tok[0].isdigit()


FILLERS = {"um", "uh", "er", "ah", "like", "so", "okay", "ok", "you", "know", "i", "mean"}


def edits_plausible(a, b):
    """Differences between anchor and caption must look like transcription
    noise: substituted words must be spelled alike (char ratio >= 0.6), and
    inserted or dropped words must be stopwords or fillers."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    soft = STOPWORDS | FILLERS
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        xa, xb = a[i1:i2], b[j1:j2]
        for x, y in zip(xa, xb):
            if difflib.SequenceMatcher(None, x, y).ratio() < 0.6:
                return False
        extra = xa[len(xb):] + xb[len(xa):]
        if any(t not in soft for t in extra):
            return False
    return True


def fuzzy(anchor_n, prev_n, cur_n, next_n):
    """Fuzzy match for caption/OCR spans. Windows are searched over
    prev+cur+next so a sentence split across cues still matches, but at least
    more than half of the matched window must lie inside the cited span. Critical tokens
    (negations, numbers, units, comparatives) must be identical and in order;
    the >= 0.9 ratio and the noise-plausibility rule apply to the rest.
    Returns (best ratio, reason or None)."""
    a = tokens(anchor_n)
    a_crit = [t for t in a if critical(t)]
    a_rest = [t for t in a if not critical(t)]
    P, C, N = tokens(prev_n), tokens(cur_n), tokens(next_n)
    w = P + C + N
    c0, c1 = len(P), len(P) + len(C)
    best, why = 0.0, "no window overlaps the cited span"
    for L in (len(a) - 1, len(a), len(a) + 1):
        if L <= 0:
            continue
        for i in range(0, max(1, len(w) - L + 1)):
            overlap = max(0, min(i + L, c1) - max(i, c0))
            if overlap * 2 <= L:  # more than half inside the cited span
                continue
            core = w[i:i + L]
            if [x for x in core if critical(x)] != a_crit:
                why = why if best else "critical token mismatch (negation/number/unit/comparative)"
                continue
            rest = [x for x in core if not critical(x)]
            r = difflib.SequenceMatcher(None, a_rest, rest, autojunk=False).ratio()
            if r >= FUZZY_MIN and not edits_plausible(a_rest, rest):
                why = why if best else "edits do not look like transcription noise"
                continue
            if r > best:
                best, why = r, None
    return best, why


def check_cite(sid, anchor, spans, order, allowed):
    if "[[" in anchor or "]]" in anchor:
        return "fail", "anchor contains citation syntax"
    s = spans.get(sid)
    if s is None:
        return "fail", "unknown span id"
    if allowed is not None and sid not in allowed:
        return "fail", "span outside unit"
    an = norm(anchor)
    words = an.split()
    if len(words) > MAX_WORDS:
        return "fail", f"anchor too long ({len(words)} words)"
    if all(w.strip(".,;:!?\"'()") in STOPWORDS for w in words):
        return "fail", "anchor is only stopwords"
    short_span = len(s["norm"].split()) < MIN_WORDS and len(s["norm"]) < MIN_CHARS
    if short_span:
        if an != s["norm"]:
            return "fail", "short span: anchor must equal the whole span"
    elif len(words) < MIN_WORDS and len(an) < MIN_CHARS:
        return "fail", f"anchor too short ({len(words)} words, {len(an)} chars)"
    # only the cited span counts, even if the same words occur elsewhere
    if an and an in s["norm"]:
        return "ok", "exact"
    if s.get("kind") in NOISY_KINDS:
        i = order.index(sid)
        prev_n = spans[order[i - 1]]["norm"] if i > 0 else ""
        next_n = spans[order[i + 1]]["norm"] if i + 1 < len(order) else ""
        r, why = fuzzy(an, prev_n, s["norm"], next_n)
        if r >= FUZZY_MIN:
            return "ok", f"fuzzy {r:.2f}"
        return "fail", f"anchor not in span ({why or f'fuzzy {r:.2f}'})"
    return "fail", "anchor not in span"


def verify(store, brief_path, allow_files):
    spans, order = load_spans(store)
    allowed = None
    if allow_files:
        allowed = set()
        for f in allow_files:
            allowed |= {l.strip() for l in open(f) if l.strip()}
    text = open(brief_path, encoding="utf-8").read()
    cites, failures, lines_report = [], [], []
    section = ""
    for ln_no, line in enumerate(text.split("\n"), 1):
        h = re.match(r"^#{1,6}\s+(?:\d+\.\s*)?(.*)", line)
        if h:
            section = h.group(1).strip().lower()
            continue
        found = CITE.findall(line)
        malformed = len(BARE_CITE.findall(line)) - len(found)
        if malformed > 0:
            failures.append({"line": ln_no, "reason": "malformed cite"})
        line_ok = False
        for sid, anchor in found:
            st, why = check_cite(sid, anchor, spans, order, allowed)
            c = {"line": ln_no, "id": sid, "anchor": anchor, "status": st, "detail": why,
                 "match": ("fuzzy" if why.startswith("fuzzy") else "exact") if st == "ok" else None,
                 "locator": spans[sid]["locator"] if sid in spans else None}
            cites.append(c)
            if st == "ok":
                line_ok = True
            else:
                failures.append({"line": ln_no, "id": sid, "reason": why})
        is_item = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
        if is_item and any(section.startswith(k) for k in CLAIM_SECTIONS):
            if not found:
                failures.append({"line": ln_no, "reason": "claim without cite"})
            elif not line_ok:
                pass  # already reported per cite
        stripped = CITE.sub("", line)
        if section != "where" and SELF_LOCATOR.search(stripped):
            failures.append({"line": ln_no, "reason": "self-written locator", "text": SELF_LOCATOR.search(stripped).group(0)})
    if not cites:
        failures.append({"line": 0, "reason": "brief has no cites"})
    return {"brief": brief_path, "cites": len(cites), "ok": sum(c["status"] == "ok" for c in cites),
            "fuzzy": sum(c["match"] == "fuzzy" for c in cites), "failures": failures, "detail": cites,
            "pass": not failures}


def section_of(outline, pdf_index):
    """Title of the deepest outline item starting at or before a PDF page."""
    best = None
    for o in outline:
        if o.get("pdf_index") is not None and o["pdf_index"] + 1 <= pdf_index:
            if best is None or o["pdf_index"] >= best["pdf_index"]:
                best = o
    return best["title"] if best else None


def locator_text(store, span, _cache={}):
    """Human locator for a span. PDF labels that occur on several pages (a
    roman-numbered index after roman front matter) carry their section."""
    if store not in _cache:
        lj = os.path.join(store, "labels.json")
        oj = os.path.join(store, "outline.json")
        _cache[store] = (json.load(open(lj)) if os.path.exists(lj) else {},
                         json.load(open(oj)) if os.path.exists(oj) else [])
    labels, outline = _cache[store]
    loc = span["locator"]
    if not re.fullmatch(r"p\d{4}", span["segment"]):
        return loc
    text = loc if loc.startswith("PDF p.") else f"p. {loc}"
    if len(labels.get("label_to_pdf", {}).get(loc, [])) > 1:
        idx = int(span["segment"][1:])
        sec = section_of(outline, idx)
        text += f" ({sec})" if sec else f" (PDF p. {idx})"
    return text


def render(store, brief_path):
    spans, _ = load_spans(store)

    def sub(m):
        s = spans.get(m.group(1))
        if not s:
            return "(citation removed)"
        return f"({locator_text(store, s)}: “{m.group(2)}”)"
    return CITE.sub(sub, open(brief_path, encoding="utf-8").read())


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    store, brief = a[0], a[1]
    if "--render" in a:
        print(render(store, brief))
        return
    allow = [a[i + 1] for i, x in enumerate(a) if x == "--allow"]
    rep = verify(store, brief, allow)
    if "--json" in a:
        json.dump(rep, open(a[a.index("--json") + 1], "w"), indent=1, ensure_ascii=False)
    print(f"{'PASS' if rep['pass'] else 'FAIL'} cites={rep['cites']} ok={rep['ok']} fuzzy={rep['fuzzy']} failures={len(rep['failures'])}")
    for f in rep["failures"]:
        print("  line", f.get("line"), f.get("id", ""), "-", f["reason"], f.get("text", ""))
    sys.exit(0 if rep["pass"] else 1)


if __name__ == "__main__":
    main()
