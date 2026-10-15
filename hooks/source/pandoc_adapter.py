"""Pandoc and document adapter (increment 6): EPUB, Markdown, Jupyter notebook, DOCX.

Emits the standard store schema:
- source.json, spans.jsonl, outline.json, segments/, units.json, labels.json, assets/, report.json, .gitignore.
Satisfies the adapter contract (contract.py).
"""
import hashlib
import html
import json
import os
import posixpath
import re
import subprocess
import sys
import urllib.parse
import zipfile
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from srcnorm import display_safe, neutralize, norm  # noqa: E402
import store as st  # noqa: E402
import units as sunits  # noqa: E402
from hidden import is_hidden, parse_css  # noqa: E402

MAX_ENTRY = 64 * 1024 * 1024     # one decompressed zip entry: 64 MB
MAX_TOTAL = 512 * 1024 * 1024    # all entries read: 512 MB
MAX_RATIO = 200                  # compression ratio beyond this is a bomb


class SourceError(Exception):
    pass


def rec(sid, segment, locator, kind, text, **extra):
    locator = st.clean_meta(locator, 200)
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
        self.stack = []          # [tag, hidden_or_skip, attrs]
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
            self.items.append((kind, t, self.buf_page, self.buf_id, tag, list(self.ids_seen)))
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


def parse_epub(path):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise SourceError(f"not an EPUB (zip): {e}")
    names = set(z.namelist())
    total = [0]
    if "META-INF/encryption.xml" in names:
        enc = zread(z, "META-INF/encryption.xml", total)
        algs = re.findall(r'EncryptionMethod[^>]*Algorithm="([^"]+)"', enc)
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
    rules = []
    for href in manifest.values():
        if href.lower().endswith(".css") and posixpath.join(base, href) in names:
            rules += parse_css(zread(z, posixpath.join(base, href), total))

    nav_href = re.search(r'<item\b[^>]*properties="[^"]*\bnav\b[^"]*"[^>]*>', opf)
    nav_href = re.search(r'\bhref="([^"]+)"', nav_href.group(0)).group(1) if nav_href else None
    raw_outline = []
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
                raw_outline.append({
                    "title": html.unescape(re.sub(r"<[^>]+>", "", tok.group(2))).strip(),
                    "href": posixpath.normpath(posixpath.join(posixpath.dirname(nav_href), urllib.parse.unquote(tok.group(1)))),
                    "depth": max(1, depth)
                })

    targets = {}
    for i, o in enumerate(raw_outline):
        f, _, anc = o["href"].partition("#")
        targets.setdefault((f, anc), i)

    # Extract chapters and items
    chapter_items = []
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
        chapter_items.append((href, p.items))
        for _, t, _, _, _, _ in p.items:
            captured += len(t)

    title_match = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.I | re.S)
    book_title = html.unescape(title_match.group(1).strip()) if title_match else "EPUB Book"
    return book_title, raw_outline, targets, chapter_items, total_chars, captured, hidden_chars


# ------------------------------------------------------------------ Markdown / Notebook / DOCX
def parse_markdown(text, default_title="Document"):
    lines = text.split("\n")
    blocks = []  # (kind, level, text)
    cur_head = default_title
    cur_tag = "para"
    buf = []
    in_code = False
    title = default_title

    for ln in lines:
        if ln.startswith("```"):
            if in_code:
                buf.append(ln)
                blocks.append(("code", 0, "\n".join(buf).strip()))
                buf, in_code = [], False
            else:
                if buf:
                    blocks.append((cur_tag, 0, " ".join(buf).strip()))
                    buf = []
                in_code = True
                buf.append(ln)
            continue
        if in_code:
            buf.append(ln)
            continue

        m_head = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m_head:
            if buf:
                blocks.append((cur_tag, 0, " ".join(buf).strip()))
                buf = []
            lvl = len(m_head.group(1))
            htxt = m_head.group(2).strip()
            if lvl == 1 and title == default_title:
                title = htxt
            blocks.append(("heading", lvl, htxt))
            continue

        if re.match(r"^(\*|-|\+|\d+\.)\s+", ln):
            if buf and cur_tag != "list":
                blocks.append((cur_tag, 0, " ".join(buf).strip()))
                buf = []
            cur_tag = "list"
            buf.append(re.sub(r"^(\*|-|\+|\d+\.)\s+", "", ln).strip())
            continue

        if not ln.strip():
            if buf:
                blocks.append((cur_tag, 0, " ".join(buf).strip()))
                buf = []
            cur_tag = "para"
            continue

        buf.append(ln.strip())

    if buf:
        blocks.append(("code" if in_code else cur_tag, 0, ("\n" if in_code else " ").join(buf).strip()))

    return title, [b for b in blocks if b[2]]


def parse_notebook(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        nb = json.load(f)
    cells = nb.get("cells", [])
    blocks = []
    doc_title = os.path.splitext(os.path.basename(path))[0]

    for idx, cell in enumerate(cells, 1):
        ctype = cell.get("cell_type", "")
        src = "".join(cell.get("source", []))
        if ctype == "markdown":
            _, sub_blocks = parse_markdown(src, default_title=f"Cell {idx}")
            for k, lvl, txt in sub_blocks:
                blocks.append((k, lvl, f"[Cell {idx}] {txt}"))
        elif ctype == "code":
            if src.strip():
                blocks.append(("code", 0, f"[Cell {idx}] ```python\n{src.strip()}\n```"))
            for out in cell.get("outputs", []):
                otext = "".join(out.get("text", []))
                if not otext and "data" in out:
                    otext = "".join(out["data"].get("text/plain", []))
                if otext.strip():
                    blocks.append(("para", 0, f"[Cell {idx} output] {otext.strip()[:1000]}"))

    return doc_title, blocks


def convert_docx(path):
    # Try pandoc first, then textutil
    cmd = ["pandoc", path, "-t", "markdown"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", path], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass

    raise SourceError("cannot convert DOCX (neither pandoc nor textutil succeeded)")


# ------------------------------------------------------------------ Adapter Build
def build(source_path, build_dir, opts=None, cache_dir=None, log=print):
    opts = opts or {}
    os.makedirs(build_dir, exist_ok=True)
    ext = os.path.splitext(source_path)[1].lower()

    with open(source_path, "rb") as f:
        content_bytes = f.read()
    sha = hashlib.sha256(content_bytes).hexdigest()
    origin = os.path.abspath(source_path)

    spans_data = []
    outline_data = []
    segments_data = []
    total_chars = captured_chars = hidden_chars = 0

    if ext == ".epub":
        title, raw_outline, targets, chapter_items, total_chars, captured_chars, hidden_chars = parse_epub(source_path)
        title = st.clean_meta(title, 120)
        seg_idx = 0
        span_k = 0
        path = []
        head = None
        para = 0

        # Build outline structure
        for i, o in enumerate(raw_outline):
            outline_data.append({
                "id": f"o{i+1:03d}",
                "title": st.clean_meta(o["title"], 120),
                "depth": o["depth"],
                "start_span": None
            })

        for href, items in chapter_items:
            first = True
            for kind, t, page, eid, tag, ids in items:
                hit = None
                for cand in ([""] if first else []) + ids:
                    if (href, cand) in targets:
                        hit = targets[(href, cand)]
                first = False

                if hit is not None or seg_idx == 0:
                    if hit is not None and hit < len(raw_outline):
                        o = raw_outline[hit]
                        path = path[:o["depth"] - 1] + [o["title"]]
                    elif not path:
                        path = [t if kind == "heading" else href]
                    seg_idx += 1
                    span_k = 0
                    para = 0
                    head = None
                    cur_seg_id = f"s{seg_idx:04d}"
                    cur_seg_title = path[-1] if path else f"Chapter {seg_idx}"
                    segments_data.append((cur_seg_id, cur_seg_title, []))
                elif kind == "heading" and not raw_outline:
                    path, head, para = [t], None, 0
                elif kind == "heading":
                    head, para = t, 0

                if kind != "heading":
                    para += 1
                span_k += 1

                sub = st.clean_meta(head or (path[-1] if len(path) > 1 else None), 120)
                loc = st.clean_meta(path[0] if path else title, 120)
                if sub and sub != loc:
                    loc += f" § {sub}"
                if kind != "heading":
                    loc += f" ¶{para}"
                if page:
                    loc = f"p. {page} ({loc})"

                cur_seg = segments_data[-1]
                sid = f"{cur_seg[0]}.{span_k}"
                s_rec = rec(sid, cur_seg[0], loc, kind, t, page=page)
                cur_seg[2].append(s_rec)
                spans_data.append(s_rec)

                if hit is not None and hit < len(outline_data) and outline_data[hit]["start_span"] is None:
                    outline_data[hit]["start_span"] = sid

        if not outline_data and segments_data:
            outline_data.append({
                "id": "o001",
                "title": title[:120],
                "depth": 1,
                "start_span": spans_data[0]["id"] if spans_data else None
            })

    else:
        # Markdown, Notebook, or DOCX
        if ext == ".ipynb":
            title, blocks = parse_notebook(source_path)
        elif ext in (".docx", ".doc"):
            md_text = convert_docx(source_path)
            title, blocks = parse_markdown(md_text, default_title=os.path.splitext(os.path.basename(source_path))[0])
        else:
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                md_text = f.read()
            title, blocks = parse_markdown(md_text, default_title=os.path.splitext(os.path.basename(source_path))[0])

        title = st.clean_meta(title, 120)
        total_chars = captured_chars = sum(len(b[2]) for b in blocks)
        seg_idx = 1
        cur_seg_id = f"s{seg_idx:04d}"
        cur_seg_title = title
        cur_seg_spans = []
        cur_heading = title
        outline_id_idx = 1
        para_count = 0

        outline_data.append({
            "id": f"o{outline_id_idx:03d}",
            "title": cur_seg_title,
            "depth": 1,
            "start_span": None
        })
        outline_id_idx += 1

        for kind, level, text in blocks:
            if kind == "heading":
                cur_heading = st.clean_meta(text, 120)
                para_count = 0
                if len(cur_seg_spans) >= 3 and level <= 2:
                    segments_data.append((cur_seg_id, cur_seg_title, cur_seg_spans))
                    seg_idx += 1
                    cur_seg_id = f"s{seg_idx:04d}"
                    cur_seg_title = cur_heading
                    cur_seg_spans = []

                oid = f"o{outline_id_idx:03d}"
                outline_id_idx += 1
                outline_data.append({
                    "id": oid,
                    "title": cur_heading,
                    "depth": min(3, max(1, level)),
                    "start_span": None
                })
                continue

            para_count += 1
            span_k = len(cur_seg_spans) + 1
            sid = f"{cur_seg_id}.{span_k}"
            loc = f"{title} § {cur_heading} ¶{para_count}" if cur_heading != title else f"{title} ¶{para_count}"
            s_rec = rec(sid, cur_seg_id, loc, kind, text)
            cur_seg_spans.append(s_rec)
            spans_data.append(s_rec)

            if outline_data and outline_data[-1]["start_span"] is None:
                outline_data[-1]["start_span"] = sid

            if len(cur_seg_spans) >= 25:
                segments_data.append((cur_seg_id, cur_seg_title, cur_seg_spans))
                seg_idx += 1
                cur_seg_id = f"s{seg_idx:04d}"
                cur_seg_title = cur_heading
                cur_seg_spans = []

        if cur_seg_spans:
            segments_data.append((cur_seg_id, cur_seg_title, cur_seg_spans))

    if not spans_data:
        raise SourceError(f"no extractable text found in {source_path}")

    if outline_data and outline_data[0]["start_span"] is None:
        outline_data[0]["start_span"] = spans_data[0]["id"]

    # Write segments directory
    seg_dir = os.path.join(build_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    seg_index = []

    for seg_id, s_title, s_spans in segments_data:
        if not s_spans:
            continue
        path = os.path.join(seg_dir, f"{seg_id}.md")
        lines = [f"[{s['id']}] " + s["text"].replace("\n", " ⏎ ") for s in s_spans]
        st.atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
        seg_toks = sum(len(s["text"]) // 4 for s in s_spans)
        seg_index.append({
            "id": seg_id,
            "locator": st.clean_meta(s_title, 120),
            "kind": "text",
            "tokens": seg_toks,
            "has_figures": False,
            "needs_vision": False,
            "spans": len(s_spans)
        })

    st.write_json(os.path.join(seg_dir, "index.json"), seg_index)

    # Write spans.jsonl
    with open(os.path.join(build_dir, "spans.jsonl"), "w", encoding="utf-8") as f:
        for s in spans_data:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # Units & Outline
    segments_summary = [(s["id"], s["locator"]) for s in seg_index]
    units_list, final_outline = sunits.build_units(spans_data, outline_data, {}, segments_summary)

    st.write_json(os.path.join(build_dir, "outline.json"), final_outline)
    st.write_json(os.path.join(build_dir, "units.json"), units_list)

    # Labels & Assets
    labels_list = [{"pdf_index": i, "label": str(i), "label_source": "synthetic"} for i in range(1, len(seg_index) + 1)]
    labels_data = {
        "pages": labels_list,
        "label_to_pdf": {str(i): [i] for i in range(1, len(seg_index) + 1)}
    }
    st.write_json(os.path.join(build_dir, "labels.json"), labels_data)

    assets_dir = os.path.join(build_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    st.write_json(os.path.join(assets_dir, "index.json"), [])

    # Report & Source metadata
    report_data = {
        "body_chars": total_chars or captured_chars,
        "captured_chars": captured_chars,
        "hidden_chars_dropped": hidden_chars,
        "spans": len(spans_data),
        "segments": len(seg_index),
        "units": len(units_list),
        "capture_ratio": round(captured_chars / max(total_chars, 1), 3),
        "validation": {"sampled": 0, "mismatched": [], "mismatch_rate": 0.0}
    }
    st.write_json(os.path.join(build_dir, "report.json"), report_data)

    source_meta = {
        "type": ext.lstrip(".").lower() or "doc",
        "origin": origin,
        "title": title,
        "adapter": "pandoc",
        "ingest_version": 1,
        "sha256": sha
    }
    st.write_json(os.path.join(build_dir, "source.json"), source_meta)

    with open(os.path.join(build_dir, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("# Schola source store: personal copies of copyrighted sources. Never commit.\n*\n")

    return report_data
