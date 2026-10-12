#!/bin/bash
# Stop hook: speak the teacher's final reply aloud (macOS `say`), when speech is toggled on.
# Never blocks Claude: emits nothing on stdout, always exits 0, and detaches the speech process.
# stop_hook_active: this hook never returns a block decision, so it cannot cause a Stop loop; when another
# hook made Claude continue, the new final message is still a fresh reply and is spoken (replacing any old one).
# Env: SPEAK_CMD (default "say"; split on whitespace, never eval'd) receives the text on stdin.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"

INPUT=$(cat)
speak_enabled || exit 0

MSG=$(jq -r '.last_assistant_message // empty | strings' <<<"$INPUT" 2>/dev/null)
if [ -z "$MSG" ]; then
  TRANSCRIPT_PATH=$(jq -r '.transcript_path // empty | strings' <<<"$INPUT" 2>/dev/null)
  if [ -f "$TRANSCRIPT_PATH" ]; then
    # Text blocks of the final assistant turn: everything after the last user entry or tool call.
    MSG=$(jq -Rrn '
      reduce (inputs | fromjson? | objects | select((.isSidechain // false) | not)) as $e ([];
        if $e.type == "user" then []
        elif $e.type == "assistant" then
          ($e.message.content // []) as $c
          | if ($c | type) == "string" then . + [$c]
            elif ($c | type) == "array" then
              if any($c[]; .type? == "tool_use") then []
              else . + [$c[] | select(.type? == "text") | .text | strings] end
            else . end
        else . end)
      | join("\n\n")' "$TRANSCRIPT_PATH" 2>/dev/null)
  fi
fi
[ -n "$MSG" ] || exit 0

TEXT=$(printf '%s' "$MSG" | bash "$HERE/speak-clean.sh" 2>/dev/null)
TEXT="${TEXT:0:$SPEAK_MAX_CHARS}"
[ -n "${TEXT//[[:space:]]/}" ] || exit 0

mkdir -p "$SPEAK_STATE_DIR" 2>/dev/null || exit 0
read -r -a CMD <<<"${SPEAK_CMD:-say}"
[ "${#CMD[@]}" -gt 0 ] || CMD=(say)

TMP=$(mktemp "$SPEAK_STATE_DIR/speak.XXXXXX") || exit 0
printf '%s\n' "$TEXT" > "$TMP"
exec 3<"$TMP"
rm -f "$TMP"

speak_stop   # a newer reply replaces whatever we were still saying
nohup "${CMD[@]}" <&3 >/dev/null 2>&1 &
PID=$!
exec 3<&-
START=$(speak_pid_start "$PID")
[ -n "$START" ] && printf '%s %s\n' "$PID" "$START" > "$SPEAK_PID_FILE"
exit 0
