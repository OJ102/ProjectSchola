#!/usr/bin/env python3
"""Real-book acceptance extras for AI Engineering (tester; labels/outline live in
tests/spikes/check_locators.py book, which uses the footer oracle).

usage: check_aie.py <store> <book.pdf>
Independent counts, computed here from poppler output, never from the store:
  - titled cross-refs: '"Title" on page N' phrases in the text layer (34: 33 arabic + "Acknowledgments" on page xx);
    every one resolved, and the resolved page's text contains the title;
  - figures: distinct pdfimages image objects minus icons repeated on 3+ pages (169 - 3 = 166);
  - no vision calls at ingest.
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter

store, pdf = sys.argv[1:3]


def out(name, good, detail=''):
    print(f"ok {name}" if good else f"FAIL {name} :: {str(detail)[:600]}")


def flat(t):
    return re.sub(r'\s+', ' ', t.replace('‐\n', '').replace('-\n', '-'))


rep = json.load(open(os.path.join(store, 'report.json')))
xr = json.load(open(os.path.join(store, 'xrefs.json')))
pages_txt = subprocess.run(['pdftotext', pdf, '-'], capture_output=True, text=True).stdout.split('\f')
phr = re.findall(r'["“]([^"”]{2,120})["”] on page (\w+)', flat('\n'.join(pages_txt)))
titled = [x for x in xr if x.get('kind') == 'titled_page']
out(f"xrefs: titled count matches independent count ({len(phr)})", len(titled) == len(phr), f"store {len(titled)} vs pdftotext {len(phr)}")
out("xrefs: 34 titled phrases (33 arabic + 1 roman)", len(phr) == 34 and sum(not p[1].isdigit() for p in phr) == 1, phr[:3])
unres = [x['text'] for x in titled if not x.get('resolved')]
out("xrefs: every titled cross-ref resolved", not unres, unres[:5])
lab = {p['pdf_index']: p['label'] for p in json.load(open(os.path.join(store, 'labels.json')))['pages']}
wrong = []
for x in titled:
    m = re.search(r'["“](.+?)["”] on page (\w+)', x['text'])
    if not m or not x.get('resolved'):
        continue
    title, label = m.group(1), m.group(2)
    p = x.get('pdf_index')
    page = flat(pages_txt[p - 1]).lower() if p and p <= len(pages_txt) else ''
    if lab.get(p) != label or re.sub(r'\W+', ' ', title.lower()).strip() not in re.sub(r'\W+', ' ', page):
        wrong.append((title, label, p, lab.get(p)))
out("xrefs: resolved page carries the label and contains the title", not wrong, wrong[:5])
rs = rep.get('xrefs', {})
out("report: xrefs titled_resolved == titled", rs.get('titled') == rs.get('titled_resolved') == len(titled), rs)

rows = [l.split() for l in subprocess.run(['pdfimages', '-list', pdf], capture_output=True, text=True).stdout.splitlines()[2:]]
imgs = [r for r in rows if len(r) > 11 and r[2] == 'image']
objs = Counter(r[10] for r in imgs)
size = {r[10]: (int(r[3]), int(r[4])) for r in imgs}
icons = {o for o, n in objs.items() if n >= 3 and max(size[o]) < 200}
want = len(objs) - len(icons)
a = rep.get('assets', {})
out(f"assets: rasters = distinct image objects {len(objs)} - repeated icons {len(icons)} = {want}", a.get('rasters') == want, a)
idx = json.load(open(os.path.join(store, 'assets', 'index.json')))
iconhit = [x['id'] for x in idx if str(x.get('object')) in icons]
out("assets: no repeated icon object in assets/index.json", not iconhit, iconhit[:5])
missing = [x['file'] for x in idx if not os.path.isfile(os.path.join(store, 'assets', x['file']))]
out("assets: every indexed asset file exists", not missing, missing[:5])
v = rep.get('vision', {})
out("vision: no calls at ingest", v.get('calls_made') == 0, v)
