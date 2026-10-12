#!/usr/bin/env python3
"""Check a locators.py output dir against the tester's footer oracle.

usage: check_locators.py book <outdir> <oracle.json>      (AI Engineering)
       check_locators.py stable <outdirA> <outdirB>        (two ingest runs)
Prints one line per check: "ok <name>" or "FAIL <name> :: <detail>".
"""
import json, os, sys

# TOC pages whose last line is a bare page number from the TOC itself (156, 401)
TOC_FALSE = {8: '156', 10: '401'}


def out(name, good, detail=''):
    print(f"ok {name}" if good else f"FAIL {name} :: {detail}")


def load(d):
    L = json.load(open(os.path.join(d, 'labels.json')))
    pages = {p['pdf_index']: p for p in L['pages']}
    spans = [json.loads(l) for l in open(os.path.join(d, 'spans.jsonl'))]
    ol = json.load(open(os.path.join(d, 'outline.json')))
    return L, pages, spans, ol


def multimap(L):
    for k, v in L.items():
        if isinstance(v, dict) and v and all(isinstance(x, list) for x in v.values()):
            return k, v
    return None, None


def book(d, oracle_path):
    L, pages, spans, ol = load(d)
    oracle = {int(k): v['label'] for k, v in json.load(open(oracle_path)).items() if int(k) not in TOC_FALSE}
    bad = [(p, lab, pages.get(p, {}).get('label')) for p, lab in sorted(oracle.items())
           if str(pages.get(p, {}).get('label', '')).lower() != lab]
    out(f"labels: all {len(oracle)} footered pages match printed footer", not bad, f"(pdf, footer, label) {bad[:10]} n={len(bad)}")
    body = [p for p, lab in oracle.items() if lab.isdigit() and int(lab) != p - 24]
    out("labels: body footers are PDF-24", not body, str(body[:10]))
    front = [p for p, lab in oracle.items() if not lab.isdigit()]
    out("labels: front matter roman", all(str(pages[p]['label']).lower() == oracle[p] for p in front), str(front))
    fp = [(p, pages[p]['label']) for p, v in TOC_FALSE.items() if str(pages[p]['label']) == v]
    out("labels: TOC numbers (PDF 8=156, 10=401) are not labels", not fp, str(fp))
    fp_raw = [(p, pages[p].get('footer')) for p, v in TOC_FALSE.items() if str(pages[p].get('footer')) == v]
    out("labels: TOC numbers not accepted as footer candidates", not fp_raw, str(fp_raw))
    nonstr = [p for p, v in pages.items() if not isinstance(v['label'], str)]
    out("labels: every label is a string", not nonstr, str(nonstr[:5]))
    out("labels: PDF 102 -> 78", pages[102]['label'] == '78', pages[102]['label'])
    # outline: pdf_index base must agree with labels.json (1-based)
    pt = [o for o in ol if o['title'] == 'Post-Training']
    out("outline: 'Post-Training' at PDF 102 (1-based like labels.json), label 78",
        len(pt) == 1 and pt[0]['pdf_index'] == 102 and pt[0].get('label') == '78', str(pt))
    ch2 = [o for o in ol if o['depth'] == 1 and 'Understanding Foundation Models' in o['title']]
    out("outline: Ch2 starts at printed 49", len(ch2) == 1 and ch2[0].get('label') == '49', str(ch2))
    out("outline: 182 items", len(ol) == 182, str(len(ol)))
    k, mm = multimap(L)
    if mm is None:
        out("labels: label -> [pdf] multimap present", False, f"labels.json keys {list(L)}")
    else:
        miss = [p for p, v in pages.items() if p not in mm.get(v['label'], [])]
        extra = [(lab, ps) for lab, ps in mm.items() for p in ps if pages.get(p, {}).get('label') != lab]
        out(f"labels: multimap '{k}' consistent with pages", not miss and not extra, f"missing {miss[:5]} extra {extra[:5]}")
    ids = [s['id'] for s in spans]
    out("spans: ids unique", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} dups")
    noloc = [s['id'] for s in spans if not s.get('locator')]
    out("spans: every span has a locator", not noloc, str(noloc[:5]))
    wrong = [s['id'] for s in spans if s['locator'] != pages[int(s['segment'][1:])]['label']]
    out("spans: locator equals its page label", not wrong, str(wrong[:5]))


def stable(a, b):
    sa, sb = load(a)[2], load(b)[2]
    ka = [(s['id'], s['sha1']) for s in sa]
    kb = [(s['id'], s['sha1']) for s in sb]
    diff = next((i for i, (x, y) in enumerate(zip(ka, kb)) if x != y), None)
    out(f"spans: ids+sha1 identical across two runs ({len(ka)} spans)", ka == kb and len(ka) > 0,
        f"len {len(ka)} vs {len(kb)}, first diff at {diff}")
    la = json.load(open(os.path.join(a, 'labels.json')))
    lb = json.load(open(os.path.join(b, 'labels.json')))
    out("labels: identical across two runs", la == lb, "labels.json differs")


def jl(d):
    return [json.loads(l) for l in open(os.path.join(d, 'spans.jsonl'))]


def epub(d):
    """tests/fixtures/sample.epub (from spikes/fixtures-src/epub.md)."""
    S = {s['text']: s for s in jl(d)}
    want = {  # text prefix -> (publisher page, locator)
        'Tides are driven': ('1', 'Chapter One: Tides ¶1'),
        'When the sun and moon': ('1', 'Chapter One: Tides § Spring and Neap Tides ¶1'),
        'When they sit': ('2', 'Chapter One: Tides § Spring and Neap Tides ¶2'),
        'Ocean currents': ('3', 'Chapter Two: Currents ¶1'),
        'Cold, salty water': ('3', 'Chapter Two: Currents § Thermohaline Circulation ¶1'),
        'The conveyor takes': ('4', None),
    }
    bad = []
    for pre, (pg, loc) in want.items():
        hit = [s for t, s in S.items() if t.startswith(pre)]
        if len(hit) != 1 or str(hit[0].get('page')) != pg or (loc and hit[0]['locator'] != loc):
            bad.append((pre, pg, loc, [(h.get('page'), h['locator']) for h in hit]))
    out("epub: locators and publisher pages match fixture", not bad, str(bad))
    ids = [s['id'] for s in jl(d)]
    out("epub: span ids unique", len(ids) == len(set(ids)), str(ids))
    ol = [o['title'] for o in json.load(open(os.path.join(d, 'outline.json')))]
    out("epub: outline from nav", ol == ['Chapter One: Tides', 'Spring and Neap Tides', 'Chapter Two: Currents',
                                        'Thermohaline Circulation'], str(ol))


def vtt(d):
    """tests/fixtures/lecture.vtt; link base https://www.youtube.com/watch?v=ID."""
    S = jl(d)
    want = [('t00:00:00', 0), ('t00:00:06', 6), ('t00:00:58', 58), ('t00:01:10', 70), ('t00:01:19', 79),
            ('t00:02:30', 150), ('t00:02:41', 161), ('t01:02:03', 3723)]
    got = [(s['id'], s.get('link')) for s in S]
    exp = [(i, f"https://www.youtube.com/watch?v=ID&t={t}s") for i, t in want]
    out("vtt: cue ids and &t= links", got == exp, f"got {got}")
    out("vtt: locator is hh:mm:ss of cue start", all(s['locator'] == s['id'][1:] for s in S), str([s['locator'] for s in S]))
    out("vtt: voice tags stripped", not any('<' in s['text'] for s in S), str([s['text'] for s in S if '<' in s['text']]))
    ol = json.load(open(os.path.join(d, 'outline.json')))
    exp_ol = [('Intro', '00:00:00'), ('Gradient descent', '00:01:10'), ('Learning rate', '00:02:30')]
    out("vtt: chapters parsed (titles clean, locators hh:mm:ss)", [(o['title'], o['locator']) for o in ol] == exp_ol,
        f"got {[(o['title'], o['locator']) for o in ol]}")


def vtt_collide(d):
    ids = [s['id'] for s in jl(d)]
    out("vtt: same-second cues get unique ids", len(ids) == len(set(ids)) == 3 and ids[0] == 't00:00:01', str(ids))


if __name__ == '__main__':
    {'book': book, 'stable': stable, 'epub': epub, 'vtt': vtt, 'vtt_collide': vtt_collide}[sys.argv[1]](*sys.argv[2:])
