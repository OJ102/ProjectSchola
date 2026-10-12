#!/usr/bin/env python3
"""Tutor-reply citation check (spike 0d, prototype of source-cite-check.sh). Tester-owned.

usage: check_tutor.py <spikes-dir> <workdir>
Builds a synthetic store + brief, feeds crafted replies.json to `dryrun.py --analyze`
(no model calls), and checks flagged ids, quote mismatches and the rendered log.
"""
import json, os, subprocess, sys

SP, W = sys.argv[1], sys.argv[2]
store, out = os.path.join(W, 'store'), os.path.join(W, 'dr')
os.makedirs(store, exist_ok=True)
os.makedirs(out, exist_ok=True)
sys.path.insert(0, SP)
from srcnorm import norm  # noqa: E402

SPANS = {
    'p0010.1': 'Tides are driven by the gravitational pull of the moon and, to a lesser degree, the sun.',
    'p0010.2': 'Spring tides happen when the sun and moon align and their pulls add up together.',
    'p0011.1': 'Neap tides happen when the sun and moon sit at right angles to each other.',
    'p0050.1': 'Ocean currents carry heat from the equator toward the poles over many years.',
}
with open(os.path.join(store, 'spans.jsonl'), 'w') as f:
    for sid, t in SPANS.items():
        f.write(json.dumps({'id': sid, 'segment': sid.split('.')[0], 'locator': sid[1:5].lstrip('0'),
                            'kind': 'para', 'text': t, 'norm': norm(t)}) + '\n')
brief = os.path.join(W, 'brief.md')
open(brief, 'w').write('# B\n\n## Core claims\n'
                       '- Moon drives tides [[cite:p0010.1 "driven by the gravitational pull of the moon"]]\n'
                       '- Spring tides [[cite:p0010.2 "when the sun and moon align and their pulls"]]\n')

REPLIES = {
    'ok_plain': 'The moon is the main driver [[cite:p0010.1]].',
    'ok_quote': 'The book says “Tides are driven by the gravitational pull of the moon” [[cite:p0010.1]].',
    'forged_id_in_store': 'Currents move heat [[cite:p0050.1]].',
    'forged_id_unknown': 'Something invented [[cite:p9999.9]].',
    'quote_wrong_span': 'It says "when the sun and moon sit at right angles" [[cite:p0010.2]].',
    'quote_fabricated': 'It says "the moon alone causes every tide on earth" [[cite:p0010.1]].',
    'quote_with_brackets': 'It says "the moon alone ]] causes every tide on earth" [[cite:p0010.1]].',
    'quote_with_newline': 'It says "the moon alone causes\nevery tide on earth" [[cite:p0010.1]].',
    'anchor_forged_in_cite': 'Tides [[cite:p0010.1 "the sun alone causes every tide on earth"]].',
}
order = list(REPLIES)
json.dump([{'user': k, 'tutor': v} for k, v in REPLIES.items()], open(os.path.join(out, 'replies.json'), 'w'))
json.dump({'session': '00000000-tester-no-such-session', 'turns': [{'cost_usd': 0}]}, open(os.path.join(out, 'dryrun.json'), 'w'))
r = subprocess.run([sys.executable, os.path.join(SP, 'dryrun.py'), store, brief, out, '--analyze'],
                   capture_output=True, text=True)
if r.returncode:
    print(f"FAIL tutor: dryrun --analyze runs on crafted replies :: {(r.stderr or r.stdout)[-400:]}")
    sys.exit()
rep = json.load(open(os.path.join(out, 'dryrun.json')))
log = open(os.path.join(out, 'dryrun-log.md')).read().split('---')
flag = rep['tutor_cites_flagged']
qm = {q['id'] + '|' + q['quote'][:20]: q for q in rep['tutor_quote_mismatch']}
qids = [(q['id'], q['quote'][:30], q.get('repoint_to')) for q in rep['tutor_quote_mismatch']]


def out_(name, good, detail):
    print(f"ok {name}" if good else f"FAIL {name} :: " + detail.replace('\n', ' '))


def logpart(k):
    return log[order.index(k)]


out_("tutor: id in brief not flagged", 'p0010.1' not in flag and 'p0010.2' not in flag, f"flagged {flag}")
out_("tutor: id in store but not brief/lookup flagged", 'p0050.1' in flag, f"flagged {flag}")
out_("tutor: unknown id flagged", 'p9999.9' in flag, f"flagged {flag}")
out_("tutor: forged ids render '(citation removed)'",
     '(citation removed)' in logpart('forged_id_in_store') and '(citation removed)' in logpart('forged_id_unknown'),
     logpart('forged_id_in_store')[-120:] + ' / ' + logpart('forged_id_unknown')[-120:])
out_("tutor: correct smart-quoted quote not a mismatch", not any('Tides are driven' in q['quote'] for q in rep['tutor_quote_mismatch']), str(qids))
out_("tutor: quote from other span re-pointed", any(q['id'] == 'p0010.2' and q.get('repoint_to') == 'p0011.1' for q in rep['tutor_quote_mismatch']), str(qids))
out_("tutor: fabricated quote caught (repoint_to None)",
     any(q['id'] == 'p0010.1' and 'moon alone causes every' in q['quote'] and q.get('repoint_to') is None for q in rep['tutor_quote_mismatch']), str(qids))
out_("tutor: fabricated quote containing ']]' caught", any(']]' in q['quote'] or 'moon alone ]]' in q['quote'] for q in rep['tutor_quote_mismatch'])
     or '(citation removed)' in logpart('quote_with_brackets'), f"mismatches {qids}; log: {logpart('quote_with_brackets').strip()[-160:]}")
out_("tutor: fabricated quote with a newline caught", any('causes' in q['quote'] and 'every' in q['quote'] and q['quote'].count('\n') for q in rep['tutor_quote_mismatch'])
     or '(citation removed)' in logpart('quote_with_newline'), f"mismatches {qids}; log: {logpart('quote_with_newline').strip()[-160:]}")
out_("tutor: forged anchor inside [[cite:id \"...\"]] caught",
     any('sun alone' in q['quote'] for q in rep['tutor_quote_mismatch']) or '(citation removed)' in logpart('anchor_forged_in_cite'),
     f"mismatches {qids}; log: {logpart('anchor_forged_in_cite').strip()[-160:]}")
out_("tutor: log renders re-pointed quote at its real span (p. 11)", '(p. 11)' in logpart('quote_wrong_span'),
     logpart('quote_wrong_span').strip()[-160:])
out_("tutor: log does not present a fabricated quote as cited", '(p. 10)' not in logpart('quote_fabricated'),
     logpart('quote_fabricated').strip()[-160:])
