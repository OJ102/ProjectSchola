#!/usr/bin/env bash
# ==============================================================================
# Adversarial Stress Test Suite: Obsidian Tables, Visualizations & Neutralization
# Target: hooks/source/cite_check.py, hooks/md-log-stop-hook.sh, skills/visualize
# Author: Adversary (Red Teamer)
# ==============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$ROOT_DIR/hooks"
CITE_CHECK="$HOOKS_DIR/source/cite_check.py"
STOP_HOOK="$HOOKS_DIR/md-log-stop-hook.sh"

FAILED=0
EXPLOITS=0
TOTAL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

pass() {
  TOTAL=$((TOTAL + 1))
  echo -e "  [${GREEN}PASS${NC}] $1"
}

fail() {
  TOTAL=$((TOTAL + 1))
  FAILED=$((FAILED + 1))
  echo -e "  [${RED}FAIL${NC}] $1"
  [ -n "${2:-}" ] && echo -e "         ${YELLOW}Details:${NC} $2"
}

exploit() {
  TOTAL=$((TOTAL + 1))
  EXPLOITS=$((EXPLOITS + 1))
  echo -e "  [${RED}${BOLD}EXPLOIT FOUND${NC}] $1"
  [ -n "${2:-}" ] && echo -e "         ${YELLOW}Breakage:${NC} $2"
}

echo -e "${BOLD}======================================================================${NC}"
echo -e "${BOLD}Starting Adversarial Red Team Attack Suite${NC}"
echo -e "${BOLD}======================================================================${NC}"

JQ_PROGRAM='
def quoted: gsub("\n"; "\n> ");
def table_spaced: gsub("(?<=[^\n|])\n(?=[ \t]*\\|)"; "\n\n") | gsub("(?<=\\|)\n(?=[ \t]*[^ \t\n|])"; "\n\n");
def is_question_result: (.toolUseResult | type) == "object" and ((.toolUseResult.questions | type) == "array");
def entry:
  if .type == "assistant" then
    .message.content[]? | select(.type == "text") | .text | table_spaced
  elif .type == "user" then
    if is_question_result then
      .toolUseResult as $r
      | $r.questions[]
      | (.options // []) as $opts
      | (if ($opts | type) == "array" and ($opts | length) > 0 then "\n" + ([ $opts[] | "> - " + (.label | quoted) ] | join("\n")) else "" end) as $opts_rendered
      | "> [!question] " + (.question | quoted)
        + $opts_rendered
        + "\n>\n> **Your answer:** " + (((($r.answers // {})[.question] // "_no answer_") | tostring) | quoted)
    elif (.message.content | type) == "string" and (.isMeta != true) then
      .message.content as $c
      | if ($c | test("<command-name>")) then
          (($c | capture("<command-name>(?<n>[^<]*)</command-name>").n) // "") as $name
          | (($c | capture("<command-args>(?<a>[\\s\\S]*?)</command-args>").a) // "") as $args
          | "> [!note] You\n> " + $name + " " + ($args | quoted)
        elif ($c | test("^\\s*<")) then empty
        else "> [!note] You\n> " + ($c | quoted)
        end
    else empty end
  else empty end;
select((.isSidechain // false) | not)
| try (entry | . + "\n\n") catch empty
'

# ------------------------------------------------------------------------------
# Test Helper: Run Python snippet importing cite_check.neutralize_note
# ------------------------------------------------------------------------------
run_neutralize_note() {
  python3 -I -c '
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "hooks/source"))
import cite_check as cc
text = sys.stdin.read()
print(cc.neutralize_note(text), end="")
' "$ROOT_DIR"
}

# ------------------------------------------------------------------------------
# Test Helper: Run check_reply through cite_check
# ------------------------------------------------------------------------------
run_check_reply() {
  python3 -I -c '
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "hooks/source"))
import cite_check as cc
text = sys.stdin.read()
# mock spans with only p0010.1 allowed
spans = {"p0010.1": {"text": "the pull of the moon creates ocean tides", "locator": "10"}}
order = ["p0010.1"]
allowed = {"p0010.1"}
class MockStore:
    pass
class MockSv:
    @staticmethod
    def locator_text(st, span):
        loc = span.get("locator", "?")
        return f"p. {loc}"
cc.sv = MockSv
res, issues = cc.check_reply(text, spans, order, allowed, MockStore())
print(res, end="")
' "$ROOT_DIR"
}

# ==============================================================================
echo -e "\n${BOLD}[Vector 1] Wikilink & Cite Mimicry / Lookahead Bypass${NC}"
# ==============================================================================

# Attack 1.1: Image embed named cite:fake.png
OUT=$(printf '![[cite:fake.png|500]]' | run_neutralize_note)
if [[ "$OUT" == *"cite:fake.png"* ]] && [[ "$OUT" != *"[ ["* ]]; then
  exploit "Vector 1.1: ![[cite:fake.png|500]] treated as wikilink instead of neutralized" "$OUT"
else
  pass "Vector 1.1: ![[cite:fake.png|500]] is neutralized to prevent citation mimicry ($OUT)"
fi

# Attack 1.2: Legitimate image named citation.png or recite.png
OUT=$(printf '![[citation.png|500]]' | run_neutralize_note)
if [[ "$OUT" == "![[citation.png|500]]" ]]; then
  pass "Vector 1.2: Legitimate embed ![[citation.png|500]] preserved intact"
else
  fail "Vector 1.2: Legitimate embed ![[citation.png|500]] was mangled: $OUT"
fi

# Attack 1.3: Case sensitivity bypass (Uppercase CITE: or Cite:)
OUT=$(printf 'Here is [[Cite:p0010.1]] and [[CITE:p0010.1]]' | run_neutralize_note)
if [[ "$OUT" == *"[[Cite:p0010.1]]"* ]] || [[ "$OUT" == *"[[CITE:p0010.1]]"* ]]; then
  exploit "Vector 1.3: Case sensitivity bypass! WIKILINK_PROTECT lacks re.I; [[Cite:...]] survives unneutralized" "$OUT"
else
  pass "Vector 1.3: Case variations [[Cite:...]] are neutralized ($OUT)"
fi

# Attack 1.4: Fallback sed regex leakage under md-log-stop-hook.sh
SED_OUT=$(printf '[[Cite:foo]] and [[ cite:bar]] and ［［cite:baz］］ and [[cite:ok]]' | LC_ALL=C sed -E 's/(\[\[|［［)[[:space:]]*[cC][iI][tT][eE]:[^]］]*(\]\]|］］)/(citation removed: cite check failed)/g; s/(\[\[|［［)[[:space:]]*[cC][iI][tT][eE]:/[ [cite:/g')
if [[ "$SED_OUT" == *"[[Cite:foo]]"* ]] || [[ "$SED_OUT" == *"[[ cite:bar]]"* ]] || [[ "$SED_OUT" == *"［［cite:baz］］"* ]]; then
  exploit "Vector 1.4: Fallback sed leaves live citations intact (case, spaces, fullwidth brackets)!" "$SED_OUT"
else
  pass "Vector 1.4: Fallback sed safely neutralizes all citation variants"
fi

# ==============================================================================
echo -e "\n${BOLD}[Vector 2] Bracket Injection & Pipes inside Tables${NC}"
# ==============================================================================

# Attack 2.1: Table cell with unescaped embed pipe ![[diagram.png|400]]
TABLE_INPUT="| Tool | Diagram | Notes |
| --- | --- | --- |
| Sample | ![[diagram.png|400]] | Important |"

OUT=$(printf '%s' "$TABLE_INPUT" | run_check_reply)
TABLE_COL_COUNT=$(python3 -c '
import re, sys
t = sys.stdin.read()
row = t.strip().split("\n")[2]
cols = [c.strip() for c in re.split(r"(?<!\\)\|", row)[1:-1]]
print(len(cols))
' <<<"$OUT")

if [ "$TABLE_COL_COUNT" -gt 3 ]; then
  exploit "Vector 2.1: Pipe inside wikilink embed ![[diagram.png|400]] shatters markdown table row ($TABLE_COL_COUNT cols instead of 3)" "Requires escaped pipe \| in tables: ![[diagram.png\|400]]"
else
  pass "Vector 2.1: Table row preserved with correct column count"
fi

# Attack 2.2: Escaped embed pipe ![[diagram.png\|400]] in table through neutralize_note
OUT=$(printf '| ![[diagram.png\|400]] |' | run_neutralize_note)
if [[ "$OUT" == '| ![[diagram.png\|400]] |' ]]; then
  pass "Vector 2.2: Escaped embed ![[diagram.png\|400]] survived neutralize_note intact"
else
  fail "Vector 2.2: Escaped embed ![[diagram.png\|400]] broken by neutralize_note: $OUT"
fi

# Attack 2.3: Citation inside inline code in table cell
CODE_TABLE='| Header 1 | Header 2 |
| --- | --- |
| `[[cite:fake]]` | Normal |'
OUT=$(printf '%s' "$CODE_TABLE" | run_check_reply)
if [[ "$OUT" == *"[[cite:fake]]"* ]]; then
  exploit "Vector 2.3: Unverified citation leaked inside inline code cell!" "$OUT"
else
  pass "Vector 2.3: Citation in inline code cell correctly neutralized ($OUT)"
fi

# Attack 2.4: Paragraph-table transition without blank line
# In CommonMark, a table immediately following text without an empty line is not parsed as a table.
TRANSCRIPT_TABLE_INPUT='{"type":"assistant","message":{"content":[{"type":"text","text":"Explanation of tidal forces:\n| Force | Direction |\n|---|---|\n| Gravity | Towards moon |"}]}}'
OUT=$(jq -r "$JQ_PROGRAM" <<<"$TRANSCRIPT_TABLE_INPUT")
HAS_INTERRUPT=$(python3 -c '
import sys
lines = sys.stdin.read().splitlines()
broken = False
for i in range(1, len(lines)):
    line = lines[i].strip()
    prev = lines[i-1].strip()
    if line.startswith("|") and prev != "" and not prev.startswith("|"):
        broken = True
        print(f"Line {i} table row directly preceded by prose: {prev}")
        break
if broken:
    sys.exit(1)
sys.exit(0)
' <<<"$OUT") && RC=0 || RC=$?

if [ $RC -ne 0 ]; then
  exploit "Vector 2.4: Table immediately following prose without blank line violates CommonMark paragraph interruption" "$HAS_INTERRUPT"
else
  pass "Vector 2.4: Table preceded by blank line separation"
fi

# ==============================================================================
echo -e "\n${BOLD}[Vector 3] Unicode / Fullwidth Spoofing${NC}"
# ==============================================================================

# Attack 3.1: Fullwidth brackets citation ［［cite:p0010.1］］
OUT=$(printf '［［cite:p0010.1］］' | run_neutralize_note)
if [[ "$OUT" == *"［［"* ]] || [[ "$OUT" == *"cite:p0010.1"* && "$OUT" != *"[ ["* ]]; then
  exploit "Vector 3.1: Fullwidth citation ［［cite:...］］ survived neutralization!" "$OUT"
else
  pass "Vector 3.1: Fullwidth citation neutralized: $OUT"
fi

# Attack 3.2: Fullwidth visual embed ![［viz-test.png］]
OUT=$(printf '![［viz-test.png］]' | run_neutralize_note)
if [[ "$OUT" == *"![ ["* ]]; then
  fail "Vector 3.2: Fullwidth visual embed ![［viz-test.png］] was destroyed into spaced brackets: $OUT" "Obsidian requires ASCII ![[]]"
else
  pass "Vector 3.2: Fullwidth embed handled: $OUT"
fi

# Attack 3.3: Zero-width space inside citation [​[cite:p0010.1]​]
ZW_OUT=$(printf '[\u200b[cite:p0010.1]\u200b]' | run_neutralize_note)
if [[ "$ZW_OUT" == *"[[cite:p0010.1]]"* ]]; then
  exploit "Vector 3.3: Zero-width space smuggled citation past neutralization!" "$ZW_OUT"
else
  pass "Vector 3.3: Zero-width space citation neutralized: $ZW_OUT"
fi

# ==============================================================================
echo -e "\n${BOLD}[Vector 4] Callout Boundaries & Question Formatting${NC}"
# ==============================================================================

# Attack 4.1: Question without options (open-ended question)
OPEN_QUESTION_JSON='{
  "type": "user",
  "toolUseResult": {
    "questions": [
      {"question": "Explain gravitational differential in your own words."}
    ],
    "answers": {
      "Explain gravitational differential in your own words.": "It is the difference in pull."
    }
  }
}'

OUT=$(jq -r "$JQ_PROGRAM" <<<"$OPEN_QUESTION_JSON")
# In CommonMark, a blank line inside a callout without `>` terminates the callout!
UNQUOTED_OR_BLANK=$(printf '%s' "$OUT" | sed '/^[[:space:]]*$/d' | grep -v '^>' || true)
if [ -n "$UNQUOTED_OR_BLANK" ]; then
  exploit "Vector 4.1: Open-ended question callout broken by line without '>' prefix: '$UNQUOTED_OR_BLANK'" "$(printf '%q' "$OUT")"
else
  pass "Vector 4.1: Open-ended question callout is continuous without blank lines"
fi

# Attack 4.2: Multi-line user answer spills out of callout
MULTILINE_ANS_JSON='{
  "type": "user",
  "toolUseResult": {
    "questions": [
      {"question": "Summarize tidal forces."}
    ],
    "answers": {
      "Summarize tidal forces.": "First point: differential pull.\nSecond point: Earth stretches into ellipsoid."
    }
  }
}'

OUT=$(jq -r "$JQ_PROGRAM" <<<"$MULTILINE_ANS_JSON")
# Check if any non-empty line lacks `> `
UNQUOTED_LINE=$(grep -v '^>' <<<"$OUT" | grep -v '^[[:space:]]*$' || true)
if [ -n "$UNQUOTED_LINE" ]; then
  exploit "Vector 4.2: Multi-line user answer spills out of callout (line lacks '> '): '$UNQUOTED_LINE'"
else
  pass "Vector 4.2: All lines of multi-line user answer properly prefixed with '> '"
fi

# Attack 4.3: Consecutive questions formatting
MULTI_Q_JSON='{
  "type": "user",
  "toolUseResult": {
    "questions": [
      {"question": "Q1?", "options": [{"label": "A"}]},
      {"question": "Q2?", "options": [{"label": "B"}]}
    ],
    "answers": {"Q1?": "A", "Q2?": "B"}
  }
}'
OUT=$(jq -r "$JQ_PROGRAM" <<<"$MULTI_Q_JSON")
Q_CALLOUT_COUNT=$(grep -c '> \[!question\]' <<<"$OUT")
if [ "$Q_CALLOUT_COUNT" -eq 2 ]; then
  pass "Vector 4.3: Multiple consecutive questions both generate [!question] callouts"
else
  fail "Vector 4.3: Consecutive questions failed to generate 2 callouts (got $Q_CALLOUT_COUNT)"
fi

# ==============================================================================
echo -e "\n${BOLD}[Vector 5] Edge Cases in neutralize_note & Bracket Nesting${NC}"
# ==============================================================================

# Attack 5.1: Nested bracket citation smuggling: [[prefix [[cite:p0010.1]] ]]
NESTED_INPUT="[[prefix [[cite:p0010.1]] ]]"
OUT=$(printf '%s' "$NESTED_INPUT" | run_neutralize_note)
if [[ "$OUT" == *"[[cite:p0010.1]]"* ]]; then
  exploit "Vector 5.1: Live citation smuggled past neutralization via nested bracket: [[prefix [[cite:...]] ]]!" "$OUT"
else
  pass "Vector 5.1: Nested brackets neutralized safely: $OUT"
fi

# Attack 5.2: Embed swallowing inner citation: ![[foo.png|[[cite:leaked]]]]
EMBED_SMUGGLE="![[foo.png|[[cite:leaked]]]]"
OUT=$(printf '%s' "$EMBED_SMUGGLE" | run_neutralize_note)
if [[ "$OUT" == *"[[cite:leaked]]"* ]]; then
  exploit "Vector 5.2: Live citation smuggled inside image embed: ![[foo.png|[[cite:leaked]]]]!" "$OUT"
else
  pass "Vector 5.2: Embed with inner citation neutralized: $OUT"
fi

# Attack 5.3: Unclosed bracket bridging to citation
UNCLOSED_BRIDGE="Check [[unclosed wikilink and then [[cite:p0010.1]]"
OUT=$(printf '%s' "$UNCLOSED_BRIDGE" | run_neutralize_note)
if [[ "$OUT" == *"[[cite:p0010.1]]"* ]]; then
  exploit "Vector 5.3: Unclosed bracket bridges across and shields live citation [[cite:p0010.1]]!" "$OUT"
else
  pass "Vector 5.3: Unclosed bracket bridging neutralized: $OUT"
fi

# Attack 5.4: Multiple embeds on single line
MULTI_EMBED="![[viz1.png|300]] and ![[viz2.png|400]]"
OUT=$(printf '%s' "$MULTI_EMBED" | run_neutralize_note)
if [[ "$OUT" == "![[viz1.png|300]] and ![[viz2.png|400]]" ]]; then
  pass "Vector 5.4: Multiple embeds on single line preserved byte-for-byte"
else
  fail "Vector 5.4: Multiple embeds corrupted: $OUT"
fi

# Attack 5.5: Mermaid subroutine syntax A[[Subroutine A]] --> B[[Subroutine B]]
MERMAID_INPUT="graph TD
  A[[Subroutine A]] --> B[[Subroutine B]]"
OUT=$(printf '%s' "$MERMAID_INPUT" | run_neutralize_note)
if [[ "$OUT" == *"A[[Subroutine A]]"* && "$OUT" == *"B[[Subroutine B]]"* ]]; then
  pass "Vector 5.5: Mermaid subroutine double brackets preserved intact"
else
  fail "Vector 5.5: Mermaid subroutine syntax broken by neutralization: $OUT"
fi

# ==============================================================================
echo -e "\n${BOLD}======================================================================${NC}"
echo -e "${BOLD}Adversarial Attack Summary:${NC}"
echo -e "  Total Tests:    $TOTAL"
echo -e "  ${GREEN}Passed:${NC}         $((TOTAL - FAILED - EXPLOITS))"
echo -e "  ${RED}Failures:${NC}       $FAILED"
echo -e "  ${RED}${BOLD}Exploits Found:${NC} $EXPLOITS"
echo -e "${BOLD}======================================================================${NC}"

if [ "$EXPLOITS" -gt 0 ]; then
  exit 2
elif [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
