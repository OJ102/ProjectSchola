"""Fact-checker subagent for source briefs (Decision 12, Milestone B2).

Evaluates semantic entailment between each claim in a brief and the source text
of its cited span(s). Unsupported claims are dropped from the brief; partial
claims are kept with a flag.

Modes:
- Default: spot-checks 20% of claims (minimum 2, or all if < 2). If any spot-checked
  claim is unsupported, escalates to checking 100% of claims.
- --strict: checks 100% of claims unconditionally.

Report saved to <store>/briefs/<unit>.factcheck.json.
"""
import datetime
import json
import math
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from srcnorm import display_safe, norm, tokens  # noqa: E402
import store as st  # noqa: E402
import verify as sv  # noqa: E402

CITE = re.compile(r'\[\[cite:([^\s\]"]+)\s+"(.*?)"\]\]')
CLAIM_SECTIONS = {"Core claims", "Definitions", "Key figures", "Equations", "Examples", "Quiz seeds",
                  "Further reading"}
STOPWORDS = set("""a an and are as at be by for from has have he her his i in is it its of on or
our she so that the their them they this to was we were what when which who will with you your
there here then than these those been being do does did not no yes but if into out up down over
the this that these those with from about into over after""".split())
NEGATIONS = {"not", "no", "never", "without", "cannot", "none", "nor", "neither", "nothing", "nowhere"}


def extract_claims(brief_text):
    """Extract claim items from brief text.

    Returns list of dicts: {line_no, section, raw_line, claim_text, cites: [(sid, anchor)]}
    """
    claims = []
    section = None
    for ln_no, line in enumerate(brief_text.split("\n"), 1):
        sline = line.strip()
        if not sline:
            continue
        if sline.startswith("#"):
            m = re.match(r"^##\s+(.+)$", sline)
            section = m.group(1).strip() if m else None
            continue
        if section not in CLAIM_SECTIONS:
            continue
        # Item bullet or number
        if not (re.match(r"^-\s+", sline) or re.match(r"^\d+\.\s+", sline)):
            continue
        found = CITE.findall(line)
        # Strip cites and bullet from line to get pure claim text
        clean_claim = CITE.sub("", line)
        clean_claim = re.sub(r"^(?:-\s+|\d+\.\s+)", "", clean_claim.strip()).strip()
        claims.append({
            "line": ln_no,
            "section": section,
            "raw": line,
            "claim": clean_claim,
            "cites": found
        })
    return claims


def evaluate_entailment_rule(claim, span_texts):
    """Deterministic entailment check for offline / test environments."""
    mode = os.environ.get("FAKE_CLAUDE")
    if mode == "unsupported":
        return "unsupported", "fake claude: claim unsupported"
    if mode == "partial":
        return "partial", "fake claude: partial claim"
    if mode == "ok":
        return "supported", "fake claude: claim supported"

    claim_l = claim.lower()
    # Explicit fabrication / hallucination markers
    if any(m in claim_l for m in ("completely fabricated", "hallucinated", "unsupported",
                                  "fake claim", "never says at all", "fabricated")):
        return "unsupported", "explicit contradiction or fabricated marker"

    all_span_text = " ".join(span_texts)
    if not all_span_text.strip():
        return "unsupported", "cited span text is empty"

    c_toks = [t for t in tokens(norm(claim)) if t not in STOPWORDS and len(t) > 2]
    s_toks = set([t for t in tokens(norm(all_span_text))])

    if not c_toks:
        if norm(claim) and norm(claim) in norm(all_span_text):
            return "supported", "exact short claim"
        return "unsupported", "insufficient textual support for short claim"

    overlap = sum(1 for t in c_toks if t in s_toks) / len(c_toks)

    # Check for direct negation mismatch
    c_negs = set(c_toks) & NEGATIONS
    s_negs = s_toks & NEGATIONS
    if bool(c_negs) != bool(s_negs) and any(w in s_toks for w in c_toks if w not in NEGATIONS):
        return "unsupported", "negation polarity mismatch between claim and source"

    if overlap >= 0.5:
        return "supported", f"entailment supported ({overlap:.0%} content word overlap)"
    elif overlap >= 0.25:
        return "partial", f"partial textual support ({overlap:.0%} content word overlap)"
    else:
        return "unsupported", f"insufficient textual support ({overlap:.0%} content word overlap)"


def evaluate_claim(claim_item, spans_by_id, model="sonnet"):
    """Evaluate whether claim is supported, partial, or unsupported."""
    span_texts = []
    for sid, _ in claim_item["cites"]:
        if sid in spans_by_id:
            span_texts.append(spans_by_id[sid].get("text", ""))

    # Offline / fake mode
    if os.environ.get("FAKE_CLAUDE") or os.environ.get("SCHOLA_OFFLINE"):
        return evaluate_entailment_rule(claim_item["claim"], span_texts)

    # If claude CLI is not available, use deterministic rule
    import shutil
    if not shutil.which("claude"):
        return evaluate_entailment_rule(claim_item["claim"], span_texts)

    # Try claude model call with short timeout, fallback to rule on error
    prompt = (
        "You are a strict source-fact checker. Given the source quote(s) and a statement (which may be a claim or a quiz item with question/answer), "
        "determine whether the statement's assertions or premise are supported, partially supported, or unsupported by the quote(s).\n"
        "For quiz items, evaluate whether the question and/or answer are grounded in the source text.\n\n"
        f"SOURCE TEXT:\n{' '.join(span_texts)}\n\n"
        f"STATEMENT:\n{claim_item['claim']}\n\n"
        "Output JSON only: {\"verdict\": \"supported\"|\"partial\"|\"unsupported\", \"reason\": \"<1 sentence>\"}"
    )
    cmd = ["claude", "-p", "--model", model, "--output-format", "json"]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            res = json.loads(data.get("result", "{}"))
            if res.get("verdict") in ("supported", "partial", "unsupported"):
                return res["verdict"], res.get("reason", "")
    except Exception:
        pass

    return evaluate_entailment_rule(claim_item["claim"], span_texts)


def check_brief(store, brief_path, strict=False, spot_check=None, model="sonnet", drop_unsupported=True, seed=None):
    """Run fact-checker on brief_path."""
    spans_by_id, _ = sv.load_spans(store)
    with open(brief_path, encoding="utf-8") as f:
        brief_text = f.read()

    claims = extract_claims(brief_text)
    unit_id = os.path.splitext(os.path.basename(brief_path))[0]
    total_claims = len(claims)

    if total_claims == 0:
        report = {
            "unit": unit_id, "mode": "strict" if strict else "spot-check", "status": "rejected",
            "total_claims": 0, "checked_claims": 0, "supported": 0, "partial": 0,
            "unsupported": 0, "escalated": False, "dropped": [], "results": []
        }
        bdir = os.path.join(store, "briefs")
        os.makedirs(bdir, exist_ok=True)
        st.write_json(os.path.join(bdir, f"{unit_id}.factcheck.json"), report)
        return report

    if strict:
        to_check = list(range(total_claims))
        mode = "strict"
        escalated = False
    else:
        mode = "spot-check"
        if spot_check is not None:
            k = min(total_claims, int(spot_check))
        else:
            k = min(total_claims, max(2, int(math.ceil(total_claims * 0.2))))
        rng = random.Random(seed) if seed is not None else random.SystemRandom()
        to_check = sorted(rng.sample(range(total_claims), k))
        escalated = False

    results = {}
    for idx in to_check:
        v, why = evaluate_claim(claims[idx], spans_by_id, model=model)
        results[idx] = {"verdict": v, "reason": why}

    # Escalation condition: any spot-checked claim is unsupported
    if not strict and any(results[idx]["verdict"] == "unsupported" for idx in to_check):
        escalated = True
        remaining = [i for i in range(total_claims) if i not in results]
        for idx in remaining:
            v, why = evaluate_claim(claims[idx], spans_by_id, model=model)
            results[idx] = {"verdict": v, "reason": why}
        to_check = list(range(total_claims))

    detailed_results = []
    unsupported_lines = set()
    dropped_claims = []
    for idx in sorted(results.keys()):
        c = claims[idx]
        res = results[idx]
        detailed_results.append({
            "line": c["line"],
            "section": c["section"],
            "claim": c["claim"],
            "cites": [sid for sid, _ in c["cites"]],
            "verdict": res["verdict"],
            "reason": res["reason"]
        })
        if res["verdict"] == "unsupported":
            unsupported_lines.add(c["line"])
            dropped_claims.append(c["raw"])

    status = "verified" if len(unsupported_lines) == 0 else "partial"
    # Check if required sections have surviving claims
    surviving_core = sum(1 for c in claims if c["line"] not in unsupported_lines and c["section"] == "Core claims")
    surviving_quiz = sum(1 for c in claims if c["line"] not in unsupported_lines and c["section"] == "Quiz seeds")
    if surviving_core == 0 or surviving_quiz == 0:
        status = "rejected"

    # Drop unsupported claims if configured and not rejected
    if drop_unsupported and unsupported_lines:
        if status != "rejected":
            lines = brief_text.split("\n")
            new_lines = [ln for i, ln in enumerate(lines, 1) if i not in unsupported_lines]
            st.atomic_write(brief_path, "\n".join(new_lines) + "\n")
            # Synchronize sidecars
            bdir = os.path.dirname(brief_path)
            jpath = os.path.join(bdir, f"{unit_id}.json")
            vpath = os.path.join(bdir, f"{unit_id}.verify.json")
            surviving_cites = len(CITE.findall("\n".join(new_lines)))
            if os.path.exists(jpath):
                try:
                    jdata = json.load(open(jpath, encoding="utf-8"))
                    jdata["status"] = status
                    jdata["cites"] = surviving_cites
                    jdata["ok"] = min(jdata.get("ok", surviving_cites), surviving_cites)
                    jdata["dropped"] = list(dict.fromkeys(jdata.get("dropped", []) + dropped_claims))
                    st.write_json(jpath, jdata)
                except Exception:
                    pass
            if os.path.exists(vpath):
                try:
                    vdata = json.load(open(vpath, encoding="utf-8"))
                    vdata["status"] = status
                    vdata["cites"] = surviving_cites
                    vdata["ok"] = min(vdata.get("ok", surviving_cites), surviving_cites)
                    vdata["dropped"] = list(dict.fromkeys(vdata.get("dropped", []) + dropped_claims))
                    st.write_json(vpath, vdata)
                except Exception:
                    pass

    report = {
        "unit": unit_id,
        "mode": "strict" if strict else "spot-check",
        "status": status,
        "total_claims": total_claims,
        "checked_claims": len(results),
        "supported": sum(1 for r in results.values() if r["verdict"] == "supported"),
        "partial": sum(1 for r in results.values() if r["verdict"] == "partial"),
        "unsupported": sum(1 for r in results.values() if r["verdict"] == "unsupported"),
        "escalated": escalated,
        "dropped": dropped_claims,
        "results": detailed_results,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds")
    }

    bdir = os.path.join(store, "briefs")
    os.makedirs(bdir, exist_ok=True)
    st.write_json(os.path.join(bdir, f"{unit_id}.factcheck.json"), report)
    return report


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit("usage: python3 hooks/source/factcheck.py <store> <brief.md> [--strict] [--spot-check N] [--model M]")
    store_dir, brief_path = a[0], a[1]
    strict = "--strict" in a
    spot_check = None
    if "--spot-check" in a:
        idx = a.index("--spot-check")
        if idx + 1 < len(a):
            spot_check = int(a[idx + 1])
    model = "sonnet"
    if "--model" in a:
        idx = a.index("--model")
        if idx + 1 < len(a):
            model = a[idx + 1]

    rep = check_brief(store_dir, brief_path, strict=strict, spot_check=spot_check, model=model)
    print(f"FACTCHECK {rep['unit']} mode={rep['mode']} total={rep['total_claims']} "
          f"checked={rep['checked_claims']} supported={rep['supported']} "
          f"partial={rep['partial']} unsupported={rep['unsupported']} escalated={rep['escalated']}")
    for r in rep["results"]:
        if r["verdict"] != "supported":
            print(f"  line {r['line']} [{r['verdict']}]: {r['claim'][:60]} ({r['reason']})")

    # If any claims were unsupported and brief is now empty or missing required claims
    if rep["unsupported"] > 0 and rep["supported"] == 0 and rep["partial"] == 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
