"""Outline ranges and teachable units, shared by every adapter.

Units are sized by tokens, not pages (core rule 3): 3-8k source tokens each,
cut at heading boundaries where possible. Units never cross a top-level
outline entry. A top-level entry (or a whole source) under 3k tokens becomes
one unit marked "undersized". Within an entry, the cut points are chosen by
dynamic programming over span boundaries: a cut at a deeper outline heading
costs more than one at a shallower heading, and a cut between two ordinary
paragraphs costs the most.

Token estimate: characters / 4, the same estimate the spikes measured.
"""
import re

MIN_TOKENS = 3000
MAX_TOKENS = 8000
TARGET = 5500
MATTER = re.compile(r"^(cover|copyright|(table of )?contents|index|colophon|about the authors?|praise|"
                    r"dedication|title page|half title|also by|back cover|front matter)\b", re.I)


def tok(chars):
    return chars // 4


def synthetic_outline(spans, n, labels):
    """No bookmarks: entries from heading-like spans (layout-detected, the
    source's own words), else page chunks of about TARGET tokens. Marked
    synthetic so the UI can say "generated outline"."""
    heads = [s for s in spans if s["kind"] == "heading"]
    ol = []
    if len(heads) >= 2:
        if spans and spans[0]["id"] != heads[0]["id"]:
            ol.append({"title": "Opening", "depth": 1, "pdf_index": spans[0].get("pdf_index"),
                       "start_span": spans[0]["id"]})
        for h in heads:
            ol.append({"title": h["text"][:120], "depth": 1, "pdf_index": h.get("pdf_index"), "start_span": h["id"]})
    else:
        acc, start = 0, None
        for s in spans:
            if start is None:
                start = s
            acc += len(s["text"])
            if tok(acc) >= TARGET:
                ol.append({"title": None, "depth": 1, "pdf_index": start.get("pdf_index"), "start_span": start["id"]})
                acc, start = 0, None
        if start is not None:
            ol.append({"title": None, "depth": 1, "pdf_index": start.get("pdf_index"), "start_span": start["id"]})
        if not ol:
            ol.append({"title": "Whole source", "depth": 1, "pdf_index": 1, "start_span": None})
    for k, o in enumerate(ol):
        o["id"] = f"s{k + 1}"
        o["synthetic"] = True
        o["label"] = labels[o["pdf_index"] - 1] if o.get("pdf_index") and o["pdf_index"] <= len(labels) else None
    return ol


def seg_range(spans, i, j, segments):
    """Segment range of the span range [i, j): it reaches back to the first
    segment when i is 0, and forward over segments without spans (blank
    pages attach to the preceding section) up to the segment before the
    next range starts, or to the last segment."""
    N = len(spans)
    first, last = spans[i]["segment"], spans[j - 1]["segment"]
    if not segments:
        return first, last
    order = {sid: k for k, (sid, _) in enumerate(segments)}
    a = 0 if i == 0 else order.get(first, 0)
    if j >= N:
        b = len(segments) - 1
    else:
        nxt = order.get(spans[j]["segment"], 0)
        b = max(order.get(last, 0), nxt - 1)
    return segments[a], segments[b]


def seg_fields(spans, i, j, segments):
    if not segments:
        return {}
    sa, sb = seg_range(spans, i, j, segments)
    return {"seg_start": sa[0], "seg_end": sb[0], "loc_start": sa[1], "loc_end": sb[1]}


def build_units(spans, ol, spans_by_page, segments=None):
    """Return (units, outline) with span/segment/locator ranges on both.
    segments: every segment in order as (id, locator), so that segments
    without spans are still covered."""
    N = len(spans)
    pos_of = {s["id"]: k for k, s in enumerate(spans)}
    # first global span position at or after each page
    page_first = {}
    nxt = N
    for p in sorted(spans_by_page, reverse=True):
        if spans_by_page[p]:
            nxt = pos_of[spans_by_page[p][0]["id"]]
        page_first[p] = nxt

    for k, o in enumerate(ol):
        if o.get("start_span") in pos_of:
            o["_pos"] = pos_of[o["start_span"]]
        elif o.get("pdf_index"):
            sp = spans_by_page.get(o["pdf_index"], [])
            kk = o.get("start_span_k", 0)
            o["_pos"] = pos_of[sp[kk]["id"]] if kk < len(sp) else page_first.get(o["pdf_index"], N)
        else:
            o["_pos"] = None
        o["_k"] = k
    placed = sorted((o for o in ol if o["_pos"] is not None), key=lambda o: (o["_pos"], o["_k"]))
    if placed and N and placed[0]["depth"] == 1:
        placed[0]["_pos"] = 0  # leading spans (a cover, a title block) belong to the first entry

    # outline ranges: until the next placed entry at the same or a shallower depth
    for a, o in enumerate(placed):
        end = N
        for b in placed[a + 1:]:
            if b["depth"] <= o["depth"] and b["_pos"] > o["_pos"]:
                end = b["_pos"]
                break
        o["_end"] = end
    for o in ol:
        if o["_pos"] is not None and o["_pos"] < o.get("_end", 0) and N:
            a, b = spans[o["_pos"]], spans[o["_end"] - 1]
            sa, sb = seg_range(spans, o["_pos"], o["_end"], segments)
            o.update(span_start=a["id"], span_end=b["id"], seg_start=sa[0] if segments else sa,
                     seg_end=sb[0] if segments else sb,
                     loc_start=sa[1] if segments else a["locator"], loc_end=sb[1] if segments else b["locator"],
                     tokens=tok(sum(len(s["text"]) for s in spans[o["_pos"]:o["_end"]])))
        else:
            o.update(span_start=None, span_end=None, seg_start=None, seg_end=None, loc_start=None, loc_end=None,
                     tokens=0)
        if o.get("title") is None:
            o["title"] = f"Pages {o['loc_start']}–{o['loc_end']}" if o["loc_start"] else "Pages"

    # cut costs at span boundaries
    cut = [3.0] * (N + 1)
    for k in range(1, N):
        c = 3.0
        if spans[k]["kind"] == "heading":
            c -= 1.0
        if spans[k]["segment"] != spans[k - 1]["segment"]:
            c -= 0.5
        cut[k] = c
    starts_at = {}
    for o in placed:
        starts_at.setdefault(o["_pos"], []).append(o)
        if o["depth"] >= 2:
            cut[o["_pos"]] = min(cut[o["_pos"]], (o["depth"] - 1) * 0.5)
    # groups: top-level entries; the first group also takes any leading spans
    # (synthetic entries only guide the cut points; they never fence units)
    tops = sorted({o["_pos"] for o in placed if o["depth"] == 1 and o["_pos"] < N and not o.get("synthetic")}
                  | {0}) if N else []
    for o in placed:
        if o.get("synthetic") and o["_pos"] not in tops:
            cut[o["_pos"]] = min(cut[o["_pos"]], 0.5)
    groups = [(a, b) for a, b in zip(tops, tops[1:] + [N]) if b > a]
    if N and sum(len(x["text"]) for x in spans) // 4 < MIN_TOKENS:
        groups = [(0, N)]  # a source under 3k tokens is one unit (rules 3 and 5)

    cum = [0]
    for s in spans:
        cum.append(cum[-1] + len(s["text"]))

    def gcost(t):
        if t > MAX_TOKENS:
            return 1000 + (t - MAX_TOKENS) / 10
        if t < MIN_TOKENS:
            return 1000 + (MIN_TOKENS - t) / 10
        return abs(t - TARGET) / 2500

    pieces = []
    total_tokens = tok(cum[-1])
    for a, b in groups:
        gt = tok(cum[b] - cum[a])
        if gt <= MAX_TOKENS:
            pieces.append((a, b, "section" if gt < MIN_TOKENS else None))
            continue
        best = {a: (0.0, None)}
        for j in range(a + 1, b + 1):
            bj = None
            i = j - 1
            while i >= a:
                t = tok(cum[j] - cum[i])
                if t > MAX_TOKENS * 1.5 and i < j - 1:
                    break
                if i in best:
                    c = best[i][0] + gcost(t) + (cut[i] if i > a else 0.0)
                    if bj is None or c < bj[0]:
                        bj = (c, i)
                i -= 1
            if bj is not None:
                best[j] = bj
        cuts, j = [], b
        while j > a:
            i = best[j][1]
            cuts.append((i, j))
            j = i
        for i, j in reversed(cuts):
            t = tok(cum[j] - cum[i])
            pieces.append((i, j, None if MIN_TOKENS <= t <= MAX_TOKENS else "unavoidable"))

    def path_at(pos):
        chain = []
        for o in placed:
            if o["_pos"] <= pos < o["_end"]:
                chain = [x for x in chain if x["depth"] < o["depth"]] + [o]
        return chain

    units = []
    for k, (i, j, small) in enumerate(pieces):
        chain = path_at(i)
        starts_here = i in starts_at
        title = " › ".join(o["title"] for o in chain) or "Untitled"
        if not starts_here and chain:
            title += " (continued)"
        top = chain[0] if chain else None
        u = {"id": f"u{k + 1:03d}", "title": title[:300], "outline_ids": [o["id"] for o in placed if i <= o["_pos"] < j],
             "outline_path": [o["id"] for o in chain],
             "span_start": spans[i]["id"], "span_end": spans[j - 1]["id"], "spans": j - i,
             "seg_start": spans[i]["segment"], "seg_end": spans[j - 1]["segment"],
             "loc_start": spans[i]["locator"], "loc_end": spans[j - 1]["locator"],
             **seg_fields(spans, i, j, segments),
             "tokens": tok(cum[j] - cum[i]),
             "role": "matter" if top is not None and MATTER.match(top["title"] or "") else "body"}
        if small:
            u["undersized"] = "whole_source" if total_tokens < MIN_TOKENS else small
        units.append(u)
    if not N and segments:
        # nothing readable (blank or unOCRed pages): the outline still
        # covers every segment
        for o in ol:
            if o["depth"] == 1:
                o.update(seg_start=segments[0][0], seg_end=segments[-1][0], loc_start=segments[0][1],
                         loc_end=segments[-1][1])
                break
    for o in ol:
        for key in ("_pos", "_end", "_k", "start_span_k"):
            o.pop(key, None)
    return units, ol
