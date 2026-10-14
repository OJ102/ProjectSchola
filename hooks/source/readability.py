"""Stdlib main-content extraction for articles (spike 0e).

usage: python3 spikes/readability.py <page.html> <out.md> [--url URL]

Builds a light DOM with html.parser, removes boilerplate (scripts, nav,
aside, footer, forms, share/comment/subscribe/related blocks by class or id),
then picks the main content:
  1. the single <article> (or <main>, or [role=main]) holding most of the text,
  2. else the best-scoring container (Arc90-style: paragraph text length and
     commas credited to parent and grandparent, penalized by link density).
The chosen subtree is serialized back to HTML and converted with pandoc to
GitHub Markdown. Heading ids are kept for URL#anchor locators.
Prints a JSON report: title, words, headings, method, and js_required when
the page is a script shell with almost no text (fail cleanly, no guessing).
Hidden elements (hidden attribute, aria-hidden, display:none, visibility,
font-size:0, opacity:0, same-colour text, off-screen; inline or via the page's
<style> rules) are dropped; screen-reader-only text is kept (decision 11).
Exit 3 = needs JavaScript, exit 4 = too slow (30 s wall clock).
"""
import html
import json
import os
import re
import signal
import subprocess
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidden import is_hidden, parse_css  # noqa: E402
from srcnorm import neutralize  # noqa: E402

TIME_LIMIT = 30  # seconds; past this the extractor exits with too_slow

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
DROP_TAGS = {"script", "style", "noscript", "svg", "form", "iframe", "button", "nav", "aside", "footer",
             "template", "select", "textarea", "canvas", "dialog", "object"}
NEG = re.compile(r"comment|share|sharing|social|related|sidebar|subscribe|newsletter|footer|\bnav|menu|"
                 r"promo|advert|\bads?\b|ad-|cookie|banner|popup|modal|signup|sign-up|paywall|breadcrumb|"
                 r"toolbar|masthead|byline|recommend|more-from|author-card|(?:entry|post|article)-meta|"
                 r"\btoc\b|table-of-contents|headerlink|post-ufi|like-button|billboard|skip", re.I)
POS = re.compile(r"article|content|entry|post-body|main|\bbody\b|story|prose|markdown|document", re.I)
UNWRAP = {"div", "span", "section", "font", "center", "header"}
MAX_DEPTH = 200
BLOCKY = {"p", "pre", "li", "blockquote", "td", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], None


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.cur = self.root
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        # depth cap: deeper elements become leaves, so recursion stays bounded
        if tag not in VOID and self.depth < MAX_DEPTH:
            self.cur = n
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            d, x = 0, self.cur
            while x is not n:
                x, d = x.parent, d + 1
            self.depth -= d + 1
            self.cur = n.parent

    def handle_data(self, data):
        t = Node("#text", [], self.cur)
        t.text = data
        self.cur.children.append(t)


def walk(n):
    stack = [n]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(reversed(x.children))


def text_of(n):
    return "".join(x.text for x in walk(n) if x.tag == "#text")


def link_text(n):
    return sum(len(text_of(x)) for x in walk(n) if x.tag == "a")


def find(n, pred):
    return [x for x in walk(n) if pred(x)]


def clean(n, rules=(), ancestors=()):
    keep = []
    for c in n.children:
        if c.tag == "#text":
            keep.append(c)
            continue
        cls = (c.attrs.get("class") or "") + " " + (c.attrs.get("id") or "")
        eid = c.attrs.get("id") or ""
        role = c.attrs.get("role") or ""
        cattrs = {k: v or "" for k, v in c.attrs.items()}
        hidden = is_hidden(c.tag, cattrs, rules, ancestors)
        if c.tag in DROP_TAGS or role in ("navigation", "complementary", "contentinfo", "banner", "dialog") or hidden:
            continue
        if c.tag == "header" and not find(c, lambda x: x.tag == "h1"):
            continue
        if c.tag not in ("article", "main", "body", "html") and (
                NEG.search(eid) or (NEG.search(cls) and not POS.search(cls))):
            continue
        clean(c, rules, ancestors + ((c.tag, cattrs),))
        keep.append(c)
    n.children = keep


def score_containers(body):
    scores = {}
    for p in find(body, lambda x: x.tag in ("p", "pre", "blockquote")):
        t = text_of(p).strip()
        if len(t) < 25:
            continue
        s = 1 + t.count(",") + min(len(t) // 100, 3)
        par, gp = p.parent, p.parent.parent if p.parent else None
        for node, w in ((par, 1.0), (gp, 0.5)):
            if node is None or node.tag in ("#root",):
                continue
            if id(node) not in scores:
                cls = (node.attrs.get("class") or "") + " " + (node.attrs.get("id") or "")
                base = 25 if POS.search(cls) else 0
                base -= 25 if NEG.search(cls) else 0
                scores[id(node)] = [node, base]
            scores[id(node)][1] += s * w
    best, bs = None, -1e9
    for node, s in scores.values():
        tl = len(text_of(node)) or 1
        s *= 1 - link_text(node) / tl
        if s > bs:
            best, bs = node, s
    return best


def serialize(n):
    """HTML for pandoc. Layout wrappers (div/span/section) are unwrapped so
    no raw HTML survives; a section's id moves onto its first heading so
    URL#anchor locators keep working."""
    if n.tag == "#text":
        return html.escape(n.text, quote=False)
    if n.tag in UNWRAP and n.attrs.get("id"):
        h = next((c for c in n.children if c.tag in ("h1", "h2", "h3", "h4", "h5", "h6")), None)
        if h is not None and not h.attrs.get("id"):
            h.attrs["id"] = n.attrs["id"]
    if n.tag in UNWRAP:
        return "".join(serialize(c) for c in n.children)
    attrs = "".join(f' {k}="{html.escape(v or "", quote=True)}"' for k, v in n.attrs.items()
                    if k in ("id", "href", "src", "alt", "title", "colspan", "rowspan", "class") and v is not None)
    inner = "".join(serialize(c) for c in n.children)
    if n.tag in VOID:
        return f"<{n.tag}{attrs}>"
    if n.tag.startswith("#"):
        return inner
    return f"<{n.tag}{attrs}>{inner}</{n.tag}>"


def meta(root, name):
    for m in find(root, lambda x: x.tag == "meta"):
        if (m.attrs.get("property") or m.attrs.get("name") or "").lower() == name:
            return (m.attrs.get("content") or "").strip()
    return None


def extract(raw):
    t = Tree()
    t.feed(raw)
    root = t.root
    title = meta(root, "og:title") or next((text_of(x).strip() for x in find(root, lambda x: x.tag == "title")), "")
    info = {"title": title, "author": meta(root, "author") or meta(root, "article:author"),
            "published": meta(root, "article:published_time") or meta(root, "date")}
    scripts = len(find(root, lambda x: x.tag == "script"))
    raw_words = len(text_of(root).split())
    body = next(iter(find(root, lambda x: x.tag == "body")), root)
    # the page's own <style> rules that hide content (decision 11)
    css = "".join(text_of(x) for x in find(root, lambda x: x.tag == "style"))
    clean(body, parse_css(css))
    words_all = len(text_of(body).split())
    method, node = None, None
    for tag in ("article", "main"):
        cands = find(body, lambda x, tag=tag: x.tag == tag or (tag == "main" and x.attrs.get("role") == "main"))
        cands = [c for c in cands if len(text_of(c).split()) > 0.25 * max(words_all, 1)]
        if cands:
            node = max(cands, key=lambda c: len(text_of(c)))
            method = tag
            break
    if node is None:
        node = score_containers(body)
        method = "scored"
    if node is None:
        node, method = body, "body"
    words = len(text_of(node).split())
    info.update({"method": method, "words": words, "page_words": raw_words, "scripts": scripts})
    if words < 150 and scripts >= 5:
        info["js_required"] = True
    return node, info


def main():
    a = sys.argv[1:]
    src, out = a[0], a[1]
    raw = open(src, encoding="utf-8", errors="replace").read()

    def too_slow(*_):
        print(json.dumps({"error": f"too_slow: extraction exceeded {TIME_LIMIT}s", "too_slow": True}))
        sys.exit(4)
    signal.signal(signal.SIGALRM, too_slow)
    signal.alarm(TIME_LIMIT)
    node, info = extract(raw)
    if info.get("js_required"):
        info["error"] = "page needs JavaScript; no main content in the HTML (use the lossy fallback)"
        print(json.dumps(info))
        sys.exit(3)
    h = serialize(node)
    md = subprocess.run(["pandoc", "-f", "html", "-t", "gfm-raw_html", "--wrap=none"], input=h,
                        capture_output=True, text=True).stdout
    md = neutralize(md)  # no literal [[ / ]] reaches spans, code blocks included
    heads = [(x.tag, x.attrs.get("id"), text_of(x).strip()[:80]) for x in walk(node) if x.tag in ("h1", "h2", "h3")]
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    info.update({"md_chars": len(md), "md_tokens_est": len(md) // 4, "headings": len(heads),
                 "headings_with_id": sum(1 for h in heads if h[1]), "first_headings": heads[:5]})
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
