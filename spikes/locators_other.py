"""Spike 0b: locators + span ids for EPUB and WebVTT captions.

usage: python3 spikes/locators_other.py epub <file.epub> <outdir>
       python3 spikes/locators_other.py vtt  <file.vtt>  <outdir> [video-url]

Writes spans.jsonl + outline.json (+ report.json) into <outdir> and prints a
summary. EPUB locator: "<chapter> § <heading> ¶n" (chapter = top-level nav
entry, heading = the most specific nav entry or in-file heading), plus the
publisher page "p. N" when epub:type="pagebreak" markers exist; a span takes
the page it STARTS on. VTT locator: hh:mm:ss of the cue start.

EPUB text extraction keeps every text node: text in div/section/span or in
unclosed <p> is flushed at the next block boundary, never dropped. Hidden
content (decision 11) is dropped and counted. report.json gives captured vs
total body characters. Zip entries are size-capped (decision 10).
Span text is neutralized (no literal "[[" / "]]", adapter contract).
"""
import hashlib
import html
import json
import os
import posixpath
import re
import sys
import urllib.parse
import zipfile
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidden import is_hidden, parse_css  # noqa: E402
from srcnorm import neutralize, norm  # noqa: E402

MAX_ENTRY = 64 * 1024 * 1024     # one decompressed zip entry
MAX_TOTAL = 512 * 1024 * 1024    # all entries read
MAX_RATIO = 200                  # compression ratio beyond this is a bomb


class SourceError(Exception):
    pass


def rec(sid, segment, locator, kind, text, **extra):
    text = neutralize(text)
    n = norm(text)
    return dict(id=sid, segment=segment, locator=locator, kind=kind, text=text, norm=n,
                sha1=hashlib.sha1(n.encode()).hexdigest(), **extra)


# ------------------------------------------------------------------ EPUB
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCKS = {"p", "div", "section", "article", "aside", "header", "footer", "main", "nav", "li", "ul", "ol",
          "pre", "blockquote", "figure", "figcaption", "caption", "table", "tr", "td", "th", "dl", "dt", "dd",
          "h1", "h2", "h3", "h4", "h5", "h6", "body", "html", "br", "hr"}
HEADS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP = {"script", "style", "head", "title", "template", "svg", "math"}
KIND = {"li": "list", "dt": "list", "dd": "list", "pre": "code", "figcaption": "caption", "caption": "caption",
        "td": "table", "th": "table"}


def page_of(a):
    for k in ("title", "aria-label"):
        v = (a.get(k) or "").strip()
        if v:
            return re.sub(r"^(?:page|pg|p\.?)\s*", "", v, flags=re.I) or v
    m = re.search(r"(?:page|pg|p)[-_]?(\d+|[ivxlcdm]+)$", a.get("id") or "", re.I)
    return m.group(1) if m else None


class ChapterParser(HTMLParser):
    def __init__(self, rules=()):
        super().__init__(convert_charrefs=True)
        self.rules = rules
        self.stack = []          # [tag, hidden_or_skip]
        self.items = []          # (kind, text, page_at_start, element_id, tag, nav_ids)
        self.buf, self.buf_page, self.buf_tag, self.buf_id = [], None, None, None
        self.page = None
        self.ids_seen = []
        self.total_chars = 0
        self.hidden_chars = 0

    def suppressed(self):
        return any(h for _, h, _ in self.stack)

    def block_tag(self):
        for t, _, _ in reversed(self.stack):
            if t in BLOCKS and t not in ("br", "hr"):
                return t
        return "p"

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if t:
            tag = self.buf_tag or "p"
            kind = "heading" if tag in HEADS else KIND.get(tag, "para")
            self.items.append((kind, t, self.buf_page, self.buf_id, tag, self.ids_seen))
            self.ids_seen = []
        self.buf, self.buf_page, self.buf_tag, self.buf_id = [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids_seen.append(a["id"])
        et = (a.get("epub:type") or "") + " " + (a.get("role") or "")
        if "pagebreak" in et:
            self.page = page_of(a) or self.page
        if tag in BLOCKS:
            self.flush()
        if tag in VOID:
            return
        hide = tag in SKIP or is_hidden(tag, a, self.rules, [(t, at) for t, _, at in self.stack])
        self.stack.append([tag, hide, a])
        if tag in BLOCKS and not self.buf_id:
            self.buf_id = a.get("id")

    def handle_startendtag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids_seen.append(a["id"])
        et = (a.get("epub:type") or "") + " " + (a.get("role") or "")
        if "pagebreak" in et:
            self.page = page_of(a) or self.page
        if tag in BLOCKS:
            self.flush()

    def handle_endtag(self, tag):
        # pop to the matching open tag; a stray end tag (e.g. </style>) is ignored
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                if tag in BLOCKS:
                    self.flush()
                del self.stack[k:]
                return

    def handle_data(self, data):
        n = len(data.strip())
        in_skip = any(t in SKIP for t, _, _ in self.stack)
        if not in_skip:
            self.total_chars += n
        if self.suppressed():
            if not in_skip:
                self.hidden_chars += n
            return
        if not self.buf and data.strip():
            self.buf_page = self.page
            self.buf_tag = self.block_tag()
        self.buf.append(data)

    def close(self):
        super().close()
        self.flush()


def zread(z, name, total):
    try:
        info = z.getinfo(name)
    except KeyError:
        raise SourceError(f"missing file in EPUB: {name}")
    if info.file_size > MAX_ENTRY or (info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO
                                      and info.file_size > 1024 * 1024):
        raise SourceError(f"EPUB entry too large or too compressed (zip bomb?): {name}")
    if total[0] + info.file_size > MAX_TOTAL:
        raise SourceError("EPUB too large in total")
    with z.open(info) as f:
        data = f.read(MAX_ENTRY + 1)
    if len(data) > MAX_ENTRY:
        raise SourceError(f"EPUB entry larger than its header says: {name}")
    total[0] += len(data)
    return data.decode("utf-8", "replace")


def epub(path, outdir):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise SourceError(f"not an EPUB (zip): {e}")
    names = set(z.namelist())
    total = [0]
    if "META-INF/encryption.xml" in names:
        enc = zread(z, "META-INF/encryption.xml", total)
        algs = re.findall(r'EncryptionMethod[^>]*Algorithm="([^"]+)"', enc)
        # font obfuscation (IDPF / Adobe) is not DRM; anything else is
        if any(a not in ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC") for a in algs):
            raise SourceError("EPUB is DRM-protected (META-INF/encryption.xml); cannot ingest")
    cont = zread(z, "META-INF/container.xml", total)
    m = re.search(r'full-path="([^"]+)"', cont)
    if not m:
        raise SourceError("container.xml has no rootfile full-path")
    opf_path = m.group(1)
    opf = zread(z, opf_path, total)
    base = posixpath.dirname(opf_path)
    manifest = {}
    for item in re.finditer(r"<item\b[^>]*>", opf):
        i, h = re.search(r'\bid="([^"]+)"', item.group(0)), re.search(r'\bhref="([^"]+)"', item.group(0))
        if i and h:
            manifest[i.group(1)] = urllib.parse.unquote(h.group(1))
    spine = [manifest[i] for i in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf) if i in manifest]
    # stylesheets: rules that hide content (decision 11)
    rules = []
    for href in manifest.values():
        if href.lower().endswith(".css") and posixpath.join(base, href) in names:
            rules += parse_css(zread(z, posixpath.join(base, href), total))
    nav_href = re.search(r'<item\b[^>]*properties="[^"]*\bnav\b[^"]*"[^>]*>', opf)
    nav_href = re.search(r'\bhref="([^"]+)"', nav_href.group(0)).group(1) if nav_href else None
    outline = []
    if nav_href and posixpath.join(base, nav_href) in names:
        nav = zread(z, posixpath.join(base, nav_href), total)
        tocm = re.search(r'<nav[^>]*epub:type="[^"]*toc[^"]*".*?</nav>', nav, re.S)
        depth = 0
        for tok in re.finditer(r'<ol\b|</ol>|<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', tocm.group(0) if tocm else "", re.S):
            g = tok.group(0)
            if g.startswith("<ol"):
                depth += 1
            elif g.startswith("</ol"):
                depth -= 1
            else:
                outline.append({"title": html.unescape(re.sub(r"<[^>]+>", "", tok.group(2))).strip(),
                                "href": posixpath.normpath(posixpath.join(posixpath.dirname(nav_href), urllib.parse.unquote(tok.group(1)))),
                                "depth": max(1, depth)})
    targets = {}
    for i, o in enumerate(outline):
        f, _, anc = o["href"].partition("#")
        targets.setdefault((f, anc), i)
    spans, ch, k, para = [], 0, 0, 0
    path, head = [], None
    total_chars = captured = hidden_chars = 0
    for href in spine:
        full = posixpath.join(base, href)
        if full not in names or href == nav_href:
            continue
        p = ChapterParser(rules)
        p.feed(zread(z, full, total))
        p.close()
        total_chars += p.total_chars
        hidden_chars += p.hidden_chars
        first = True
        for kind, t, page, eid, tag, ids in p.items:
            captured += len(t)
            hit = None
            for cand in ([""] if first else []) + ids:
                if (href, cand) in targets:
                    hit = targets[(href, cand)]
            first = False
            if hit is not None or ch == 0:
                if hit is not None:
                    o = outline[hit]
                    path = path[:o["depth"] - 1] + [o["title"]]
                elif not path:
                    path = [t if kind == "heading" else href]
                ch, k, para, head = ch + 1, 0, 0, None
            elif kind == "heading" and not outline:
                path, head, para = [t], None, 0
            elif kind == "heading":
                head, para = t, 0
            if kind != "heading":
                para += 1
            k += 1
            sub = head or (path[-1] if len(path) > 1 else None)
            loc = path[0] + (f" § {sub}" if sub else "")
            if kind != "heading":
                loc += f" ¶{para}"
            spans.append(rec(f"ch{ch:02d}.{k}", f"ch{ch:02d}", loc, kind, t, page=page,
                             href=f"{href}#{eid}" if eid else href))
    report = {"body_chars": total_chars, "captured_chars": captured, "hidden_chars_dropped": hidden_chars,
              "capture_ratio": round(captured / total_chars, 3) if total_chars else None}
    write(outdir, spans, outline, report)
    return spans, outline


# ------------------------------------------------------------------ VTT
TS = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"


def secs(m, off):
    h, mi, s, ms = (m.group(off + i) for i in range(4))
    if int(mi) > 59 or int(s) > 59:
        return None
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def hms(t):
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def parse_clock(s):
    parts = [int(x) for x in s.split(":")]
    if len(parts) == 2:
        parts = [0] + parts
    h, m, sec = parts
    return None if m > 59 or sec > 59 else h * 3600 + m * 60 + sec


def video_link(url, start):
    """Deep link at a time: only http(s); the t parameter goes in the query,
    the fragment is kept at the end."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k != "t"]
    q.append(("t", f"{int(start)}s"))
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), u.fragment))


def vtt(path, outdir, url=None):
    txt = open(path, encoding="utf-8-sig", errors="replace").read().replace("\r\n", "\n").replace("\r", "\n")
    cues, bad = [], 0
    for block in re.split(r"\n\s*\n", txt):
        lines = block.strip("\n").split("\n")
        for i, ln in enumerate(lines):
            m = re.match(TS + r"\s*-->\s*" + TS, ln.strip())
            if m:
                a, b = secs(m, 1), secs(m, 5)
                if a is None or b is None:
                    bad += 1
                    break
                body = " ".join(lines[i + 1:])
                body = re.sub(r"<[^>]+>", "", body)  # voice/class/timestamp tags
                body = html.unescape(re.sub(r"\s+", " ", body)).strip()
                if body:
                    cues.append((a, b, body))
                break
    cues.sort(key=lambda c: c[0])
    spans, used = [], {}
    for start, end, body in cues:
        # auto-captions repeat rolling text: drop exact repeats of the previous cue
        if spans and norm(body) == spans[-1]["norm"]:
            continue
        base = "t" + hms(start)
        used[base] = used.get(base, 0) + 1
        sid = base if used[base] == 1 else f"{base}.{used[base]}"
        extra = {"start": start, "end": end, "segment_window": f"w{int(start // 60):04d}"}
        if url:
            link = video_link(url, start)
            if link:
                extra["link"] = link
        spans.append(rec(sid, extra["segment_window"], hms(start), "cue", body, **extra))
    chapters = []
    notes = "\n".join(l for l in txt.split("\n") if l.startswith("NOTE"))
    for m in re.finditer(r"(\d{1,2}:\d{2}(?::\d{2})?)\s+([^,\n]+)", notes):
        t = parse_clock(m.group(1))
        if t is not None:
            chapters.append({"title": m.group(2).strip().rstrip(".").strip(), "locator": hms(t), "depth": 1})
    write(outdir, spans, chapters, {"cues": len(cues), "bad_timestamps": bad})
    return spans, chapters


def write(outdir, spans, outline, report=None):
    os.makedirs(outdir, exist_ok=True)
    ids = [s["id"] for s in spans]
    assert len(ids) == len(set(ids)), "duplicate span ids"
    with open(os.path.join(outdir, "spans.jsonl"), "w") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    json.dump(outline, open(os.path.join(outdir, "outline.json"), "w"), indent=1, ensure_ascii=False)
    if report is not None:
        json.dump(report, open(os.path.join(outdir, "report.json"), "w"), indent=1)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    kind, src, out = sys.argv[1:4]
    try:
        sp, ol = (epub(src, out) if kind == "epub" else vtt(src, out, sys.argv[4] if len(sys.argv) > 4 else None))
    except (SourceError, OSError, UnicodeError) as e:
        sys.exit(f"{kind}: {e}")
    print(json.dumps({"spans": len(sp), "outline": ol}, ensure_ascii=False, indent=1))
    for s in sp:
        print(f'{s["id"]:<14} {s["locator"]:<50} {s.get("page") or ""} {s["text"][:50]}')
