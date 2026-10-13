#!/usr/bin/env python3
"""Generic adapter contract check (tester). Works on any store an adapter wrote.

usage: check_contract.py <storedir> [verify_dir]
  verify_dir: directory holding source_verify.py (default: spikes/, until the
  production gate ships in increment 2).
Prints one line per check: "ok <name>" or "FAIL <name> :: <detail>".

Contract (plan "Increments", RESULTS.md "Adapter contract additions"):
  - schema-valid files (source.json, segments/index.json, spans.jsonl, outline.json,
    units.json, report.json; labels.json for paged sources);
  - unique span ids, every span has a locator, spans reference real segments;
  - the outline covers every segment;
  - units are 3-8k tokens (chars/4), cover every span once in reading order; a source
    under 3k tokens is one unit (tiny-source rule, decision 18);
  - an anchor sampled from every span round-trips through source-verify, and an
    anchor from another span is rejected on the wrong id;
  - no literal [[ or ]] in span text; every page index is 1-based;
  - report.json gives captured vs total text.
"""
import hashlib
import json
import os
import random
import re
import sys

STORE = sys.argv[1]
VERIFY_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'spikes')
UNIT_MIN, UNIT_MAX = 3000, 8000


def out(name, good, detail=''):
    print(f"ok {name}" if good else f"FAIL {name} :: {str(detail)[:600]}")


def jload(rel):
    p = os.path.join(STORE, rel)
    try:
        return json.load(open(p, encoding='utf-8')), None
    except Exception as e:  # noqa: BLE001
        return None, f"{rel}: {type(e).__name__}: {e}"


def toks(text):
    return len(text) / 4


# --- files -------------------------------------------------------------------
src, e = jload('source.json')
out("schema: source.json parses", src is not None, e)
if src is not None:
    miss = [k for k in ('type', 'origin', 'title', 'adapter', 'ingest_version') if k not in src]
    out("schema: source.json has type/origin/title/adapter/ingest_version", not miss, f"missing {miss}; keys {list(src)}")
    h = src.get('sha256') or src.get('etag')
    out("schema: source.json has sha256 or etag", bool(h), f"keys {list(src)}")
    if src.get('sha256'):
        out("schema: source.json sha256 is 64 hex", bool(re.fullmatch(r'[0-9a-f]{64}', src['sha256'])), src['sha256'])

gi = os.path.join(STORE, '.gitignore')
out("store: .gitignore is '*'", os.path.isfile(gi) and '*' in open(gi).read().split(), open(gi).read() if os.path.isfile(gi) else 'missing')

segidx, e = jload('segments/index.json')
out("schema: segments/index.json parses as a list", isinstance(segidx, list) and segidx, e or type(segidx).__name__)
segidx = segidx if isinstance(segidx, list) else []
seg_ids = [s.get('id') for s in segidx]
out("segments: ids unique", len(seg_ids) == len(set(seg_ids)), f"{len(seg_ids) - len(set(seg_ids))} dups")
bad = [s.get('id') for s in segidx if not all(k in s for k in ('id', 'locator', 'kind', 'tokens', 'has_figures', 'needs_vision'))]
out("segments: every entry has id/locator/kind/tokens/has_figures/needs_vision", not bad, f"{len(bad)} bad, first {bad[:3]}; keys {list(segidx[0]) if segidx else []}")
KINDS = {'text', 'math', 'figure', 'table', 'code', 'scan', 'slide'}
bad = sorted({s.get('kind') for s in segidx if not (s.get('kind') == 'blank' and not s.get('spans'))} - KINDS)
out("segments: kind in text|math|figure|table|code|scan|slide (or blank with 0 spans)", not bad, bad)
nofile = [i for i in seg_ids if i and not os.path.isfile(os.path.join(STORE, 'segments', f'{i}.md'))]
out("segments: every indexed segment has segments/<id>.md", not nofile, nofile[:5])

spans = []
try:
    spans = [json.loads(l) for l in open(os.path.join(STORE, 'spans.jsonl'), encoding='utf-8') if l.strip()]
    out("schema: spans.jsonl parses", bool(spans), "empty")
except Exception as ex:  # noqa: BLE001
    out("schema: spans.jsonl parses", False, ex)
bad = [s.get('id') for s in spans if not all(k in s for k in ('id', 'segment', 'locator', 'text', 'norm', 'sha1'))]
out("spans: every span has id/segment/locator/text/norm/sha1", not bad, f"{len(bad)} bad, first {bad[:3]}")
ids = [s.get('id') for s in spans]
out(f"spans: ids unique ({len(ids)})", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} dups")
bad = [s.get('id') for s in spans if not s.get('id') or not re.fullmatch(r'[A-Za-z0-9:._-]+', str(s['id']))]
out("spans: ids are <segment>.<n> safe tokens", not bad, bad[:5])
bad = [s['id'] for s in spans if not str(s['id']).startswith(str(s.get('segment')))]
out("spans: id starts with its segment id", not bad, bad[:5])
noloc = [s.get('id') for s in spans if not str(s.get('locator') or '').strip()]
out("spans: every span has a locator", not noloc, noloc[:5])
segset = set(seg_ids)
orphan = [s['id'] for s in spans if s.get('segment') not in segset]
out("spans: every span's segment is indexed", not orphan, orphan[:5])
cite = [s['id'] for s in spans if any(x in str(s.get(k, '')) for k in ('text', 'norm') for x in ('[[', ']]'))]
out("spans: no literal [[ or ]] in text/norm", not cite, cite[:5])
empty = [s['id'] for s in spans if not str(s.get('text', '')).strip()]
out("spans: no empty text", not empty, empty[:5])
badsha = [s['id'] for s in spans if s.get('sha1') not in (hashlib.sha1(s.get('norm', '').encode()).hexdigest(),
                                                         hashlib.sha1(s.get('text', '').encode()).hexdigest())]
out("spans: sha1 = sha1(norm or text)", not badsha, badsha[:5])
# reading order: segments appear in index order, span numbers ascend inside a segment
pos = {i: k for k, i in enumerate(seg_ids)}
order_bad = [(a['id'], b['id']) for a, b in zip(spans, spans[1:]) if pos.get(a.get('segment'), 0) > pos.get(b.get('segment'), 0)]
out("spans: in segment reading order", not order_bad, order_bad[:3])

# --- 1-based indices ------------------------------------------------------------
def walk(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}")
            if (k == 'pdf_index' or k.endswith('_index') or k in ('page', 'segment_start', 'segment_end')) and isinstance(v, int):
                yield path + '.' + k, v
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")


zero = []
for rel in ('labels.json', 'outline.json', 'units.json', 'segments/index.json', 'report.json'):
    j, _ = jload(rel)
    if j is not None:
        zero += [(rel, p, v) for p, v in walk(j) if v < 1]
out("indices: every *_index/page field is >= 1 (1-based)", not zero, zero[:5])

labels, _ = jload('labels.json')
if labels is not None:
    pages = labels.get('pages', [])
    pi = [p.get('pdf_index') for p in pages]
    out("labels: pdf_index runs 1..N", pi == list(range(1, len(pi) + 1)), pi[:5])
    nonstr = [p.get('pdf_index') for p in pages if not isinstance(p.get('label'), str) or not p['label']]
    out("labels: every label is a non-empty string", not nonstr, nonstr[:5])
    nosrc = [p.get('pdf_index') for p in pages if not p.get('label_source')]
    out("labels: every page has label_source", not nosrc, nosrc[:5])
    mm = labels.get('label_to_pdf')
    if isinstance(mm, dict):
        pmap = {p['pdf_index']: p['label'] for p in pages}
        miss = [i for i, l in pmap.items() if i not in mm.get(l, [])]
        extra = [(l, i) for l, v in mm.items() for i in v if pmap.get(i) != l]
        out("labels: label_to_pdf multimap consistent", not miss and not extra, f"missing {miss[:5]} extra {extra[:5]}")
    else:
        out("labels: label_to_pdf multimap present", False, f"keys {list(labels)}")
    if segidx and pages:
        out("labels: one page per PDF segment", len(pages) == len(segidx), f"{len(pages)} pages, {len(segidx)} segments")

# --- outline covers all segments --------------------------------------------------
outline, e = jload('outline.json')
items = outline if isinstance(outline, list) else (outline or {}).get('items', []) if isinstance(outline, dict) else []
out("schema: outline.json parses with items", bool(items), e or f"type {type(outline).__name__}")
if items:
    bad = [o for o in items if not all(k in o for k in ('title', 'depth')) or not str(o.get('title', '')).strip()]
    out("outline: every item has title and depth", not bad, bad[:3])
    bad = [o for o in items if not isinstance(o.get('depth'), int) or o['depth'] < 1]
    out("outline: depth is int >= 1", not bad, bad[:3])
    # Coverage: each item has a segment range [start, end] (preferred) or a start; segments
    # between the first item's start and the end must all fall in some range, and the first
    # segment must be covered too (front matter belongs to some item).
    def rng(o):
        r = o.get('segment_range') or o.get('segments')
        if isinstance(r, list) and len(r) == 2:
            return r[0], r[1]
        if 'seg_start' in o:
            return o['seg_start'], o.get('seg_end', o['seg_start'])
        if 'segment_start' in o:
            return o['segment_start'], o.get('segment_end', o['segment_start'])
        return None
    rs = [rng(o) for o in items]
    if all(r is not None for r in rs):
        covered = set()
        unknown = []
        for a, b in rs:
            if a not in pos or b not in pos:
                unknown.append((a, b))
                continue
            covered.update(seg_ids[pos[a]:pos[b] + 1])
        out("outline: ranges name real segments", not unknown, unknown[:5])
        unc = [s for s in seg_ids if s not in covered]
        out(f"outline: ranges cover all {len(seg_ids)} segments", not unc, f"{len(unc)} uncovered, first {unc[:5]}")
    else:
        out("outline: every item has a segment range", False, f"item keys {list(items[0])}")

# --- units -----------------------------------------------------------------
units, e = jload('units.json')
ulist = units if isinstance(units, list) else (units or {}).get('units', []) if isinstance(units, dict) else []
out("schema: units.json parses with units", bool(ulist), e or f"type {type(units).__name__}")
if ulist and spans:
    span_tok = {s['id']: toks(s['text']) for s in spans}
    total = sum(span_tok.values())
    uids = [u.get('id') for u in ulist]
    out("units: ids unique and slug-safe", len(uids) == len(set(uids)) and all(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}', str(i)) for i in uids), uids[:5])

    def members(u):
        if isinstance(u.get('spans'), list):
            return u['spans']
        if u.get('span_start') in ids and u.get('span_end') in ids:
            return ids[ids.index(u['span_start']):ids.index(u['span_end']) + 1]
        r = u.get('span_range')
        if isinstance(r, list) and len(r) == 2 and r[0] in ids and r[1] in ids:
            return ids[ids.index(r[0]):ids.index(r[1]) + 1]
        r = u.get('segment_range') or u.get('segments')
        if isinstance(r, list) and len(r) == 2 and r[0] in pos and r[1] in pos:
            segs = set(seg_ids[pos[r[0]]:pos[r[1]] + 1])
            return [s['id'] for s in spans if s['segment'] in segs]
        return None
    mem = [members(u) for u in ulist]
    if any(m is None for m in mem):
        out("units: each unit names its spans (spans | span_range | segment_range)", False, f"unit keys {list(ulist[0])}")
    else:
        flat = [i for m in mem for i in m]
        out("units: cover every span exactly once, in reading order", flat == ids,
            f"{len(flat)} listed vs {len(ids)} spans; dup {len(flat) - len(set(flat))}; missing {len(set(ids) - set(flat))}")
        sizes = [(u.get('id'), round(sum(span_tok.get(i, 0) for i in m))) for u, m in zip(ulist, mem)]
        stated = [(u.get('id'), u.get('tokens')) for u in ulist]
        drift = [(a, b, s[1]) for (a, b), s in zip(stated, sizes) if not isinstance(b, (int, float)) or abs(b - s[1]) > max(50, 0.1 * s[1])]
        out("units: stated tokens match chars/4 of member spans (10%)", not drift, drift[:5])
        if total < UNIT_MIN:
            # plan: units are merged to the 3-8k target; a whole source under 3k cannot reach it, so it is one unit
            out(f"units: tiny source ({round(total)} tok < {UNIT_MIN}) is one unit", len(ulist) == 1, sizes)
        else:
            # Units never cross a top-level outline entry (builder's rule, accepted): a unit under 3k is allowed
            # only when it is a whole top-level entry that is itself under 3k (tester recount).
            tops = [o for o in items if o.get('depth') == 1]
            def top_members(o):
                a, b = o.get('span_start'), o.get('span_end')
                return ids[ids.index(a):ids.index(b) + 1] if a in ids and b in ids else []
            small_tops = {tuple(m) for m in map(top_members, tops) if m and sum(span_tok[i] for i in m) < UNIT_MIN}
            oob = [x for x, m in zip(sizes, mem) if not UNIT_MIN <= x[1] <= UNIT_MAX and not (x[1] < UNIT_MIN and tuple(m) in small_tops)]
            und = [x for x, m in zip(sizes, mem) if x[1] < UNIT_MIN and tuple(m) in small_tops]
            out(f"units: all {len(sizes)} within {UNIT_MIN}-{UNIT_MAX} tokens ({len(und)} whole small top-level entries)", not oob,
                f"{len(oob)} out of bounds: {oob[:8]}")

# --- report -----------------------------------------------------------------
rep, e = jload('report.json')
out("schema: report.json parses", rep is not None, e)
if rep is not None:
    flat = json.dumps(rep)
    cap = [k for k in ('captured_chars', 'total_chars') if k in flat]
    out("report: captured vs total text reported", len(cap) == 2, f"keys {list(rep)}")
    def find(o, k):
        if isinstance(o, dict):
            if k in o:
                return o[k]
            for v in o.values():
                r = find(v, k)
                if r is not None:
                    return r
    c, t = find(rep, 'captured_chars'), find(rep, 'total_chars')
    if isinstance(c, (int, float)) and isinstance(t, (int, float)):
        out("report: captured <= total, total > 0", 0 < t and 0 <= c <= t, f"captured {c} total {t}")

# --- anchor round-trip through the gate -----------------------------------------------
sys.path.insert(0, os.path.abspath(VERIFY_DIR))
try:
    import source_verify as sv  # noqa: E402
except Exception as ex:  # noqa: BLE001
    out("verify: gate importable", False, ex)
    sys.exit(0)
if spans:
    smap, order = sv.load_spans(STORE)
    rnd = random.Random(1)
    fails, uncitable = [], []
    for s in spans:
        w = s['text'].split()
        starts = [0] if len(w) <= 8 else rnd.sample(range(len(w) - 7), min(20, len(w) - 7))
        why_all = []
        for k in starts:
            a = s['text'] if len(w) <= 8 else ' '.join(w[k:k + 8])
            st, why, _ = sv.check_cite(s['id'], a, smap, order, None)
            if st == 'ok':
                break
            why_all.append((why, a[:80]))
        else:
            # windows rejected only by the anchor-size/stopword floor mean the span has no citable text
            if all(x[0].startswith(('anchor is only stopwords', 'anchor too short', 'anchor contains citation syntax')) for x in why_all):
                uncitable.append((s['id'], s['text'][:60]))
            else:
                fails.append((s['id'],) + why_all[0])
    out(f"verify: an anchor from every span round-trips ({len(spans)} spans)", not fails, f"{len(fails)} fail: {fails[:4]}")
    out("verify: spans with no citable anchor <= 1%", len(uncitable) <= 0.01 * len(spans), f"{len(uncitable)}: {uncitable[:4]}")
    # wrong id: an anchor from span X cited on span Y (whose text lacks it) must fail
    leak = []
    sample = rnd.sample(spans, min(200, len(spans)))
    for s in sample:
        w = s['text'].split()
        if len(w) < 10:
            continue
        a = ' '.join(w[1:9])
        other = rnd.choice(spans)
        if other['id'] == s['id'] or sv.norm(a) in sv.norm(other['text']):
            continue
        st, _, _ = sv.check_cite(other['id'], a, smap, order, None)
        if st == 'ok' and sv.find_tokens(a, other['text']) is None and other.get('kind') not in sv.NOISY_KINDS:
            leak.append((s['id'], other['id'], a))
    out("verify: anchor cited on the wrong span is rejected", not leak, leak[:3])
