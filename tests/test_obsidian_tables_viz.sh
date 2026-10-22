#!/bin/bash
# tests/test_obsidian_tables_viz.sh
# Verification test suite for Obsidian table rendering, visual embeds, wikilinks,
# and Decision 31 citation checking.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$ROOT/hooks"
HOOKS_SOURCE="$HOOKS/source"

PASS=0
FAIL=0
FAILED=()

ok()   { PASS=$((PASS+1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  FAIL $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/         /'; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/schola-obsidian-test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

echo "======================================================================"
echo "== Obsidian Tables & Visual Embeds Verification Suite =="
echo "======================================================================"

# ----------------------------------------------------------------------------
# 1. MARKDOWN TABLE RENDERING IN OBSIDIAN NOTES
# ----------------------------------------------------------------------------
echo "== 1. Markdown Table Rendering =="

# Test 1.1: Markdown table in assistant explanation has blank lines around it
fresh_test() {
  TEST_DIR="$WORK/t$RANDOM$RANDOM"
  mkdir -p "$TEST_DIR/vault/sessions"
  TR="$TEST_DIR/transcript.jsonl"
  VAULT="$TEST_DIR/vault"
  export OBSIDIAN_VAULT_PATH="$VAULT"
}

fresh_test
cat > "$TR" <<'EOF'
{"type":"user","timestamp":"2026-10-22T10:00:00Z","message":{"role":"user","content":"<command-name>/teach</command-name><command-args>Tides</command-args>"}}
{"type":"assistant","timestamp":"2026-10-22T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"Here is the tides comparison table:\n| Tide Type | Alignment | Range |\n| :--- | :--- | :--- |\n| Spring Tide | Sun-Moon Align | Maximum |\n| Neap Tide | Right Angles | Minimum |\nThis table summarizes the alignment effects."}]}}
EOF

OBSIDIAN_VAULT_PATH="$VAULT" jq -nc --arg tr "$TR" --arg sid "sess-table-1" '{transcript_path:$tr, session_id:$sid}' \
  | bash "$HOOKS/md-log-stop-hook.sh" >/dev/null 2>&1
LOG="$VAULT/sessions/2026-10-22 Tides.md"

if [ -f "$LOG" ]; then
  # Check that there is a blank line before the table header
  if grep -B1 '| Tide Type |' "$LOG" | head -n1 | grep -q '^$'; then
    ok "tables: explanation followed by table has blank line before header"
  else
    bad "tables: explanation followed by table has blank line before header" "$(grep -C2 '| Tide Type |' "$LOG")"
  fi

  # Check that there is a blank line after the table rows
  if grep -A1 '| Neap Tide |' "$LOG" | tail -n1 | grep -q '^$'; then
    ok "tables: table followed by text has blank line after table"
  else
    bad "tables: table followed by text has blank line after table" "$(grep -C2 '| Neap Tide |' "$LOG")"
  fi
else
  bad "tables: log file created for session" "missing $LOG"
fi

# Test 1.2: Answered question callout > [!question] formats cleanly with
#           >\n> **Your answer:** and table following has blank line
fresh_test
cat > "$TR" <<'EOF'
{"type":"user","timestamp":"2026-10-22T10:00:00Z","message":{"role":"user","content":"<command-name>/teach</command-name><command-args>Tides</command-args>"}}
{"type":"user","timestamp":"2026-10-22T10:01:00Z","toolUseResult":{"questions":[{"question":"What aligns during spring tides?","options":[{"label":"Sun and Moon"},{"label":"Earth and Mars"}]}],"answers":{"What aligns during spring tides?":"Sun and Moon"}}}
{"type":"assistant","timestamp":"2026-10-22T10:01:05Z","message":{"role":"assistant","content":[{"type":"text","text":"| Feature | Moon | Sun |\n|---|---|---|\n| Pull | Major | Minor |"}]}}
EOF

OBSIDIAN_VAULT_PATH="$VAULT" jq -nc --arg tr "$TR" --arg sid "sess-table-2" '{transcript_path:$tr, session_id:$sid}' \
  | bash "$HOOKS/md-log-stop-hook.sh" >/dev/null 2>&1
LOG="$VAULT/sessions/2026-10-22 Tides.md"

if [ -f "$LOG" ]; then
  # Check that callout contains > **Your answer:**
  if grep -q '> \*\*Your answer:\*\* Sun and Moon' "$LOG"; then
    ok "tables: answered question callout keeps **Your answer:** inside callout block"
  else
    bad "tables: answered question callout keeps **Your answer:** inside callout block" "$(grep -C3 'Your answer' "$LOG")"
  fi

  # Check that callout has > separator line before **Your answer:**
  if grep -B1 '> \*\*Your answer:\*\*' "$LOG" | head -n1 | grep -q '^>$'; then
    ok "tables: callout has clean > separator line before **Your answer:**"
  else
    bad "tables: callout has clean > separator line before **Your answer:**" "$(grep -B2 '> \*\*Your answer:\*\*' "$LOG")"
  fi

  # Check that table following the callout is separated by blank line so Obsidian parses as GFM table
  if grep -B1 '| Feature | Moon | Sun |' "$LOG" | head -n1 | grep -q '^$'; then
    ok "tables: table following question callout is separated by blank line"
  else
    bad "tables: table following question callout is separated by blank line" "$(grep -C2 '| Feature |' "$LOG")"
  fi
else
  bad "tables: log file created for question test" "missing $LOG"
fi

# Test 1.3: Table embedded inside a question callout has > prefixes and proper callout formatting
fresh_test
cat > "$TR" <<'EOF'
{"type":"user","timestamp":"2026-10-22T10:00:00Z","message":{"role":"user","content":"<command-name>/teach</command-name><command-args>Tides</command-args>"}}
{"type":"user","timestamp":"2026-10-22T10:01:00Z","toolUseResult":{"questions":[{"question":"Which row is correct?\n| Case | Result |\n|---|---|\n| Align | Spring |","options":[{"label":"Case Align"}]}],"answers":{"Which row is correct?\n| Case | Result |\n|---|---|\n| Align | Spring |":"Case Align"}}}
EOF

OBSIDIAN_VAULT_PATH="$VAULT" jq -nc --arg tr "$TR" --arg sid "sess-table-3" '{transcript_path:$tr, session_id:$sid}' \
  | bash "$HOOKS/md-log-stop-hook.sh" >/dev/null 2>&1
LOG="$VAULT/sessions/2026-10-22 Tides.md"

if [ -f "$LOG" ]; then
  if grep -q '> | Case | Result |' "$LOG" && grep -B1 '> | Case | Result |' "$LOG" | head -n1 | grep -q '^>$'; then
    ok "tables: table inside question callout is properly prefixed with > and blank callout line"
  else
    bad "tables: table inside question callout is properly prefixed with > and blank callout line" "$(grep -C3 'Case' "$LOG")"
  fi
else
  bad "tables: log file created for callout table" "missing $LOG"
fi

# ----------------------------------------------------------------------------
# 2. OBSIDIAN VISUAL EMBEDS & WIKILINKS PRESERVATION
# ----------------------------------------------------------------------------
echo "== 2. Visual Embeds & Wikilinks Preservation =="

while IFS= read -r line; do
  case "$line" in
    PASS:*) ok "viz: ${line#PASS:}" ;;
    FAIL:*) bad "viz: $(cut -d: -f2 <<<"$line")" "$(cut -d: -f3- <<<"$line")" ;;
  esac
done < <(python3 -I - "$HOOKS_SOURCE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import cite_check as c

def check(name, text, needle):
    rendered, _ = c.check_reply(text, {}, [], set(), "store")
    if needle in rendered:
        print(f"PASS:{name}")
    else:
        print(f"FAIL:{name}:{rendered}")

check("embed_dimensions", "See ![[viz-topic-12345.png|500]] inline.", "![[viz-topic-12345.png|500]]")
check("embed_plain", "See ![[viz-topic-67890.png]] inline.", "![[viz-topic-67890.png]]")
check("embed_svg", "See ![[diagram.svg|600]] inline.", "![[diagram.svg|600]]")
check("note_wikilink", "Review [[Some Note]] before continuing.", "[[Some Note]]")
check("note_wikilink_alias", "Review [[Some Note|Display Name]] before continuing.", "[[Some Note|Display Name]]")
check("mermaid_subroutine", "graph TD\nA[[Subroutine A]] --> B[[Subroutine B]]", "A[[Subroutine A]] --> B[[Subroutine B]]")
check("markdown_image", "![diagram](../viz/viz-topic-12345.png)", "![diagram](../viz/viz-topic-12345.png)")
multi = "First ![[viz-1.png|400]] then [[Note A]] and finally ![[viz-2.png|600]]"
check("multi_embed", multi, multi)
PY
)

# Test 2.6: End-to-end transcript with visual embeds in md-log-stop-hook.sh
fresh_test
cat > "$TR" <<'EOF'
{"type":"user","timestamp":"2026-10-22T10:00:00Z","message":{"role":"user","content":"<command-name>/teach</command-name><command-args>Tides</command-args>"}}
{"type":"assistant","timestamp":"2026-10-22T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"Here is the diagram:\n![[viz-tides-1001.png|500]]\nImage: /vault/viz/viz-tides-1001.png\n\nAlso reference [[Lunar Gravity]] and subroutine:\n```mermaid\nflowchart LR\n  X[[Tidal Bulb]] --> Y[[Ocean Flow]]\n```"}]}}
EOF

OBSIDIAN_VAULT_PATH="$VAULT" jq -nc --arg tr "$TR" --arg sid "sess-viz-e2e" '{transcript_path:$tr, session_id:$sid}' \
  | bash "$HOOKS/md-log-stop-hook.sh" >/dev/null 2>&1
LOG="$VAULT/sessions/2026-10-22 Tides.md"

if [ -f "$LOG" ]; then
  grep -q '!\[\[viz-tides-1001\.png|500\]\]' "$LOG" && ok "viz: native wikilink image embed survives md-log-stop-hook.sh" \
    || bad "viz: native wikilink image embed survives md-log-stop-hook.sh" "$(cat "$LOG")"
  grep -q '\[\[Lunar Gravity\]\]' "$LOG" && ok "viz: note wikilink survives md-log-stop-hook.sh" \
    || bad "viz: note wikilink survives md-log-stop-hook.sh" "$(cat "$LOG")"
  grep -q 'X\[\[Tidal Bulb\]\] --> Y\[\[Ocean Flow\]\]' "$LOG" && ok "viz: mermaid subroutine survives md-log-stop-hook.sh" \
    || bad "viz: mermaid subroutine survives md-log-stop-hook.sh" "$(cat "$LOG")"
  ! grep -q '\[ \[' "$LOG" && ! grep -q '\] \]' "$LOG" && ok "viz: zero spurious bracket spacing introduced in clean note" \
    || bad "viz: zero spurious bracket spacing introduced in clean note" "$(grep -E '\[ \[|\] \]' "$LOG")"
else
  bad "viz: log created for end-to-end viz test" "missing $LOG"
fi

# ----------------------------------------------------------------------------
# 3. DECISION 31 CITATION CHECKING & NEUTRALIZATION
# ----------------------------------------------------------------------------
echo "== 3. Decision 31 Citation Integrity =="

fresh_test
STORE="$TEST_DIR/store"
mkdir -p "$STORE"

python3 -I - "$STORE" "$HOOKS_SOURCE" <<'PY'
import json, os, sys
store, hooks_source = sys.argv[1], sys.argv[2]
sys.path.insert(0, hooks_source)
import srcnorm

text = "Tides are driven by the gravitational pull of the moon and the sun across planetary oceans."
open(os.path.join(store, "spans.jsonl"), "w").write(json.dumps({
    "id": "p0010.1", "segment": "p0010", "locator": "10", "kind": "para",
    "text": text, "norm": srcnorm.norm(text)
}) + "\n")
open(os.path.join(store, "labels.json"), "w").write(json.dumps({"pages": [{"pdf_index": 10, "label": "10"}]}))
open(os.path.join(store, "outline.json"), "w").write(json.dumps([]))
PY

while IFS= read -r line; do
  case "$line" in
    PASS:*) ok "cite: ${line#PASS:}" ;;
    FAIL:*) bad "cite: $(cut -d: -f2 <<<"$line")" "$(cut -d: -f3- <<<"$line")" ;;
  esac
done < <(python3 -I - "$STORE" "$HOOKS_SOURCE" <<'PY'
import sys
store_dir, hooks_source = sys.argv[1], sys.argv[2]
sys.path.insert(0, hooks_source)
import cite_check as c
import verify as sv

spans, order = sv.load_spans(store_dir)
allowed = {"p0010.1"}

# Test 3.1: Valid citation resolves to locator
r1, _ = c.check_reply("Gravity causes tides [[cite:p0010.1]].", spans, order, allowed, store_dir)
if "(p. 10)" in r1 and "[[cite:" not in r1:
    print("PASS:valid_citation_resolves")
else:
    print(f"FAIL:valid_citation_resolves:{r1}")

# Test 3.2: Unverified / forged citation is rewritten to (citation removed)
r2, _ = c.check_reply("Fake claim [[cite:p9999.9]].", spans, order, allowed, store_dir)
if "(citation removed)" in r2 and "[[cite:" not in r2:
    print("PASS:unverified_citation_removed")
else:
    print(f"FAIL:unverified_citation_removed:{r2}")

# Test 3.3: Malformed / unclosed citation token is neutralized
r3, _ = c.check_reply("Notice [[cite:unclosed here.", spans, order, allowed, store_dir)
if "[ [cite:unclosed" in r3 and "[[cite:" not in r3:
    print("PASS:malformed_citation_neutralized")
else:
    print(f"FAIL:malformed_citation_neutralized:{r3}")

# Test 3.4: Adversarial citation mimicry: ![[cite:fake.png|500]] is not treated as safe wikilink
r4, _ = c.check_reply("Attack ![[cite:fake.png|500]] here.", spans, order, allowed, store_dir)
if "[[cite:" not in r4 and (("(citation removed)" in r4) or ("[ [cite:" in r4)):
    print("PASS:citation_mimicry_neutralized")
else:
    print(f"FAIL:citation_mimicry_neutralized:{r4}")

# Test 3.5: Fullwidth citation tokens neutralized
r5, _ = c.check_reply("Fullwidth ［［cite:p0010.1］］ attack.", spans, order, allowed, store_dir)
if "［［" not in r5 and "[[cite:" not in r5 and "[ [cite:p0010.1] ]" in r5:
    print("PASS:fullwidth_citation_neutralized")
else:
    print(f"FAIL:fullwidth_citation_neutralized:{r5}")

# Test 3.6: Mixed reply: wikilink embed preserved while citations resolved / removed
mixed = "Diagram: ![[viz-tides-1.png|500]] Note: [[Lunar Orbit]] Fact: [[cite:p0010.1]] Fake: [[cite:p9999.9]]"
r6, _ = c.check_reply(mixed, spans, order, allowed, store_dir)
if "![[viz-tides-1.png|500]]" in r6 and "[[Lunar Orbit]]" in r6 and "(p. 10)" in r6 and "(citation removed)" in r6 and "[[cite:" not in r6:
    print("PASS:mixed_reply_preserves_embeds_and_verifies_cites")
else:
    print(f"FAIL:mixed_reply_preserves_embeds_and_verifies_cites:{r6}")
PY
)

# Test 3.7: Fallback sed in md-log-stop-hook.sh preserves wikilinks while stripping cites
sed_input='![[viz-demo.png|500]] [[My Note]] [[cite:p0010.1]] [[cite:unclosed'
sed_output=$(printf '%s' "$sed_input" | LC_ALL=C sed -E 's/\[\[cite:[^]]*\]\]/(citation removed: cite check failed)/g; s/\[\[cite:/[ [cite:/g')

if grep -q '!\[\[viz-demo\.png|500\]\]' <<<"$sed_output" \
   && grep -q '\[\[My Note\]\]' <<<"$sed_output" \
   && grep -q '(citation removed: cite check failed)' <<<"$sed_output" \
   && grep -q '\[ \[cite:unclosed' <<<"$sed_output"; then
  ok "cite: fallback sed in md-log-stop-hook.sh preserves wikilinks and embeds while neutralizing cites"
else
  bad "cite: fallback sed in md-log-stop-hook.sh preserves wikilinks and embeds while neutralizing cites" "$sed_output"
fi

# ----------------------------------------------------------------------------
# 4. IDEMPOTENCY & LOG STABILITY
# ----------------------------------------------------------------------------
echo "== 4. Idempotency & Stability =="

fresh_test
cat > "$TR" <<'EOF'
{"type":"user","timestamp":"2026-10-22T10:00:00Z","message":{"role":"user","content":"<command-name>/teach</command-name><command-args>Tides</command-args>"}}
{"type":"assistant","timestamp":"2026-10-22T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"Explanation:\n| A | B |\n|---|---|\n| 1 | 2 |\nDiagram: ![[viz-sample.png|500]]"}]}}
EOF

OBSIDIAN_VAULT_PATH="$VAULT" jq -nc --arg tr "$TR" --arg sid "sess-idem" '{transcript_path:$tr, session_id:$sid}' \
  | bash "$HOOKS/md-log-stop-hook.sh" >/dev/null 2>&1
LOG="$VAULT/sessions/2026-10-22 Tides.md"
cp "$LOG" "$TEST_DIR/first_run.md"

OBSIDIAN_VAULT_PATH="$VAULT" jq -nc --arg tr "$TR" --arg sid "sess-idem" '{transcript_path:$tr, session_id:$sid}' \
  | bash "$HOOKS/md-log-stop-hook.sh" >/dev/null 2>&1
diff_out=$(diff -u "$TEST_DIR/first_run.md" "$LOG" || true)

if [ -z "$diff_out" ]; then
  ok "stability: repeated hook execution on same transcript is 100% idempotent"
else
  bad "stability: repeated hook execution on same transcript is 100% idempotent" "$diff_out"
fi

# ----------------------------------------------------------------------------
# SUMMARY
# ----------------------------------------------------------------------------
echo "======================================================================"
echo "Results: passed=$PASS failed=$FAIL total=$((PASS+FAIL))"
if [ $FAIL -gt 0 ]; then
  echo "Failed tests:"
  for f in "${FAILED[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
echo "ALL OBSIDIAN TABLE & VISUALIZATION VERIFICATION TESTS PASSED."
exit 0
