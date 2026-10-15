"""Milestone B3 tests: spaced review, small-source fast path, 'Where does it say...?' query.

Usage: python3 tests/source/check_b3.py <hooks_dir> <work_dir>
"""
import datetime
import json
import os
import re
import subprocess
import sys

HOOKS_DIR = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.abspath("hooks")
WORK_DIR = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.abspath("/tmp/schola_b3_test")

os.environ["SCHOLA_OFFLINE"] = "1"
os.environ["SCHOLA_SOURCES_DIR"] = os.path.join(WORK_DIR, "src")
os.environ["OBSIDIAN_VAULT_PATH"] = os.path.join(WORK_DIR, "vault")

sys.path.insert(0, os.path.join(HOOKS_DIR, "source"))
import store as st  # noqa: E402
import verify as sv  # noqa: E402
import brief as sbrief  # noqa: E402

os.makedirs(os.environ["SCHOLA_SOURCES_DIR"], exist_ok=True)
os.makedirs(os.environ["OBSIDIAN_VAULT_PATH"], exist_ok=True)

PASS_COUNT = 0
FAIL_COUNT = 0


def o(name, cond, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"ok {name}")
    else:
        FAIL_COUNT += 1
        print(f"FAIL {name} :: {detail}")


# -----------------------------------------------------------------------------
# Setup test store
# -----------------------------------------------------------------------------
SLUG = "ocean"
STORE = os.path.join(os.environ["SCHOLA_SOURCES_DIR"], SLUG)
os.makedirs(STORE, exist_ok=True)

# Outline
outline_data = [
    {"id": "c1", "title": "Ocean Dynamics", "depth": 1, "pdf_index": 10, "loc": "10"}
]
st.write_json(os.path.join(STORE, "outline.json"), outline_data)

# Source.json
source_meta = {"title": "Ocean Dynamics and Tides", "status": "complete", "pages": 20}
st.write_json(os.path.join(STORE, "source.json"), source_meta)

# Labels
labels_data = {
    "pages": [{"pdf_index": 10, "label": "10"}, {"pdf_index": 11, "label": "11"}],
    "label_to_pdf": {"10": [10], "11": [11]}
}
st.write_json(os.path.join(STORE, "labels.json"), labels_data)

# Spans
spans = [
    {
        "id": "p0010.1",
        "segment": "p0010",
        "locator": "10",
        "kind": "para",
        "text": "Tides are driven by the gravitational pull of the moon and the sun on Earth's oceans.",
        "norm": "tides are driven by the gravitational pull of the moon and the sun on earth's oceans"
    },
    {
        "id": "p0010.2",
        "segment": "p0010",
        "locator": "10",
        "kind": "para",
        "text": "Spring tides occur when the sun and moon align at new and full moons, creating maximal tidal bulges.",
        "norm": "spring tides occur when the sun and moon align at new and full moons creating maximal tidal bulges"
    },
    {
        "id": "p0010.3",
        "segment": "p0010",
        "locator": "10",
        "kind": "para",
        "text": "Neap tides occur during first and third quarter moons when gravitational forces act at right angles.",
        "norm": "neap tides occur during first and third quarter moons when gravitational forces act at right angles"
    },
    {
        "id": "p0010.4",
        "segment": "p0010",
        "locator": "10",
        "kind": "para",
        "text": "The Coriolis effect deflects moving water masses to the right in the Northern Hemisphere.",
        "norm": "the coriolis effect deflects moving water masses to the right in the northern hemisphere"
    },
    {
        "id": "p0011.1",
        "segment": "p0011",
        "locator": "11",
        "kind": "para",
        "text": "Deep thermohaline circulation is driven by global density gradients created by surface heat and freshwater fluxes.",
        "norm": "deep thermohaline circulation is driven by global density gradients created by surface heat and freshwater fluxes"
    }
]
with open(os.path.join(STORE, "spans.jsonl"), "w", encoding="utf-8") as f:
    for s in spans:
        f.write(json.dumps(s) + "\n")

# Units: small unit (u001, tokens = 250 < 1500 threshold)
units_data = [
    {
        "id": "u001",
        "role": "body",
        "tokens": 250,
        "span_start": "p0010.1",
        "span_end": "p0010.4",
        "loc_start": "10",
        "loc_end": "10",
        "title": "Tidal Dynamics",
        "outline_path": ["c1"]
    },
    {
        "id": "u002",
        "role": "body",
        "tokens": 4000,  # larger unit
        "span_start": "p0011.1",
        "span_end": "p0011.1",
        "loc_start": "11",
        "loc_end": "11",
        "title": "Thermohaline Circulation",
        "outline_path": ["c1"]
    }
]
st.write_json(os.path.join(STORE, "units.json"), units_data)

# Segments
seg_dir = os.path.join(STORE, "segments")
os.makedirs(seg_dir, exist_ok=True)
with open(os.path.join(seg_dir, "p0010.md"), "w", encoding="utf-8") as f:
    f.write("[p0010.1] Tides are driven by the gravitational pull of the moon and the sun on Earth's oceans.\n")
    f.write("[p0010.2] Spring tides occur when the sun and moon align at new and full moons, creating maximal tidal bulges.\n")
    f.write("[p0010.3] Neap tides occur during first and third quarter moons when gravitational forces act at right angles.\n")
    f.write("[p0010.4] The Coriolis effect deflects moving water masses to the right in the Northern Hemisphere.\n")

with open(os.path.join(seg_dir, "p0011.md"), "w", encoding="utf-8") as f:
    f.write("[p0011.1] Deep thermohaline circulation is driven by global density gradients created by surface heat and freshwater fluxes.\n")


# -----------------------------------------------------------------------------
# 1. Spaced Review Scheduling Tests
# -----------------------------------------------------------------------------
import io
import contextlib

def silent_mark(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return sbrief.cmd_mark(*args, **kwargs)

# 1a. Initial completion schedules interval = 1d
today_d = datetime.date.today()
today_str = today_d.isoformat()
tomorrow_str = (today_d + datetime.timedelta(days=1)).isoformat()

silent_mark(SLUG, "u001", "done", "4/5", "first pass solid")

prog = sbrief.progress(STORE)
u1 = prog["units"]["u001"]
o("b3 review initial mark sets status done", u1.get("status") == "done", u1)
o("b3 review initial mark sets reps 1", u1.get("reps") == 1, u1)
o("b3 review initial mark sets interval 1d", u1.get("interval_days") == 1, u1)
o("b3 review initial mark sets next_due tomorrow", u1.get("next_due") == tomorrow_str, u1)

# Same-day repeated mark does not advance reps or grow interval
silent_mark(SLUG, "u001", "done", "4/5", "repeated mark on same day")
prog = sbrief.progress(STORE)
u1 = prog["units"]["u001"]
o("b3 review same-day mark keeps reps 1", u1.get("reps") == 1, u1)
o("b3 review same-day mark keeps interval 1d", u1.get("interval_days") == 1, u1)

# Marking done without --score does not schedule next_due
silent_mark(SLUG, "u002", "done", None, "no score mark")
prog = sbrief.progress(STORE)
u2 = prog["units"]["u002"]
o("b3 review mark done without score has no next_due", u2.get("next_due") is None, u2)

# Marking done with score > total (e.g. 6/5) is rejected
rc_bad_score = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "mark", SLUG, "u001", "done", "--score", "6/5"],
                              capture_output=True, text=True)
o("b3 review mark rejects score > total (6/5)", rc_bad_score.returncode != 0, rc_bad_score.stderr)

# 1b. Vault progress.md mirroring
vault_prog_path = os.path.join(os.environ["OBSIDIAN_VAULT_PATH"], "sources", SLUG, "progress.md")
o("b3 review vault progress.md exists", os.path.isfile(vault_prog_path))

with open(vault_prog_path, encoding="utf-8") as f:
    prog_md = f.read()

o("b3 review vault progress.md has reviews_due frontmatter", "reviews_due:" in prog_md, prog_md[:200])
o("b3 review vault progress.md contains Review queue heading", "## Review queue" in prog_md)
o("b3 review vault progress.md shows unit in review queue", "| u001 |" in prog_md and "1d | 1 | 4/5 |" in prog_md)

# Simulate next review day (last_reviewed in the past)
prog["units"]["u001"]["last_reviewed"] = (today_d - datetime.timedelta(days=1)).isoformat()
st.write_json(os.path.join(STORE, "progress.json"), prog)

# 1c. Spaced interval advancement on successful review
silent_mark(SLUG, "u001", "done", "5/5", "second pass review")
prog = sbrief.progress(STORE)
u1 = prog["units"]["u001"]
in_3d = (today_d + datetime.timedelta(days=3)).isoformat()
o("b3 review pass advances reps to 2", u1.get("reps") == 2, u1)
o("b3 review pass advances interval to 3d", u1.get("interval_days") == 3, u1)
o("b3 review pass schedules next_due in 3 days", u1.get("next_due") == in_3d, u1)

# Simulate next review day for lapse
prog["units"]["u001"]["last_reviewed"] = (today_d - datetime.timedelta(days=3)).isoformat()
st.write_json(os.path.join(STORE, "progress.json"), prog)

# 1d. Lapse / failure resets interval to 1d
silent_mark(SLUG, "u001", "done", "1/5", "struggled on tricky quiz")
prog = sbrief.progress(STORE)
u1 = prog["units"]["u001"]
o("b3 review failure resets reps to 1", u1.get("reps") == 1, u1)
o("b3 review failure resets interval to 1d", u1.get("interval_days") == 1, u1)
o("b3 review failure resets next_due to tomorrow", u1.get("next_due") == tomorrow_str, u1)

# 1e. Review CLI listing
# Test when no reviews are due today (since due tomorrow)
res_rev = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "review", SLUG],
                         capture_output=True, text=True, env=dict(os.environ))
o("b3 review CLI prints no reviews currently due", "No reviews currently due for ocean" in res_rev.stdout, res_rev.stdout)

# Test review --all lists scheduled upcoming reviews
res_rev_all = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "review", SLUG, "--all"],
                             capture_output=True, text=True, env=dict(os.environ))
o("b3 review CLI --all lists scheduled unit", "u001 | Ocean Dynamics (p. 10)" in res_rev_all.stdout, res_rev_all.stdout)

# Simulate past due date
prog["units"]["u001"]["next_due"] = "2026-01-01"
st.write_json(os.path.join(STORE, "progress.json"), prog)
sbrief.mirror(STORE, SLUG, prog)

res_rev_due = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "review", SLUG],
                             capture_output=True, text=True, env=dict(os.environ))
o("b3 review CLI identifies overdue review", "u001 | Ocean Dynamics (p. 10) | 2026-01-01 (DUE)" in res_rev_due.stdout, res_rev_due.stdout)


# -----------------------------------------------------------------------------
# 2. Small-Source Fast Path Tests
# -----------------------------------------------------------------------------
# u001 has 250 tokens (< 1500). Running brief should succeed via fast-path without LLM calls!
res_fp = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "brief", SLUG, "u001"],
                        capture_output=True, text=True, env=dict(os.environ))

o("b3 fastpath exits 0", res_fp.returncode == 0, res_fp.stderr)
o("b3 fastpath prints fast path header tag", "· fast path ·" in res_fp.stdout, res_fp.stdout)
o("b3 fastpath prints verified status", "· verified ·" in res_fp.stdout, res_fp.stdout)
o("b3 fastpath renders citations as page locators", "(p. 10: “" in res_fp.stdout, res_fp.stdout)
o("b3 fastpath renders no raw [[cite: tokens", "[[cite:" not in res_fp.stdout, res_fp.stdout)

b_meta = json.load(open(os.path.join(STORE, "briefs", "u001.json")))
o("b3 fastpath metadata records fast_path true", b_meta.get("fast_path") is True, b_meta)
o("b3 fastpath metadata records status verified", b_meta.get("status") == "verified", b_meta)
o("b3 fastpath verify sidecar created", os.path.isfile(os.path.join(STORE, "briefs", "u001.verify.json")))

v_meta = json.load(open(os.path.join(STORE, "briefs", "u001.verify.json")))
o("b3 fastpath verify sidecar status verified", v_meta.get("status") == "verified", v_meta)
o("b3 fastpath verify sidecar has 0 failures", len(v_meta.get("failures", [])) == 0, v_meta)

# Direct fastpath command
res_fp_cmd = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "fastpath", SLUG, "u001"],
                            capture_output=True, text=True, env=dict(os.environ))
o("b3 fastpath direct CLI exits 0", res_fp_cmd.returncode == 0, res_fp_cmd.stderr)


# -----------------------------------------------------------------------------
# 3. 'Where does it say...?' Query Command Tests
# -----------------------------------------------------------------------------
# 3a. Substring search returning locator and verbatim quote
res_where = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", SLUG, "gravitational pull"],
                           capture_output=True, text=True, env=dict(os.environ))

o("b3 where exits 0 on match", res_where.returncode == 0, res_where.stderr)
o("b3 where formats locator and quote", "p. 10: “" in res_where.stdout, res_where.stdout)
o("b3 where includes verbatim query match", "gravitational pull" in res_where.stdout, res_where.stdout)
o("b3 where includes cite token with span id", "[[cite:p0010.1]]" in res_where.stdout, res_where.stdout)

# Check quote length limit (<= 25 words per Decision)
quote_match = re.search(r"“([^”]+)”", res_where.stdout)
words_in_quote = len(quote_match.group(1).split()) if quote_match else 999
o("b3 where quote is <= 25 words", words_in_quote <= 25, f"word count: {words_in_quote}")

# 3b. Search with different casing
res_case = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", SLUG, "CORIOLIS EFFECT"],
                          capture_output=True, text=True, env=dict(os.environ))
o("b3 where is case-insensitive", "p. 10: “" in res_case.stdout and "[[cite:p0010.4]]" in res_case.stdout, res_case.stdout)

# 3c. JSON format output
res_json = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", SLUG, "thermohaline", "--json"],
                          capture_output=True, text=True, env=dict(os.environ))
o("b3 where --json exits 0", res_json.returncode == 0, res_json.stderr)
try:
    j_res = json.loads(res_json.stdout)
    o("b3 where --json produces valid JSON array", isinstance(j_res, list) and len(j_res) == 1, j_res)
    o("b3 where --json record has required keys",
      all(k in j_res[0] for k in ("span_id", "locator", "quote", "full_text")), j_res)
    o("b3 where --json correct locator p. 11", j_res[0]["locator"] == "p. 11", j_res)
except Exception as e:
    o("b3 where --json parses", False, str(e))

# 3d. Clean handling of non-matching queries
res_nomatch = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", SLUG, "quantum entanglement"],
                             capture_output=True, text=True, env=dict(os.environ))
o("b3 where non-matching query exits 0", res_nomatch.returncode == 0, res_nomatch.stderr)
o("b3 where non-matching query prints clear message", "No matches found for 'quantum entanglement' in ocean" in res_nomatch.stdout, res_nomatch.stdout)

# 3e. Safe handling of regex metacharacters in query
res_meta = subprocess.run([sys.executable, os.path.join(HOOKS_DIR, "source", "brief.py"), "where", SLUG, "pull.*moon[0-9]+"],
                          capture_output=True, text=True, env=dict(os.environ))
o("b3 where regex metacharacters handled literally without error", res_meta.returncode == 0, res_meta.stderr)

sys.exit(0 if FAIL_COUNT == 0 else 1)
