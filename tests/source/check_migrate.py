#!/usr/bin/env python3
"""Span-id migration on a hash change (tester).

usage: check_migrate.py setup <store> <workdir>   plant two briefs, then edit <workdir>/b.tex
       check_migrate.py check <store> <workdir>   after re-ingesting the rebuilt b.pdf
"""
import json
import os
import sys

mode, d, m = sys.argv[1:4]


def o(n, g, x=''):
    print(("ok " if g else "FAIL ") + n + ("" if g else f" :: {str(x)[:400]}"))


def setup():
    S = [json.loads(l) for l in open(f"{d}/spans.jsonl")]
    paras = [s for s in S if s['kind'] == 'para' and 'marker' in s['text']]
    keep = paras[40]; gone = paras[41]
    os.makedirs(f"{d}/briefs", exist_ok=True)
    open(f"{d}/briefs/keep.md", "w").write(f'## Core claims\n- x [[cite:{keep["id"]} "{" ".join(keep["text"].split()[:8])}"]]\n')
    open(f"{d}/briefs/gone.md", "w").write(f'## Core claims\n- y [[cite:{gone["id"]} "{" ".join(gone["text"].split()[:8])}"]]\n')
    open(f"{d}/briefs/gone.verify.json", "w").write('{"status": "verified"}')
    json.dump({"keep": keep, "gone": gone}, open(f"{m}/picked.json", "w"))
    # edit the source: insert two paragraphs near the start (shifts ids), delete the "gone" paragraph's marker text
    t = open(f"{m}/b.tex").read()
    mk = [w for w in gone['text'].split() if w.startswith('marker')][0]
    lines = t.split('\n\n')
    lines = [p for p in lines if mk not in p]
    i = next(k for k, p in enumerate(lines) if '\\chapter{Tides}' in p)
    lines.insert(i + 1, 'Inserted paragraph one about the tidal shift and new words appear here for testing.')
    lines.insert(i + 1, 'Inserted paragraph two about the tidal shift and other words appear here for testing.')
    open(f"{m}/b.tex", "w").write('\n\n'.join(lines))
    print("ok setup briefs and edited source")


def check():
    def o(n, g, x=''): print(("ok " if g else "FAIL ") + n + ("" if g else f" :: {str(x)[:400]}"))
    pk = json.load(open(f"{m}/picked.json"))
    S = {json.loads(l)['id']: json.loads(l) for l in open(f"{d}/spans.jsonl")}
    mig = json.load(open(f"{d}/migration.json")) if os.path.exists(f"{d}/migration.json") else {}
    o("migration.json written with old/new sha256", mig.get('old_sha256') and mig.get('new_sha256') and mig['old_sha256'] != mig['new_sha256'], list(mig))
    new = mig.get('map', {}).get(pk['keep']['id'])
    o("kept span mapped to a new id with the same text", new and S.get(new, {}).get('sha1') == pk['keep']['sha1'], (pk['keep']['id'], new))
    o("kept span id actually moved (ids rebuilt)", new and new != pk['keep']['id'], (pk['keep']['id'], new))
    o("removed span listed as unmatched", pk['gone']['id'] in mig.get('unmatched', []), mig.get('unmatched', [])[:5])
    b = open(f"{d}/briefs/keep.md").read() if os.path.exists(f"{d}/briefs/keep.md") else ''
    o("brief with all ids mapped rewritten in place", new and f"[[cite:{new} " in b, b)
    o("brief with an unmatched id moved to briefs/.stale/ with its verify.json",
      os.path.exists(f"{d}/briefs/.stale/gone.md") and os.path.exists(f"{d}/briefs/.stale/gone.verify.json") and not os.path.exists(f"{d}/briefs/gone.md"), os.listdir(f"{d}/briefs"))
    src = json.load(open(f"{d}/source.json"))
    o("source.json sha256 is the new hash", src.get('sha256') == mig.get('new_sha256'), src.get('sha256'))
    rep = json.load(open(f"{d}/report.json"))
    o("report.json has a migration summary", 'migration' in rep, list(rep))


{'setup': setup, 'check': check}[mode]()
