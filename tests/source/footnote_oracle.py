#!/usr/bin/env python3
"""Tester's footnote-marker / exponent oracle (pdfium, independent of poppler and hooks/source).

usage: footnote_oracle.py <book.pdf>   (run with .spike-venv/bin/python: needs pypdfium2)
Prints JSON {markers: [[page, number, before]], exponents: [...], marker_chars, ...}.
A raised number is a digit run set at superscript size (< 0.7 x the page's body
font) that does not open a footnote body (a footnote body's number is followed by
footnote-size text). It is a footnote marker when a
footnote body starting with that number exists on the same or a neighbouring page;
otherwise it is kept (an exponent, or a marker whose body was not found).
"""
import json
import re
import statistics
import sys

import pypdfium2 as pdfium
import pypdfium2.raw as raw

pdf = pdfium.PdfDocument(sys.argv[1])
pages = []
for i in range(len(pdf)):
    tp = pdf[i].get_textpage()
    n = tp.count_chars()
    ch = []
    for k in range(n):
        c = chr(raw.FPDFText_GetUnicode(tp, k))
        sz = raw.FPDFText_GetFontSize(tp, k)
        l, b, r, t = tp.get_charbox(k)
        ch.append((c, sz, b, t))
    pages.append((tp.get_text_range(), ch))

raised_all, fbodies = [], []
for i, (text, ch) in enumerate(pages, 1):
    sizes = [c[1] for c in ch if c[0].isalpha()]
    if not sizes:
        fbodies.append(set()); raised_all.append([]); continue
    body = statistics.mode(round(s, 1) for s in sizes)
    # footnote bodies: a line that starts with a small number then a space, set below body size
    F, R = set(), []
    k = 0
    def small(k):
        # below body size, or (odd font scales such as 2100pt letters) under 0.7x the letter before it
        if ch[k][1] < body * 0.85:
            return True
        p = next((c for c in reversed(ch[max(0, k - 3):k]) if c[0].isalnum()), None)
        return p is not None and ch[k][1] < p[1] * 0.7

    while k < len(ch):
        if ch[k][0].isdigit() and small(k):
            j = k
            while j < len(ch) and ch[j][0].isdigit() and ch[j][1] <= ch[k][1] * 1.01:
                j += 1
            num = ''.join(c[0] for c in ch[k:j])
            # a footnote body: at a line start, its number is followed by footnote-size text
            nxt = next((c for c in ch[j:j + 6] if not c[0].isspace()), None)
            line_start = k == 0 or ch[k - 1][0] in '\r\n'
            if line_start and nxt and nxt[0] not in ')]}.,;:+-=^' and body * 0.7 <= nxt[1] < body * 0.9:
                F.add(num)
            elif ch[k][1] < body * 0.7 or not ch[k][1] < body * 0.85:
                # superscript size (about 6pt on a 10.5pt body). Compare with the preceding larger letter or
                # digit on the same line: above it = raised (kept), below it = a subscript (x1, t2: ignored).
                # Across a line break pdfium's geometry is unreliable, so size alone decides.
                prev, crossed = None, False
                for c in reversed(ch[max(0, k - 6):k]):
                    if c[0] in '\r\n':
                        crossed = True
                        break
                    if c[0].isalnum() and c[1] >= ch[k][1] * 1.2:   # any larger text (sidebars use 8.8pt)
                        prev = c
                        break
                if (prev and ch[k][2] > prev[2] + 0.5) or (prev is None and crossed):
                    before = ''.join(c[0] for c in ch[max(0, k - 12):k]).replace('\r', ' ').replace('\n', ' ')
                    R.append((i, num, before))
            k = j
        else:
            k += 1
    fbodies.append(F); raised_all.append(R)

markers, kept = [], []
for i, R in enumerate(raised_all):
    near = set().union(*fbodies[max(0, i - 1):i + 2])
    for r in R:
        (markers if r[1] in near else kept).append(r)
same = [r for i, R in enumerate(raised_all) for r in R if r[1] in fbodies[i]]
json.dump({'markers': markers, 'kept': kept, 'marker_count': len(markers),
           'marker_chars': sum(len(r[1]) for r in markers),
           'same_page_marker_chars': sum(len(r[1]) for r in same), 'same_page_markers': len(same)},
          sys.stdout, ensure_ascii=False, indent=0)
