"""Page-label fit for PDFs (spike 0b, revised per the Step 0 design decisions).

Input: per-page visual lines (y, text) from the text layer, or from OCR of the
header/footer strips for pages with no text layer. Output: one string label per
PDF page plus its source, a label -> [pdf indices] multimap and flags.

Rules (design decisions 3-5):
- Candidates come only from the outermost 3 lines at the top and bottom, and
  only in anchored shapes: the line is the label alone, "N | head" / "head | N",
  "Page N", or "N head" / "head N" where head is a running head that repeats on
  >=30% of pages in a +/-15 page window. The outermost matching line wins.
- Label schemes are strings: arabic, roman (valid numeral, <= 400, and it must be
  supported by a neighbour in the same run), chapter-prefixed ("3-14", "A-1").
- Bare-number candidates are ignored on TOC pages and on number-dense pages.
- Runs of (scheme, offset) are built from the candidates. A single point that
  disagrees with two agreeing neighbours is corrected. Gaps between runs take
  the next run extrapolated backward when that gives >= 1 (footer-less chapter
  openers start the new numbering), else the previous run forward.
- Fewer than 20 pages: labels are kept only when the footers are unambiguous
  (no corrections and >60% coverage); otherwise PDF index labels.
- /PageLabels is cross-checked against the fit: >2% disagreement on footered
  pages means the footers win and label_conflict is recorded.
"""
import re
from collections import Counter

ROMAN_VALS = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
              (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
LAB = r"(\d{1,5}|[ivxlcdm]{1,8}|[IVXLCDM]{1,8}|[A-Za-z]{1,2}[-‐–.]\d{1,4}|\d{1,3}[-‐–.]\d{1,4})"
# separators between page number and running head: | • · and an em dash
# (Computer Modern renders "|" as an em dash in OT1 text)
SEP = r"[|\u2022\u00b7\u2014]"
# (shape, regex, label group, running-head group or None)
SHAPES = [
    ("bare", re.compile(rf"^\s*[-–—]?\s*{LAB}\s*[-–—]?\s*$"), 1, None),
    ("pipe", re.compile(rf"^\s*{LAB}\s*{SEP}"), 1, None),
    ("pipe", re.compile(rf"{SEP}\s*{LAB}\s*$"), 1, None),
    ("page", re.compile(rf"^\s*page\s+{LAB}(?:\s+of\s+\d+)?\s*$", re.I), 1, None),
    ("head", re.compile(rf"^\s*{LAB}\s+(\S.*)$"), 1, 2),
    ("head", re.compile(rf"^(.*\S)\s+{LAB}\s*$"), 2, 1),
]
MAX_ROMAN = 400
WINDOW = 15


def roman(n):
    s = ""
    for v, r in ROMAN_VALS:
        while n >= v:
            s, n = s + r, n - v
    return s


def from_roman(s):
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = s.lower()
    if not s or any(c not in vals for c in s):
        return None
    tot = sum(-vals[c] if i + 1 < len(s) and vals[s[i + 1]] > vals[c] else vals[c] for i, c in enumerate(s))
    return tot if 0 < tot <= MAX_ROMAN and roman(tot) == s else None


def parse_label(s):
    """'14' -> ('#', 14); 'xii' -> ('r', 12); '3-14' -> ('3-', 14). None if not a label."""
    s = s.strip().replace("‐", "-").replace("–", "-")
    if s.isdigit():
        v = int(s)
        return ("#", v) if 0 < v < 100000 else None
    m = re.fullmatch(r"([A-Za-z]{1,2}|\d{1,3})([-.])(\d{1,4})", s)
    if m:
        return (m.group(1).upper() + m.group(2), int(m.group(3)))
    v = from_roman(s)
    if v:
        return ("R" if s.isupper() else "r", v)
    return None


def fmt_label(key, v):
    if key == "#":
        return str(v)
    if key == "r":
        return roman(v)
    if key == "R":
        return roman(v).upper()
    return f"{key}{v}"


def head_key(t):
    return re.sub(r"\s+", " ", re.sub(r"[\d|\u2022\u00b7\u2014]", "", t)).strip().lower()


def candidates(lines_per_page, skip_bare=frozenset()):
    """Per page: list of (key, value, label, where). Outermost matching line wins
    per side; the bottom side is preferred, then the top."""
    n = len(lines_per_page)
    # running heads: text of edge lines with numbers removed
    edge_heads = []
    for lines in lines_per_page:
        edge = lines[:3] + lines[-3:]
        edge_heads.append({head_key(t) for _, t in edge if len(head_key(t)) >= 3})
    out = []
    for i, lines in enumerate(lines_per_page):
        lo, hi = max(0, i - WINDOW), min(n, i + WINDOW + 1)
        cnt = Counter(h for j in range(lo, hi) for h in edge_heads[j])
        need = max(2, 0.3 * (hi - lo))
        found = []
        for side, seq in (("footer", list(reversed(lines[-3:]))), ("header", lines[:3])):
            for _, t in seq:
                # every anchored label on the line is a candidate ("50 | head
                # head | 51" carries two); the fit picks the consistent one
                hits = []
                for shape, rx, lg, hg in SHAPES:
                    m = rx.search(t)
                    if not m:
                        continue
                    lab = m.group(lg)
                    if hg and cnt[head_key(m.group(hg))] < need:
                        continue
                    if shape == "bare" and i in skip_bare:
                        continue
                    pl = parse_label(lab)
                    if pl and (pl[0], pl[1], lab, side) not in hits:
                        hits.append((pl[0], pl[1], lab, side))
                if hits:
                    found.extend(hits)
                    break  # outermost matching line on this side wins
        out.append(found)
    return out


def fit(cands, n):
    """Return (labels, sources, flags). labels[i] is a string or None."""
    # 1. choose one candidate per page: the one whose (key, offset) is most
    #    common among other pages' candidates within the window
    pts = {}
    allc = [(i, k, i - v) for i, cs in enumerate(cands) for k, v, _, _ in cs]
    for i, cs in enumerate(cands):
        if not cs:
            continue
        best, bscore = None, -1
        for k, v, lab, side in cs:
            score = sum(1 for j, k2, o2 in allc if j != i and abs(j - i) <= WINDOW and k2 == k and o2 == i - v)
            if score > bscore:
                best, bscore = (k, i - v, lab), score
        pts[i] = best
    flags = []
    # 2. correct single outliers sandwiched between agreeing neighbours
    idx = sorted(pts)
    for a, b, c in zip(idx, idx[1:], idx[2:]):
        if pts[a][:2] == pts[c][:2] != pts[b][:2]:
            k, off = pts[a][:2]
            if b - off >= 1:
                flags.append({"pdf_index": b + 1, "footer": pts[b][2], "corrected_to": fmt_label(k, b - off)})
                pts[b] = (k, off, None)
    # 3. runs of equal (key, offset); unsupported single roman points dropped
    runs = []
    for i in sorted(pts):
        k, off, _ = pts[i]
        if runs and runs[-1]["key"] == k and runs[-1]["off"] == off:
            runs[-1]["pages"].append(i)
        else:
            runs.append({"key": k, "off": off, "pages": [i]})
    # a lone arabic/roman point with no agreeing neighbour is noise (a price
    # code on the back cover, a stray "c"); a lone "3-14" can be a short chapter
    dropped = [r for r in runs if r["key"] in ("#", "r", "R") and len(r["pages"]) < 2 and len(pts) > 2]
    runs = [r for r in runs if r not in dropped]
    for r in dropped:
        flags.append({"pdf_index": r["pages"][0] + 1, "footer": pts[r["pages"][0]][2], "ignored": "unsupported single point"})
    labels, sources = [None] * n, [None] * n
    for r in runs:
        for i in range(r["pages"][0], r["pages"][-1] + 1):
            v = i - r["off"]
            if labels[i] is None and v >= 1:
                labels[i] = fmt_label(r["key"], v)
                if i in pts and pts[i][2] is None:
                    sources[i] = "corrected"
                elif i in pts and pts[i][:2] == (r["key"], r["off"]):
                    sources[i] = "footer"
                else:
                    sources[i] = "interpolated"
    # 4. gaps between runs, and the ends
    for ri, r in enumerate(runs):
        nxt = runs[ri + 1] if ri + 1 < len(runs) else None
        gap_end = nxt["pages"][0] if nxt else min(n, r["pages"][-1] + 4)
        for i in range(r["pages"][-1] + 1, gap_end):
            if labels[i] is not None:
                continue
            if nxt and i - nxt["off"] >= 1:
                labels[i] = fmt_label(nxt["key"], i - nxt["off"])
            elif i - r["off"] >= 1:
                labels[i] = fmt_label(r["key"], i - r["off"])
            else:
                continue
            sources[i] = "interpolated"
    if runs:
        r = runs[0]
        for i in range(r["pages"][0] - 1, -1, -1):
            if i - r["off"] < 1:
                break
            labels[i], sources[i] = fmt_label(r["key"], i - r["off"]), "interpolated"
    return labels, sources, flags


def resolve(lines_per_page, dict_labels=None, skip_bare=frozenset()):
    """Full precedence. Returns dict with labels, sources, multimap, report."""
    n = len(lines_per_page)
    cands = candidates(lines_per_page, skip_bare)
    raw = [c[0][2].lower() if c else None for c in cands]
    labels, sources, flags = fit(cands, n)
    covered = sum(1 for l in labels if l is not None)
    footered = sum(1 for s in sources if s in ("footer", "corrected"))
    rep = {"footer_candidates": sum(1 for r in raw if r), "corrections": flags,
           "fit_coverage": round(covered / n, 3) if n else 0}
    use_fit = covered > 0.6 * n
    if n < 20 and (flags or not use_fit):
        use_fit = False
        rep["short_source"] = "ambiguous footers; PDF index labels used"
    final, fsrc = labels[:], sources[:]
    if not use_fit:
        final, fsrc = [None] * n, [None] * n
    # cross-check /PageLabels against footered pages
    if dict_labels is not None:
        both = [i for i in range(n) if sources[i] == "footer" and dict_labels[i]]
        dis = [i for i in both if dict_labels[i].lower() != labels[i].lower()]
        rep["pagelabels_checked"] = len(both)
        rep["pagelabels_disagree"] = len(dis)
        if both and len(dis) > 0.02 * len(both):
            rep["label_conflict"] = {"pages": [i + 1 for i in dis[:20]], "count": len(dis),
                                     "decision": "footers win"}
        else:
            final = [dict_labels[i] or final[i] for i in range(n)]
            fsrc = ["pagelabels" if dict_labels[i] else fsrc[i] for i in range(n)]
    for i in range(n):
        if final[i] is None:
            final[i], fsrc[i] = f"PDF p. {i + 1}", "fallback"
    mm = {}
    for i, l in enumerate(final):
        mm.setdefault(l, []).append(i + 1)
    rep["duplicate_labels"] = {l: v for l, v in mm.items() if len(v) > 1}
    rep["sources"] = dict(Counter(fsrc))
    return {"labels": final, "sources": fsrc, "raw": raw, "fit": labels, "fit_sources": sources,
            "multimap": mm, "report": rep}
