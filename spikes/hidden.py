"""Hidden-content detection for HTML-based adapters (decision 11).

Dropped: the `hidden` attribute; aria-hidden="true" (any case); inline styles
with display:none, visibility:hidden or collapse, font-size:0, opacity:0
(best effort), text colour equal to the background colour in the same style
(best effort), off-screen positioning (left/top/text-indent <= -999px, best
effort); and the same declarations reached through simple selectors
(.class, #id, tag, tag.class) in the page's own <style> blocks or CSS files.
Kept on purpose: screen-reader-only text (.sr-only, .visually-hidden,
clip rect(0 0 0 0)); it is real accessible content.
"""
import re

SR_ONLY = re.compile(r"sr-only|screen-reader|visually-hidden|visuallyhidden|a11y", re.I)


def _hides(decls, attrs_class=""):
    d = re.sub(r"\s+", "", decls.lower())
    d = d.replace("!important", "")
    if re.search(r"(^|;)display:none", d) or re.search(r"(^|;)visibility:(hidden|collapse)", d):
        return True
    if re.search(r"(^|;)font-size:0(\.0+)?(px|pt|em|rem|%)?(;|$)", d):
        return True
    if re.search(r"(^|;)opacity:0(\.0+)?(;|$)", d):
        return True
    fg = re.search(r"(?:^|;)color:([^;]+)", d)
    bg = re.search(r"(?:^|;)background(?:-color)?:([^;]+)", d)
    if fg and bg and _colour(fg.group(1)) and _colour(fg.group(1)) == _colour(bg.group(1)):
        return True
    if not SR_ONLY.search(attrs_class) and re.search(r"(^|;)(left|top|text-indent|margin-left):-\d{4,}", d):
        return True
    return False


NAMED = {"white": "#ffffff", "black": "#000000", "transparent": None}


def _colour(v):
    v = v.strip()
    if v in NAMED:
        return NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    m = re.fullmatch(r"#([0-9a-f]{6})", v)
    if m:
        return v
    m = re.fullmatch(r"rgba?\((\d+),(\d+),(\d+)(?:,[\d.]+)?\)", v)
    if m:
        return "#%02x%02x%02x" % tuple(int(x) for x in m.groups())
    return None


def css_hidden_selectors(css):
    """Simple selectors whose rule hides content."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = set()
    for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        if not _hides(m.group(2)):
            continue
        for sel in m.group(1).split(","):
            sel = sel.strip().lower()
            if re.fullmatch(r"[a-z0-9]*(?:[.#][\w-]+)?", sel) and sel and not SR_ONLY.search(sel):
                out.add(sel)
    return out


def is_hidden(tag, attrs, selectors=frozenset()):
    """attrs: dict of lower-case attribute names -> values."""
    if "hidden" in attrs:
        return True
    if (attrs.get("aria-hidden") or "").strip().lower() == "true":
        return True
    if tag == "input" and (attrs.get("type") or "").lower() == "hidden":
        return True
    cls = attrs.get("class") or ""
    if _hides(attrs.get("style") or "", cls):
        return True
    if selectors:
        names = {tag}
        for c in cls.lower().split():
            names |= {"." + c, f"{tag}.{c}"}
        if attrs.get("id"):
            names |= {"#" + attrs["id"].lower(), f"{tag}#{attrs['id'].lower()}"}
        if names & selectors:
            return True
    return False
