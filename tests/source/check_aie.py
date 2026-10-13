#!/usr/bin/env python3
"""Real-book acceptance extras for AI Engineering (tester; labels/outline live in
tests/spikes/check_locators.py book, which uses the footer oracle).

usage: check_aie.py <store> <book.pdf> [footnote_oracle.json]
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
oracle = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None


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

# Superscript rule on the real book: exponents keep "^N"; footnote markers whose body is on the page are stripped.
S = [json.loads(l) for l in open(os.path.join(store, 'spans.jsonl'))]
txt = lambda pre: ' '.join(s['text'] for s in S if pre in s['text'])
t = txt('TeraFLOP/s: 6')
out("supers: '6 × 10^13' and '5.2 × 10^18' keep their exponents", '6 × 10^13' in t and '5.2 × 10^18' in t, t[-120:])
out("supers: footnote marker 16 after 'a day.' stripped", re.search(r'FLOPs a day\.\s*\^?16', t) is None, t[-60:])
t = txt('activities being judged')
out("supers: footnote marker 11 after 'judged' stripped (body 11 on the same page)", 'judged—which' in t, t[:120])
fn = {}
for s in S:
    m = re.match(r'(\d{1,3})\s', s['text'])
    if s['kind'] == 'footnote' and m:
        fn.setdefault(s['segment'], set()).add(m.group(1))
left = [(s['id'], m.group()) for s in S if s['kind'] != 'footnote'
        for m in re.finditer(r'\^(\d{1,3})', s['text']) if m.group(1) in fn.get(s['segment'], set())]
out("supers: no '^N' left where footnote N's body is on the same page", not left, left[:5])

# Exact footnote-marker target from tests/source/footnote_oracle.py (pdfium, independent of poppler):
# raised numbers whose footnote body is on the same or a neighbouring page are markers; the rest are kept as ^N.
if oracle is not None:
    O = Counter((m[0], m[1]) for m in oracle['markers'])
    St = Counter((int(s['segment'][1:]), str(n['no'])) for s in S for n in (s.get('notes') or []))
    # Accepted exceptions (lead's ruling 2026-10-13, used only if builder's one generic fix attempt fails):
    # {(page, number): reason}. Empty means every oracle marker must be stripped and linked.
    ACCEPTED = {}
    o_only = sorted(k for k in O if O[k] > St[k] and k not in ACCEPTED)
    s_only = sorted(k for k in St if St[k] > O[k])
    out(f"supers: per-marker (page, number) match with oracle ({sum(O.values())} markers, {len(ACCEPTED)} accepted exceptions)",
        not o_only and not s_only, f"oracle only {o_only}; store only {s_only}")
    fm = (rep.get('text') or {}).get('footnote_marker_chars')
    want_chars = oracle['marker_chars'] - sum(len(n) for (pg, n) in ACCEPTED if O[(pg, n)] > St[(pg, n)])
    out(f"supers: footnote_marker_chars == oracle {oracle['marker_chars']} less accepted exceptions ({want_chars})",
        fm == want_chars, f"store {fm}")
    alltext = ' '.join(s['text'] for s in S)
    want = Counter()
    for pg, num, before in oracle['kept']:
        b = re.search(r'([A-Za-z]|\d+(?:\.\d+)?)\s*$', before)
        if not b or '∑' in before or '∏' in before or pg == 149:
            continue  # display-math limits and formula pages go to vision; not checked as text
        want[f"{b.group(1)}^{num}"] += 1
    miss = [(k, n, alltext.count(k)) for k, n in want.items() if alltext.count(k) < n]
    out(f"supers: every oracle exponent is stored as base^N ({sum(want.values())})", not miss, f"(form, oracle, store) {miss}")
