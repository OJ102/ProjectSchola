"""Spike 0b: deterministic PDF locators + span ids.

usage: python3 spikes/locators.py <pdf> <outdir> [--seed N]

Writes into <outdir>: labels.json, outline.json, spans.jsonl, segments/pNNNN.md,
report.json. Prints a summary. Everything here is computed by code: page labels
(/PageLabels dict -> footer regex -> monotonic fit -> interpolation -> fallback),
outline titles (PDF bookmarks), span ids (<segment>.<n>).
"""
import hashlib
import html
import json
import os
import random
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdfmini import PDF, Ref, text as pdftext, roman  # noqa: E402
from srcnorm import join_lines, norm  # noqa: E402

BAND = 0.08  # top/bottom fraction of the page treated as header/footer band
ROMAN_RE = r"[ivxlcdm]+"


def from_roman(s):
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = s.lower()
    tot = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        tot += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return tot if roman(tot) == s else None


# ---------------------------------------------------------------- text layer
def bbox_pages(pdf_path):
    """Return per page: {'w','h','blocks':[{'y0','y1','x0','lines':[str], 'lh':float}]}."""
    r = subprocess.run(["pdftotext", "-bbox-layout", pdf_path, "-"], capture_output=True)
    if r.returncode != 0:
        # poppler 26.04 aborts in -bbox modes when the Info dict holds an empty
        # string (hyperref's default). pdftohtml -xml survives; rebuild blocks.
        return xml_pages(pdf_path)
    h = r.stdout.decode("utf-8", "replace")
    pages = []
    for pm in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', h, re.S):
        blocks = []
        for bm in re.finditer(r'<block xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</block>', pm.group(3), re.S):
            lines, lhs = [], []
            for lm in re.finditer(r'<line xMin="[\d.]+" yMin="([\d.]+)" xMax="[\d.]+" yMax="([\d.]+)">(.*?)</line>', bm.group(5), re.S):
                words = [html.unescape(w) for w in re.findall(r">([^<]*)</word>", lm.group(3))]
                lines.append(" ".join(words))
                lhs.append(float(lm.group(2)) - float(lm.group(1)))
            if lines:
                blocks.append({"x0": float(bm.group(1)), "y0": float(bm.group(2)), "y1": float(bm.group(4)),
                               "lines": lines, "lh": sorted(lhs)[len(lhs) // 2]})
        pages.append({"w": float(pm.group(1)), "h": float(pm.group(2)), "blocks": blocks})
    return pages


def xml_pages(pdf_path):
    """Fallback text layer via pdftohtml -xml: group lines into blocks by
    vertical gap and font. Coordinates are scaled back to PDF points."""
    r = subprocess.run(["pdftohtml", "-xml", "-i", "-q", "-stdout", pdf_path], capture_output=True, check=True)
    x = r.stdout.decode("utf-8", "replace")
    pages = []
    for pm in re.finditer(r'<page number="\d+"[^>]*height="(\d+)" width="(\d+)">(.*?)</page>', x, re.S):
        ph, pw = float(pm.group(1)), float(pm.group(2))
        scale = 1.5  # pdftohtml default zoom
        lines = []
        for tm in re.finditer(r'<text top="(-?\d+)" left="(-?\d+)" width="\d+" height="(\d+)" font="(\d+)">(.*?)</text>', pm.group(3), re.S):
            t = html.unescape(re.sub(r"<[^>]+>", "", tm.group(5)))
            lines.append((int(tm.group(1)), int(tm.group(2)), int(tm.group(3)), tm.group(4), t))
        lines.sort(key=lambda l: (l[0], l[1]))
        # merge fragments on the same baseline into one line
        groups = []
        for l in lines:
            if groups and abs(groups[-1][0][0] - l[0]) <= 3:
                groups[-1].append(l)
            else:
                groups.append([l])
        merged = []
        for g in groups:
            g.sort(key=lambda l: l[1])
            merged.append((min(l[0] for l in g), g[0][1], max(l[2] for l in g), g[0][3],
                           " ".join(l[4] for l in g)))
        blocks, cur = [], None
        for top, left, hgt, font, t in merged:
            if cur and top - cur["_bottom"] <= hgt * 0.6 and font == cur["_font"]:
                cur["lines"].append(t)
                cur["_bottom"] = top + hgt
            else:
                cur = {"x0": left / scale, "y0": top / scale, "lines": [t], "lh": hgt / scale,
                       "_bottom": top + hgt, "_font": font}
                blocks.append(cur)
        for b in blocks:
            b["y1"] = b.pop("_bottom") / scale
            b.pop("_font")
        pages.append({"w": pw / scale, "h": ph / scale, "blocks": blocks, "via": "pdftohtml"})
    return pages


# ---------------------------------------------------------------- page labels
FOOTER_RES = [
    re.compile(rf"^\s*(\d+|{ROMAN_RE})\s*\|", re.I),           # "78 | Chapter 2"
    re.compile(rf"\|\s*(\d+|{ROMAN_RE})\s*$", re.I),           # "Post-Training | 79"
    re.compile(rf"^\s*page\s+(\d+|{ROMAN_RE})\b", re.I),       # "Page 12"
    re.compile(rf"^\s*[-–]?\s*(\d+|{ROMAN_RE})\s*[-–]?\s*$", re.I),  # bare "12" / "- 12 -"
]


def band_text(page, top):
    h = page["h"]
    bs = [b for b in page["blocks"] if (b["y1"] <= h * BAND if top else b["y0"] >= h * (1 - BAND))]
    bs.sort(key=lambda b: (round(b["y0"]), b["x0"]))
    return " ".join(" ".join(b["lines"]) for b in bs)


def footer_label(page):
    for top in (False, True):
        t = band_text(page, top).strip()
        if not t:
            continue
        for rx in FOOTER_RES:
            m = rx.search(t)
            if m:
                return m.group(1).lower(), "footer" if not top else "header"
    return None, None


def fit_labels(raw, n):
    """Monotonic fit. raw[i] = label str or None. Returns (labels, sources).

    Arabic labels vote for offset = i - value; roman labels vote for a roman
    offset. Each page takes the offset that is the majority in a +/-15 page
    window of its own numeral system; outliers are corrected to the fit.
    """
    arab = {i: int(v) for i, v in enumerate(raw) if v and v.isdigit()}
    rom = {i: from_roman(v) for i, v in enumerate(raw) if v and not v.isdigit() and from_roman(v)}
    labels, sources = [None] * n, [None] * n

    def window_offset(points, i, w=15):
        votes = {}
        for j in range(max(0, i - w), min(n, i + w + 1)):
            if j in points:
                votes[j - points[j]] = votes.get(j - points[j], 0) + 1
        if not votes:
            return None, 0
        off, c = max(votes.items(), key=lambda kv: kv[1])
        return off, c

    last_arab = max(arab) if arab else -1
    for i in range(n):
        # Arabic wins wherever its extrapolation is >= 1 (body numbering starts
        # at 1 on the first main page, often a footer-less chapter opener);
        # roman covers the front matter before that.
        system, v = None, 0
        for cand in (arab, rom):
            off, votes = window_offset(cand, i)
            if off is not None and votes >= 2 and i - off >= 1:
                system, v = cand, i - off
                break
        if system is None:
            continue
        lab = str(v) if system is arab else roman(v)
        if raw[i] == lab:
            labels[i], sources[i] = lab, "footer"
        elif raw[i]:
            labels[i], sources[i] = lab, "corrected"
        elif i <= last_arab + 3 or system is rom:
            labels[i], sources[i] = lab, "interpolated"
    for i in range(n):
        if labels[i] is None:
            labels[i], sources[i] = f"PDF p. {i + 1}", "fallback"
    return labels, sources


# ---------------------------------------------------------------- outline
def outline(pdf):
    cat = pdf.catalog()
    page_idx = {num: i for i, num in enumerate(pdf.pages())}
    names = {}
    nroot = pdf.resolve(cat.get("Names"))
    if isinstance(nroot, dict) and "Dests" in nroot:
        def walk(nd, d=0):
            nd = pdf.resolve(nd)
            if not isinstance(nd, dict) or d > 32:
                return
            arr = pdf.resolve(nd.get("Names")) or []
            for k in range(0, len(arr) - 1, 2):
                names[arr[k]] = pdf.resolve(arr[k + 1])
            for kid in pdf.resolve(nd.get("Kids")) or []:
                walk(kid, d + 1)
        walk(nroot["Dests"])
    old_dests = pdf.resolve(cat.get("Dests")) or {}

    def dest_page(item):
        d = item.get("Dest")
        a = pdf.resolve(item.get("A"))
        if d is None and isinstance(a, dict):
            d = a.get("D")
        d = pdf.resolve(d)
        if isinstance(d, (bytes, str)) and not isinstance(d, list):
            d = names.get(d if isinstance(d, bytes) else d.encode()) or old_dests.get(str(d))
            d = pdf.resolve(d)
        if isinstance(d, dict):
            d = pdf.resolve(d.get("D"))
        if isinstance(d, list) and d and isinstance(d[0], Ref):
            return page_idx.get(d[0].num)
        if isinstance(d, list) and d and isinstance(d[0], int):
            return d[0]
        return None

    out, seen = [], set()

    def walk_items(ref, depth):
        while ref is not None and isinstance(ref, Ref) and ref.num not in seen:
            seen.add(ref.num)
            it = pdf.resolve(ref)
            if not isinstance(it, dict):
                break
            out.append({"title": pdftext(pdf.resolve(it.get("Title", b""))).strip(), "depth": depth,
                        "pdf_index": dest_page(it)})
            if "First" in it:
                walk_items(it["First"], depth + 1)
            ref = it.get("Next")
    root = pdf.resolve(cat.get("Outlines"))
    if isinstance(root, dict) and "First" in root:
        walk_items(root["First"], 1)
    return out


# ---------------------------------------------------------------- spans
def spans_for_page(i, page, body_lh):
    h = page["h"]
    out = []
    for b in sorted(page["blocks"], key=lambda b: (b["y0"], b["x0"])):
        if b["y0"] >= h * (1 - BAND) or b["y1"] <= h * BAND:
            continue  # header/footer band
        t = join_lines(b["lines"])
        if not t.strip():
            continue
        kind = "para"
        if b["lh"] < body_lh * 0.9 and re.match(r"^\d+\s", t):
            kind = "footnote"
        elif re.match(r"^(Figure|Table|Example)\s+\d+[-.]\d+\.", t):
            kind = "caption"
        elif re.match(r"^(\d+\.|[•●▪\-*])\s", t):
            kind = "list"
        out.append({"text": t, "kind": kind})
    return [dict(id=f"p{i + 1:04d}.{k + 1}", **s) for k, s in enumerate(out)]


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    pdf_path, outdir = args[0], args[1]
    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 7
    t0 = time.time()
    os.makedirs(os.path.join(outdir, "segments"), exist_ok=True)
    sha = hashlib.sha256(open(pdf_path, "rb").read()).hexdigest()
    pdf = PDF(pdf_path)
    n = len(pdf.pages())
    t_parse = time.time() - t0

    dict_labels = pdf.page_labels()
    pages = bbox_pages(pdf_path)
    t_text = time.time() - t0 - t_parse
    if len(pages) != n:
        print(f"WARN: text layer pages {len(pages)} != page tree {n}")
        n = min(n, len(pages))

    raw, raw_src = [], []
    for p in pages[:n]:
        lab, src = footer_label(p)
        raw.append(lab)
        raw_src.append(src)
    fit, fit_src = fit_labels(raw, n)

    # Final labels: dictionary wins when present; record agreement with footers.
    if dict_labels and len(dict_labels) >= n:
        final = dict_labels[:n]
        final_src = ["pagelabels"] * n
    else:
        final, final_src = fit, fit_src

    agree = sum(1 for i in range(n) if raw[i] and raw[i] == final[i].lower())
    disagree = [(i + 1, raw[i], final[i]) for i in range(n) if raw[i] and raw[i] != final[i].lower()]
    fit_vs_dict = None
    if dict_labels:
        fit_vs_dict = sum(1 for i in range(n) if fit[i].lower() == dict_labels[i].lower())

    labels = [{"pdf_index": i + 1, "label": final[i], "label_source": final_src[i],
               "footer": raw[i], "fit": fit[i], "fit_source": fit_src[i]} for i in range(n)]
    json.dump({"sha256": sha, "pages": labels}, open(os.path.join(outdir, "labels.json"), "w"), indent=1)

    ol = outline(pdf)
    for o in ol:
        o["label"] = final[o["pdf_index"]] if o["pdf_index"] is not None and o["pdf_index"] < n else None
    json.dump(ol, open(os.path.join(outdir, "outline.json"), "w"), indent=1)

    # body line height = most common block line height
    lhs = {}
    for p in pages[:n]:
        for b in p["blocks"]:
            k = round(b["lh"], 1)
            lhs[k] = lhs.get(k, 0) + len(b["lines"])
    body_lh = max(lhs.items(), key=lambda kv: kv[1])[0] if lhs else 12

    nspans = 0
    ids = set()
    kinds = {}
    with open(os.path.join(outdir, "spans.jsonl"), "w") as f:
        for i, p in enumerate(pages[:n]):
            sp = spans_for_page(i, p, body_lh)
            seg = []
            for s in sp:
                assert s["id"] not in ids
                ids.add(s["id"])
                kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
                nt = norm(s["text"])
                rec = {"id": s["id"], "segment": f"p{i + 1:04d}", "locator": final[i], "kind": s["kind"],
                       "text": s["text"], "norm": nt, "sha1": hashlib.sha1(nt.encode()).hexdigest()}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                seg.append(f"<!-- {s['id']} -->\n{s['text']}\n")
                nspans += 1
            with open(os.path.join(outdir, "segments", f"p{i + 1:04d}.md"), "w") as sf:
                sf.write(f"<!-- page {final[i]} (PDF {i + 1}) -->\n\n" + "\n".join(seg))

    # Cross-reference check: '"Title" on page N' must find Title on the page labelled N
    label_to_idx = {final[i].lower(): i for i in range(n)}
    page_norm = [norm(" ".join(join_lines(b["lines"]) for b in p["blocks"])) for p in pages[:n]]
    xrefs, xok, xbad = 0, 0, []
    for i in range(n):
        for m in re.finditer(r'"([^"]{3,80})" on page (\d+)', page_norm[i]):
            xrefs += 1
            title, lab = m.group(1).strip(" ,."), m.group(2)
            j = label_to_idx.get(lab)
            if j is not None and title in page_norm[j]:
                xok += 1
            else:
                xbad.append((i + 1, title, lab))

    # Validation sample: random pages that carry a footer must agree with label
    rnd = random.Random(seed)
    with_footer = [i for i in range(n) if raw[i]]
    sample = rnd.sample(with_footer, min(10, len(with_footer)))
    sample_ok = sum(1 for i in sample if raw[i] == final[i].lower())

    rep = {
        "pdf_pages": n, "sha256": sha,
        "pagelabels_dict": dict_labels is not None,
        "footer_labels_read": sum(1 for r in raw if r),
        "footer_agree_with_final": agree,
        "footer_disagree": disagree[:20], "footer_disagree_count": len(disagree),
        "fit_matches_dict": fit_vs_dict,
        "fit_sources": {s: fit_src.count(s) for s in set(fit_src)},
        "outline_items": len(ol), "outline_unresolved": sum(1 for o in ol if o["pdf_index"] is None),
        "top_level": [(o["title"], o["pdf_index"] + 1 if o["pdf_index"] is not None else None, o["label"]) for o in ol if o["depth"] == 1],
        "spans": nspans, "span_kinds": kinds, "body_line_height": body_lh,
        "xrefs": xrefs, "xrefs_resolved": xok, "xrefs_failed": xbad[:15],
        "sample10_ok": f"{sample_ok}/{len(sample)}",
        "seconds": {"parse_pdf": round(t_parse, 2), "text_layer": round(t_text, 2), "total": round(time.time() - t0, 2)},
    }
    json.dump(rep, open(os.path.join(outdir, "report.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
