#!/bin/bash
# Rebuild this session's Obsidian log from the transcript (runs on Stop, each answered question, and SessionEnd).
# Output: $OBSIDIAN_VAULT_PATH/sessions/<date> <teach topic>.md, rewritten in full each time so it's idempotent.
# The file is found again by its `session:` frontmatter, so it can be renamed once the topic is known.

VAULT_PATH="${OBSIDIAN_VAULT_PATH:-${HOME}/Obsidian/learn}"
SESSIONS_DIR="${VAULT_PATH}/sessions"

INPUT=$(cat)
TRANSCRIPT_PATH=$(jq -r '.transcript_path // empty' <<<"$INPUT")
SESSION_ID=$(jq -r '.session_id // empty' <<<"$INPUT")
[ -f "$TRANSCRIPT_PATH" ] || exit 0
[ -n "$SESSION_ID" ] || SESSION_ID=$(basename "$TRANSCRIPT_PATH" .jsonl)
SID="${SESSION_ID:0:8}"
mkdir -p "$SESSIONS_DIR"

TOPIC=$(jq -r '
  select(.type == "user" and (.message.content | type) == "string")
  | .message.content
  | select(test("<command-name>[^<]*teach</command-name>"))
  | (capture("<command-args>(?<a>[\\s\\S]*?)</command-args>").a // empty)
' "$TRANSCRIPT_PATH" 2>/dev/null | grep -v '^[[:space:]]*$' | head -1)
SAFE_TOPIC=$(printf '%s' "$TOPIC" | tr -d '/\\:*?"<>|#^[]' | tr -s ' ' | sed 's/^ //; s/ $//' | cut -c1-60)

DATE=$(jq -r 'select(.timestamp) | .timestamp' "$TRANSCRIPT_PATH" 2>/dev/null | head -1 | cut -c1-10)
[ -n "$DATE" ] || DATE=$(date +%Y-%m-%d)

EXISTING=$(grep -l -x "session: $SESSION_ID" "$SESSIONS_DIR"/*.md 2>/dev/null | head -1)
[ -n "$EXISTING" ] || EXISTING=$(ls "$SESSIONS_DIR"/*-"$SID".md 2>/dev/null | head -1)

BASE="$DATE ${SAFE_TOPIC:-session $SID}"
LOG_FILE="$SESSIONS_DIR/$BASE.md"
if [ -f "$LOG_FILE" ] && [ "$LOG_FILE" != "$EXISTING" ]; then
  LOG_FILE="$SESSIONS_DIR/$BASE ($SID).md"
fi

# Quote every line of a multi-line string so it stays inside an Obsidian callout.
JQ_PROGRAM='
def quoted: gsub("\n"; "\n> ");
def is_question_result: (.toolUseResult | type) == "object" and ((.toolUseResult.questions | type) == "array");
def entry:
  if .type == "assistant" then
    .message.content[]? | select(.type == "text") | .text
  elif .type == "user" then
    if is_question_result then
      .toolUseResult as $r
      | $r.questions[]
      | "> [!question] " + (.question | quoted) + "\n"
        + ([.options[]? | "> - " + (.label | quoted)] | join("\n"))
        + "\n\n**Your answer:** " + (($r.answers // {})[.question] // "_no answer_")
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
| try (entry | . + "\n") catch empty
'

TMP="${LOG_FILE}.tmp"
{
  echo "---"
  echo "session: $SESSION_ID"
  echo "topic: \"${TOPIC//\"/}\""
  echo "date: $DATE"
  echo "---"
  echo ""
  echo "# ${SAFE_TOPIC:-Teaching session}"
  echo ""
  jq -r "$JQ_PROGRAM" "$TRANSCRIPT_PATH" 2>/dev/null
} > "$TMP" && mv "$TMP" "$LOG_FILE"

if [ -n "$EXISTING" ] && [ "$EXISTING" != "$LOG_FILE" ]; then
  rm -f "$EXISTING"
fi

exit 0
