#!/bin/bash
# Stop hook: speak the teacher's final reply aloud (macOS `say`), when speech is toggled on.
# Never blocks Claude: emits nothing on stdout, always exits 0, and detaches the speech process.
# stop_hook_active: this hook never returns a block decision, so it cannot cause a Stop loop; when another
# hook made Claude continue, the new final message is still a fresh reply and is spoken (replacing any old one).
# Ordering: each run takes a sequence number on entry; after the (slow) cleaning it only speaks if no newer
# reply, interrupt or toggle-off has happened since. Stop, spawn and pid-file write happen under one lock.
# Env: SPEAK_CMD (default "say"; split on whitespace, never eval'd) receives the text on stdin.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"

INPUT=$(cat)
speak_enabled || exit 0

speak_lock || exit 0
MY_SEQ=$(speak_bump_seq)
speak_unlock

# jq rejects lone UTF-16 surrogates (\ud800); retry with them replaced by U+FFFD.
unsurrogate() { sed -E 's/\\u[dD][89a-fA-F][0-9a-fA-F]{2}/\\ufffd/g'; }
field() { jq -r "$1" <<<"$INPUT" 2>/dev/null || unsurrogate <<<"$INPUT" | jq -r "$1" 2>/dev/null; }

MSG=$(field '.last_assistant_message // empty | strings')
if [ -z "$MSG" ]; then
  TRANSCRIPT_PATH=$(field '.transcript_path // empty | strings')
  if [ -f "$TRANSCRIPT_PATH" ]; then
    # Text blocks of the final assistant turn: everything after the last user entry or tool call.
    MSG=$(jq -Rrn '
      reduce (inputs
              | (fromjson? // (gsub("\\\\u[dD][89a-fA-F][0-9a-fA-F]{2}"; "\\ufffd") | fromjson?))
              | objects | select((.isSidechain // false) | not)) as $e ([];
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

# Cap before cleaning (cleaning cost grows with size), then again after.
MSG="${MSG:0:$((SPEAK_MAX_CHARS * 2))}"
TEXT=$(printf '%s' "$MSG" | bash "$HERE/speak-clean.sh" 2>/dev/null)
TEXT="${TEXT:0:$SPEAK_MAX_CHARS}"

read -r -a CMD <<<"${SPEAK_CMD:-say}"
[ "${#CMD[@]}" -gt 0 ] || CMD=(say)

speak_lock || exit 0
if [ "$(speak_seq)" != "$MY_SEQ" ] || ! speak_enabled; then speak_unlock; exit 0; fi
speak_stop   # any newer reply replaces what we were still saying, even an unspeakable one
if [[ $TEXT =~ [^[:space:]] ]] && TMP=$(mktemp "$SPEAK_STATE_DIR/speak.XXXXXX"); then
  printf '%s\n' "$TEXT" > "$TMP"
  exec 3<"$TMP"
  rm -f "$TMP"
  nohup "${CMD[@]}" <&3 >/dev/null 2>&1 &
  PID=$!
  exec 3<&-
  START=$(speak_pid_start "$PID")
  [ -n "$START" ] && printf '%s %s\n' "$PID" "$START" > "$SPEAK_PID_FILE"
fi
speak_unlock
exit 0
