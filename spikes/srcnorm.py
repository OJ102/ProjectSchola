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
INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")


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
