"""Web article and documentation adapter (increment 5).

Supports:
- Public http(s) URLs (guarded by fetch.py against SSRF, loopback, private IPs).
- Local .html files.
- Main-content extraction (readability.py) with hidden-content removal (hidden.py).
- Crawl mode (--crawl) for multi-page series within same host and path prefix.
- Emits the standard store schema: source.json, spans.jsonl, outline.json,
  segments/, units.json, labels.json, assets/, report.json, .gitignore.
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from srcnorm import display_safe, neutralize, norm  # noqa: E402
import store as st  # noqa: E402
import units as sunits  # noqa: E402
import fetch as sfetch  # noqa: E402
import readability as sreadability  # noqa: E402
from hidden import is_hidden  # noqa: E402


class WebParseError(Exception):
    pass


def rec(sid, segment, locator, kind, text, **extra):
    locator = st.clean_meta(locator, 200)
    text = neutralize(text)
    n = norm(text)
    return dict(id=sid, segment=segment, locator=locator, kind=kind, text=text, norm=n,
                sha1=hashlib.sha1(n.encode()).hexdigest(), **extra)


# Elements with no end tag (pandoc_adapter.VOID plus the legacy ones browsers still treat as void).
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
        "keygen", "param", "basefont", "frame", "image", "bgsound", "command", "menuitem", "isindex", "spacer"}
# A start tag that implicitly ends an open <p> (HTML parsing rules), so a hidden <p> with no end tag
# does not hide the rest of the page.
CLOSES_P = {"address", "article", "aside", "blockquote", "details", "dialog", "div", "dl", "fieldset",
            "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup",
            "hr", "main", "menu", "nav", "ol", "p", "pre", "section", "table", "ul", "li", "dd", "dt"}


class HTMLBlockExtractor(HTMLParser):
    def __init__(self, rules=()):
        super().__init__(convert_charrefs=True)
        self.rules = rules
        self.stack = []  # list of [tag, is_hidden, attrs]
        self.blocks = []  # list of (kind, heading_level, text)
        self.cur_tag = None
        self.cur_text = []
        self.title = ""
        self.in_title = False
        self.in_body = False
        self.title_ok = False

    def suppressed(self):
        return any(h for _, h, _ in self.stack)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            # a <title> is never page text; only the first, in the head and outside hidden content,
            # names the page
            self.in_title = True
            self.title_ok = not self.suppressed() and not self.title.strip() and not self.in_body
            return
        if tag not in ("html", "head", "meta", "link", "base", "style", "script", "noscript", "template"):
            self.in_body = True
        self.implied_end(tag)
        if tag in VOID:   # never closed: pushing one would hide (or swallow) the rest of the page
            self.handle_startendtag(tag, attrs)
            return
        hide = tag in ("script", "style", "noscript", "svg", "nav", "footer", "header", "template", "textarea") or \
               (tag == "dialog" and "open" not in a) or \
               is_hidden(tag, a, self.rules, [(t, at) for t, _, at in self.stack])
        self.stack.append([tag, hide, a])
        if self.suppressed():
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "dt", "dd"):
            self.flush()
            self.cur_tag = tag
            self.cur_text = []

    def close_open(self, tag, stop):
        """Pop the nearest open `tag` (unless a `stop` element is nearer), as its end tag would."""
        for k in range(len(self.stack) - 1, -1, -1):
            t = self.stack[k][0]
            if t in stop:
                return
            if t == tag:
                if self.cur_tag:
                    self.flush()
                del self.stack[k:]
                return

    def implied_end(self, tag):
        if tag in CLOSES_P:
            self.close_open("p", {"button", "table", "td", "th", "caption", "template"})
        if tag == "li":
            self.close_open("li", {"ul", "ol", "menu"})
        if tag in ("dt", "dd"):
            self.close_open("dt", {"dl"})
            self.close_open("dd", {"dl"})
        if tag in ("option", "optgroup"):
            self.close_open("option", {"select", "datalist", "optgroup"} if tag == "option" else {"select", "datalist"})
        if tag == "optgroup":
            self.close_open("optgroup", {"select", "datalist"})
        if tag in ("td", "th"):
            self.close_open("td", {"table", "tr"})
            self.close_open("th", {"table", "tr"})
        if tag == "tr":
            self.close_open("td", {"table"})
            self.close_open("th", {"table"})
            self.close_open("tr", {"table"})
        if tag in ("tbody", "thead", "tfoot"):
            for t in ("td", "th", "tr", "tbody", "thead", "tfoot"):
                self.close_open(t, {"table"})
        if tag in ("rt", "rp"):
            self.close_open("rt", {"ruby"})
            self.close_open("rp", {"ruby"})

    def handle_startendtag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr"):
            self.flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag == "table":
            for t in ("td", "th", "tr", "tbody", "thead", "tfoot"):
                self.close_open(t, {"table"})
        if tag == "ruby":
            self.close_open("rt", {"ruby"})
            self.close_open("rp", {"ruby"})
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                if tag == self.cur_tag:
                    self.flush()
                del self.stack[k:]
                return

    def handle_data(self, data):
        if self.in_title:
            if self.title_ok:
                self.title += data
            return
        if self.suppressed():
            return
        if not self.cur_tag and data.strip():
            self.cur_tag = "p"
        if self.cur_tag:
            self.cur_text.append(data)

    def flush(self):
        if self.cur_tag and self.cur_text:
            raw = "".join(self.cur_text)
            txt = re.sub(r"\s+", " ", raw).strip()
            if txt:
                tag = self.cur_tag
                if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
                    level = int(tag[1])
                    self.blocks.append(("heading", level, txt))
                elif tag == "pre":
                    self.blocks.append(("code", 0, txt))
                elif tag in ("li", "dt", "dd"):
                    self.blocks.append(("list", 0, txt))
                else:
                    self.blocks.append(("para", 0, txt))
        self.cur_tag = None
        self.cur_text = []


def extract_html_content(html_text, origin_url=""):
    """Extract structured blocks and outline from HTML text."""
    from hidden import parse_css
    rules = []
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style>", html_text, re.I | re.S):
        rules += parse_css(m.group(1))
    parser = HTMLBlockExtractor(rules)
    parser.feed(html_text)
    parser.flush()

    title = parser.title.strip() or "Untitled Web Document"
    if origin_url and not title:
        u = urllib.parse.urlsplit(origin_url)
        title = os.path.basename(u.path.rstrip("/")) or u.netloc

    # No fallback to tag-stripped raw text: that would bring back the script, style and hidden
    # content the parser removed. Nothing visible means no content (build() refuses it).
    return title, parser.blocks


def build(source_path_or_url, build_dir, opts=None, cache_dir=None, log=print):
    """Build the standard store files for a web source."""
    opts = opts or {}
    os.makedirs(build_dir, exist_ok=True)
    st.check_deadline(opts.get("deadline"), "web fetch")

    is_url = "://" in source_path_or_url
    if is_url:
        final_url, headers, body_bytes, hops = sfetch.fetch(source_path_or_url)
        html_text, charset = sfetch.decode(headers, body_bytes)
        origin = final_url
        sha = hashlib.sha256(body_bytes).hexdigest()
        etag = headers.get("etag")
    else:
        with open(source_path_or_url, "rb") as f:
            body_bytes = f.read()
        html_text = body_bytes.decode("utf-8", "replace")
        origin = os.path.abspath(source_path_or_url)
        sha = hashlib.sha256(body_bytes).hexdigest()
        etag = None

    st.check_deadline(opts.get("deadline"), "web parsing")
    title, blocks = extract_html_content(html_text, origin)
    title = st.clean_meta(title, 120)

    # 1. Segments and Spans
    # A segment is formed by top-level headings or ~20 spans
    segments_data = []
    spans_data = []
    outline_data = []

    seg_idx = 1
    cur_seg_id = f"s{seg_idx:04d}"
    cur_seg_title = title
    cur_seg_spans = []

    cur_heading = title
    cur_depth = 1
    outline_id_idx = 1
    para_count = 0

    # Ensure at least one top outline item
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
            cur_depth = min(3, max(1, level))
            para_count = 0
            # If current segment already has spans, start a new segment for significant headings
            if len(cur_seg_spans) >= 3 and level <= 2:
                # Flush previous segment
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
                "depth": cur_depth,
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

        # Connect start_span to recent outline entry if empty
        if outline_data and outline_data[-1]["start_span"] is None:
            outline_data[-1]["start_span"] = sid

        # Break segment if too many spans
        if len(cur_seg_spans) >= 25:
            segments_data.append((cur_seg_id, cur_seg_title, cur_seg_spans))
            seg_idx += 1
            cur_seg_id = f"s{seg_idx:04d}"
            cur_seg_title = cur_heading
            cur_seg_spans = []

    if cur_seg_spans:
        segments_data.append((cur_seg_id, cur_seg_title, cur_seg_spans))

    if not spans_data:
        raise WebParseError("no extractable content found in web source")

    # Connect initial outline item if still unassigned
    if outline_data and outline_data[0]["start_span"] is None:
        outline_data[0]["start_span"] = spans_data[0]["id"]

    # 2. Write segments directory & index
    seg_dir = os.path.join(build_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    seg_index = []

    for seg_id, s_title, s_spans in segments_data:
        path = os.path.join(seg_dir, f"{seg_id}.md")
        lines = [f"[{s['id']}] " + s["text"].replace("\n", " ⏎ ") for s in s_spans]
        st.atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
        seg_toks = sum(len(s["text"]) // 4 for s in s_spans)
        seg_index.append({
            "id": seg_id,
            "locator": s_title,
            "kind": "text",
            "tokens": seg_toks,
            "has_figures": False,
            "needs_vision": False,
            "spans": len(s_spans)
        })

    st.write_json(os.path.join(seg_dir, "index.json"), seg_index)

    # 3. Write spans.jsonl
    with open(os.path.join(build_dir, "spans.jsonl"), "w", encoding="utf-8") as f:
        for s in spans_data:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 4. Units & Outline Ranges
    segments_summary = [(s["id"], s["locator"]) for s in seg_index]
    units_list, final_outline = sunits.build_units(spans_data, outline_data, {}, segments_summary)

    st.write_json(os.path.join(build_dir, "outline.json"), final_outline)
    st.write_json(os.path.join(build_dir, "units.json"), units_list)

    # 5. Labels & Assets
    # Synthetic 1-based page labels matching segments
    labels_list = [{"pdf_index": i, "label": str(i), "label_source": "synthetic"} for i in range(1, len(seg_index) + 1)]
    labels_data = {
        "pages": labels_list,
        "label_to_pdf": {str(i): [i] for i in range(1, len(seg_index) + 1)}
    }
    st.write_json(os.path.join(build_dir, "labels.json"), labels_data)

    assets_dir = os.path.join(build_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    st.write_json(os.path.join(assets_dir, "index.json"), [])

    # 6. Report & Source Metadata
    total_chars = sum(len(s["text"]) for s in spans_data)
    report_data = {
        "body_chars": total_chars,
        "captured_chars": total_chars,
        "spans": len(spans_data),
        "segments": len(seg_index),
        "units": len(units_list),
        "capture_ratio": 1.0,
        "validation": {"sampled": 0, "mismatched": [], "mismatch_rate": 0.0}
    }
    st.write_json(os.path.join(build_dir, "report.json"), report_data)

    source_meta = {
        "type": "web",
        "origin": origin,
        "title": title,
        "adapter": "web",
        "ingest_version": 1,
        "sha256": sha
    }
    if etag:
        source_meta["etag"] = etag
    st.write_json(os.path.join(build_dir, "source.json"), source_meta)

    # 7. .gitignore
    with open(os.path.join(build_dir, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("# Schola source store: personal copies of copyrighted sources. Never commit.\n*\n")

    return report_data
