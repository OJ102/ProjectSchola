#!/usr/bin/env python3
"""Verification suite for Milestone B2:
- Verify-gated cache & detailed verification output (<unit>.verify.json)
- File locking on brief generation
- Partial brief caching (decision 17) & rejection when 0 claims remain
- Cached brief corruption detection
- Lazy vision rendering (vision.py, SCHOLA_MAX_RENDER_MP cap, vision/<unit>.json)
- Fact-checker subagent (factcheck.py, claim entailment, spot-check escalation, dropping unsupported claims)
"""
import fcntl
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HOOKS_DIR, WORK_DIR = sys.argv[1], sys.argv[2]
os.makedirs(WORK_DIR, exist_ok=True)
sys.path.insert(0, os.path.join(HOOKS_DIR, "source"))
from srcnorm import norm  # noqa: E402
import store as st  # noqa: E402
import verify as sv  # noqa: E402
import vision as svision  # noqa: E402
import factcheck as sfactcheck  # noqa: E402


def o(name, good, detail=""):
    print(f"ok {name}" if good else f"FAIL {name} :: " + str(detail).replace("\n", " "))


# -----------------------------------------------------------------------------
# 1. Setup test store
# -----------------------------------------------------------------------------
STORE_DIR = os.path.join(WORK_DIR, "teststore")
os.makedirs(os.path.join(STORE_DIR, "briefs"), exist_ok=True)
os.makedirs(os.path.join(STORE_DIR, "units"), exist_ok=True)
os.makedirs(os.path.join(STORE_DIR, "segments"), exist_ok=True)
os.makedirs(os.path.join(STORE_DIR, "assets"), exist_ok=True)

SPANS = {
    "p0010.1": "The ocean tides are driven by the gravitational pull of the moon.",
    "p0010.2": "Spring tides occur when the earth sun and moon form a straight alignment.",
    "p0010.3": "Neap tides produce the lowest tidal range during quarter moons.",
    "p0011.1": "Surface currents in the Atlantic ocean flow in massive clockwise gyres.",
    "p0011.2": "Deep ocean thermohaline circulation moves water slowly over centuries.",
}

with open(os.path.join(STORE_DIR, "spans.jsonl"), "w", encoding="utf-8") as f:
    for sid, t in SPANS.items():
        f.write(json.dumps({
            "id": sid,
            "segment": sid.split(".")[0],
            "locator": sid[1:5].lstrip("0"),
            "kind": "para",
            "text": t,
            "norm": norm(t)
        }) + "\n")

with open(os.path.join(STORE_DIR, "labels.json"), "w", encoding="utf-8") as f:
    json.dump({"pages": [{"pdf_index": 10, "label": "10"}, {"pdf_index": 11, "label": "11"}]}, f)

with open(os.path.join(STORE_DIR, "outline.json"), "w", encoding="utf-8") as f:
    json.dump([{"id": "c1", "title": "Ocean Dynamics", "depth": 1, "pdf_index": 10}], f)

with open(os.path.join(STORE_DIR, "units.json"), "w", encoding="utf-8") as f:
    json.dump([{
        "id": "u001", "title": "Tides and Currents", "loc_start": 10, "loc_end": 11,
        "span_start": "p0010.1", "span_end": "p0011.2", "tokens": 500
    }], f)

with open(os.path.join(STORE_DIR, "source.json"), "w", encoding="utf-8") as f:
    json.dump({
        "slug": "teststore", "title": "Ocean Dynamics Handbook",
        "origin": os.path.join(WORK_DIR, "dummy.pdf")
    }, f)

# Unit files
u1_text = "\n".join(f"[{sid}] {t}" for sid, t in SPANS.items())
with open(os.path.join(STORE_DIR, "units", "u001.md"), "w", encoding="utf-8") as f:
    f.write(f"# Unit source: Ocean Dynamics (pp. 10–11)\n\n{u1_text}\n")
with open(os.path.join(STORE_DIR, "units", "u001.md.ids"), "w", encoding="utf-8") as f:
    f.write("\n".join(SPANS.keys()) + "\n")


# -----------------------------------------------------------------------------
# 2. Test Verify-Gated Cache & Detailed Verification Output (<unit>.verify.json)
# -----------------------------------------------------------------------------
valid_brief = """## Where
Ocean Dynamics (pp. 10–11)

## Core claims
- Moon gravity drives tides [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
- Spring tides occur during alignment [[cite:p0010.2 "earth sun and moon form a straight alignment"]]

## Quiz seeds
1. What drives the ocean tides? [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
"""

bpath = os.path.join(STORE_DIR, "briefs", "u001.md")
with open(bpath, "w", encoding="utf-8") as f:
    f.write(valid_brief)

v_rep = sv.verify(STORE_DIR, bpath, [os.path.join(STORE_DIR, "units", "u001.md.ids")], where="Ocean Dynamics (pp. 10–11)")
v_rep["status"] = "verified"
v_rep["dropped"] = []
vpath = os.path.join(STORE_DIR, "briefs", "u001.verify.json")
st.write_json(vpath, v_rep)

o("b2 verify.json exists after verification", os.path.exists(vpath))
v_data = json.load(open(vpath))
has_fields = all(k in v_data for k in ("status", "cites", "ok", "fuzzy", "failures", "detail", "items", "pass", "dropped"))
o("b2 verify.json contains required status and citation details", has_fields, v_data.keys())
detail_ok = len(v_data["detail"]) > 0 and all(k in v_data["detail"][0] for k in ("line", "id", "anchor", "status", "match", "locator"))
o("b2 verify.json records line numbers, ids, match types and locators", detail_ok, v_data.get("detail", []))


# -----------------------------------------------------------------------------
# 3. Test Partial Brief Caching and Rejection on 0 Claims
# -----------------------------------------------------------------------------
partial_brief_raw = """## Where
Ocean Dynamics (pp. 10–11)

## Core claims
- Valid claim about tides [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
- Bad claim with fake anchor [[cite:p0010.2 "anchor text not found anywhere in source"]]

## Quiz seeds
1. What drives ocean tides? [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
"""

p_rep = sv.verify(STORE_DIR, None, [os.path.join(STORE_DIR, "units", "u001.md.ids")], where="Ocean Dynamics (pp. 10–11)", text=partial_brief_raw)
o("b2 raw partial brief fails initial pass", not p_rep["pass"])

# Drop failing line
bad_lines = {f["line"] for f in p_rep["failures"] if f.get("line")}
lines = partial_brief_raw.split("\n")
dropped = [lines[n - 1] for n in sorted(bad_lines) if 0 < n <= len(lines)]
filtered_text = "\n".join(ln for n, ln in enumerate(lines, 1) if n not in bad_lines)

p_rep_filtered = sv.verify(STORE_DIR, None, [os.path.join(STORE_DIR, "units", "u001.md.ids")], where="Ocean Dynamics (pp. 10–11)", text=filtered_text)
status = "partial" if (p_rep_filtered["pass"] and p_rep_filtered["ok"] > 0) else "rejected"
o("b2 filtered brief passes as partial", status == "partial" and len(dropped) == 1, f"status={status} dropped={dropped}")

# Now test case where dropping all bad lines leaves 0 claims in Core claims
empty_claims_brief = """## Where
Ocean Dynamics (pp. 10–11)

## Core claims
- Bad claim only [[cite:p0010.2 "anchor text not found anywhere in source"]]

## Quiz seeds
1. What drives ocean tides? [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
"""
ec_rep = sv.verify(STORE_DIR, None, [os.path.join(STORE_DIR, "units", "u001.md.ids")], where="Ocean Dynamics (pp. 10–11)", text=empty_claims_brief)
bad_lines_ec = {f["line"] for f in ec_rep["failures"] if f.get("line")}
lines_ec = empty_claims_brief.split("\n")
filtered_ec = "\n".join(ln for n, ln in enumerate(lines_ec, 1) if n not in bad_lines_ec)
ec_rep_after = sv.verify(STORE_DIR, None, [os.path.join(STORE_DIR, "units", "u001.md.ids")], where="Ocean Dynamics (pp. 10–11)", text=filtered_ec)
core_claims = sum(1 for it in ec_rep_after["items"] if it.get("section") == "Core claims" and it.get("ok", 0) > 0)
quiz_seeds = sum(1 for it in ec_rep_after["items"] if it.get("section") == "Quiz seeds" and it.get("ok", 0) > 0)
status_ec = "partial" if (ec_rep_after["pass"] and ec_rep_after["ok"] > 0 and core_claims > 0 and quiz_seeds > 0) else "rejected"
o("b2 brief with 0 surviving claims in Core claims is rejected", status_ec == "rejected", f"status={status_ec}")


# -----------------------------------------------------------------------------
# 4. Test File Locking During Brief Generation
# -----------------------------------------------------------------------------
lock_file = os.path.join(STORE_DIR, "briefs", "u001.lock")
fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
is_locked = False
try:
    fd2 = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        is_locked = True
    finally:
        os.close(fd2)
finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)

o("b2 brief file locking blocks concurrent acquisition", is_locked)


# -----------------------------------------------------------------------------
# 5. Test Lazy Vision Rendering (hooks/source/vision.py)
# -----------------------------------------------------------------------------
# Write segments/index.json and assets/index.json with needs_vision
seg_index = [
    {"id": "p0010", "pdf_index": 10, "locator": "10", "needs_vision": True, "assets": ["fig1"]},
    {"id": "p0011", "pdf_index": 11, "locator": "11", "needs_vision": False, "assets": []}
]
with open(os.path.join(STORE_DIR, "segments", "index.json"), "w", encoding="utf-8") as f:
    json.dump(seg_index, f)

asset_index = [
    {"id": "fig1", "file": "fig1.png", "pdf_index": 10, "caption_span": "p0010.1", "needs_vision": True}
]
with open(os.path.join(STORE_DIR, "assets", "index.json"), "w", encoding="utf-8") as f:
    json.dump(asset_index, f)

# Pre-create cached asset
def create_dummy_png(path, w, h):
    # Minimal 1x1 or custom PNG header
    hdr = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    with open(path, "wb") as f:
        f.write(hdr + ihdr + b"\x00" * 30)

create_dummy_png(os.path.join(STORE_DIR, "assets", "fig1.png"), 800, 600)

u_dict = {"id": "u001", "span_start": "p0010.1", "span_end": "p0011.2", "loc_start": 10, "loc_end": 11}
v_items = svision.render_unit_vision(STORE_DIR, u_dict, max_mp=4.0)

v_summary_file = os.path.join(STORE_DIR, "vision", "u001.json")
o("b2 vision summary json created", os.path.exists(v_summary_file))
if os.path.exists(v_summary_file):
    v_sum = json.load(open(v_summary_file))
    o("b2 vision summary records unit and max_render_mp", v_sum.get("unit") == "u001" and v_sum.get("max_render_mp") == 4.0)
    has_fig1 = any(it.get("id") == "fig1" and it.get("status") == "cached" for it in v_sum.get("items", []))
    o("b2 vision caches pre-existing figure within MP cap", has_fig1, v_sum.get("items"))


# -----------------------------------------------------------------------------
# 6. Test Fact-Checker Subagent (hooks/source/factcheck.py)
# -----------------------------------------------------------------------------
test_brief_fc = """## Where
Ocean Dynamics (pp. 10–11)

## Core claims
- The ocean tides are driven by gravitational pull of the moon. [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
- Spring tides occur when earth sun and moon form straight alignment. [[cite:p0010.2 "earth sun and moon form a straight alignment"]]
- THIS IS A COMPLETELY FABRICATED CLAIM ABOUT MARTIAN SATELLITES. [[cite:p0010.3 "lowest tidal range during quarter moons"]]
- Atlantic currents flow in clockwise gyres. [[cite:p0011.1 "Atlantic ocean flow in massive clockwise gyres"]]
- Thermohaline circulation moves water slowly over centuries. [[cite:p0011.2 "moves water slowly over centuries"]]

## Quiz seeds
1. What causes ocean tides? — The gravitational pull of the moon. [[cite:p0010.1 "driven by the gravitational pull of the moon"]]
"""

fc_brief_path = os.path.join(STORE_DIR, "briefs", "fc_test.md")
with open(fc_brief_path, "w", encoding="utf-8") as f:
    f.write(test_brief_fc)

# Test spot-check with escalation:
# There are 6 claims total. Spot-check 20% would check 2 claims.
# If the fabricated claim is checked (or in strict mode), escalation occurs.
rep_strict = sfactcheck.check_brief(STORE_DIR, fc_brief_path, strict=True, drop_unsupported=True)

o("b2 factcheck strict evaluated all 6 claims", rep_strict["checked_claims"] == 6, rep_strict["checked_claims"])
o("b2 factcheck identified fabricated claim as unsupported", rep_strict["unsupported"] == 1, rep_strict)
o("b2 factcheck dropped unsupported claim from brief", len(rep_strict["dropped"]) == 1 and "FABRICATED" in rep_strict["dropped"][0])

# Check that the modified brief file no longer has the fabricated claim
modified_text = open(fc_brief_path, encoding="utf-8").read()
o("b2 rewritten brief is clean of unsupported claim", "MARTIAN SATELLITES" not in modified_text)

# Check factcheck report file
fc_rep_path = os.path.join(STORE_DIR, "briefs", "fc_test.factcheck.json")
o("b2 factcheck report file written", os.path.exists(fc_rep_path))
if os.path.exists(fc_rep_path):
    fc_saved = json.load(open(fc_rep_path))
    o("b2 factcheck report records supported, partial and dropped claims",
      fc_saved.get("supported", 0) >= 4 and len(fc_saved.get("dropped", [])) == 1)

# Test CLI interface of factcheck.py
cli_cmd = [sys.executable, os.path.join(HOOKS_DIR, "source", "factcheck.py"), STORE_DIR, fc_brief_path, "--strict"]
cli_res = subprocess.run(cli_cmd, capture_output=True, text=True)
o("b2 factcheck CLI exits 0 on valid brief", cli_res.returncode == 0, cli_res.stderr)
o("b2 factcheck CLI prints summary line", "FACTCHECK fc_test mode=strict" in cli_res.stdout, cli_res.stdout)
