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

# One render at a time per session: the live watcher (md-log-watch.py) and the Stop/answer hooks
# all call this script. The transcript is read inside the lock, so the last writer always has the
# newest content. Same lock as speak_lock: a symlink naming "pid lstart", broken at once if its owner is gone.
STATE_DIR="${MD_LOG_STATE_DIR:-$HOME/.claude/learn-md-log}"
case "$STATE_DIR" in -*) STATE_DIR="./$STATE_DIR" ;; esac
LOCK="$STATE_DIR/$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9_-' '_' | cut -c1-80).lock"
pid_start() { LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//; s/ *$//'; }
md_lock() {
  mkdir -p -- "$STATE_DIR" 2>/dev/null || return 1
  local i=0 owner pid start
  LOCK_ME="$$ $(pid_start $$)"
  while ! ln -s "$LOCK_ME" "$LOCK" 2>/dev/null; do
    if [ -L "$LOCK" ]; then
      owner=$(readlink "$LOCK" 2>/dev/null)
      read -r pid start <<<"$owner"
      case "$pid" in ''|*[!0-9]*) pid=0 ;; esac
      if [ "$pid" -le 1 ] || [ -z "$start" ] || [ "$(pid_start "$pid")" != "$start" ]; then
        [ "$(readlink "$LOCK" 2>/dev/null)" = "$owner" ] && rm -f "$LOCK"
      fi
    elif [ -e "$LOCK" ]; then
      rmdir "$LOCK" 2>/dev/null || mv -f "$LOCK" "$LOCK.stale.$$" 2>/dev/null
    fi
    i=$((i + 1)); [ "$i" -ge 400 ] && return 1   # 20s
    sleep 0.05
  done
}
md_unlock() { [ "$(readlink "$LOCK" 2>/dev/null)" = "$LOCK_ME" ] && rm -f "$LOCK"; return 0; }
md_lock || exit 0
trap md_unlock EXIT

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

# Source mode: check the tutor's citations in the rendered note before it is moved into place, still
# under the lock, so no live citation token ever reaches the vault and no render can land after the
# check. Exits at once for other sessions (hooks/source/cite_check.py).
CITE_CHECK="$(cd "$(dirname "$0")" && pwd)/source/cite_check.py"
cite_check_note() {
  [ -f "$CITE_CHECK" ] && MD_LOG_LOCK_HELD="$LOCK_ME" MD_LOG_FILE="$1" python3 -I "$CITE_CHECK" --hook <<<"$INPUT"
  return 0
}

TMP="${LOG_FILE}.tmp.$$"
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
} > "$TMP" && cite_check_note "$TMP" && mv -f "$TMP" "$LOG_FILE"

if [ -n "$EXISTING" ] && [ "$EXISTING" != "$LOG_FILE" ]; then
  rm -f "$EXISTING"
fi

exit 0
