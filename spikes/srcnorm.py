"""Shared text normalization for span text and citation anchors (spike).

Normalizes formatting only, never words: Unicode compatibility forms
(ligatures), hyphen and quote variants, line-break hyphens, whitespace and case.
"""
import re
import unicodedata

HYPHENS = "‐‑‒–—―−­"
QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
          "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
          "«": '"', "»": '"'}
# Zero-width / bidi controls that would let two visually identical strings differ
INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩؜᠎﻿]")


def join_lines(lines):
    """Join text-layer lines into one paragraph, removing line-break hyphens.

    U+2010 at a line end is a typesetter's break hyphen ("Super‐" + "vised");
    drop it. An ASCII '-' at a line end is kept (it may be a real compound).
    """
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("‐") or out.endswith("­"):
            out = out[:-1] + ln
        elif out.endswith("-"):
            out += ln
        else:
            out = (out + " " + ln) if out else ln
    return out


def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = INVISIBLE.sub("", s)
    s = "".join(QUOTES.get(c, c) for c in s)
    s = "".join("-" if c in HYPHENS else c for c in s)
    s = re.sub(r"-\s*\n\s*", "-", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# Tokens for anchor matching (decision 13): words/numbers (with inner ' . ,)
# and single punctuation marks. Matching compares token sequences, so "safe"
# never matches inside "unsafe" and "12" never inside "112".
TOKEN = re.compile(r"[^\W_]+(?:['.,][^\W_]+)*|[^\w\s]")


def norm_map(s):
    """Per-character normalization with a map back to the original string:
    returns (normalized, idx) where idx[k] is the source index of char k."""
    out, idx = [], []
    for i, ch in enumerate(s):
        if INVISIBLE.match(ch):
            continue
        for c in unicodedata.normalize("NFKC", ch):
            c = QUOTES.get(c, c)
            c = "-" if c in HYPHENS else c
            for lc in c.lower():
                out.append(lc)
                idx.append(i)
    return "".join(out), idx


def tokens(s):
    """Normalized tokens of s."""
    return TOKEN.findall(norm_map(s)[0])


def find_tokens(anchor, text):
    """Locate the anchor's token sequence in text on token boundaries.
    Returns the matched ORIGINAL substring of text, or None."""
    a = tokens(anchor)
    if not a:
        return None
    n, idx = norm_map(text)
    toks = [(m.group(0), m.start(), m.end()) for m in TOKEN.finditer(n)]
    words = [t[0] for t in toks]
    L = len(a)
    for i in range(len(words) - L + 1):
        if words[i:i + L] == a:
            return text[idx[toks[i][1]]:idx[toks[i + L - 1][2] - 1] + 1]
    return None


CONTROL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f-\u009f]")


def display_safe(s):
    """Strip bidi, zero-width and control characters before showing text."""
    return CONTROL.sub("", INVISIBLE.sub("", s))


def neutralize(s):
    """Stored span text must never contain citation syntax: break "[[" and
    "]]" with a word joiner (adapter contract, every source type)."""
    return s.replace("[[", "[\u2060[").replace("]]", "]\u2060]")
