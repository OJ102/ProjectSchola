"""PDF adapter (born-digital and scanned). Promoted from spikes/locators.py.

build(pdf_path, out_dir, opts) writes the shared store schema into out_dir
(see store.py) and returns the ingest report. Everything here is computed by
code: page labels (/PageLabels dictionary cross-checked against an anchored
footer fit, decisions 3-5 and 14), outline titles (PDF bookmarks), span ids
(p<pdf index, 4 digits>.<n>, 1-based) and cross-references.

Text comes from pdftotext -bbox-layout (decision 37). pdftohtml -xml, which
omits invisible text (render modes 3 and 7), is the visibility reference:
each text-layer line is aligned with the tokens pdftohtml shows on its row,
and words left over are invisible and dropped, embedded OCR layers included
(decision 35); so is text outside the CropBox. When pdftotext aborts (poppler
26.04 on an empty Info string) the text comes from pdftohtml alone. Pages
without visible text are OCRed by us (ocr.py) unless their render is blank.
Claude vision is never called here: pages and assets that need it are marked
and counted for the lazy vision step.

Words with a near-zero glyph height or placed outside the page box are
dropped; white text and text with no ink on the page render are kept but
flagged suspect_hidden.
"""
import concurrent.futures
import difflib
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
from srcnorm import LINEBREAK_MARK, neutralize, norm
from store import clean_meta, write_json

BAND = 0.08          # top/bottom fraction of the page treated as header/footer band
REPEAT_SHARE = 0.30  # running heads outside the band: lines repeating on >30% of pages
HIDDEN_HEIGHT = 1.0  # points; glyphs smaller than this are invisible to a reader
TOKENS_PER_ASSET = 2000
ICON_MAX = 160       # px: a raster this small that repeats on 3+ pages is decoration
MATH_CHARS = set("∑∫∮√∂∇≈≠≡≤≥≪≫∈∉⊂⊆⊃∪∩∀∃∝∞±∓×÷⋅·→←↔⇒⇐⇔↦αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")
CAPTION = re.compile(r"^(Figure|Fig\.|Table|Example|Listing)\s+([A-Z]?\d+(?:[-.]\d+)*)[.:]?\s")
# code signals: braces, semicolons, assignments, calls ("f("), brackets,
# prompts and keywords; bare parentheses are common in prose and do not count
CODE_LINE = re.compile(r"[{};=<>\[\]]|\w\(|^\s*(def|class|import|from|return|for|while|if|elif|else:|print|"
                       r"\$|>>>|#|//|let|const|var|function|pip|python)\b")
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "ﬅ": "st", "ﬆ": "st"}
SUPERSCRIPT_MARK = re.compile(r"^(\d{1,3}|[*†‡§])$")
TOOL_TIMEOUT = 600
MAX_IMAGE_PX = 50_000_000   # a page whose images exceed this is not decoded (decision 32)
MAX_DOC_IMAGE_PX = 1_500_000_000  # decode budget for a whole document (a book of figures uses ~300 MP)
LEADER = re.compile(r"(?:\s*\.){4,}\s*")


class AdapterError(Exception):
    pass


class DeadlineExceeded(AdapterError):
    """The ingest wall clock ran out; per-page OCR results are cached, so
    running /ingest again resumes."""


def unligature(s):
    return "".join(LIGATURES.get(c, c) for c in s)


# ---------------------------------------------------------------- text layer
WORD_RE = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">([^<]*)</word>')
LINE_RE = re.compile(r'<line xMin="[-\d.]+" yMin="[-\d.]+" xMax="[-\d.]+" yMax="[-\d.]+">(.*?)</line>', re.S)
BLOCK_RE = re.compile(r'<block xMin="[-\d.]+" yMin="[-\d.]+" xMax="[-\d.]+" yMax="[-\d.]+">(.*?)</block>', re.S)
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
XPAGE_RE = re.compile(r'<page number="\d+"[^>]*height="(\d+)" width="(\d+)">(.*?)</page>', re.S)
XTEXT_RE = re.compile(r'<text top="(-?\d+)" left="(-?\d+)" width="(\d+)" height="(\d+)" font="(\d+)">(.*?)</text>', re.S)
FONTSPEC_RE = re.compile(r'<fontspec id="(\d+)"[^>]*color="(#[0-9a-fA-F]{6})"')
XML_ZOOM = 1.5  # pdftohtml default zoom
WHITE = 0xF0    # every channel at or above this counts as white text
INK_CONTRAST = 30  # grey levels; less than this across a word's box means no visible ink
INK_MIN_PX = 4     # words rendered smaller than this cannot be judged
BLANK_DARK_SHARE = 0.001  # a page render with fewer ink pixels than this is blank
BLANK_CONTRAST = 25       # grey levels below the page median that count as ink


def nsp(s):
    """Characters that count for the captured-vs-total report (no whitespace)."""
    return len(re.sub(r"\s", "", s))


def new_stats():
    return {"total_chars": 0, "hidden_chars": 0, "marker_chars": 0, "invisible_chars": 0,
            "suspect_chars": 0, "outside_crop_chars": 0}


def run_tool(cmd, deadline):
    """Run a poppler tool with the time left before the ingest deadline."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise DeadlineExceeded("the ingest time limit ran out")
    return subprocess.run(cmd, capture_output=True, timeout=min(TOOL_TIMEOUT, max(0.5, left)))


def check_deadline(deadline, stage):
    if time.monotonic() > deadline:
        raise DeadlineExceeded(f"the ingest time limit ran out ({stage})")


def make_line(words, stats, w, h, keep_markers):
    """words: [(x0, y0, x1, y1, text, suspect)] of one text-layer line. Drops
    words too small to see or placed off the page, and superscript footnote
    markers; returns a line dict (suspect if any kept word is) or None."""
    vis = []
    for wd in words:
        x0, y0, x1, y1, t = wd[:5]
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
    for k, wd in enumerate(vis):
        x0, y0, x1, y1, t = wd[:5]
        # a small raised number after a word is a footnote marker ("conversations.21")
        if not keep_markers and k > 0 and SUPERSCRIPT_MARK.match(t) and (y1 - y0) < 0.8 * hm \
                and y1 < base - 0.15 * hm:
            stats["marker_chars"] += nsp(t)
            continue
        kept.append(wd)
    suspect = any(len(v) > 5 and v[5] for v in kept)
    if suspect:
        stats["suspect_chars"] += sum(nsp(v[4]) for v in kept if len(v) > 5 and v[5])
    return {"x0": min(v[0] for v in kept), "y0": min(v[1] for v in kept), "x1": max(v[2] for v in kept),
            "y1": max(v[3] for v in kept), "text": unligature(" ".join(v[4] for v in kept)), "h": hm,
            "suspect": suspect}


def math_density(text):
    if not text:
        return 0.0
    return sum(1 for c in text if c in MATH_CHARS) / max(1, len(text))


def block_of(lines):
    lhs = sorted(l["y1"] - l["y0"] for l in lines)
    return {"x0": min(l["x0"] for l in lines), "y0": min(l["y0"] for l in lines),
            "x1": max(l["x1"] for l in lines), "y1": max(l["y1"] for l in lines),
            "lines": lines, "lh": lhs[len(lhs) // 2]}


def xml_index(x):
    """pdftohtml -xml pages: size and text fragments with their colour.
    pdftohtml omits invisible text (render mode 3), which is what makes it a
    visibility reference for the pdftotext words (decision 30)."""
    colors = {k: v.lower() for k, v in FONTSPEC_RE.findall(x)}
    pages = []
    for pm in XPAGE_RE.finditer(x):
        ph, pw = float(pm.group(1)) / XML_ZOOM, float(pm.group(2)) / XML_ZOOM
        frags = []
        for tm in XTEXT_RE.finditer(pm.group(3)):
            t = html.unescape(re.sub(r"<[^>]+>", "", tm.group(6)))
            top, left, wd, hg = (int(tm.group(i)) / XML_ZOOM for i in (1, 2, 3, 4))
            c = colors.get(tm.group(5), "#000000")
            white = all(int(c[k:k + 2], 16) >= WHITE for k in (1, 3, 5))
            frags.append((left, top, left + wd, top + hg, tm.group(5), t, white))
        rows = {}
        for f in frags:
            for r in range(int(f[1] // 8) - 1, int(f[3] // 8) + 2):
                rows.setdefault(r, []).append(f)
        pages.append({"w": pw, "h": ph, "frags": frags, "rows": rows})
    return pages


def frag_words(f):
    """Split a pdftohtml fragment into words with boxes estimated from the
    character positions (fragments carry one box for the whole run)."""
    x0, y0, x1, y1, _, t, white = f[0], f[1], f[2], f[3], f[4], f[5], f[6]
    n = max(1, len(t))
    cw = (x1 - x0) / n
    return [(x0 + m.start() * cw, y0, x0 + m.end() * cw, y1, m.group(0), white) for m in re.finditer(r"\S+", t)]


def page_text_chars(page):
    return sum(len(l["text"]) for b in page["blocks"] for l in b["lines"])


def baseline_lines(frags, stats, w, h, keep_markers, ink, i, split_gaps):
    """Fragments -> lines: one baseline (+/-2pt) per line, left to right.
    With split_gaps, a horizontal gap wider than three line heights (a
    column gutter) starts a new line."""
    groups = []
    for f in sorted(frags, key=lambda f: (f[1], f[0])):
        if groups and abs(groups[-1][0][1] - f[1]) <= 2:
            groups[-1].append(f)
        else:
            groups.append([f])
    lines = []
    for g in groups:
        g.sort(key=lambda f: f[0])
        runs = [[g[0]]]
        for f in g[1:]:
            prev = runs[-1][-1]
            if split_gaps and f[0] - prev[2] > 3 * max(1.0, prev[3] - prev[1]):
                runs.append([f])
            else:
                runs[-1].append(f)
        for run in runs:
            words, prev = [], None
            for f in run:
                # visibility is judged per fragment: its box is exact, word
                # boxes inside it are only estimates. White text on a dark box
                # is fine when the render shows it; unrendered white text, or
                # any text without ink, is suspect
                seen = ink(i, f[0], f[1], f[2], f[3], w, h) if ink is not None else None
                suspect = seen is False or (f[6] and seen is not True)
                fw = [(x0, y0, x1, y1, t, suspect) for x0, y0, x1, y1, t, _ in frag_words(f)]
                # pdftohtml splits a word at a font change ("(" + "SFT" + ")");
                # a fragment that touches the previous one continues its word
                if fw and words and prev is not None and f[0] - prev[2] < 0.15 * max(1.0, f[3] - f[1]) \
                        and not prev[5][-1:].isspace() and not f[5][:1].isspace():
                    a, b = words[-1], fw[0]
                    words[-1] = (a[0], min(a[1], b[1]), b[2], max(a[3], b[3]), a[4] + b[4], a[5] or b[5])
                    fw = fw[1:]
                words.extend(fw)
                prev = f
            ln = make_line(words, stats, w, h, keep_markers) if words else None
            if ln:
                ln["font"] = run[0][4]
                lines.append(ln)
    return lines


def crop_clips(pdf, n):
    """Per page, the CropBox (intersected with the MediaBox) in top-left
    MediaBox coordinates, the space pdftohtml and pdftoppm use; None where
    the page tree cannot be read. Text outside it is not shown by viewers."""
    out = [None] * n
    if pdf is None:
        return out
    try:
        nums = pdf.pages()
    except PDFError:
        return out
    for i, num in enumerate(nums[:n]):
        node, media, crop, hops = pdf.resolve(Ref(num, 0)), None, None, 0
        while isinstance(node, dict) and hops < 64:
            media = media if media is not None else pdf.resolve(node.get("MediaBox"))
            crop = crop if crop is not None else pdf.resolve(node.get("CropBox"))
            node = pdf.resolve(node.get("Parent"))
            hops += 1
        try:
            m = [float(pdf.resolve(v)) for v in media]
            c = [float(pdf.resolve(v)) for v in crop] if isinstance(crop, list) and len(crop) == 4 else m
        except (TypeError, ValueError):
            continue
        if len(m) != 4:
            continue
        m = [min(m[0], m[2]), min(m[1], m[3]), max(m[0], m[2]), max(m[1], m[3])]
        c = [max(m[0], min(c[0], c[2])), max(m[1], min(c[1], c[3])), min(m[2], max(c[0], c[2])), min(m[3], max(c[1], c[3]))]
        out[i] = (c[0] - m[0], m[3] - c[3], c[2] - m[0], m[3] - c[1])
    return out


def row_chars(xp, y0, y1):
    """The non-space characters pdftohtml shows on the row y0..y1, left to
    right, as (char, fragment, char id). Characters, not words: pdftohtml
    and pdftotext split words differently (font changes, superscripts)."""
    cy = (y0 + y1) / 2
    seen, frags = set(), []
    for r in range(int(y0 // 8) - 1, int(y1 // 8) + 2):
        for f in xp["rows"].get(r, []):
            if id(f) not in seen and f[1] - 1 <= cy <= f[3] + 1:
                seen.add(id(f))
                frags.append(f)
    frags.sort(key=lambda f: f[0])
    return [(c, f, (id(f), k)) for f in frags for k, c in enumerate(f[5].lower()) if not c.isspace()]


def visible_words(words, chars, used):
    """Align a text-layer line (pdftotext words, left to right) with the
    visible characters pdftohtml shows on that row that no other line has
    claimed. A word is visible only if every one of its characters aligns:
    an invisible word between visible ones is left over, even when the same
    word appears elsewhere on the row (decisions 35 and 37). Returns {word
    index: fragment} and marks the matched characters as used."""
    avail = [c for c in chars if c[2] not in used]
    a, owner = [], []
    for k, wd in enumerate(words):
        for c in wd[4].lower():
            if not c.isspace():
                a.append(c)
                owner.append(k)
    b = "".join(c for c, _, _ in avail)
    a = "".join(a)
    hit = [None] * len(a)
    if a == b:
        hit = list(range(len(a)))
    else:
        for i, j, n in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            for k in range(n):
                hit[i + k] = j + k
    need, got, frag = {}, {}, {}
    for pos, k in enumerate(owner):
        need[k] = need.get(k, 0) + 1
        if hit[pos] is not None:
            got[k] = got.get(k, 0) + 1
            frag.setdefault(k, avail[hit[pos]][1])
    out = {}
    for k in need:
        if got.get(k) == need[k]:
            out[k] = frag[k]
    for pos, k in enumerate(owner):
        if k in out and hit[pos] is not None:
            used.add(avail[hit[pos]][2])
    return out


def match_score(words, chars):
    """Lines that equal their row exactly go first, then by similarity."""
    a = "".join(c for wd in words for c in wd[4].lower() if not c.isspace())
    b = "".join(c for c, _, _ in chars)
    if a == b:
        return 2.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a else 0.0


def regroup(lines):
    """Rebuild blocks from lines after invisible words were removed (they
    can split one visible paragraph across several text-layer blocks)."""
    out, cur = [], []
    for ln in merge_baseline(lines):
        if cur:
            prev = cur[-1]
            hgt = max(1.0, prev["y1"] - prev["y0"])
            if ln["y0"] - prev["y1"] > 0.8 * hgt or abs((ln["y1"] - ln["y0"]) - hgt) > 0.3 * hgt \
                    or abs(ln["x0"] - prev["x0"]) > 3 * hgt and abs(ln["x0"] - cur[0]["x0"]) > 3 * hgt:
                out.append(block_of(cur))
                cur = []
        cur.append(ln)
    if cur:
        out.append(block_of(cur))
    return out


def parse_bbox(doc, xpages, ink, clips=None):
    """pdftotext -bbox-layout pages: the content (decision 37). Every word
    is checked for visibility: a word pdftohtml does not show in that place
    is invisible text (render mode 3 or 7) and is dropped, OCR layers
    included (decision 35); so is a word outside the page's CropBox. White
    text, and text with no ink where the page was rendered (clipped away or
    covered), is kept but flagged suspect_hidden."""
    pages = []
    for i, pm in enumerate(PAGE_RE.finditer(doc)):
        w, h = float(pm.group(1)), float(pm.group(2))
        body = pm.group(3)
        xp = xpages[i] if xpages is not None and i < len(xpages) else None
        clip = clips[i] if clips is not None and i < len(clips) else None
        raw_words = [html.unescape(m.group(5)) for m in WORD_RE.finditer(body)]
        keep_markers = math_density(" ".join(raw_words)) > 0.01
        stats = new_stats()
        # gather every text-layer line first; lines that align best with the
        # visible text claim its tokens first, so an invisible word cannot
        # take a token a visible line needs
        tl = []
        for bk, bm in enumerate(BLOCK_RE.finditer(body)):
            for lm in LINE_RE.finditer(bm.group(1)):
                raw = [(float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)),
                        html.unescape(m.group(5))) for m in WORD_RE.finditer(lm.group(1))]
                if raw:
                    toks = row_chars(xp, min(r[1] for r in raw), max(r[3] for r in raw)) if xp is not None else None
                    tl.append([bk, raw, toks, None])
        used = set()
        for item in sorted(tl, key=lambda it: -match_score(it[1], it[2]) if it[2] is not None else 0):
            if item[2] is not None:
                item[3] = visible_words(item[1], item[2], used)
        dropped_any = False
        by_block = {}
        for bk, raw, toks, vis in tl:
            words = []
            for k, (x0, y0, x1, y1, t) in enumerate(raw):
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if clip and not (clip[0] - 1 <= cx <= clip[2] + 1 and clip[1] - 1 <= cy <= clip[3] + 1):
                    stats["total_chars"] += nsp(t)
                    stats["hidden_chars"] += nsp(t)
                    stats["outside_crop_chars"] += nsp(t)
                    dropped_any = True
                    continue
                white = False
                if vis is not None:
                    f = vis.get(k)
                    if f is None:
                        stats["total_chars"] += nsp(t)
                        stats["invisible_chars"] += nsp(t)
                        dropped_any = True
                        continue
                    white = f[6]
                seen = ink(i, x0, y0, x1, y1, w, h) if ink is not None else None
                # white text on a dark box is fine when the render shows it;
                # unrendered white text, or any text without ink, is suspect
                suspect = seen is False or (white and seen is not True)
                words.append((x0, y0, x1, y1, t, suspect))
            ln = make_line(words, stats, w, h, keep_markers)
            if ln:
                by_block.setdefault(bk, []).append(ln)
        if dropped_any:
            blocks = regroup([l for bk in sorted(by_block) for l in by_block[bk]])
        else:
            blocks = [block_of(by_block[bk]) for bk in sorted(by_block)]
        pages.append({"w": w, "h": h, "blocks": blocks, "stats": stats})
    return pages


def visible_pages(xpages, layout, ink, clips=None):
    """Fallback text layer when pdftotext aborts (poppler 26.04 on an empty
    Info string): built only from what pdftohtml shows, which omits
    invisible text (render modes 3 and 7). Fragments are grouped by
    vertical gap and font (or by pdftotext block boxes, if given)."""
    pages = []
    for i, xp in enumerate(xpages):
        w, h = xp["w"], xp["h"]
        stats = new_stats()
        raw_text = " ".join(f[5] for f in xp["frags"])
        keep_markers = math_density(raw_text) > 0.01
        lay = layout[i] if layout is not None and i < len(layout) else None
        boxes = lay[2] if lay else []
        assigned, loose = {}, []
        clip = clips[i] if clips is not None and i < len(clips) else None
        for f in xp["frags"]:
            cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
            if clip and not (clip[0] - 1 <= cx <= clip[2] + 1 and clip[1] - 1 <= cy <= clip[3] + 1):
                stats["total_chars"] += nsp(f[5])
                stats["hidden_chars"] += nsp(f[5])
                stats["outside_crop_chars"] += nsp(f[5])
                continue
            hits = [k for k, (a, b, c, d) in enumerate(boxes) if a - 1 <= cx <= c + 1 and b - 1 <= cy <= d + 1]
            if hits:
                k = min(hits, key=lambda k: (boxes[k][2] - boxes[k][0]) * (boxes[k][3] - boxes[k][1]))
                assigned.setdefault(k, []).append(f)
            else:
                loose.append(f)
        blocks = []
        for k in sorted(assigned):
            lines = baseline_lines(assigned[k], stats, w, h, keep_markers, ink, i, False)
            if lines:
                blocks.append(block_of(lines))
        cur = None
        for ln in baseline_lines(loose, stats, w, h, keep_markers, ink, i, True):
            hgt = ln["y1"] - ln["y0"]
            if cur and ln["y0"] - cur[-1]["y1"] <= hgt * 0.6 and ln["font"] == cur[-1]["font"]:
                cur.append(ln)
            else:
                cur = [ln]
                blocks.append(cur)
        blocks = [b if isinstance(b, dict) else block_of(b) for b in blocks]
        if lay:
            # characters pdftotext reads that pdftohtml does not show
            stats["invisible_chars"] = max(0, lay[3] - stats["total_chars"])
            stats["total_chars"] += stats["invisible_chars"]
        pages.append({"w": w, "h": h, "blocks": blocks, "stats": stats})
    return pages


class InkMap:
    """Low-resolution greyscale renders of the pages (pdftoppm
    -scale-to bounds the pixels whatever the page size). A word whose box
    has no contrast was clipped away or painted over."""

    def __init__(self, pdf_path, pages, workdir, deadline, max_side=1000):
        self.dir = workdir
        self.side = max_side
        self.files = {}
        os.makedirs(workdir, exist_ok=True)
        ranges, cur = [], None
        for p in sorted(pages):
            if cur and p == cur[1] + 1 and p - cur[0] < 64:
                cur[1] = p
            else:
                cur = [p, p]
                ranges.append(cur)

        def job(r):
            try:
                run_tool(["pdftoppm", "-gray", "-scale-to", str(max_side), "-f", str(r[0]), "-l", str(r[1]),
                          "-forcenum", pdf_path, os.path.join(workdir, f"r{r[0]}")], deadline)
            except subprocess.TimeoutExpired:
                return
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(job, ranges))
        for name in os.listdir(workdir):
            m = re.match(r"^r\d+-0*(\d+)\.pgm$", name)
            if m:
                self.files[int(m.group(1))] = os.path.join(workdir, name)
        self.cache_page, self.cache = None, None
        self.checked = set(self.files)

    def _load(self, page):
        if self.cache_page == page:
            return self.cache
        self.cache_page, self.cache = page, None
        try:
            with open(self.files[page], "rb") as f:
                data = f.read()
            m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", data)
            if m:
                self.cache = (int(m.group(1)), int(m.group(2)), data[m.end():])
        except (OSError, KeyError):
            pass
        return self.cache

    def dark_share(self, page):
        """Share of pixels at least BLANK_CONTRAST grey levels darker than
        the page's median (its background) on a 1-based page, or None when
        it was not rendered. Relative, never absolute (decision 36): light
        grey text on white counts; faint noise and blank paper give ~0."""
        img = self._load(page) if page in self.files else None
        if not img:
            return None
        W, H, px = img
        counts = [px.count(bytes([v])) for v in range(256)]
        half, acc, med = (W * H) / 2, 0, 255
        for v in range(256):
            acc += counts[v]
            if acc >= half:
                med = v
                break
        return sum(counts[:max(0, med - BLANK_CONTRAST + 1)]) / max(1, W * H)

    def __call__(self, i, x0, y0, x1, y1, w, h, masks=()):
        """True (ink), False (no ink) or None (cannot tell) for a word on the
        0-based page i. Pixels inside the mask boxes (visible text drawn in
        the same place) are ignored."""
        if i + 1 not in self.files:
            return None
        img = self._load(i + 1)
        if not img:
            return None
        W, H, px = img
        sx, sy = W / w, H / h
        a, b = int(x0 * sx), int(round(x1 * sx))
        top, bot = int(y0 * sy), int(round(y1 * sy))
        if bot - top < INK_MIN_PX or b <= a:
            return None
        a, b = max(0, a), min(W, b)
        top, bot = max(0, top), min(H, bot)
        pm = [(int(m[0] * sx) - 1, int(m[1] * sy) - 1, int(round(m[2] * sx)) + 1, int(round(m[3] * sy)) + 1)
              for m in masks]
        lo, hi, any_px = 255, 0, False
        for r in range(top, bot):
            cuts = sorted((ma, mb) for ma, mt, mb, mbot in pm if mt <= r < mbot and mb > a and ma < b)
            x = a
            for ma, mb in cuts + [(b, b)]:
                if ma > x:
                    row = px[r * W + x:r * W + min(ma, b)]
                    if row:
                        any_px = True
                        lo, hi = min(lo, min(row)), max(hi, max(row))
                x = max(x, mb)
                if x >= b:
                    break
        if not any_px:
            return None
        return hi - lo >= INK_CONTRAST


def ocr_blocks(lines, w, h, clip=None):
    """Group OCR lines into blocks: tesseract paragraph ids when present,
    else vertical gaps and left-edge jumps. Lines outside the CropBox are
    dropped like any text a viewer does not show."""
    stats = new_stats()
    out = []
    for l in lines:
        cx, cy = l["x"] + l["w"] / 2, l["y"] + l["h"] / 2
        if clip and not (clip[0] - 1 <= cx <= clip[2] + 1 and clip[1] - 1 <= cy <= clip[3] + 1):
            stats["outside_crop_chars"] += nsp(l["text"])
            continue
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
def code_like(lines):
    n = len(lines)
    return n >= 2 and sum(1 for l in lines if CODE_LINE.search(l["text"])) >= 0.6 * n \
        and sum(len(l["text"]) for l in lines) / n < 100


def split_paragraphs(b):
    """Poppler sometimes puts two paragraphs in one block. A new paragraph
    starts at an indented line that follows a short line."""
    lines = b["lines"]
    if len(lines) < 3 or code_like(lines):
        return [b]
    bx0, bx1 = b["x0"], b["x1"]
    groups = [[lines[0]]]
    for prev, ln in zip(lines, lines[1:]):
        indent = ln["x0"] - bx0
        if 3 < indent < 0.3 * (bx1 - bx0) and prev["x1"] < bx1 - 15:
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return [block_of(g) for g in groups]


def merge_baseline(lines):
    """Fragments on one baseline (a section number and its title) become one line."""
    out = []
    for ln in sorted(lines, key=lambda l: (round(l["y0"]), l["x0"])):
        if out and abs(out[-1]["y0"] - ln["y0"]) <= 2 and out[-1]["x1"] - 1 <= ln["x0"] \
                <= out[-1]["x1"] + 3 * max(1.0, ln["y1"] - ln["y0"]):
            p = out[-1]
            out[-1] = dict(p, x1=max(p["x1"], ln["x1"]), y1=max(p["y1"], ln["y1"]), text=p["text"] + " " + ln["text"],
                           suspect=p.get("suspect") or ln.get("suspect"))
        else:
            out.append(ln)
    return out


def layout_page(blocks, w):
    """Reading order. A page is two-column when at least three wide lines
    sit wholly on each side of the centre and the two sides overlap
    vertically. Then full-width lines split the page into bands, and within
    a band the left column is read before the right one; the column lines
    are regrouped into paragraphs. Returns (blocks, two_column)."""
    mid = w / 2

    def side(l):
        if l["x1"] <= mid + 0.02 * w:
            return "L"
        if l["x0"] >= mid - 0.02 * w:
            return "R"
        return "F"
    lines = [l for b in blocks for l in b["lines"]]
    wide = [l for l in lines if l["x1"] - l["x0"] >= 0.25 * w]
    L = [l for l in wide if side(l) == "L"]
    R = [l for l in wide if side(l) == "R"]
    twocol = False
    if len(L) >= 3 and len(R) >= 3:
        r0, r1 = min(l["y0"] for l in R), max(l["y1"] for l in R)
        l0, l1 = min(l["y0"] for l in L), max(l["y1"] for l in L)
        twocol = sum(1 for l in L if r0 <= l["y0"] <= r1) >= 2 and sum(1 for l in R if l0 <= l["y0"] <= l1) >= 2
    if not twocol:
        out = []
        for b in sorted(blocks, key=lambda b: (round(b["y0"], 1), b["x0"])):
            out.extend(split_paragraphs(b))
        return out, False
    units = []
    for b in blocks:
        sides = {side(l) for l in b["lines"]}
        if len(sides) == 1:
            units.append((sides.pop(), b["lines"]))
        else:
            units.extend((side(l), [l]) for l in b["lines"])
    units.sort(key=lambda u: (u[1][0]["y0"], u[1][0]["x0"]))
    ordered, band = [], []

    def flush():
        for s in ("L", "R"):
            col = merge_baseline([l for us, ls in band if us == s for l in ls])
            ordered.append(col)
        band.clear()
    for s, ls in units:
        if s == "F":
            flush()
            ordered.append(merge_baseline(ls))
        else:
            band.append((s, ls))
    flush()
    out = []
    for col in ordered:
        cur = []
        for ln in col:
            if cur:
                prev = cur[-1]
                hgt = max(1.0, prev["y1"] - prev["y0"])
                if ln["y0"] - prev["y1"] > 0.8 * hgt or abs((ln["y1"] - ln["y0"]) - hgt) > 0.3 * hgt \
                        or ln["y0"] < prev["y0"]:
                    out.extend(split_paragraphs(block_of(cur)))
                    cur = []
            cur.append(ln)
        if cur:
            out.extend(split_paragraphs(block_of(cur)))
    return out, True


def join_text(lines, vocab):
    """Join a paragraph's lines. U+2010 and a soft hyphen at a line end are
    break hyphens; an ASCII '-' is dropped only when the joined word occurs
    elsewhere in the document and the hyphenated form does not
    ("at-" + "tention"), else kept ("long-" + "range")."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("‐") or out.endswith("­"):
            out = out[:-1] + ln
        elif out.endswith("-"):
            m = re.search(r"([A-Za-z]+)-$", out)
            n = re.match(r"([a-z]+)", ln)
            if m and n and break_hyphen(m.group(1).lower(), n.group(1).lower(), vocab):
                out = out[:-1] + ln
            else:
                out += ln
        else:
            out = (out + " " + ln) if out else ln
    return out


def break_hyphen(a, b, vocab):
    """Is "a-" + "b" at a line end a typesetter's break? A compound seen
    elsewhere keeps its hyphen; a joined word seen elsewhere drops it; two
    real words ("long-" "range") keep it; anything else ("dif-" "ferent")
    is a break."""
    words, compounds = vocab or (set(), set())
    if f"{a}-{b}" in compounds:
        return False
    if a + b in words:
        return True
    return not (a in words and b in words)


def build_vocab(pages):
    """Words and hyphenated compounds seen inside lines (not at line ends)."""
    words, compounds = set(), set()
    for p in pages:
        for b in p["blocks"]:
            for l in b["lines"]:
                t = l["text"]
                if t.endswith("-"):
                    t = t.rsplit(" ", 1)[0] if " " in t else ""
                for wd in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", t):
                    lw = wd.lower()
                    if "-" in lw:
                        compounds.add(lw)
                        words.update(lw.split("-"))
                    else:
                        words.add(lw)
    return words, compounds


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


def page_spans(i, page, body_lh, heads, vocab=None):
    """Spans of one page (0-based i), in reading order, with band and
    running-head lines removed. Returns (spans, band_chars, two_column)."""
    h = page["h"]
    band_chars = 0
    body = []
    for b in page["blocks"]:
        # a heading-sized line in the band (a title at the top of a page)
        # is content, unless it is a running head
        title_like = b["lh"] >= 1.3 * body_lh and not (heads and head_key(b["lines"][0]["text"]) in heads)
        if in_band(b, h) and not title_like:
            band_chars += sum(nsp(l["text"]) for l in b["lines"])
        else:
            body.append(b)
    if heads and body:
        ys = sorted(body, key=lambda b: b["y0"])
        for b in (ys[0], ys[-1]):
            if len(b["lines"]) == 1 and head_key(b["lines"][0]["text"]) in heads and b in body and len(body) > 1:
                band_chars += nsp(b["lines"][0]["text"])
                body.remove(b)
    ordered, twocol = layout_page(body, page["w"])
    out = []
    for b in ordered:
        joined = join_text([l["text"] for l in b["lines"]], vocab)
        if not joined.strip():
            continue
        kind = block_kind(b, joined, body_lh)
        # TOC dot leaders ("Index . . . . . 497") carry no words; fold them
        t = code_text(b) if kind == "code" else LEADER.sub(" \u2026 ", joined)
        rec = {"text": neutralize(t), "kind": kind}
        if any(l.get("suspect") for l in b["lines"]):
            rec["suspect_hidden"] = True
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
def list_images(pdf_path, deadline):
    r = run_tool(["pdfimages", "-list", pdf_path], deadline)
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


def extract_images(pdf_path, raw_dir, pages, deadline):
    """Native-format extraction (no JPEG 2000 decode: -png takes ~45 s on a
    230-image book, -all ~6 s). Only the given pages, in contiguous ranges,
    so pages holding an oversized image are never decoded. Conversion for
    vision happens lazily."""
    os.makedirs(raw_dir, exist_ok=True)
    ranges, cur = [], None
    for p in sorted(pages):
        if cur and p == cur[1] + 1:
            cur[1] = p
        else:
            cur = [p, p]
            ranges.append(cur)
    for a, b in ranges:
        run_tool(["pdfimages", "-all", "-p", "-f", str(a), "-l", str(b), pdf_path,
                  os.path.join(raw_dir, f"i{a}")], deadline)
    # files are i<range start>-<page>-<n>, n counting every listed image
    # (smasks included) from 0 within one pdfimages run
    files = {}
    for name in os.listdir(raw_dir):
        m = re.match(r"^i(\d+)-\d+-(\d+)\.(\w+)$", name)
        if m:
            files[(int(m.group(1)), int(m.group(2)))] = name
    return files, [tuple(r) for r in ranges]


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

    # 1. parse in parallel: text layer (pdftotext, plus pdftohtml as the
    #    visibility reference and fallback), image list, PDF objects; then
    #    native image extraction, skipping oversized images (decision 32)
    deadline = opts.get("deadline") or (time.monotonic() + 600)
    os.makedirs(os.path.join(out, "segments"), exist_ok=True)
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    raw_dir = os.path.join(out, "assets", ".raw")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            f_bbox = ex.submit(run_tool, ["pdftotext", "-bbox-layout", pdf_path, "-"], deadline)
            f_xml = ex.submit(run_tool, ["pdftohtml", "-xml", "-i", "-q", "-stdout", pdf_path], deadline)
            f_list = ex.submit(list_images, pdf_path, deadline)
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
            img_rows = f_list.result()
            # a page whose images add up to more than MAX_IMAGE_PX is never
            # decoded (no extraction, render or OCR; listed in the report).
            # Figure extraction alone also has a whole-document budget, where
            # an image shared by many pages counts once; it never limits OCR
            # (decision 36)
            page_px = {}
            for r in img_rows:
                page_px[r["page"]] = page_px.get(r["page"], 0) + r["width"] * r["height"]
            big_pages = {pg for pg, px in page_px.items() if px > MAX_IMAGE_PX}
            image_pages = {r["page"] for r in img_rows if r["type"] == "image"}
            extract_skip, budget, seen_objs = set(), 0, set()
            for r in sorted(img_rows, key=lambda r: r["page"]):
                if r["object"] not in seen_objs:
                    seen_objs.add(r["object"])
                    budget += r["width"] * r["height"]
                if budget > MAX_DOC_IMAGE_PX:
                    extract_skip.add(r["page"])
            f_imgs = ex.submit(extract_images, pdf_path, raw_dir, image_pages - big_pages - extract_skip, deadline) \
                if opts.get("assets", True) else None
            rx = f_xml.result()
            xpages = xml_index(rx.stdout.decode("utf-8", "replace")) if rx.returncode == 0 else None
            rb = f_bbox.result()
            if xpages is None:
                # pdftohtml is the visibility reference; without it no
                # text can be trusted (fail closed)
                raise AdapterError("cannot read the PDF's visible text (pdftohtml failed; is it encrypted with a "
                                   "user password?)")
            ink = InkMap(pdf_path, set(range(1, len(xpages) + 1)) - big_pages, os.path.join(cache_dir, "ink"),
                         deadline)
            clips = crop_clips(pdf, len(xpages))
            if rb.returncode == 0:
                pages = parse_bbox(rb.stdout.decode("utf-8", "replace"), xpages, ink, clips)
                text_via = "pdftotext"
            else:
                pages, text_via = visible_pages(xpages, None, ink, clips), "pdftohtml"
            ink_checked = len(ink.checked)
            dark = {p: ink.dark_share(p) for p in range(1, len(pages) + 1) if page_text_chars(pages[p - 1]) < 10}
            shutil.rmtree(os.path.join(cache_dir, "ink"), ignore_errors=True)
            del rb
            img_files, img_ranges = f_imgs.result() if f_imgs else ({}, [])
    except subprocess.TimeoutExpired:
        raise DeadlineExceeded("the ingest time limit ran out while reading the PDF")
    timing["text_layer"] = round(time.time() - t0, 2)
    check_deadline(deadline, "text layer")
    encrypted = bool(pdf and pdf.encrypted)
    n = len(pages)
    if n == 0:
        raise AdapterError("the PDF has no pages poppler can read")
    if n_tree is not None and n_tree != n:
        log(f"note: text layer has {n} pages, page tree {n_tree}; using {min(n, n_tree)}")
        n = min(n, n_tree)
    if dict_labels is not None:
        dict_labels = (dict_labels + [None] * n)[:n]
    # pages that are never rendered or OCRed: pages holding an oversized
    # image (decision 32). Oversized pages are rendered downscaled to the
    # pixel cap instead (ocr.capped_dpi).
    no_render = set(big_pages)

    # 2. OCR pages with no text layer
    ppi = {}
    for r in img_rows:
        if r["type"] == "image":
            ppi[r["page"]] = max(ppi.get(r["page"], 0), r["ppi"])
    textless = [i for i in range(n) if page_text_chars(pages[i]) < 10]
    scanned = len(textless) > 0.5 * n
    # a page without visible text is OCRed by us (decision 35) unless its
    # render is blank: under 0.1% of pixels at least 25 grey levels darker
    # than the page median (faint noise, a dot or a thin rule is blank)
    textless_blank = [i for i in textless if dark.get(i + 1) is not None and dark[i + 1] < BLANK_DARK_SHARE]
    engine, engine_note = ocrmod.pick_engine(opts.get("ocr", "auto"))
    if engine == "applevision" and not scanned and opts.get("ocr", "auto") == "auto" \
            and not ocrmod.applevision_binary(build=False) and "tesseract" in ocrmod.engines_available():
        # a few textless pages in a born-digital book (covers, plates) do not
        # justify the one-time Apple Vision build; tesseract reads them
        engine = "tesseract"
    if textless and engine == "applevision" and not ocrmod.applevision_binary():
        # build the tool once, before the worker pool; fall back if it cannot be built
        fallback = "tesseract" if opts.get("ocr", "auto") == "auto" and "tesseract" in ocrmod.engines_available() else None
        engine, engine_note = fallback, "applevision unavailable" + ("; used tesseract" if fallback else "")
    ocr_done, ocr_failed, ocr_skipped, ocr_failed_pages = [], [], [], []
    if textless and engine:
        os.makedirs(os.path.join(cache_dir, "ocr"), exist_ok=True)

        def do(i):
            cp = os.path.join(cache_dir, "ocr", f"p{i + 1:04d}.{engine}.json")
            if os.path.exists(cp):
                try:
                    return i, json.load(open(cp)), True
                except ValueError:
                    pass
            if time.monotonic() > deadline:
                raise DeadlineExceeded("the ingest time limit ran out during OCR")
            dpi = min(300, max(150, ppi.get(i + 1, 200)))
            lines = ocrmod.ocr_page(pdf_path, i + 1, pages[i]["w"], pages[i]["h"], engine, dpi)
            tmp = cp + ".tmp"
            with open(tmp, "w") as f:
                json.dump(lines, f)
            os.replace(tmp, cp)
            return i, lines, False
        todo = [i for i in textless if i + 1 not in no_render and i not in textless_blank]
        ocr_skipped = [i + 1 for i in textless if i + 1 in no_render and i not in textless_blank]
        workers = max(1, min(4, os.cpu_count() or 2))
        late = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(do, i) for i in todo]
            futs_page = {fu: i + 1 for fu, i in zip(futs, todo)}
            for fu in concurrent.futures.as_completed(futs):
                try:
                    i, lines, _ = fu.result()
                except DeadlineExceeded as e:
                    late = e
                    continue
                except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                    ocr_failed.append(str(e)[:120])
                    ocr_failed_pages.append(futs_page[fu])
                    continue
                blocks, st = ocr_blocks(lines, pages[i]["w"], pages[i]["h"], clips[i] if i < len(clips) else None)
                pages[i]["blocks"], pages[i]["stats"], pages[i]["ocr"] = blocks, st, engine
                ocr_done.append(i + 1)
        if late:
            raise DeadlineExceeded(f"{late}; {len(ocr_done)} of {len(todo)} pages are cached")
    timing["ocr"] = round(time.time() - t0, 2)
    check_deadline(deadline, "ocr")

    # 3. page labels
    vls = [visual_lines(p["blocks"]) for p in pages[:n]]
    strip_pages = []
    for i in range(n):
        if i + 1 in no_render or time.monotonic() > deadline:
            continue
        if pages[i].get("ocr") and not edge_has_label(vls[i]):
            extra = ocrmod.strip_lines(pdf_path, i + 1, pages[i]["w"], pages[i]["h"])
            if extra:
                vls[i] = sorted(vls[i] + [(l["y"], l["text"]) for l in extra])
                strip_pages.append(i + 1)
        elif i in textless and not pages[i].get("ocr") and i not in textless_blank:
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
    check_deadline(deadline, "labels")

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
    totals = {"total_chars": 0, "hidden_chars": 0, "marker_chars": 0, "band_chars": 0, "captured_chars": 0,
              "invisible_chars": 0, "suspect_chars": 0, "outside_crop_chars": 0}
    vocab = build_vocab(pages[:n])
    kinds, twocol_pages = {}, []
    for i in range(n):
        sp, band_chars, twocol = page_spans(i, pages[i], body_lh, heads, vocab)
        if twocol:
            twocol_pages.append(i + 1)
        for k in ("total_chars", "hidden_chars", "marker_chars", "invisible_chars", "suspect_chars",
                  "outside_crop_chars"):
            totals[k] += pages[i]["stats"][k]
        totals["band_chars"] += band_chars
        recs = []
        for s in sp:
            nt = norm(s["text"])
            rec = {"id": s["id"], "segment": f"p{i + 1:04d}", "pdf_index": i + 1, "locator": final[i],
                   "kind": s["kind"], "text": s["text"], "norm": nt, "sha1": hashlib.sha1(nt.encode()).hexdigest()}
            if s.get("suspect_hidden"):
                rec["suspect_hidden"] = True
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
    check_deadline(deadline, "spans")

    # 6. assets: rasters (deduped by object id, decorative icons dropped)
    asset_index, asset_pages = [], {}
    adir = os.path.join(out, "assets")
    if img_rows:
        obj_pages = {}
        for r in img_rows:
            if r["type"] == "image":
                obj_pages.setdefault(r["object"], []).append(r["page"])
        seen_obj = set()
        dropped_icons, dup, scan_images, big_images = 0, 0, 0, 0
        # file key of each listed image: (start page of its pdfimages run,
        # its index among the rows of that run)
        img_key, counters = {}, {}
        for r in img_rows:
            run = next((a for a, b in img_ranges if a <= r["page"] <= b), None)
            if run is not None:
                img_key[r["num"]] = (run, counters.get(run, 0))
                counters[run] = counters.get(run, 0) + 1
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
            if r["page"] in big_pages or r["page"] in extract_skip:
                big_images += 1
                continue
            name = img_files.get(img_key.get(r["num"]))
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
                     "scan_images": scan_images, "oversized_skipped": big_images,
                     "skipped_pages": sorted(big_pages | extract_skip)}
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
        if i + 1 in no_render:
            continue
        if kind == "math" or vector_fig or (kind == "scan" and not pages[i].get("ocr")) or \
                (i in textless and not pages[i].get("ocr") and not asset_pages.get(i + 1) and kind != "blank"):
            render_jobs.append((i + 1, "math" if kind == "math" else ("vector_figure" if vector_fig else "scan")))
    if opts.get("assets", True) and render_jobs:
        def rj(job):
            pg, why = job
            aid = f"page{pg:04d}"
            if time.monotonic() > deadline:
                return pg, why, aid, False
            dpi = ocrmod.capped_dpi(pages[pg - 1]["w"], pages[pg - 1]["h"], 150)
            try:
                ok = ocrmod.render(pdf_path, pg, dpi, os.path.join(adir, aid + ".png"), gray=False)
            except subprocess.TimeoutExpired:
                ok = False
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
    check_deadline(deadline, "assets")

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
    units, ol = unitsmod.build_units(spans, ol, spans_by_page,
                                     [(f"p{i + 1:04d}", final[i]) for i in range(n)])
    timing["units"] = round(time.time() - t0, 2)
    check_deadline(deadline, "units")

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
    # (a footer line can carry two numbers, "50 | Chapter 3: Top 10"; the
    # label must match one of the page's candidates, not only the first)
    cands = res["candidates"]
    sample_bad = [{"pdf_index": i + 1, "footer": raw[i], "label": final[i]} for i in sample
                  if final[i].lower() not in cands[i]]

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
            "footer_agree": sum(1 for i in range(n) if raw[i] and final[i].lower() in res["candidates"][i]),
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
                 "header_footer_chars": totals["band_chars"], "hidden_chars_dropped": totals["hidden_chars"] + totals["invisible_chars"],
                 "footnote_marker_chars": totals["marker_chars"],
                 "captured_share": round(totals["captured_chars"] / totals["total_chars"], 4)
                 if totals["total_chars"] else 0.0},
        "hidden": {"invisible_chars_dropped": totals["invisible_chars"],
                   "outside_cropbox_chars_dropped": totals["outside_crop_chars"],
                   "suspect_chars": totals["suspect_chars"],
                   "suspect_spans": [x["id"] for x in spans if x.get("suspect_hidden")][:100],
                   "suspect_span_count": sum(1 for x in spans if x.get("suspect_hidden")),
                   "ink_checked_pages": ink_checked},
        "two_column_pages": twocol_pages[:200],
        "ocr": {"textless_pages": len(textless), "engine": engine, "requested": opts.get("ocr", "auto"),
                "note": engine_note, "pages_ocred": len(ocr_done), "failed": ocr_failed[:10],
                "failed_pages": sorted(ocr_failed_pages),
                "blank_skipped": len(textless_blank),
                "blank_pages": sorted(i + 1 for i in textless_blank),
                "skipped_oversized": sorted(ocr_skipped),
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
