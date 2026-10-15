"""Hidden-content detection for HTML-based adapters (decision 11).

Dropped: the `hidden` attribute; aria-hidden="true" (any case); hidden
inputs; inline styles with display:none, visibility:hidden|collapse,
font-size:0, opacity:0, color:transparent, height/width:0 with overflow:hidden,
text colour equal to the background (best effort), off-screen positioning
(left/top/text-indent <= -999px, best effort); and the same declarations
reached through the page's own CSS (<style> blocks, EPUB CSS files).

CSS support (round 5): selectors made of compounds (tag, .class, #id,
[attr], [attr=value]) joined by descendant or child combinators; CSS
escapes decoded; @media blocks other than screen/all ignored (print rules do
not hide screen text); later rules override earlier ones (a later
display:block un-hides). Specificity is not computed: source order decides.
Selectors with pseudo-classes are ignored.
Kept on purpose: screen-reader-only text (.sr-only, .visually-hidden, clip
rect); it is real accessible content.
"""
import re

SR_ONLY = re.compile(r"sr-only|screen-reader|visually-hidden|visuallyhidden|a11y", re.I)
NAMED = {"white": "#ffffff", "black": "#000000"}


def _colour(v):
    v = v.strip()
    if v in NAMED:
        return NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3})", v, re.I)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1)).lower()
    if re.fullmatch(r"#[0-9a-f]{6}", v, re.I):
        return v.lower()
    m = re.fullmatch(r"rgba?\((\d+),(\d+),(\d+)(?:,\s*([\d.]+))?\)", v, re.I)
    if m:
        if m.group(4) is not None and float(m.group(4)) == 0.0:
            return "transparent"
        return "#%02x%02x%02x" % tuple(int(x) for x in m.groups()[:3])
    return None


def effect(decls, cls=""):
    """True if the declarations hide the element, False if they explicitly
    show it (display/visibility set to something visible), None otherwise."""
    d = re.sub(r"\s+", "", decls.lower()).replace("!important", "")
    props = {}
    for part in d.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            props[k] = v  # later declaration in the block wins
    hide = False
    if props.get("display") == "none" or props.get("visibility") in ("hidden", "collapse"):
        hide = True
    fs = props.get("font-size", "")
    m_fs = re.fullmatch(r"(\d+(?:\.\d+)?)(px|pt|em|rem|vw|vh|%)?", fs)
    if m_fs and float(m_fs.group(1)) <= 0.1:
        hide = True
    if re.fullmatch(r"0(\.0+)?", props.get("opacity", "x")):
        hide = True
    col = props.get("color", "")
    if col == "transparent" or _colour(col) == "transparent":
        hide = True
    if props.get("overflow") in ("hidden", "clip") and any(
        (m := re.fullmatch(r"(\d+(?:\.\d+)?)(px|pt|em|rem|vw|vh|%)?", props.get(k, "x"))) and float(m.group(1)) <= 0.1
        for k in ("height", "width", "max-height", "max-width")
    ):
        hide = True
    fg, bg = props.get("color"), props.get("background-color", props.get("background"))
    if fg and bg and _colour(fg) and _colour(fg) == _colour(bg):
        hide = True
    if not SR_ONLY.search(cls):
        tf = props.get("transform", "")
        if tf and re.search(r"scale(?:3d|[xy])?\(\s*0(?:\.0+)?(?:\s*,|\s*\))", tf):
            hide = True
        cp = props.get("clip-path", "")
        if cp and re.search(r"circle\(\s*0|inset\(\s*(?:100%|50%)\s*\)|polygon\(\s*0\s+0(?:\s*,\s*0\s+0)*\s*\)", cp):
            hide = True
        for k in ("left", "top", "text-indent", "margin-left"):
            val = props.get(k, "")
            m = re.fullmatch(r"-(\d+)(px|pt|em|rem|vw|vh|%)?", val)
            if m:
                num = int(m.group(1))
                unit = m.group(2) or "px"
                if unit in ("em", "rem") and num >= 50:
                    hide = True
                elif unit in ("vw", "vh", "%") and num >= 100:
                    hide = True
                elif num >= 999:
                    hide = True
    if hide:
        return True
    if ("display" in props) or props.get("visibility") == "visible":
        return False
    return None


def _unescape(css):
    css = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(int(m.group(1), 16)), css)
    return re.sub(r"\\(.)", r"\1", css)


def _strip_media(css):
    """Drop @media blocks that do not apply to screens (and other @-blocks)."""
    out, i = [], 0
    while i < len(css):
        m = re.compile(r"@([\w-]+)([^{;]*)([{;])").search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        if m.group(3) == ";":
            i = m.end()
            continue
        depth, j = 1, m.end()
        while j < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        inner = css[m.end():j - 1]
        cond = m.group(2).lower()
        if m.group(1).lower() == "media" and ("screen" in cond or "all" in cond) and "print" not in cond:
            out.append(_strip_media(inner))
        i = j
    return "".join(out)


COMPOUND = re.compile(r"^([a-z][\w-]*|\*)?((?:[.#][\w-]+|\[[^\]]+\])*)$")


def _parse_selector(sel):
    sel = sel.strip().lower()
    if not sel or ":" in sel or SR_ONLY.search(sel):
        return None
    parts = [p for p in re.split(r"\s*>\s*|\s+", sel) if p]
    out = []
    for p in parts:
        m = COMPOUND.match(p)
        if not m:
            return None
        tag = m.group(1) if m.group(1) not in (None, "*") else None
        classes = re.findall(r"\.([\w-]+)", m.group(2))
        ids = re.findall(r"#([\w-]+)", m.group(2))
        attrs = []
        for a in re.findall(r"\[([^\]]+)\]", m.group(2)):
            am = re.fullmatch(r"\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*['\"]?([^'\"]*)['\"]?)?\s*", a)
            if not am:
                return None
            attrs.append((am.group(1), am.group(2), am.group(3)))
        out.append((tag, classes, ids, attrs))
    return out


def parse_css(css):
    """Ordered list of (selector compounds, effect) for rules that hide or show."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = _strip_media(_unescape(css))
    rules = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        eff = effect(m.group(2))
        if eff is None:
            continue
        for sel in m.group(1).split(","):
            comp = _parse_selector(sel)
            if comp:
                rules.append((comp, eff))
    return rules


def _match(compound, tag, attrs):
    ctag, classes, ids, cattrs = compound
    if ctag and ctag != tag:
        return False
    have = set((attrs.get("class") or "").lower().split())
    if any(c not in have for c in classes):
        return False
    if any((attrs.get("id") or "").lower() != i for i in ids):
        return False
    for name, op, val in cattrs:
        if name not in attrs:
            return False
        v = (attrs.get(name) or "").lower()
        if op == "=" and v != val:
            return False
        if op == "~=" and val not in v.split():
            return False
        if op == "^=" and not v.startswith(val):
            return False
        if op == "$=" and not v.endswith(val):
            return False
        if op == "*=" and val not in v:
            return False
    return True


def _selector_matches(comp, tag, attrs, ancestors):
    if not _match(comp[-1], tag, attrs):
        return False
    k = len(ancestors) - 1
    for c in reversed(comp[:-1]):
        while k >= 0 and not _match(c, *ancestors[k]):
            k -= 1
        if k < 0:
            return False
        k -= 1
    return True


def css_hidden_selectors(css):
    """Back-compat name: the parsed rule list."""
    return parse_css(css)


def is_hidden(tag, attrs, rules=(), ancestors=()):
    """attrs: dict of lower-case attribute names -> values; ancestors: list of
    (tag, attrs) from the root down to the parent."""
    if "hidden" in attrs:
        return True
    if (attrs.get("aria-hidden") or "").strip().lower() == "true":
        return True
    if tag in ("template", "textarea"):
        return True
    if tag == "dialog" and "open" not in attrs:
        return True
    if tag == "input" and (attrs.get("type") or "").lower() == "hidden":
        return True
    cls = attrs.get("class") or ""
    inline = effect(_unescape(attrs.get("style") or ""), cls)
    if inline is not None:
        if inline:
            return True
        return False
    result = False
    for comp, eff in rules:
        if _selector_matches(comp, tag, attrs, ancestors):
            result = eff  # later rules win
    if result:
        return True

    # Check inherited / default white or same-color text
    is_sr = SR_ONLY.search(cls) or any(SR_ONLY.search(at.get("class") or "") for _, at in ancestors)
    if not is_sr:
        effective_fg = None
        for _, at in [(tag, attrs)] + list(reversed(ancestors)):
            st_val = _unescape(at.get("style") or "")
            m_fg = re.search(r"(?:^|;)\s*color\s*:\s*([^;!]+)", st_val, re.I)
            if m_fg:
                effective_fg = _colour(m_fg.group(1))
                break
        if effective_fg:
            if effective_fg == "transparent":
                return True
            effective_bg = "#ffffff"  # default canvas background
            for _, at in [(tag, attrs)] + list(reversed(ancestors)):
                st_val = _unescape(at.get("style") or "")
                m_bg = re.search(r"(?:^|;)\s*(?:background-color|background)\s*:\s*([^;!]+)", st_val, re.I)
                if m_bg:
                    col = _colour(m_bg.group(1))
                    if col and col != "transparent":
                        effective_bg = col
                        break
            if effective_fg == effective_bg:
                return True

    return False
