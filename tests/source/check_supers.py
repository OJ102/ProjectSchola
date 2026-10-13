#!/usr/bin/env python3
"""Superscript rule on the store built from tests/source/supers.tex (tester).

usage: check_supers.py <store>
A raised number is stripped as a footnote marker only when a footnote body with
that number exists on the same page; otherwise it is kept as "^N".
"""
import json
import re
import sys

d = sys.argv[1]


def o(n, g, x=''):
    print(("ok " if g else "FAIL ") + n + ("" if g else f" :: {str(x)[:300]}"))


S = [json.loads(l) for l in open(f"{d}/spans.jsonl")]
T = [s['text'] for s in S]
has = lambda w: any(w in t for t in T)
o("superscript kept: 'mc²' stored as 'mc^2'", has('E = mc^2 for mass'), T[:3])
o("superscript kept: '10³' stored as '10^3'", has('holds 10^3 metres'), T[:3])
o("superscript with no footnote body on its page kept: 'x^7'", has('value x^7 grows'), [t for t in T if 'value x' in t])
o("footnote marker stripped from body text", has('moon closely. The next sentence') and not re.search(r'closely\.\s*\^?1', ' '.join(T)),
  [t for t in T if 'closely' in t])
fn = [s for s in S if 'sun adds a smaller pull' in s['text']]
o("footnote body stored as kind footnote", fn and all(s['kind'] == 'footnote' for s in fn), [(s['id'], s['kind']) for s in fn])
o("footnote body keeps a word boundary after its number ('1 The' or 'The')",
  fn and all(re.match(r'^(\d+\s+|\^?\d+\s+)?The sun', s['text']) for s in fn), [s['text'][:40] for s in fn])
