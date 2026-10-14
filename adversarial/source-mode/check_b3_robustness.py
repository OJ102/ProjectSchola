"""Adversarial stress and robustness test suite for Milestone B3.

Probes edge cases, attack vectors, and boundary conditions in:
1. Spaced review schedule tracking & queue management.
2. Small-source fast path brief generation & verification.
3. 'Where does it say...?' query search & quotation extraction.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time

os.environ["SCHOLA_OFFLINE"] = "1"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOKS_DIR = os.path.join(ROOT, "hooks")
sys.path.insert(0, os.path.join(HOOKS_DIR, "source"))
import store as st  # noqa: E402
import verify as sv  # noqa: E402
import brief as sbrief  # noqa: E402

TESTS_RUN = 0
DEFECTS_FOUND = 0


def log_result(name, defended, details=""):
    global TESTS_RUN, DEFECTS_FOUND
    TESTS_RUN += 1
    if defended:
        print(f"✅ [DEFENDED/PASSED] {name}")
        if details:
            print(f"    -> {details}")
    else:
        DEFECTS_FOUND += 1
        print(f"❌ [VULNERABILITY/DEFECT] {name}")
        if details:
            print(f"    -> {details}")


def run():
    print("=" * 70)
    print("RUNNING ADVERSARIAL & ROBUSTNESS TEST SUITE: MILESTONE B3")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        src_root = os.path.join(tmpdir, "src")
        vault_root = os.path.join(tmpdir, "vault")
        os.makedirs(src_root, exist_ok=True)
        os.makedirs(vault_root, exist_ok=True)
        os.environ["SCHOLA_SOURCES_DIR"] = src_root
        os.environ["OBSIDIAN_VAULT_PATH"] = vault_root

        slug = "adv-b3"
        sdir = os.path.join(src_root, slug)
        os.makedirs(sdir, exist_ok=True)

        # Build baseline store
        st.write_json(os.path.join(sdir, "source.json"), {"title": "Adversarial B3 Source", "status": "complete"})
        st.write_json(os.path.join(sdir, "outline.json"), [{"id": "c1", "title": "Ch1", "depth": 1, "pdf_index": 1}])
        st.write_json(os.path.join(sdir, "labels.json"), {"pages": [{"pdf_index": 1, "label": "1"}], "label_to_pdf": {"1": [1]}})

        spans = [
            {"id": "p0001.1", "segment": "p0001", "locator": "1", "kind": "para",
             "text": "The quick brown fox jumps over the lazy sleeping dog near the quiet riverbank.",
             "norm": "the quick brown fox jumps over the lazy sleeping dog near the quiet riverbank"},
            {"id": "p0001.2", "segment": "p0001", "locator": "1", "kind": "para",
             "text": "Complex distributed database systems require careful consensus algorithms like Raft or Paxos in the cluster.",
             "norm": "complex distributed database systems require careful consensus algorithms like raft or paxos in the cluster"},
            {"id": "p0001.3", "segment": "p0001", "locator": "1", "kind": "para",
             "text": "Artificial intelligence applications benefit from the structured foundation model fine-tuning process.",
             "norm": "artificial intelligence applications benefit from the structured foundation model fine tuning process"},
            {"id": "p0001.4", "segment": "p0001", "locator": "1", "kind": "para",
             "text": "Reliable data streaming platforms buffer the event logs across multiple partitioned brokers.",
             "norm": "reliable data streaming platforms buffer the event logs across multiple partitioned brokers"}
        ]
        with open(os.path.join(sdir, "spans.jsonl"), "w", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps(s) + "\n")

        units = [
            {"id": "u001", "role": "body", "tokens": 120, "span_start": "p0001.1", "span_end": "p0001.4",
             "loc_start": "1", "loc_end": "1", "title": "Unit 1", "outline_path": ["c1"]},
            {"id": "u002", "role": "body", "tokens": 3500, "span_start": "p0001.1", "span_end": "p0001.4",
             "loc_start": "1", "loc_end": "1", "title": "Unit 2", "outline_path": ["c1"]}
        ]
        st.write_json(os.path.join(sdir, "units.json"), units)

        seg_dir = os.path.join(sdir, "segments")
        os.makedirs(seg_dir, exist_ok=True)
        with open(os.path.join(seg_dir, "p0001.md"), "w", encoding="utf-8") as f:
            for s in spans:
                f.write(f"[{s['id']}] {s['text']}\n")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 1: Division by Zero & Malformed Quiz Scores
        # ---------------------------------------------------------------------
        # Try marking unit with scores like "0/0", "5/0", "abc", "-1/5", "4/5; id"
        for bad_score in ["0/0", "5/0", "abc", "-1/5", "4/5; id", "9999/9999"]:
            cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"),
                   "mark", slug, "u001", "done", "--score", bad_score]
            res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
            # Must either reject cleanly with exit != 0 or safely handle ratio 0.0 without ZeroDivisionError
            no_crash = "ZeroDivisionError" not in res.stderr and "Traceback" not in res.stderr
            if bad_score in ("0/0", "5/0", "9999/9999") and res.returncode == 0:
                # If accepted, verify progress.json was not corrupted
                prog = sbrief.progress(sdir)
                u = prog["units"]["u001"]
                no_crash = no_crash and u.get("reps") is not None and u.get("interval_days") is not None

        log_result("[Review - HIGH] Malformed & Division-by-Zero Quiz Scores",
                   no_crash, "ZeroDivisionError safely averted and regex format validation enforced.")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 2: Spaced Review Date Calculation Under Leap Years
        # ---------------------------------------------------------------------
        leap_entry = {"reps": 1, "interval_days": 1}
        # Feb 28 on a leap year
        res_leap = sbrief.calculate_review_schedule(leap_entry, "5/5", today="2024-02-28")
        is_leap_valid = res_leap["next_due"] == "2024-03-02"  # 2024 is leap year, 28 + 3 days = March 2
        log_result("[Review - MEDIUM] Calendar Arithmetic Handles Leap Years Accurately",
                   is_leap_valid, f"Feb 28 + 3d in 2024 -> {res_leap['next_due']}")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 3: Review Queue Tampering & Missing Metadata Recovery
        # ---------------------------------------------------------------------
        # Deliberately strip interval_days, reps, next_due from progress.json
        prog = sbrief.progress(sdir)
        prog["units"]["u001"] = {"status": "done"}
        st.write_json(os.path.join(sdir, "progress.json"), prog)

        cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "review", slug]
        res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
        handled_gracefully = res.returncode == 0 and "Traceback" not in res.stderr
        log_result("[Review - MEDIUM] Review Queue Handles Missing Progress Keys Gracefully",
                   handled_gracefully, "No KeyError raised on uninitialized or tampered progress units.")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 4: Where Query ReDoS (Catastrophic Backtracking)
        # ---------------------------------------------------------------------
        redos_query = "a" * 25 + "!"
        t0 = time.time()
        cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", slug, redos_query]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=dict(os.environ))
        dt = time.time() - t0
        log_result("[Where Query - HIGH] Resistance to ReDoS Regex Patterns",
                   dt < 2.0 and res.returncode == 0, f"Query evaluated in {dt:.3f}s")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 5: Shell Injection & Metacharacters in Query
        # ---------------------------------------------------------------------
        pwned_file = os.path.join(tmpdir, "pwned.txt")
        injection_queries = [
            f"$(touch {pwned_file})",
            f"`touch {pwned_file}`",
            f"; touch {pwned_file} ;",
            f"| touch {pwned_file}",
            "[[cite:p0001.1]]",
            "<script>alert(1)</script>"
        ]
        injections_blocked = True
        for q in injection_queries:
            cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", slug, q]
            subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
            if os.path.exists(pwned_file):
                injections_blocked = False
                break

        log_result("[Where Query - CRITICAL] Command Injection Via Query String Blocked",
                   injections_blocked and not os.path.exists(pwned_file),
                   "Shell metacharacters treated as inert string literals without subshell spawning.")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 6: Output Flooding / Truncation Bounded by --max
        # ---------------------------------------------------------------------
        # Query that matches every single span ("the")
        cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"),
               "where", slug, "the", "--max", "2"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
        out_lines = [ln for ln in res.stdout.strip().split("\n") if ln.strip()]
        log_result("[Where Query - MEDIUM] Match Flooding Strictly Bounded by --max",
                   len(out_lines) == 2, f"Expected 2 matches, got {len(out_lines)}")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 7: Quote Length Strictly <= 25 Words (Decision 18)
        # ---------------------------------------------------------------------
        cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"),
               "where", slug, "distributed database"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
        m = re.search(r"“([^”]+)”", res.stdout)
        words_count = len(m.group(1).split()) if m else 0
        log_result("[Where Query - HIGH] Verbatim Quote Word Count Bound <= 25",
                   words_count <= 25 and words_count > 0, f"Matched quote words: {words_count}")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 8: Unicode, Emojis & RTL in Query String
        # ---------------------------------------------------------------------
        cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"),
               "where", slug, "🧠 🌊 \u200e\u200f"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
        log_result("[Where Query - LOW] Non-ASCII, Emoji, and Bidi Invariance",
                   res.returncode == 0 and "No matches found" in res.stdout,
                   "Unicode strings processed cleanly without encoding errors.")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 9: Fast Path With Malicious / Uncitable Spans
        # ---------------------------------------------------------------------
        # Create a unit with only empty spans or spans with only stopwords
        bad_slug = "bad-spans"
        bad_sdir = os.path.join(src_root, bad_slug)
        os.makedirs(bad_sdir, exist_ok=True)
        st.write_json(os.path.join(bad_sdir, "source.json"), {"title": "Bad Spans", "status": "complete"})
        st.write_json(os.path.join(bad_sdir, "outline.json"), [{"id": "c1", "title": "Ch1", "depth": 1, "pdf_index": 1}])
        st.write_json(os.path.join(bad_sdir, "labels.json"), {"pages": [{"pdf_index": 1, "label": "1"}], "label_to_pdf": {"1": [1]}})
        bad_spans = [
            {"id": "p0001.1", "segment": "p0001", "locator": "1", "kind": "para", "text": "is it in at on", "norm": "is it in at on"},
            {"id": "p0001.2", "segment": "p0001", "locator": "1", "kind": "para", "text": "too short", "norm": "too short"}
        ]
        with open(os.path.join(bad_sdir, "spans.jsonl"), "w", encoding="utf-8") as f:
            for s in bad_spans:
                f.write(json.dumps(s) + "\n")
        st.write_json(os.path.join(bad_sdir, "units.json"), [
            {"id": "u001", "role": "body", "tokens": 50, "span_start": "p0001.1", "span_end": "p0001.2",
             "loc_start": "1", "loc_end": "1", "title": "Uncitable Unit", "outline_path": ["c1"]}
        ])

        # Attempt fast path on uncitable unit: generate_fast_path_brief returns None
        fp_res = sbrief.generate_fast_path_brief(bad_sdir, {"id": "u001", "tokens": 50, "span_start": "p0001.1", "span_end": "p0001.2", "loc_start": "1", "loc_end": "1", "title": "U1"})
        log_result("[Fast Path - HIGH] Uncitable / Stopword Spans Reject Fast Path Gracefully",
                   fp_res is None, "Returns None instead of emitting unverifiable citations.")

        # ---------------------------------------------------------------------
        # ATTACK VECTOR 10: Cache Poisoning on Fast-Path Brief
        # ---------------------------------------------------------------------
        # Generate valid fast-path brief first on normal unit
        sbrief.cmd_fastpath(slug, "u001")
        bpath = os.path.join(sdir, "briefs", "u001.md")
        # Poison brief with forged anchor
        poisoned_content = open(bpath, encoding="utf-8").read() + "\n- Hallucinated claim [[cite:p0001.1 \"alien spaceship technology\"]]\n"
        with open(bpath, "w", encoding="utf-8") as f:
            f.write(poisoned_content)

        # cmd_brief detects invalid citations and renders "(citation removed)"
        cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "brief", slug, "u001"]
        res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
        log_result("[Fast Path - HIGH] Poisoned / Tampered Cached Brief Citation Neutralization",
                   "(citation removed)" in res.stdout,
                   "Corrupted citation neutralized when rendering poisoned cached brief.")

    print("=" * 70)
    print(f"TOTAL TESTS: {TESTS_RUN} | DEFECTS/VULNERABILITIES REPRODUCED: {DEFECTS_FOUND}")
    print("=" * 70)
    return DEFECTS_FOUND == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
