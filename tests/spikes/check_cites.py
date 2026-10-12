#!/usr/bin/env python3
"""Citation-gate checks (spike 0c) on a hand-made store. Tester-owned.

usage: check_cites.py <spikes-dir> <workdir>
Prints "ok <name>" / "FAIL <name> :: <detail>" lines.
"""
import json, os, subprocess, sys

SP, W = sys.argv[1], sys.argv[2]
STORE = os.path.join(W, 'store')
os.makedirs(STORE, exist_ok=True)
sys.path.insert(0, SP)
from srcnorm import norm  # noqa: E402  (builder's normalizer, used only to fill the "norm" field)

SPANS = [
    # id, kind, text (as stored by the adapter)
    ('p0010.1', 'para', 'Tides are driven by the gravitational pull of the moon and, to a lesser degree, the sun.'),
    ('p0010.2', 'para', 'Super‐vised ﬁnetuning uses “high-quality” demonstration data written by human labelers.'),
    ('p0010.3', 'para', 'This failure mode is not uncommon in practice for most large language models today.'),
    ('p0010.4', 'para', 'Short span here.'),
    ('p0010.5', 'para', 'It is in the way of the one that is not to be, and so it was for them all.'),
    ('p0010.6', 'para', 'The model was trained on 300 billion tokens over roughly 34 days of compute.'),
    ('p0012.1', 'para', 'Use non-toxic paints at -40 degrees and mix 1/2 cup of water before you start.'),
    ('p0012.2', 'para', 'Wait 3-5 days before use and then rinse the brush twice with 2.5 litres of water.'),
    ('p0012.3', 'para', "The model doesn't overfit when the dataset is large and properly deduplicated."),
    ('p0011.1', 'para', 'Reward models score each response so that preference finetuning can follow.'),
    ('t00:01:10', 'cue', 'Gradient descent takes a step in the direction that lowers the loss the fastest.'),
    ('t00:01:19', 'cue', 'That direction is the negative of the gradient.'),
    ('t00:02:30', 'cue', 'The learning rate sets the size of each step. Too large and training diverges.'),
    ('t00:02:41', 'cue', 'Too small and training crawls along for ages.'),
    ('t00:03:00', 'cue', 'This vaccine can cure the disease in most patients after two doses.'),
    ('t00:03:10', 'cue', 'Um, masks, uh, prevent the spread of the virus in crowded rooms.'),
    ('t00:03:20', 'cue', 'The committee will accept the proposal from every member of the colour group.'),
]
with open(os.path.join(STORE, 'spans.jsonl'), 'w') as f:
    for sid, kind, text in SPANS:
        seg = sid.split('.')[0] if sid.startswith('p') else 'w0001'
        f.write(json.dumps({'id': sid, 'segment': seg, 'locator': sid, 'kind': kind, 'text': text,
                            'norm': norm(text)}, ensure_ascii=False) + '\n')
ALLOW = os.path.join(W, 'unit.ids')
open(ALLOW, 'w').write('\n'.join(s[0] for s in SPANS if s[0] != 'p0011.1') + '\n')  # p0011.1 is outside the unit


def run(body):
    p = os.path.join(W, 'b.md')
    open(p, 'w').write(body)
    r = subprocess.run(['bash', os.path.join(SP, 'source-verify.sh'), STORE, p, '--allow', ALLOW],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


# Strict schema (decision 17): exact "## " headings, Core claims + Quiz seeds required, Where one line.
SEED = '1. What drives tides? [[cite:p0010.1 "Tides are driven by the gravitational pull of the moon"]]'


def brief(claim, where='Synthetic unit'):
    return f"## Where\n{where}\n\n## Core claims\n{claim}\n\n## Quiz seeds\n{SEED}\n"


def expect(name, body, want_pass):
    rc, o = run(body)
    good = (rc == 0) == want_pass
    print(f"ok {name}" if good else f"FAIL {name} :: expected {'PASS' if want_pass else 'reject'}, got rc={rc}: {o[:300]}")


C = lambda sid, a: f'[[cite:{sid} "{a}"]]'
# --- baseline and normalization (must pass)
expect("cite: verbatim anchor passes", brief("- Tides follow the moon. " + C('p0010.1', 'driven by the gravitational pull of the moon')), True)
expect("cite: case and whitespace normalized", brief("- x " + C('p0010.1', 'DRIVEN  by the Gravitational   pull of the moon')), True)
expect("cite: U+2010 hyphen + ligature + smart quotes normalized",
       brief("- SFT " + C('p0010.2', 'Super-vised finetuning uses "high-quality" demonstration data')), True)
expect("cite: ASCII anchor vs curly quotes in span", brief("- SFT " + C('p0010.2', "finetuning uses \"high-quality\" demonstration data written")), True)
expect("cite: short span quoted whole passes", brief("- s " + C('p0010.4', 'Short span here.')), True)
expect("cite: section 'Where' may name a page", brief("- x " + C('p0010.1', 'driven by the gravitational pull of the moon'), where='Chapter 2, page 78'), True)
expect("fuzzy: filler deletion passes (um/uh dropped)", brief("- m " + C('t00:03:10', 'masks prevent the spread of the virus in crowded rooms')), True)
# --- forgeries (must be rejected)
expect("forge: changed word", brief("- x " + C('p0010.1', 'driven by the gravitational pull of the sun')), False)
expect("forge: wrong (existing) span id", brief("- x " + C('p0010.6', 'driven by the gravitational pull of the moon')), False)
expect("forge: unknown span id", brief("- x " + C('p9999.1', 'driven by the gravitational pull of the moon')), False)
expect("forge: span outside unit", brief("- x " + C('p0011.1', 'Reward models score each response so that preference')), False)
expect("forge: claim item with no cite", brief("- Tides follow the moon."), False)
expect("forge: one good cite + no cite on second claim", brief("- a " + C('p0010.1', 'driven by the gravitational pull of the moon') + "\n- Rewards come first."), False)
expect("forge: near-miss one letter", brief("- x " + C('p0010.1', 'driven by the gravitational pul of the moon')), False)
expect("forge: near-miss extra word", brief("- x " + C('p0010.1', 'driven by the strong gravitational pull of the moon')), False)
expect("forge: number changed", brief("- x " + C('p0010.6', 'trained on 400 billion tokens over roughly 34 days')), False)
expect("forge: anchor across two spans", brief("- x " + C('p0010.1', 'lesser degree, the sun. Super-vised finetuning uses')), False)
expect("forge: [[ inside anchor", brief("- x " + C('p0010.1', 'driven by the [[gravitational pull of the moon')), False)
expect("forge: malformed cite (no anchor)", brief("- x [[cite:p0010.1]]"), False)
# word-boundary near-miss: substring of "not uncommon" that drops the negation
expect("forge: partial-word anchor flips meaning ('uncommon' -> 'common')",
       brief("- x " + C('p0010.3', 'common in practice for most large language models')), False)
expect("forge: anchor starting mid-word", brief("- x " + C('p0010.1', 'ides are driven by the gravitational pull')), False)
# --- anchor floor (decision 1)
expect("floor: 5 words <30 chars rejected", brief("- x " + C('p0010.1', 'pull of the moon and')), False)
expect("floor: 6 words passes", brief("- x " + C('p0010.1', 'pull of the moon and, to')), True)
expect("floor: >=30 chars with <6 words passes", brief("- x " + C('p0010.2', 'demonstration data written by human')), True)
expect("floor: stopword-only anchor rejected", brief("- x " + C('p0010.5', 'and so it was for them')), False)
LONG = 'Gradient descent takes a step in the direction that lowers the loss the fastest. That direction is the negative of the gradient. The learning rate'
expect("floor: >25 words rejected (fuzzy cue, would otherwise pass)", brief("- x " + C('t00:01:19', LONG)), False)
expect("floor: short span partially quoted rejected", brief("- s " + C('p0010.4', 'Short span')), False)
# --- self-written locators
expect("loc: 'page 12' in a claim rejected", brief("- On page 12 the book says " + C('p0010.1', 'driven by the gravitational pull of the moon')), False)
expect("loc: 'p. 12' in a claim rejected", brief("- See p. 12 " + C('p0010.1', 'driven by the gravitational pull of the moon')), False)
expect("loc: '00:01:10' in a claim rejected", brief("- At 00:01:10 " + C('t00:01:10', 'gradient descent takes a step in the direction')), False)
expect("loc: mm:ss '1:10' in a claim rejected", brief("- At 1:10 the lecturer says " + C('t00:01:10', 'gradient descent takes a step in the direction')), False)
# --- fuzzy only for cue/ocr, with critical tokens exact (decision 2)
expect("fuzzy: not allowed for para (misspelling)", brief("- x " + C('p0010.1', 'drivn by the gravitational pull of the moon')), False)
expect("fuzzy: negation inserted rejected", brief("- x " + C('t00:01:10', 'gradient descent does not take a step in the direction that lowers')), False)
expect("fuzzy: comparative swapped rejected", brief("- x " + C('t00:02:30', 'the learning rate sets the size of each step. too small and training diverges')), False)
expect("fuzzy: content word swapped rejected", brief("- x " + C('t00:01:10', 'gradient descent takes a step in the direction that raises the loss')), False)
expect("fuzzy: neighbour cue's text under this id rejected", brief("- x " + C('t00:01:10', 'that direction is the negative of the gradient')), False)
# --- claim lines that are not list items
expect("forge: prose claim line in Core claims without cite", brief("Tides are caused by the sun alone.\n- y " + C('p0010.1', 'driven by the gravitational pull of the moon')), False)
expect("forge: bold pseudo-heading section, item without cite",
       "## Where\nx\n\n## Core claims\n- y " + C('p0010.1', 'driven by the gravitational pull of the moon') + "\n**Examples**\n- Tides are caused by the sun alone.\n\n## Quiz seeds\n" + SEED + "\n", False)
expect("schema: baseline brief is schema-valid", brief("- ok " + C('p0010.1', 'driven by the gravitational pull of the moon')), True)
expect("schema: missing Quiz seeds rejected", "## Where\nx\n\n## Core claims\n- a " + C('p0010.1', 'driven by the gravitational pull of the moon') + "\n", False)
expect("schema: unknown heading rejected", brief("- a " + C('p0010.1', 'driven by the gravitational pull of the moon')) + "\n## Fun facts\n- the moon is cheese\n", False)
expect("schema: bullet '•' claim without dash rejected", brief("• Tides are caused by the sun alone."), False)
expect("schema: table row claim rejected", brief("| claim | Tides are caused by the sun alone |"), False)
expect("loc: 'pg. 12' rejected", brief("- pg. 12 " + C('p0010.1', 'driven by the gravitational pull of the moon')), False)
expect("forge: anchor ending mid-word", brief("- x " + C('p0010.1', 'driven by the gravitational pull of the mo')), False)
expect("fuzzy: anchor mostly from the next cue rejected", brief("- x " + C('t00:01:10', 'loss the fastest. That direction is the')), False)
# --- decision 22: exact anchors align to real token boundaries
B22 = lambda sid, a: brief("- x " + C(sid, a))
expect("d22: control: anchor on token boundaries passes", B22('p0012.1', 'Use non-toxic paints at -40 degrees'), True)
expect("d22: 'toxic' inside 'non-toxic' rejected", B22('p0012.1', 'toxic paints at -40 degrees and mix'), False)
expect("d22: '40' inside '-40' rejected", B22('p0012.1', '40 degrees and mix 1/2 cup of water'), False)
expect("d22: anchor ending at '1' of '1/2' rejected", B22('p0012.1', 'at -40 degrees and mix 1'), False)
expect("d22: '2 cup' inside '1/2 cup' rejected", B22('p0012.1', '2 cup of water before you start.'), False)
expect("d22: '5 days' inside '3-5 days' rejected", B22('p0012.2', '5 days before use and then rinse'), False)
expect("d22: '5 litres' inside '2.5 litres' rejected", B22('p0012.2', '5 litres of water'), False)
expect("d22: anchor ending at 'doesn' before apostrophe rejected", B22('p0012.3', 'The model doesn'), False)
expect("d22: 't overfit' after apostrophe rejected", B22('p0012.3', 't overfit when the dataset is large'), False)
expect("d22: control: '3-5 days' whole passes", B22('p0012.2', 'Wait 3-5 days before use and then rinse'), True)
# --- decision 23: no fuzzy content-word substitutions
expect("d23: cause for cure rejected", B22('t00:03:00', 'this vaccine can cause the disease in most patients'), False)
expect("d23: present for prevent rejected", B22('t00:03:10', 'masks present the spread of the virus in crowded rooms'), False)
expect("d23: except for accept rejected", B22('t00:03:20', 'the committee will except the proposal from every member'), False)
expect("d23: misspelt content word rejected (gradiant)", B22('t00:01:10', 'gradiant descent takes a step in the direction that lowers the loss'), False)
expect("d23: US/UK variant allowed (color/colour)", B22('t00:03:20', 'the proposal from every member of the color group'), True)
expect("d23: digit vs number word allowed (2 doses)", B22('t00:03:00', 'cure the disease in most patients after 2 doses'), True)
rc, o = run(B22('t00:03:10', 'masks prevent the spread of the virus in crowded rooms'))
print("ok d23: fuzzy cite reported as fuzzy" if rc == 0 and 'fuzzy=1' in o else f"FAIL d23: fuzzy cite reported as fuzzy :: rc={rc} {o[:200]}")
# --- decision 24: every line in every section is cited or a fixed form
OKC = "- ok " + C('p0010.1', 'driven by the gravitational pull of the moon')
def full(extra):
    return f"## Where\nSynthetic unit\n\n## Core claims\n{OKC}\n\n{extra}\n\n## Quiz seeds\n{SEED}\n"
def full_after(extra):
    return f"## Where\nSynthetic unit\n\n## Core claims\n{OKC}\n\n## Quiz seeds\n{SEED}\n\n{extra}\n"
expect("d24: Prerequisites with a source assertion rejected", full("## Prerequisites\n- Tides are caused by the sun alone."), False)
expect("d24: Further reading entry without cite rejected", full_after("## Further reading\n- Darwin, The Tides (1898)."), False)
expect("d24: Further reading entry with cite passes",
       full_after("## Further reading\n- Reward model paper " + C('p0010.6', 'trained on 300 billion tokens over roughly 34 days')), True)
expect("d24: Source caveats free text rejected", full_after("## Source caveats\n- The author overstates the role of the moon."), False)
expect("d24: Source caveats fixed form '[unclear] <span>' passes", full_after("## Source caveats\n- [unclear] t00:01:19"), True)
expect("d24: Source caveats '[injection-flagged] <span>' passes", full_after("## Source caveats\n- [injection-flagged] p0010.5"), True)
expect("d24: Source caveats fixed form with trailing claim rejected", full_after("## Source caveats\n- [unclear] t00:01:19 the lecturer says the moon is cheese"), False)
expect("d24: Definitions line without cite rejected", full("## Definitions\n- Tide: a rise of the sea caused by the sun."), False)
expect("d24: stray text under Where (2nd line) rejected", "## Where\nSynthetic unit\nThe moon is cheese.\n\n## Core claims\n" + OKC + "\n\n## Quiz seeds\n" + SEED + "\n", False)
# lead ruling: <span> in caveats is a span id that exists in the unit; quoted text not allowed
expect("d24: caveat with span id outside unit rejected", full_after("## Source caveats\n- [unclear] p0011.1"), False)
expect("d24: caveat with unknown span id rejected", full_after("## Source caveats\n- [ocr-uncertain] p9999.1"), False)
expect("d24: caveat with quoted text instead of id rejected", full_after('## Source caveats\n- [unclear] "That direction is the negative of the gradient."'), False)
expect("d24: '[ocr-uncertain] <id>' passes", full_after("## Source caveats\n- [ocr-uncertain] t00:01:19"), True)
# lead ruling: brief status lives only in the verify.json sidecar; a status line in the brief is rejected
expect("schema: leading '<!-- status: verified -->' line rejected", "<!-- status: verified -->\n" + brief("- ok " + C('p0010.1', 'driven by the gravitational pull of the moon')), False)
expect("schema: leading '<!-- status: partial; dropped 1 -->' line rejected", "<!-- status: partial; dropped 1 item(s) -->\n" + brief("- ok " + C('p0010.1', 'driven by the gravitational pull of the moon')), False)
