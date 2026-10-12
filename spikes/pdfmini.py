"""Minimal stdlib-only PDF object reader (spike).

Enough to read the catalog, /PageLabels and the page tree, including objects
packed in compressed object streams (/Type/ObjStm), which is where many modern
PDFs hide the catalog. It does not trust the xref table: it scans for every
`N G obj` and every object stream, so it also survives broken xrefs.
Not a general PDF library: no encryption, no incremental-update ordering
beyond "last definition wins".
"""
import re
import sys
import zlib

WS = b" \t\r\n\f\x00"
DELIM = b"()<>[]{}/%"


class Ref:
    __slots__ = ("num", "gen")

    def __init__(self, num, gen):
        self.num, self.gen = num, gen

    def __repr__(self):
        return f"{self.num} {self.gen} R"


class Name(str):
    pass


class Parser:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def ws(self):
        d = self.d
        while self.p < len(d):
            c = d[self.p:self.p + 1]
            if c in (b" ", b"\t", b"\r", b"\n", b"\f", b"\x00"):
                self.p += 1
            elif c == b"%":
                while self.p < len(d) and d[self.p:self.p + 1] not in (b"\r", b"\n"):
                    self.p += 1
            else:
                break

    def token(self):
        self.ws()
        d, s = self.d, self.p
        while self.p < len(d) and d[self.p] not in WS and d[self.p] not in DELIM:
            self.p += 1
        return d[s:self.p]

    def obj(self):
        self.ws()
        d = self.d
        c = d[self.p:self.p + 1]
        if c == b"<" and d[self.p:self.p + 2] == b"<<":
            self.p += 2
            out = {}
            while True:
                self.ws()
                if d[self.p:self.p + 2] == b">>":
                    self.p += 2
                    return out
                k = self.obj()
                v = self.obj()
                out[str(k)] = v
        if c == b"<":
            e = d.index(b">", self.p)
            h = re.sub(rb"\s", b"", d[self.p + 1:e])
            self.p = e + 1
            if len(h) % 2:
                h += b"0"
            return bytes.fromhex(h.decode())
        if c == b"[":
            self.p += 1
            out = []
            while True:
                self.ws()
                if d[self.p:self.p + 1] == b"]":
                    self.p += 1
                    return out
                out.append(self.obj())
        if c == b"/":
            self.p += 1
            t = self.token()
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), t).decode("latin-1"))
        if c == b"(":
            return self.string()
        t = self.token()
        if not t:
            self.p += 1
            return None
        if re.fullmatch(rb"[+-]?\d+", t):
            save = self.p
            t2 = self.token()
            if re.fullmatch(rb"\d+", t2):
                t3 = self.token()
                if t3 == b"R":
                    return Ref(int(t), int(t2))
            self.p = save
            return int(t)
        if re.fullmatch(rb"[+-]?(\d+\.?\d*|\.\d+)", t):
            return float(t)
        if t == b"true":
            return True
        if t == b"false":
            return False
        if t == b"null":
            return None
        return t  # keyword (obj/endobj/stream)

    def string(self):
        d = self.d
        self.p += 1
        depth, out = 1, bytearray()
        esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
        while self.p < len(d):
            c = d[self.p:self.p + 1]
            self.p += 1
            if c == b"\\":
                n = d[self.p:self.p + 1]
                self.p += 1
                if n in esc:
                    out += esc[n]
                elif n.isdigit():
                    o = n
                    while len(o) < 3 and d[self.p:self.p + 1].isdigit():
                        o += d[self.p:self.p + 1]
                        self.p += 1
                    out.append(int(o, 8) & 255)
                elif n in (b"\r", b"\n"):
                    if n == b"\r" and d[self.p:self.p + 1] == b"\n":
                        self.p += 1
                else:
                    out += n
            elif c == b"(":
                depth += 1
                out += c
            elif c == b")":
                depth -= 1
                if depth == 0:
                    return bytes(out)
                out += c
            else:
                out += c
        return bytes(out)


def text(b):
    """Decode a PDF text string (UTF-16BE with BOM, else PDFDocEncoding~latin-1)."""
    if isinstance(b, bytes):
        if b[:2] == b"\xfe\xff":
            return b[2:].decode("utf-16-be", "replace")
        return b.decode("latin-1")
    return str(b)


class PDF:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.d = f.read()
        if not self.d.startswith(b"%PDF"):
            raise ValueError("not a PDF")
        self.objs = {}
        self.encrypted = b"/Encrypt" in self.d
        self._scan()

    def _stream(self, dic, start):
        d = self.d
        p = start
        if d[p:p + 2] == b"\r\n":
            p += 2
        elif d[p:p + 1] in (b"\n", b"\r"):
            p += 1
        n = dic.get("Length")
        if isinstance(n, int) and d[p + n:p + n + 20].lstrip().startswith(b"endstream"):
            raw = d[p:p + n]
        else:
            raw = d[p:d.index(b"endstream", p)]
        f = dic.get("Filter")
        if f == "FlateDecode" or f == ["FlateDecode"]:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompressobj().decompress(raw)
        return raw if f is None else None

    def _scan(self):
        d = self.d
        for m in re.finditer(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b", d):
            num = int(m.group(1))
            ps = Parser(d, m.end())
            try:
                o = ps.obj()
            except Exception:
                continue
            if isinstance(o, dict):
                ps.ws()
                if d[ps.p:ps.p + 6] == b"stream":
                    o = dict(o)
                    o["__stream_at"] = ps.p + 6
            self.objs[num] = o
        # expand object streams
        for num, o in list(self.objs.items()):
            if isinstance(o, dict) and o.get("Type") == "ObjStm" and "__stream_at" in o:
                o = {k: self.resolve(v) for k, v in o.items()}
                body = self._stream(o, o["__stream_at"])
                if body is None:
                    continue
                n, first = o["N"], o["First"]
                hdr = Parser(body)
                pairs = [(hdr.obj(), hdr.obj()) for _ in range(n)]
                for onum, off in pairs:
                    if onum not in self.objs or not isinstance(self.objs[onum], dict) or self.objs[onum].get("Type") != "ObjStm":
                        self.objs.setdefault(onum, Parser(body, first + off).obj())

    def resolve(self, v):
        seen = 0
        while isinstance(v, Ref) and seen < 32:
            v = self.objs.get(v.num)
            seen += 1
        return v

    def stream(self, num):
        o = self.objs[num]
        o2 = {k: self.resolve(v) for k, v in o.items()}
        return self._stream(o2, o["__stream_at"])

    def catalog(self):
        # prefer the trailer /Root (from trailer dict or xref stream dict)
        roots = re.findall(rb"/Root\s+(\d+)\s+\d+\s+R", self.d)
        if roots:
            c = self.objs.get(int(roots[-1]))
            if isinstance(c, dict):
                return c
        for o in self.objs.values():
            if isinstance(o, dict) and o.get("Type") == "Catalog":
                return o
        return None

    def pages(self):
        """Page object numbers in document order."""
        cat = self.catalog()
        out = []

        def walk(node_ref, depth=0):
            if depth > 64:
                return
            node = self.resolve(node_ref)
            if not isinstance(node, dict):
                return
            if node.get("Type") == "Pages" or "Kids" in node:
                for k in self.resolve(node.get("Kids")) or []:
                    walk(k, depth + 1)
            else:
                out.append(node_ref.num if isinstance(node_ref, Ref) else None)
        walk(cat["Pages"])
        return out

    def number_tree(self, root):
        """Flatten a number tree into a sorted list of (key, value)."""
        out = []

        def walk(n, depth=0):
            n = self.resolve(n)
            if not isinstance(n, dict) or depth > 32:
                return
            nums = self.resolve(n.get("Nums")) or []
            for i in range(0, len(nums) - 1, 2):
                out.append((nums[i], self.resolve(nums[i + 1])))
            for k in self.resolve(n.get("Kids")) or []:
                walk(k, depth + 1)
        walk(root)
        return sorted(out, key=lambda kv: kv[0])

    def page_labels(self):
        """Return list of labels (one per page) from /PageLabels, or None."""
        cat = self.catalog()
        if not cat or "PageLabels" not in cat:
            return None
        ranges = self.number_tree(cat["PageLabels"])
        n = len(self.pages())
        labels = []
        for i, (start, spec) in enumerate(ranges):
            end = ranges[i + 1][0] if i + 1 < len(ranges) else n
            spec = spec or {}
            st, pre, first = spec.get("S"), text(self.resolve(spec.get("P", b""))), spec.get("St", 1)
            for k in range(start, min(end, n)):
                v = first + (k - start)
                labels.append(pre + fmt(st, v))
        return labels


def roman(n):
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    s = ""
    for v, r in vals:
        while n >= v:
            s, n = s + r, n - v
    return s


def alpha(n):
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def fmt(style, v):
    if style == "D":
        return str(v)
    if style == "r":
        return roman(v)
    if style == "R":
        return roman(v).upper()
    if style == "a":
        return alpha(v)
    if style == "A":
        return alpha(v).upper()
    return ""


if __name__ == "__main__":
    pdf = PDF(sys.argv[1])
    cat = pdf.catalog()
    print("objects:", len(pdf.objs), "encrypted:", pdf.encrypted)
    print("catalog keys:", sorted(k for k in (cat or {}) if not k.startswith("__")))
    print("pages:", len(pdf.pages()))
    labels = pdf.page_labels()
    print("PageLabels:", "absent" if labels is None else labels[:40])
