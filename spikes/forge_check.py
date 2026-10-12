"""Spike 0c: mutate every cite of a verified brief and confirm the gate rejects each forgery.

usage: python3 spikes/forge_check.py <storedir> <brief.md> <unit.ids>

Mutations per cite: swap a word, insert "not", change a number, drop the
anchor to 3 words, cite the next span id, cite a span outside the unit,
join the anchor with text from the next span, remove the cite entirely.
Prints a table of mutation -> rejected/total. Exit 1 if any forgery passes.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_verify as sv  # noqa: E402


def main():
    store, brief, ids = sys.argv[1:4]
    spans, order = sv.load_spans(store)
    allowed = {l.strip() for l in open(ids) if l.strip()}
    outside = next((i for i in order if i not in allowed), None)
    text = open(brief, encoding="utf-8").read()
    cites = sv.CITE.findall(text)
    tmp = brief + ".forge.md"
    results, leaks = {}, []

    def run(name, mutated):
        open(tmp, "w").write(mutated)
        rep = sv.verify(store, tmp, [ids])
        r = results.setdefault(name, [0, 0])
        r[1] += 1
        if not rep["pass"]:
            r[0] += 1
        else:
            leaks.append((name, mutated[:0]))

    for sid, anchor in cites:
        orig = f'[[cite:{sid} "{anchor}"]]'
        words = anchor.split()
        k = order.index(sid)
        muts = {}
        if len(words) >= 3:
            w = words[:]
            w[len(w) // 2] = "banana"
            muts["word swapped"] = f'[[cite:{sid} "{" ".join(w)}"]]'
            w = words[:]
            w.insert(len(w) // 2, "not")
            muts["negation inserted"] = f'[[cite:{sid} "{" ".join(w)}"]]'
            cut = sv.norm(" ".join(words[:3]))
            if len(cut) < sv.MIN_CHARS and cut != spans[sid]["norm"]:  # else still a legal quote
                muts["anchor cut to 3 words"] = f'[[cite:{sid} "{" ".join(words[:3])}"]]'
        if re.search(r"\d", anchor):
            muts["number changed"] = f'[[cite:{sid} "{re.sub(r"\d", lambda m: str((int(m.group()) + 1) % 10), anchor, count=1)}"]]'
        if k + 1 < len(order) and spans[order[k + 1]]["norm"].find(sv.norm(anchor)) < 0:
            muts["next span id"] = f'[[cite:{order[k + 1]} "{anchor}"]]'
        if outside:
            muts["span outside unit"] = f'[[cite:{outside} "{" ".join(spans[outside]["text"].split()[:12])}"]]'
        if k + 1 < len(order):
            nxt = spans[order[k + 1]]["text"].split()[:4]
            muts["anchor spans two spans"] = f'[[cite:{sid} "{" ".join(words[-3:] + nxt)}"]]'
        muts["cite removed"] = ""
        for name, rep in muts.items():
            run(name, text.replace(orig, rep, 1))
    os.remove(tmp)
    print(f"cites mutated: {len(cites)}")
    for name, (rej, tot) in results.items():
        print(f"  {name:<24} rejected {rej}/{tot}")
    print("LEAKS:", len(leaks))
    sys.exit(1 if leaks else 0)


if __name__ == "__main__":
    main()
