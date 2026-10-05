#!/bin/bash
# Stop hook: rebuild this session's Obsidian log from the transcript after every reply.
# Output: $OBSIDIAN_VAULT_PATH/sessions/<date>-<session>.md (rewritten in full each time, so it's idempotent).

VAULT_PATH="${OBSIDIAN_VAULT_PATH:-${HOME}/Obsidian/learn}"
SESSIONS_DIR="${VAULT_PATH}/sessions"

INPUT=$(cat)
TRANSCRIPT_PATH=$(jq -r '.transcript_path // empty' <<<"$INPUT")
SESSION_ID=$(jq -r '.session_id // empty' <<<"$INPUT")
[ -f "$TRANSCRIPT_PATH" ] || exit 0
[ -n "$SESSION_ID" ] || SESSION_ID=$(basename "$TRANSCRIPT_PATH" .jsonl)
SID="${SESSION_ID:0:8}"

mkdir -p "$SESSIONS_DIR"
LOG_FILE=$(ls "$SESSIONS_DIR"/*-"$SID".md 2>/dev/null | head -1)
[ -n "$LOG_FILE" ] || LOG_FILE="${SESSIONS_DIR}/$(date +%Y-%m-%d)-${SID}.md"

# Quote every line of a multi-line string so it stays inside an Obsidian callout.
JQ_PROGRAM='
def quoted: gsub("\n"; "\n> ");
select((.isSidechain // false) | not)
| if .type == "assistant" then
    .message.content[]? | select(.type == "text") | .text
  elif .type == "user" then
    if (.toolUseResult | type) == "object" and .toolUseResult.questions != null then
      .toolUseResult as $r
      | $r.questions[]
      | "> [!question] " + (.question | quoted) + "\n"
        + ([.options[]? | "> - " + (.label | quoted)] | join("\n"))
        + "\n\n**Your answer:** " + ($r.answers[.question] // "_no answer_")
    elif (.message.content | type) == "string" and (.isMeta != true) then
      .message.content as $c
      | if ($c | test("<command-name>")) then
          ($c | capture("<command-name>(?<n>[^<]*)</command-name>").n) as $name
          | ($c | capture("<command-args>(?<a>[\\s\\S]*?)</command-args>")? .a // "") as $args
          | "> [!note] You\n> " + $name + " " + ($args | quoted)
        elif ($c | test("^\\s*<")) then empty
        else "> [!note] You\n> " + ($c | quoted)
        end
    else empty end
  else empty end
| . + "\n"
'

TMP="${LOG_FILE}.tmp"
{
  echo "# Teaching session $(basename "$LOG_FILE" .md)"
  echo ""
  jq -r "$JQ_PROGRAM" "$TRANSCRIPT_PATH" 2>/dev/null
} > "$TMP" && mv "$TMP" "$LOG_FILE"

exit 0
