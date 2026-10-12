"""Shared text normalization for span text and citation anchors.

Promoted from spikes/srcnorm.py (increment 1).

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


# Code spans keep their line breaks in spans.jsonl; segment files write them as
# " ⏎ " so one span stays one line. Both forms normalize to whitespace.
LINEBREAK_MARK = "\u23ce"


def norm(s):
    s = s.replace(LINEBREAK_MARK, " ")
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
        if ch == LINEBREAK_MARK:
            ch = " "
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
            # the match must not start or end inside a larger word, number,
            # range or fraction: "toxic" in "non-toxic", "40" in "-40",
            # "2" in "1/2", "5" in "3-5" (a sentence-final "." may follow)
            # (an em dash between words is ordinary punctuation, so it is
            # judged on the ORIGINAL character, not the normalized "-")
            s0, e0 = toks[i][1], toks[i + L - 1][2]
            before = text[idx[s0] - 1] if s0 > 0 and idx[s0] > 0 else " "
            after = n[e0] if e0 < len(n) else " "
            after_o = text[idx[e0]] if e0 < len(n) else " "
            after2 = n[e0 + 1] if e0 + 1 < len(n) else " "
            joiners = "-/.+\u00b1\u2010\u2011\u2012\u2013\u2212"
            # a spaced range "3 \u2013 5": the dash follows a number
            head = text[:idx[s0]].rstrip() if s0 > 0 else ""
            spaced_range = head[-1:] in ("\u2013", "\u2212", "/", "-") and head[:-1].rstrip()[-1:].isdigit()
            if before.isalnum() or before in joiners or spaced_range or after.isalnum() \
                    or (after in "-/" and after_o in joiners) or (after == "." and after2.isalnum()):
                continue
            return text[idx[s0]:idx[e0 - 1] + 1]
    return None


CONTROL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f-\u009f]")


def display_safe(s):
    """Strip bidi, zero-width and control characters before showing text."""
    return CONTROL.sub("", INVISIBLE.sub("", s))


def neutralize(s):
    """Stored span text must never contain citation syntax: break "[[" and
    "]]" with a word joiner (adapter contract, every source type)."""
    return s.replace("[[", "[\u2060[").replace("]]", "]\u2060]")
