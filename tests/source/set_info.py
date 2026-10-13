#!/usr/bin/env python3
"""Append an incremental update that replaces a PDF's /Info (tester fixture helper).

usage: set_info.py <in.pdf> <out.pdf> <title> <author>
Title and author are written as UTF-16BE hex strings, so any character (bidi
controls, newlines, BEL) reaches the reader unchanged. Needs a classic xref
table (pdflatex with \\pdfobjcompresslevel=0).
"""
import re
import sys

src, dst, title, author = sys.argv[1:5]
d = open(src, 'rb').read()
sx = int(re.findall(rb'startxref\s+(\d+)', d)[-1])
tr = re.findall(rb'trailer\s*<<(.*?)>>\s*startxref', d, re.S)[-1]
size = int(re.search(rb'/Size\s+(\d+)', tr).group(1))
root = re.search(rb'/Root\s+(\d+\s+\d+\s+R)', tr).group(1)
hexs = lambda s: b'<FEFF' + s.encode('utf-16-be').hex().upper().encode() + b'>'
if not d.endswith(b'\n'):
    d += b'\n'
off = len(d)
obj = b'%d 0 obj\n<< /Title ' % size + hexs(title) + b' /Author ' + hexs(author) + b' >>\nendobj\n'
xref = len(d) + len(obj)
tail = (b'xref\n%d 1\n%010d 00000 n \ntrailer\n<< /Size %d /Root ' % (size, off, size + 1) + root +
        b' /Info %d 0 R /Prev %d >>\nstartxref\n%d\n%%%%EOF\n' % (size, sx, xref))
open(dst, 'wb').write(d + obj + tail)
