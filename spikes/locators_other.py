"""Spike 0b: locators + span ids for EPUB and WebVTT captions.

usage: python3 spikes/locators_other.py epub <file.epub> <outdir>
       python3 spikes/locators_other.py vtt  <file.vtt>  <outdir> [youtube-url]

Writes spans.jsonl + outline.json into <outdir> and prints a summary.
EPUB locator: "<chapter> § <heading> ¶n" plus "p. N" when the publisher placed
epub:type="pagebreak" markers. VTT locator: hh:mm:ss of the cue start.
"""
import hashlib
import html
import json
import os
import posixpath
import re
import sys
import zipfile
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srcnorm import norm  # noqa: E402


def rec(sid, segment, locator, kind, text, **extra):
    n = norm(text)
    return dict(id=sid, segment=segment, locator=locator, kind=kind, text=text, norm=n,
                sha1=hashlib.sha1(n.encode()).hexdigest(), **extra)


# ------------------------------------------------------------------ EPUB
class ChapterParser(HTMLParser):
    BLOCKS = {"p", "li", "pre", "blockquote", "figcaption", "td", "th", "dt", "dd"}
    HEADS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []  # (kind, text, page, element_id)
        self.buf, self.cur, self.depth = [], None, 0
        self.page = None
        self.skip = 0
        self.last_id = None
        self.ids_seen = []  # element ids met since the previous item (nav targets)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if a.get("id"):
            self.last_id = a["id"]
            self.ids_seen.append(a["id"])
        et = a.get("epub:type", "") or a.get("role", "")
        if "pagebreak" in et or a.get("role") == "doc-pagebreak":
            self.page = a.get("title") or a.get("aria-label") or re.sub(r"\D", "", a.get("id", "")) or self.page
        if tag in self.HEADS or tag in self.BLOCKS:
            if self.cur is None:
                self.cur, self.buf, self.depth = tag, [], 0
                self.cur_id = a.get("id") or self.last_id
            if tag == self.cur:
                self.depth += 1
        if tag == "br" and self.cur:
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if self.cur and tag == self.cur:
            self.depth -= 1
            if self.depth == 0:
                t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
                if t:
                    kind = "heading" if tag in self.HEADS else {"li": "list", "pre": "code", "figcaption": "caption"}.get(tag, "para")
                    self.items.append((kind, t, self.page, self.cur_id, tag, self.ids_seen))
                    self.ids_seen = []
                self.cur = None

    def handle_data(self, data):
        if self.cur and not self.skip:
            self.buf.append(data)


def epub(path, outdir):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    if "META-INF/encryption.xml" in names:
        enc = z.read("META-INF/encryption.xml").decode("utf-8", "replace")
        algs = re.findall(r'EncryptionMethod[^>]*Algorithm="([^"]+)"', enc)
        # font obfuscation (IDPF / Adobe) is not DRM; anything else is
        if any(a not in ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC") for a in algs):
            sys.exit("EPUB is DRM-protected (META-INF/encryption.xml); cannot ingest")
    cont = z.read("META-INF/container.xml").decode()
    opf_path = re.search(r'full-path="([^"]+)"', cont).group(1)
    opf = z.read(opf_path).decode("utf-8", "replace")
    base = posixpath.dirname(opf_path)
    manifest = {m.group(1): m.group(2) for m in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf)}
    manifest.update({m.group(2): m.group(1) for m in re.finditer(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf)})
    spine = [manifest[i] for i in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf) if i in manifest]
    # nav toc (EPUB3) -> titles per file#anchor
    nav_href = re.search(r'<item\b[^>]*properties="[^"]*nav[^"]*"[^>]*href="([^"]+)"', opf) or \
        re.search(r'<item\b[^>]*href="([^"]+)"[^>]*properties="[^"]*nav', opf)
    outline = []
    if nav_href:
        nav = z.read(posixpath.join(base, nav_href.group(1))).decode("utf-8", "replace")
        tocm = re.search(r'<nav[^>]*epub:type="toc".*?</nav>', nav, re.S)
        depth = 0
        for tok in re.finditer(r"<(/?)ol\b|<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", tocm.group(0) if tocm else "", re.S):
            if tok.group(1) is not None and tok.group(0).startswith("<"):
                if tok.group(0).startswith("</ol"):
                    depth -= 1
                elif tok.group(0).startswith("<ol"):
                    depth += 1
            if tok.group(2):
                outline.append({"title": html.unescape(re.sub(r"<[^>]+>", "", tok.group(3))).strip(),
                                "href": posixpath.normpath(posixpath.join(posixpath.dirname(nav_href.group(1)), tok.group(2))),
                                "depth": depth})
    # nav targets relative to the OPF dir: (file, anchor) -> outline index
    nav_dir = posixpath.dirname(nav_href.group(1)) if nav_href else ""
    targets = {}
    for i, o in enumerate(outline):
        f, _, anc = o["href"].partition("#")
        targets.setdefault((f, anc), i)
    spans, ch, k, para = [], 0, 0, 0
    path, head = [], None  # current nav path (titles by depth), sub-heading
    for href in spine:
        full = posixpath.join(base, href)
        if full not in names or (nav_href and href == nav_href.group(1)):
            continue
        p = ChapterParser()
        p.feed(z.read(full).decode("utf-8", "replace"))
        file_key = posixpath.normpath(posixpath.join(nav_dir, posixpath.relpath(href, nav_dir or ".")))
        first = True
        for kind, t, page, eid, tag, ids in p.items:
            hit = None
            for cand in ([""] if first else []) + ids:
                if (href, cand) in targets or (file_key, cand) in targets:
                    hit = targets.get((href, cand), targets.get((file_key, cand)))
            first = False
            if hit is not None or ch == 0:
                if hit is not None:
                    o = outline[hit]
                    path = path[:max(0, o["depth"] - 1)] + [o["title"]]
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
            loc = " › ".join(path)
            if head:
                loc += f" § {head}"
            if kind != "heading":
                loc += f" ¶{para}"
            spans.append(rec(f"ch{ch:02d}.{k}", f"ch{ch:02d}", loc, kind, t, page=page,
                             href=f"{href}#{eid}" if eid else href))
    write(outdir, spans, outline)
    return spans, outline


# ------------------------------------------------------------------ VTT
TS = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"


def secs(m, off):
    h, mi, s, ms = (m.group(off + i) for i in range(4))
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def hms(t):
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def vtt(path, outdir, url=None):
    txt = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", txt):
        lines = block.strip("\n").split("\n")
        for i, ln in enumerate(lines):
            m = re.match(TS + r"\s*-->\s*" + TS, ln.strip())
            if m:
                body = " ".join(lines[i + 1:])
                body = re.sub(r"<[^>]+>", "", body)  # voice/class/timestamp tags
                body = html.unescape(re.sub(r"\s+", " ", body)).strip()
                if body:
                    cues.append((secs(m, 1), secs(m, 5), body))
                break
    cues.sort(key=lambda c: c[0])
    # auto-captions repeat rolling text; drop cues whose text is a prefix-dup of previous
    spans, used = [], {}
    for start, end, body in cues:
        if spans and norm(body) == spans[-1]["norm"]:
            continue
        base = "t" + hms(start)
        used[base] = used.get(base, 0) + 1
        sid = base if used[base] == 1 else f"{base}.{used[base]}"
        loc = hms(start)
        extra = {"start": start, "end": end, "segment_window": f"w{int(start // 60):04d}"}
        if url:
            extra["link"] = f"{url}{'&' if '?' in url else '?'}t={int(start)}s"
        spans.append(rec(sid, extra["segment_window"], loc, "cue", body, **extra))
    chapters = []
    for m in re.finditer(r"(\d{1,2}:\d{2}(?::\d{2})?)\s+([A-Z][^,\n]*)", "\n".join(l for l in txt.split("\n") if l.startswith("NOTE"))):
        chapters.append({"title": m.group(2).strip(), "locator": m.group(1), "depth": 1})
    write(outdir, spans, chapters)
    return spans, chapters


def write(outdir, spans, outline):
    os.makedirs(outdir, exist_ok=True)
    ids = [s["id"] for s in spans]
    assert len(ids) == len(set(ids)), "duplicate span ids"
    with open(os.path.join(outdir, "spans.jsonl"), "w") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    json.dump(outline, open(os.path.join(outdir, "outline.json"), "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    kind, src, out = sys.argv[1:4]
    sp, ol = (epub(src, out) if kind == "epub" else vtt(src, out, sys.argv[4] if len(sys.argv) > 4 else None))
    print(json.dumps({"spans": len(sp), "outline": ol}, ensure_ascii=False, indent=1))
    for s in sp:
        print(f'{s["id"]:<14} {s["locator"]:<50} {s.get("page") or ""} {s["text"][:50]}')
