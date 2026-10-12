"""PDF adapter (born-digital and scanned). Promoted from spikes/locators.py.

build(pdf_path, out_dir, opts) writes the shared store schema into out_dir
(see store.py) and returns the ingest report. Everything here is computed by
code: page labels (/PageLabels dictionary cross-checked against an anchored
footer fit, decisions 3-5 and 14), outline titles (PDF bookmarks), span ids
(p<pdf index, 4 digits>.<n>, 1-based) and cross-references.

Text comes from the text layer (pdftotext -bbox-layout, with a pdftohtml -xml
fallback for the poppler 26.04 crash on empty Info strings). Pages without a
text layer are OCRed (ocr.py). Claude vision is never called here: pages and
assets that need it are marked and counted for the lazy vision step.

Hidden content never becomes a span: words with a near-zero glyph height or
placed outside the page box are dropped and counted in the report.
"""
import concurrent.futures
import hashlib
import html
import json
import os
import random
import re
import shutil
import subprocess
import time

import ocr as ocrmod
import pagelabels
import units as unitsmod
from pdfmini import PDF, PDFError, Ref, text as pdftext
from srcnorm import LINEBREAK_MARK, join_lines, neutralize, norm
from store import clean_meta, write_json

BAND = 0.08          # top/bottom fraction of the page treated as header/footer band
REPEAT_SHARE = 0.30  # running heads outside the band: lines repeating on >30% of pages
HIDDEN_HEIGHT = 1.0  # points; glyphs smaller than this are invisible to a reader
TOKENS_PER_ASSET = 2000
ICON_MAX = 160       # px: a raster this small that repeats on 3+ pages is decoration
MATH_CHARS = set("∑∫∮√∂∇≈≠≡≤≥≪≫∈∉⊂⊆⊃∪∩∀∃∝∞±∓×÷⋅·→←↔⇒⇐⇔↦αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")
CAPTION = re.compile(r"^(Figure|Fig\.|Table|Example|Listing)\s+([A-Z]?\d+(?:[-.]\d+)*)[.:]?\s")
CODE_LINE = re.compile(r"[{}();=<>\[\]]|^\s*(def|class|import|from|return|for|while|if|elif|else:|print|"
                       r"\$|>>>|#|//|let|const|var|function|pip|python)\b")
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "ﬅ": "st", "ﬆ": "st"}
SUPERSCRIPT_MARK = re.compile(r"^(\d{1,3}|[*†‡§])$")
TOOL_TIMEOUT = 600
LEADER = re.compile(r"(?:\s*\.){4,}\s*")


class AdapterError(Exception):
    pass


def unligature(s):
    return "".join(LIGATURES.get(c, c) for c in s)


# ---------------------------------------------------------------- text layer
WORD_RE = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">([^<]*)</word>')
LINE_RE = re.compile(r'<line xMin="[-\d.]+" yMin="[-\d.]+" xMax="[-\d.]+" yMax="[-\d.]+">(.*?)</line>', re.S)
BLOCK_RE = re.compile(r'<block xMin="[-\d.]+" yMin="[-\d.]+" xMax="[-\d.]+" yMax="[-\d.]+">(.*?)</block>', re.S)
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)


def nsp(s):
    """Characters that count for the captured-vs-total report (no whitespace)."""
    return len(re.sub(r"\s|\u2060", "", s))


def new_stats():
    return {"total_chars": 0, "hidden_chars": 0, "marker_chars": 0}


def make_line(words, stats, w, h, keep_markers):
    """words: [(x0, y0, x1, y1, text)] of one text-layer line. Drops hidden
    words and superscript footnote markers; returns a line dict or None."""
    vis = []
    for wd in words:
        x0, y0, x1, y1, t = wd
        stats["total_chars"] += nsp(t)
        if (y1 - y0) < HIDDEN_HEIGHT or x1 < -1 or x0 > w + 1 or y1 < -1 or y0 > h + 1:
            stats["hidden_chars"] += nsp(t)
            continue
        vis.append(wd)
    if not vis:
        return None
    hs = sorted(v[3] - v[1] for v in vis)
    hm = hs[len(hs) // 2]
    base = sorted(v[3] for v in vis)[len(vis) // 2]
    kept = []
    for k, (x0, y0, x1, y1, t) in enumerate(vis):
        # a small raised number after a word is a footnote marker ("conversations.21")
        if not keep_markers and k > 0 and SUPERSCRIPT_MARK.match(t) and (y1 - y0) < 0.8 * hm \
                and y1 < base - 0.15 * hm:
            stats["marker_chars"] += nsp(t)
            continue
        kept.append((x0, y0, x1, y1, t))
    return {"x0": min(v[0] for v in kept), "y0": min(v[1] for v in kept), "x1": max(v[2] for v in kept),
            "y1": max(v[3] for v in kept), "text": unligature(" ".join(v[4] for v in kept)), "h": hm}


def math_density(text):
    if not text:
        return 0.0
    return sum(1 for c in text if c in MATH_CHARS) / max(1, len(text))


def bbox_pages(pdf_path):
    r = subprocess.run(["pdftotext", "-bbox-layout", pdf_path, "-"], capture_output=True, timeout=TOOL_TIMEOUT)
    if r.returncode != 0:
        # poppler 26.04 aborts in -bbox modes when the Info dict holds an empty
        # string (hyperref's default). pdftohtml -xml survives; rebuild blocks.
        return xml_pages(pdf_path), "pdftohtml"
    doc = r.stdout.decode("utf-8", "replace")
    pages = []
    for pm in PAGE_RE.finditer(doc):
        w, h = float(pm.group(1)), float(pm.group(2))
        body = pm.group(3)
        raw_words = [html.unescape(m.group(5)) for m in WORD_RE.finditer(body)]
        keep_markers = math_density(" ".join(raw_words)) > 0.01
        stats = new_stats()
        blocks = []
        for bm in BLOCK_RE.finditer(body):
            lines = []
            for lm in LINE_RE.finditer(bm.group(1)):
                words = [(float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)),
                          html.unescape(m.group(5))) for m in WORD_RE.finditer(lm.group(1))]
                ln = make_line(words, stats, w, h, keep_markers)
                if ln:
                    lines.append(ln)
            if lines:
                blocks.append(block_of(lines))
        pages.append({"w": w, "h": h, "blocks": blocks, "stats": stats})
    return pages, "pdftotext"


def block_of(lines):
    lhs = sorted(l["y1"] - l["y0"] for l in lines)
    return {"x0": min(l["x0"] for l in lines), "y0": min(l["y0"] for l in lines),
            "x1": max(l["x1"] for l in lines), "y1": max(l["y1"] for l in lines),
            "lines": lines, "lh": lhs[len(lhs) // 2]}


def xml_pages(pdf_path):
    """Fallback text layer via pdftohtml -xml: lines grouped into blocks by
    vertical gap and font. Coordinates are scaled back to PDF points."""
    r = subprocess.run(["pdftohtml", "-xml", "-i", "-q", "-stdout", pdf_path], capture_output=True,
                       timeout=TOOL_TIMEOUT)
    if r.returncode != 0:
        raise AdapterError("cannot read the PDF text layer (pdftotext and pdftohtml both failed; "
                           "is it encrypted with a user password?)")
    x = r.stdout.decode("utf-8", "replace")
    pages = []
    scale = 1.5  # pdftohtml default zoom
    for pm in re.finditer(r'<page number="\d+"[^>]*height="(\d+)" width="(\d+)">(.*?)</page>', x, re.S):
        ph, pw = float(pm.group(1)) / scale, float(pm.group(2)) / scale
        stats = new_stats()
        frags = []
        for tm in re.finditer(r'<text top="(-?\d+)" left="(-?\d+)" width="(\d+)" height="(\d+)" font="(\d+)">(.*?)</text>',
                              pm.group(3), re.S):
            t = html.unescape(re.sub(r"<[^>]+>", "", tm.group(6)))
            top, left, wd, hg = (int(tm.group(i)) / scale for i in (1, 2, 3, 4))
            frags.append((top, left, wd, hg, tm.group(5), t))
        frags.sort(key=lambda f: (f[0], f[1]))
        groups = []
        for f in frags:
            if groups and abs(groups[-1][0][0] - f[0]) <= 2:
                groups[-1].append(f)
            else:
                groups.append([f])
        lines = []
        for g in groups:
            g.sort(key=lambda f: f[1])
            words = [(f[1], f[0], f[1] + f[2], f[0] + f[3], f[5]) for f in g]
            ln = make_line(words, stats, pw, ph, True)
            if ln:
                ln["font"] = g[0][4]
                lines.append(ln)
        blocks, cur = [], None
        for ln in lines:
            hgt = ln["y1"] - ln["y0"]
            if cur and ln["y0"] - cur["lines"][-1]["y1"] <= hgt * 0.6 and ln["font"] == cur["lines"][-1]["font"]:
                cur["lines"].append(ln)
            else:
                cur = {"lines": [ln]}
                blocks.append(cur)
        pages.append({"w": pw, "h": ph, "blocks": [block_of(b["lines"]) for b in blocks], "stats": stats})
    return pages


def ocr_blocks(lines, w, h):
    """Group OCR lines into blocks: tesseract paragraph ids when present,
    else vertical gaps and left-edge jumps."""
    stats = new_stats()
    out = []
    for l in lines:
        ln = make_line([(l["x"], l["y"], l["x"] + l["w"], l["y"] + l["h"], l["text"])], stats, w, h, True)
        if ln:
            ln["par"], ln["conf"] = l.get("par"), l.get("conf")
            out.append(ln)
    out.sort(key=lambda l: (l["y0"], l["x0"]))
    blocks, cur = [], None
    for ln in out:
        hgt = max(1.0, ln["y1"] - ln["y0"])
        if cur is not None:
            prev = cur[-1]
            if ln["par"] is not None:
                same = ln["par"] == prev["par"]
            else:
                same = ln["y0"] - prev["y1"] <= 0.7 * hgt and abs(ln["x0"] - prev["x0"]) < 3 * hgt
            if same:
                cur.append(ln)
                continue
        cur = [ln]
        blocks.append(cur)
    return [block_of(b) for b in blocks], stats


# ---------------------------------------------------------------- reading order
def order_blocks(blocks, w):
    """Single column: top to bottom. Two columns (2+ blocks wholly on each
    side of the gutter): full-width blocks split the page into bands; within
    a band the left column is read before the right one."""
    mid = w / 2
    side = []
    for b in blocks:
        if b["x1"] <= mid + 0.03 * w and b["x0"] < mid - 0.1 * w:
            side.append("L")
        elif b["x0"] >= mid - 0.03 * w:
            side.append("R")
        else:
            side.append("F")
    nl = sum(1 for s, b in zip(side, blocks) if s == "L" and len(b["lines"]) >= 2)
    nr = sum(1 for s, b in zip(side, blocks) if s == "R" and len(b["lines"]) >= 2)
    if nl < 2 or nr < 2:
        return sorted(blocks, key=lambda b: (round(b["y0"], 1), b["x0"])), False
    items = sorted(zip(side, blocks), key=lambda sb: (sb[1]["y0"], sb[1]["x0"]))
    out, band = [], []

    def flush():
        out.extend(b for s, b in band if s == "L")
        out.extend(b for s, b in band if s == "R")
        band.clear()
    for s, b in items:
        if s == "F":
            flush()
            out.append(b)
        else:
            band.append((s, b))
    flush()
    return out, True


# ---------------------------------------------------------------- labels input
def visual_lines(blocks):
    """Text-layer lines merged by baseline (+/-3pt), top to bottom: [(y, text)]."""
    ls = sorted(((l["y0"], l["x0"], l["text"]) for b in blocks for l in b["lines"]), key=lambda l: (l[0], l[1]))
    out = []
    for y, x, t in ls:
        if out and abs(out[-1][0] - y) <= 3:
            out[-1][2].append((x, t))
        else:
            out.append([y, x, [(x, t)]])
    return [(y, " ".join(t for _, t in sorted(parts))) for y, _, parts in out]


def number_dense(vl):
    if len(vl) < 8:
        return False
    return sum(1 for _, t in vl if re.search(r"\d\s*$", t)) > 0.4 * len(vl)


LABEL_TOKEN = re.compile(r"(^|\s)(\d{1,5}|[ivxlcdm]{1,8}|[IVXLCDM]{1,8})(\s|$)")


def edge_has_label(vl):
    edge = vl[:3] + vl[-3:]
    return any(LABEL_TOKEN.search(t) for _, t in edge)


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
            if not isinstance(arr, list):
                arr = []
            for k in range(0, len(arr) - 1, 2):
                if isinstance(arr[k], (bytes, str)):
                    names[arr[k] if isinstance(arr[k], bytes) else arr[k].encode()] = pdf.resolve(arr[k + 1])
            kids = pdf.resolve(nd.get("Kids")) or []
            for kid in kids if isinstance(kids, list) else []:
                walk(kid, d + 1)
        walk(nroot["Dests"])
    old_dests = pdf.resolve(cat.get("Dests"))
    old_dests = old_dests if isinstance(old_dests, dict) else {}

    def dest_page(item):
        d = item.get("Dest")
        a = pdf.resolve(item.get("A"))
        if d is None and isinstance(a, dict):
            d = a.get("D")
        d = pdf.resolve(d)
        if isinstance(d, (bytes, str)):
            d = names.get(d if isinstance(d, bytes) else d.encode()) or old_dests.get(str(d))
            d = pdf.resolve(d)
        if isinstance(d, dict):
            d = pdf.resolve(d.get("D"))
        if isinstance(d, list) and d and isinstance(d[0], Ref):
            return page_idx.get(d[0].num)
        if isinstance(d, list) and d and isinstance(d[0], int) and not isinstance(d[0], bool):
            return d[0] if 0 <= d[0] < len(page_idx) else None
        return None

    out, seen = [], set()

    def walk_items(ref, depth):
        if depth > 32:
            return
        while ref is not None and isinstance(ref, Ref) and ref.num not in seen and len(out) < 20000:
            seen.add(ref.num)
            pdf.tick()
            it = pdf.resolve(ref)
            if not isinstance(it, dict):
                break
            out.append({"title": clean_meta(pdftext(pdf.resolve(it.get("Title", b"")))), "depth": depth,
                        "pdf_index": dest_page(it)})
            if "First" in it:
                walk_items(it["First"], depth + 1)
            ref = it.get("Next")
    root = pdf.resolve(cat.get("Outlines"))
    if isinstance(root, dict) and "First" in root:
        walk_items(root["First"], 1)
    return out


def xmp_meta(pdf):
    """Title and author from the catalog's XMP metadata stream (many
    publishers leave the Info dictionary empty)."""
    out = {}
    try:
        ref = pdf.catalog().get("Metadata")
        if not isinstance(ref, Ref) or not isinstance(pdf.objs.get(ref.num), dict):
            return out
        x = (pdf.stream(ref.num) or b"")[:2_000_000].decode("utf-8", "replace")
    except (PDFError, KeyError, ValueError, TypeError):
        return out
    for key, tag in (("title", "dc:title"), ("author", "dc:creator")):
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", x, re.S)
        if m:
            items = re.findall(r"<rdf:li\b[^>]*>(.*?)</rdf:li>", m.group(1), re.S)
            val = "; ".join(html.unescape(re.sub(r"<[^>]+>", "", i)).strip() for i in items) if items else ""
            if val:
                out[key] = clean_meta(val)
    return out


# ---------------------------------------------------------------- spans
def block_kind(b, text, body_lh):
    n = len(b["lines"])
    if b["lh"] < body_lh * 0.9 and re.match(r"^\d+\s", text):
        return "footnote"
    if CAPTION.match(text):
        return "caption"
    if n >= 2 and sum(1 for l in b["lines"] if CODE_LINE.search(l["text"])) >= 0.6 * n \
            and sum(len(l["text"]) for l in b["lines"]) / n < 100:
        return "code"
    if n <= 2 and b["lh"] >= body_lh * 1.2 and len(text) < 150:
        return "heading"
    if re.match(r"^(\d+\.|[•●▪◦‣\-*–])\s", text):
        return "list"
    return "para"


def code_text(b):
    """Code keeps its line breaks and indentation (approximated from x offsets)."""
    cw = max(1.0, b["lh"] * 0.5)
    return "\n".join(" " * max(0, int(round((l["x0"] - b["x0"]) / cw))) + l["text"] for l in b["lines"])


def head_key(t):
    return re.sub(r"\s+", " ", re.sub(r"[\d|•·—]", "", t)).strip().lower()


def running_heads(pages, n):
    """Single-line blocks at the top or bottom of the body area whose text
    (digits removed) repeats on more than 30% of pages: running heads that
    sit outside the header/footer band."""
    cnt = {}
    for p in pages[:n]:
        seen = set()
        body = [b for b in p["blocks"] if not in_band(b, p["h"])]
        body.sort(key=lambda b: b["y0"])
        for b in body[:1] + body[-1:]:
            if len(b["lines"]) == 1:
                k = head_key(b["lines"][0]["text"])
                # running heads are titles, not sentences
                if len(k) >= 3 and not k.endswith((".", "!", "?", ":", ";", ",")):
                    seen.add(k)
        for k in seen:
            cnt[k] = cnt.get(k, 0) + 1
    return {k for k, c in cnt.items() if n >= 10 and c > REPEAT_SHARE * n}


def in_band(b, h):
    return b["y0"] >= h * (1 - BAND) or b["y1"] <= h * BAND


def page_spans(i, page, body_lh, heads):
    """Spans of one page (0-based i), in reading order, with band and
    running-head lines removed. Returns (spans, band_chars, two_column)."""
    h = page["h"]
    band_chars = 0
    body = []
    for b in page["blocks"]:
        if in_band(b, h):
            band_chars += sum(nsp(l["text"]) for l in b["lines"])
        else:
            body.append(b)
    if heads and body:
        ys = sorted(body, key=lambda b: b["y0"])
        for b in (ys[0], ys[-1]):
            if len(b["lines"]) == 1 and head_key(b["lines"][0]["text"]) in heads and b in body and len(body) > 1:
                band_chars += nsp(b["lines"][0]["text"])
                body.remove(b)
    ordered, twocol = order_blocks(body, page["w"])
    out = []
    for b in ordered:
        joined = join_lines([l["text"] for l in b["lines"]])
        if not joined.strip():
            continue
        kind = block_kind(b, joined, body_lh)
        # TOC dot leaders ("Index . . . . . 497") carry no words; fold them
        t = code_text(b) if kind == "code" else LEADER.sub(" \u2026 ", joined)
        rec = {"text": neutralize(t), "kind": kind}
        confs = [l["conf"] for l in b["lines"] if l.get("conf") is not None]
        if confs:
            rec["ocr_conf"] = round(sum(confs) / len(confs), 3)
        out.append(rec)
    for k, s in enumerate(out):
        s["id"] = f"p{i + 1:04d}.{k + 1}"
    return out, band_chars, twocol


def section_at(ol, pdf_index):
    best = None
    for o in ol:
        if o["pdf_index"] is not None and o["pdf_index"] <= pdf_index and \
                (best is None or o["pdf_index"] >= best["pdf_index"]):
            best = o
    return best["title"] if best else None


# ---------------------------------------------------------------- assets
def list_images(pdf_path):
    r = subprocess.run(["pdfimages", "-list", pdf_path], capture_output=True, timeout=TOOL_TIMEOUT)
    rows = []
    if r.returncode != 0:
        return rows
    for ln in r.stdout.decode("utf-8", "replace").splitlines()[2:]:
        f = ln.split()
        if len(f) < 14 or not f[0].isdigit():
            continue
        try:
            rows.append({"page": int(f[0]), "num": int(f[1]), "type": f[2], "width": int(f[3]),
                         "height": int(f[4]), "enc": f[8], "object": int(f[10]), "ppi": int(float(f[12]))})
        except ValueError:
            continue
    return rows


def extract_images(pdf_path, raw_dir):
    """Native-format extraction (no JPEG 2000 decode: -png takes ~45 s on a
    230-image book, -all ~6 s). Conversion for vision happens lazily."""
    os.makedirs(raw_dir, exist_ok=True)
    subprocess.run(["pdfimages", "-all", "-p", pdf_path, os.path.join(raw_dir, "i")], capture_output=True,
                   timeout=TOOL_TIMEOUT)
    files = {}
    for name in os.listdir(raw_dir):
        m = re.match(r"^i-(\d+)-(\d+)\.(\w+)$", name)
        if m:
            files[int(m.group(2))] = name
    return files


# ---------------------------------------------------------------- cross-refs
XREF_TITLED = re.compile(r'"([^"]{3,80})" on page ([0-9ivxlcdm]+(?:[-.]\d+)?)')
XREF_PAGE = re.compile(r"\b(?:on|see) pages? ([0-9]{1,5})\b")
XREF_CHAPTER = re.compile(r"\b(?:in|see|of) chapters? (\d{1,2})\b")


def resolve_xrefs(spans, multimap, ol, spans_by_page):
    l2p = {}
    for lab, idxs in multimap.items():
        l2p.setdefault(lab.lower(), []).extend(idxs)
    chapters = {}
    for o in ol:
        m = re.match(r"^\s*(?:chapter\s+)?(\d{1,2})[.:\s]", o["title"], re.I)
        if o["depth"] == 1 and m and o["pdf_index"] and m.group(1) not in chapters:
            chapters[m.group(1)] = o
    out = []
    for s in spans:
        nt = s["norm"]
        here = int(s["segment"][1:])
        titled_at = set()
        for m in XREF_TITLED.finditer(nt):
            titled_at.add(m.start(2))
            title, lab = m.group(1).strip(" ,."), m.group(2)
            hits = []
            for j in l2p.get(lab, []):
                for t in spans_by_page.get(j, []):
                    if title in t["norm"]:
                        hits.append((abs(j - here), j, t["id"]))
                        break
            hits.sort()
            out.append({"span": s["id"], "kind": "titled_page", "text": m.group(0)[:120], "label": lab,
                        "resolved": bool(hits), "pdf_index": hits[0][1] if hits else None,
                        "target_span": hits[0][2] if hits else None})
        for m in XREF_PAGE.finditer(nt):
            if any(abs(m.start(1) - a) < 3 for a in titled_at):
                continue
            idxs = l2p.get(m.group(1), [])
            tgt = min(idxs, key=lambda j: abs(j - here)) if idxs else None
            out.append({"span": s["id"], "kind": "page", "text": m.group(0), "label": m.group(1),
                        "resolved": tgt is not None, "pdf_index": tgt, "ambiguous": len(idxs) > 1,
                        "target_span": (spans_by_page.get(tgt) or [{}])[0].get("id") if tgt else None})
        for m in XREF_CHAPTER.finditer(nt):
            o = chapters.get(m.group(1))
            out.append({"span": s["id"], "kind": "chapter", "text": m.group(0), "label": m.group(1),
                        "resolved": o is not None, "pdf_index": o["pdf_index"] if o else None,
                        "target_outline": o["id"] if o else None})
    return out


# ---------------------------------------------------------------- build
def find_heading_span(page_spans_list, title):
    nt = norm(title)
    for k, s in enumerate(page_spans_list):
        sn = s["norm"]
        if sn == nt or (len(sn) >= 4 and (nt.endswith(sn) or nt.startswith(sn))) or \
                (nt in sn and len(sn) < len(nt) + 20):
            return k
    return 0


def build(pdf_path, out, opts, cache_dir, log=print):
    t0 = time.time()
    timing = {}
    n_req_vision = opts.get("vision", "auto")

    # 1. parse in parallel: text layer, image list, native image extraction
    os.makedirs(os.path.join(out, "segments"), exist_ok=True)
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    raw_dir = os.path.join(out, "assets", ".raw")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f_text = ex.submit(bbox_pages, pdf_path)
        f_list = ex.submit(list_images, pdf_path)
        f_imgs = ex.submit(extract_images, pdf_path, raw_dir) if opts.get("assets", True) else None
        pdf_err, pdf, n_tree, dict_labels, ol, xmp = None, None, None, None, [], {}
        try:
            pdf = PDF(pdf_path)
            n_tree = len(pdf.pages())
            dict_labels = pdf.page_labels()
            try:
                ol = outline(pdf)
            except (PDFError, RecursionError) as e:
                pdf_err = f"outline: {e}"
            xmp = xmp_meta(pdf)
        except (PDFError, RecursionError, MemoryError) as e:
            # fail closed (decision 10): no dictionary, no outline; labels from footers
            pdf_err = str(e)
        timing["parse_pdf"] = round(time.time() - t0, 2)
        pages, text_via = f_text.result()
        img_rows = f_list.result()
        img_files = f_imgs.result() if f_imgs else {}
    timing["text_layer"] = round(time.time() - t0, 2)
    encrypted = bool(pdf and pdf.encrypted)
    n = len(pages)
    if n == 0:
        raise AdapterError("the PDF has no pages poppler can read")
    if n_tree is not None and n_tree != n:
        log(f"note: text layer has {n} pages, page tree {n_tree}; using {min(n, n_tree)}")
        n = min(n, n_tree)
    if dict_labels is not None:
        dict_labels = (dict_labels + [None] * n)[:n]

    # 2. OCR pages with no text layer
    ppi = {}
    for r in img_rows:
        if r["type"] == "image":
            ppi[r["page"]] = max(ppi.get(r["page"], 0), r["ppi"])
    textless = [i for i in range(n) if sum(len(l["text"]) for b in pages[i]["blocks"] for l in b["lines"]) < 10]
    scanned = len(textless) > 0.5 * n
    engine, engine_note = ocrmod.pick_engine(opts.get("ocr", "auto"))
    if textless and engine == "applevision" and not ocrmod.applevision_binary():
        # build the tool once, before the worker pool; fall back if it cannot be built
        fallback = "tesseract" if opts.get("ocr", "auto") == "auto" and "tesseract" in ocrmod.engines_available() else None
        engine, engine_note = fallback, "applevision unavailable" + ("; used tesseract" if fallback else "")
    ocr_done, ocr_failed = [], []
    if textless and engine:
        os.makedirs(os.path.join(cache_dir, "ocr"), exist_ok=True)

        def do(i):
            cp = os.path.join(cache_dir, "ocr", f"p{i + 1:04d}.{engine}.json")
            if os.path.exists(cp):
                try:
                    return i, json.load(open(cp)), True
                except ValueError:
                    pass
            dpi = min(300, max(150, ppi.get(i + 1, 200)))
            lines = ocrmod.ocr_page(pdf_path, i + 1, pages[i]["w"], pages[i]["h"], engine, dpi)
            tmp = cp + ".tmp"
            with open(tmp, "w") as f:
                json.dump(lines, f)
            os.replace(tmp, cp)
            return i, lines, False
        workers = max(1, min(4, os.cpu_count() or 2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(do, i) for i in textless]
            for fu in concurrent.futures.as_completed(futs):
                try:
                    i, lines, _ = fu.result()
                except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                    ocr_failed.append(str(e)[:120])
                    continue
                blocks, st = ocr_blocks(lines, pages[i]["w"], pages[i]["h"])
                pages[i]["blocks"], pages[i]["stats"], pages[i]["ocr"] = blocks, st, engine
                ocr_done.append(i + 1)
    timing["ocr"] = round(time.time() - t0, 2)

    # 3. page labels
    vls = [visual_lines(p["blocks"]) for p in pages[:n]]
    strip_pages = []
    for i in range(n):
        if pages[i].get("ocr") and not edge_has_label(vls[i]):
            extra = ocrmod.strip_lines(pdf_path, i + 1, pages[i]["w"], pages[i]["h"])
            if extra:
                vls[i] = sorted(vls[i] + [(l["y"], l["text"]) for l in extra])
                strip_pages.append(i + 1)
        elif i in textless and not pages[i].get("ocr"):
            # no OCR engine (or --ocr none/vision): page numbers from the strips only
            extra = ocrmod.strip_lines(pdf_path, i + 1, pages[i]["w"], pages[i]["h"]) if engine_note != "none" else []
            if extra:
                vls[i] = sorted((l["y"], l["text"]) for l in extra)
                strip_pages.append(i + 1)
    skip_bare = {i for i, vl in enumerate(vls) if number_dense(vl)}
    for k, o in enumerate(ol):
        if re.fullmatch(r"(table of )?contents\.?", o["title"].strip(), re.I) and o["pdf_index"] is not None:
            nxt = next((x["pdf_index"] for x in ol[k + 1:] if x["pdf_index"] is not None and x["depth"] <= o["depth"]),
                       o["pdf_index"] + 1)
            skip_bare |= set(range(o["pdf_index"], max(nxt, o["pdf_index"] + 1)))
    res = pagelabels.resolve(vls, dict_labels, frozenset(skip_bare))
    final, raw = res["labels"], res["raw"]
    labels = [{"pdf_index": i + 1, "label": final[i], "label_source": res["sources"][i], "footer": raw[i],
               "fit": res["fit"][i], "fit_source": res["fit_sources"][i]} for i in range(n)]
    lj = {"pages": labels, "label_to_pdf": res["multimap"]}
    for key in ("label_conflict", "footer_outliers", "dict_overridden"):
        if res["report"].get(key):
            lj[key] = res["report"][key]
    timing["labels"] = round(time.time() - t0, 2)

    # 4. outline: 1-based indices, labels, ids
    ol = [o for o in ol if o["title"] or o["pdf_index"] is not None]
    for k, o in enumerate(ol):
        idx = o["pdf_index"]
        o["id"] = f"o{k + 1}"
        o["pdf_index"] = idx + 1 if idx is not None and idx < n else None
        o["label"] = final[idx] if idx is not None and idx < n else None

    # 5. spans
    lhs = {}
    for p in pages[:n]:
        for b in p["blocks"]:
            key = round(b["lh"], 1)
            lhs[key] = lhs.get(key, 0) + len(b["lines"])
    body_lh = max(lhs.items(), key=lambda kv: kv[1])[0] if lhs else 12
    heads = running_heads(pages, n)
    spans, spans_by_page, seg_index = [], {}, []
    totals = {"total_chars": 0, "hidden_chars": 0, "marker_chars": 0, "band_chars": 0, "captured_chars": 0}
    kinds, twocol_pages = {}, []
    for i in range(n):
        sp, band_chars, twocol = page_spans(i, pages[i], body_lh, heads)
        if twocol:
            twocol_pages.append(i + 1)
        for k in ("total_chars", "hidden_chars", "marker_chars"):
            totals[k] += pages[i]["stats"][k]
        totals["band_chars"] += band_chars
        recs = []
        for s in sp:
            nt = norm(s["text"])
            rec = {"id": s["id"], "segment": f"p{i + 1:04d}", "pdf_index": i + 1, "locator": final[i],
                   "kind": s["kind"], "text": s["text"], "norm": nt, "sha1": hashlib.sha1(nt.encode()).hexdigest()}
            if "ocr_conf" in s:
                rec["ocr"] = pages[i]["ocr"]
                rec["ocr_conf"] = s["ocr_conf"]
            if len(res["multimap"].get(final[i], [])) > 1:
                # duplicate label: carry the section so citations can disambiguate
                rec["locator_ambiguous"] = True
                rec["section"] = section_at(ol, i + 1)
            recs.append(rec)
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
            totals["captured_chars"] += nsp(s["text"])
        spans.extend(recs)
        spans_by_page[i + 1] = recs
    timing["spans"] = round(time.time() - t0, 2)

    # 6. assets: rasters (deduped by object id, decorative icons dropped)
    asset_index, asset_pages = [], {}
    adir = os.path.join(out, "assets")
    if img_rows:
        obj_pages = {}
        for r in img_rows:
            if r["type"] == "image":
                obj_pages.setdefault(r["object"], []).append(r["page"])
        seen_obj = set()
        dropped_icons, dup, scan_images = 0, 0, 0
        for r in img_rows:
            if r["type"] != "image" or r["page"] > n:
                continue
            if r["object"] in seen_obj:
                dup += 1
                continue
            seen_obj.add(r["object"])
            pgs = sorted(set(obj_pages[r["object"]]))
            if len(pgs) >= 3 and max(r["width"], r["height"]) <= ICON_MAX:
                dropped_icons += 1
                continue
            name = img_files.get(r["num"])
            if not name:
                continue
            if pages[r["page"] - 1].get("ocr"):
                # the scan of an OCRed page is the page itself, not a figure
                scan_images += 1
                continue
            ext = name.rsplit(".", 1)[1]
            aid = f"img{r['page']:04d}-{r['object']}"
            os.replace(os.path.join(raw_dir, name), os.path.join(adir, f"{aid}.{ext}"))
            cap = next((s for s in spans_by_page.get(r["page"], []) if s["kind"] == "caption"), None)
            asset_index.append({"id": aid, "file": f"{aid}.{ext}", "kind": "raster", "pdf_index": r["page"],
                                "locator": final[r["page"] - 1], "pages": pgs, "object": r["object"],
                                "width": r["width"], "height": r["height"], "format": ext,
                                "caption_span": cap["id"] if cap else None, "needs_vision": True})
            asset_pages.setdefault(r["page"], []).append(aid)
        img_stats = {"rows": len(img_rows), "duplicates": dup, "icons_dropped": dropped_icons,
                     "scan_images": scan_images}
    else:
        img_stats = {"rows": 0}
    shutil.rmtree(raw_dir, ignore_errors=True)

    # 7. classify segments; render math pages and vector-figure pages
    seg_kind = {}
    render_jobs = []
    for i in range(n):
        sp = spans_by_page[i + 1]
        text = " ".join(s["text"] for s in sp)
        md = math_density(text)
        caps = [s for s in sp if s["kind"] == "caption"]
        code_chars = sum(len(s["text"]) for s in sp if s["kind"] == "code")
        if i in textless and not sp:
            kind = "figure" if asset_pages.get(i + 1) else "blank"
        elif pages[i].get("ocr") or i in textless:
            kind = "scan"
        elif md > 0.003 and sum(1 for c in text if c in MATH_CHARS) >= 8:
            kind = "math"
        elif asset_pages.get(i + 1) or any(c["text"].startswith(("Figure", "Fig.")) for c in caps):
            kind = "figure"
        elif any(c["text"].startswith("Table") for c in caps):
            kind = "table"
        elif text and code_chars > 0.5 * len(text):
            kind = "code"
        else:
            kind = "text"
        seg_kind[i + 1] = kind
        vector_fig = kind == "figure" and not asset_pages.get(i + 1)
        if kind == "math" or vector_fig or (kind == "scan" and not pages[i].get("ocr")) or \
                (i in textless and not pages[i].get("ocr") and not asset_pages.get(i + 1) and kind != "blank"):
            render_jobs.append((i + 1, "math" if kind == "math" else ("vector_figure" if vector_fig else "scan")))
    if opts.get("assets", True) and render_jobs:
        def rj(job):
            pg, why = job
            aid = f"page{pg:04d}"
            ok = ocrmod.render(pdf_path, pg, 150, os.path.join(adir, aid + ".png"), gray=False)
            return pg, why, aid, ok
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            for pg, why, aid, ok in ex.map(rj, render_jobs):
                if ok:
                    cap = next((s for s in spans_by_page[pg] if s["kind"] == "caption"), None)
                    asset_index.append({"id": aid, "file": aid + ".png", "kind": f"page_render:{why}",
                                        "pdf_index": pg, "locator": final[pg - 1], "pages": [pg],
                                        "format": "png", "dpi": 150,
                                        "caption_span": cap["id"] if cap else None, "needs_vision": True})
                    asset_pages.setdefault(pg, []).append(aid)
    asset_index.sort(key=lambda a: (a["pdf_index"], a["id"]))
    vision_mode = n_req_vision
    for i in range(n):
        k = seg_kind[i + 1]
        nv = {"all": True, "none": False}.get(vision_mode, k in ("math", "figure", "table")
                                              or (k == "scan" and not pages[i].get("ocr")))
        sp = spans_by_page[i + 1]
        seg_index.append({"id": f"p{i + 1:04d}", "pdf_index": i + 1, "locator": final[i], "kind": k,
                          "spans": len(sp), "tokens": sum(len(s["text"]) for s in sp) // 4,
                          "has_figures": bool(asset_pages.get(i + 1)), "assets": asset_pages.get(i + 1, []),
                          "needs_vision": nv, "ocr": pages[i].get("ocr")})
    if vision_mode == "none":
        for a in asset_index:
            a["needs_vision"] = False
    timing["assets"] = round(time.time() - t0, 2)

    # 8. outline ranges, synthetic outline when there is none, units
    if not ol:
        ol = unitsmod.synthetic_outline(spans, n, final)
    else:
        first = min((o["pdf_index"] for o in ol if o["pdf_index"]), default=1)
        if first > 1:
            ol.insert(0, {"id": "o0", "title": "Front matter", "depth": 1, "pdf_index": 1, "label": final[0],
                          "synthetic": True})
    for o in ol:
        if o["pdf_index"] and o.get("title") and not o.get("start_span"):
            o["start_span_k"] = find_heading_span(spans_by_page.get(o["pdf_index"], []), o["title"])
    units, ol = unitsmod.build_units(spans, ol, spans_by_page)
    timing["units"] = round(time.time() - t0, 2)

    # 9. cross-references
    xrefs = resolve_xrefs(spans, res["multimap"], ol, spans_by_page)

    # 10. validation: 10 random footered pages must carry their footer label
    # (pages whose footer the fit deliberately overrode, as a lying or stray
    # number, are reported as outliers, not sampled; neither are PDF-index
    # fallback labels, which by design do not follow the footers)
    rnd = random.Random(int(hashlib.sha256(pdf_path.encode() + str(n).encode()).hexdigest()[:8], 16))
    outlier_pages = {f["pdf_index"] - 1 for f in res["report"].get("footer_outliers", [])}
    with_footer = [i for i in range(n) if raw[i] and i not in outlier_pages and res["sources"][i] != "fallback"]
    sample = sorted(rnd.sample(with_footer, min(10, len(with_footer))))
    sample_bad = [{"pdf_index": i + 1, "footer": raw[i], "label": final[i]} for i in sample
                  if raw[i] != final[i].lower()]

    # 11. write
    with open(os.path.join(out, "spans.jsonl"), "w") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    for i in range(n):
        lines = [f"[{s['id']}] {s['text'].replace(chr(10), ' ' + LINEBREAK_MARK + ' ')}"
                 for s in spans_by_page[i + 1]]
        with open(os.path.join(out, "segments", f"p{i + 1:04d}.md"), "w") as f:
            f.write(f"<!-- page {final[i]} (PDF {i + 1}) -->\n" + "\n".join(lines) + ("\n" if lines else ""))
    write_json(os.path.join(out, "segments", "index.json"), seg_index)
    write_json(os.path.join(out, "labels.json"), lj)
    write_json(os.path.join(out, "outline.json"), ol)
    write_json(os.path.join(out, "units.json"), units)
    write_json(os.path.join(out, "xrefs.json"), xrefs)
    write_json(os.path.join(out, "assets", "index.json"), asset_index)

    lr = res["report"]
    srcs = lr.get("sources", {})
    vision_assets = [a for a in asset_index if a["needs_vision"]]
    scan_vision_pages = [s for s in seg_index if s["kind"] == "scan" and s["needs_vision"]]
    titled = [x for x in xrefs if x["kind"] == "titled_page"]
    rep = {
        "type": "scanned-pdf" if scanned else "pdf", "xmp": xmp,
        "pdf_pages": n, "encrypted": encrypted, "pdf_error": pdf_err, "text_layer_via": text_via,
        "labels": {
            "pagelabels_dict": dict_labels is not None,
            "sources": srcs,
            "read": srcs.get("footer", 0) + srcs.get("pagelabels", 0) + srcs.get("corrected", 0),
            "interpolated": srcs.get("interpolated", 0), "fallback": srcs.get("fallback", 0),
            "interpolated_pages": [l["pdf_index"] for l in labels if l["label_source"] == "interpolated"][:200],
            "footer_labels_read": sum(1 for r in raw if r),
            "footer_agree": sum(1 for i in range(n) if raw[i] and raw[i] == final[i].lower()),
            "pagelabels_checked": lr.get("pagelabels_checked"), "pagelabels_disagree": lr.get("pagelabels_disagree"),
            "label_conflict": lr.get("label_conflict"), "footer_outliers": lr.get("footer_outliers", []),
            "dict_overridden": lr.get("dict_overridden"), "short_source": lr.get("short_source"),
            "duplicate_labels": {k: v for k, v in list(lr.get("duplicate_labels", {}).items())[:50]},
            "fit_coverage": lr.get("fit_coverage"),
        },
        "validation": {"sampled": len(sample), "mismatched": sample_bad,
                       "mismatch_rate": round(len(sample_bad) / len(sample), 3) if sample else 0.0},
        "outline": {"items": sum(1 for o in ol if not o.get("synthetic")), "synthetic": any(o.get("synthetic") for o in ol),
                    "depth": max((o["depth"] for o in ol), default=0),
                    "unresolved": sum(1 for o in ol if o["pdf_index"] is None),
                    "top_level": [[o["title"], o["pdf_index"], o["label"]] for o in ol if o["depth"] == 1][:60]},
        "spans": len(spans), "span_kinds": kinds, "units": len(units),
        "segment_kinds": {k: list(seg_kind.values()).count(k) for k in sorted(set(seg_kind.values()))},
        "text": {"total_chars": totals["total_chars"], "captured_chars": totals["captured_chars"],
                 "header_footer_chars": totals["band_chars"], "hidden_chars_dropped": totals["hidden_chars"],
                 "footnote_marker_chars": totals["marker_chars"],
                 "captured_share": round(totals["captured_chars"] / totals["total_chars"], 4)
                 if totals["total_chars"] else 0.0},
        "two_column_pages": twocol_pages[:200],
        "ocr": {"textless_pages": len(textless), "engine": engine, "requested": opts.get("ocr", "auto"),
                "note": engine_note, "pages_ocred": len(ocr_done), "failed": ocr_failed[:10],
                "strip_pages": len(strip_pages)},
        "assets": dict(img_stats, total=len(asset_index),
                       rasters=sum(1 for a in asset_index if a["kind"] == "raster"),
                       rendered_pages=sum(1 for a in asset_index if a["kind"].startswith("page_render"))),
        "vision": {"mode": vision_mode, "calls_made": 0,
                   "assets_needing_vision": len(vision_assets),
                   "estimated_tokens": len(vision_assets) * TOKENS_PER_ASSET,
                   "scan_pages_without_text": len(scan_vision_pages)},
        "xrefs": {"total": len(xrefs), "resolved": sum(1 for x in xrefs if x["resolved"]),
                  "titled": len(titled), "titled_resolved": sum(1 for x in titled if x["resolved"]),
                  "failed": [x for x in xrefs if not x["resolved"]][:15]},
        "seconds": dict(timing, total=round(time.time() - t0, 2)),
    }
    return rep
