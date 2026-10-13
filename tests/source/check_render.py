#!/usr/bin/env python3
"""Decision 32 render cap on a store built from a huge page (tester).

usage: check_render.py <store> <max_mp> <word expected in the OCR text>
"""
import json
import os
import struct
import sys

d, cap, word = sys.argv[1], float(sys.argv[2]), sys.argv[3].lower()


def o(n, g, x=''):
    print(("ok " if g else "FAIL ") + n + ("" if g else f" :: {str(x)[:300]}"))


rep = json.load(open(os.path.join(d, 'report.json')))
o("oversized page not skipped (downscaled instead)", not (rep.get('ocr') or {}).get('skipped_oversized'), rep.get('ocr'))


def png_wh(p):
    b = open(p, 'rb').read(24)
    return struct.unpack('>II', b[16:24]) if b[:8] == b'\x89PNG\r\n\x1a\n' else None


big = []
for root, _, fs in os.walk(d):
    for f in fs:
        if f.endswith('.png'):
            wh = png_wh(os.path.join(root, f))
            if wh and wh[0] * wh[1] > cap * 1.05e6:
                big.append((f, wh))
o(f"every rendered PNG in the store is <= {cap:g} MP", not big, big[:3])
S = [json.loads(l) for l in open(os.path.join(d, 'spans.jsonl'))]
o(f"downscaled page still OCRs its words ('{word}')", any(word in s['text'].lower() for s in S), [s['text'][:60] for s in S])
