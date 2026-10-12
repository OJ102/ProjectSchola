#!/bin/bash
# PreToolUse(AskUserQuestion) hook: Stop only fires at the end of a turn, so in a /teach probe the
# teaching text and the quiz questions would never be heard. Before the question dialog opens, speak the
# assistant text of this turn that hasn't been spoken yet, then each question and its option labels.
# Async, prints nothing, always exits 0; never affects whether the tool runs.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"

INPUT=$(cat)
speak_enabled || exit 0
speak_enter || exit 0
SPOKEN_FILE=$(speak_spoken_file)

# Wait (we're async) up to ~1.5s for this tool call to reach the transcript, so the text written just
# before it is there too. Then voice the turn's text after the marker, up to this tool call, and move the
# marker to it. If the entry never shows up, voice only the question; the earlier text stays unmarked and
# Stop picks it up.
MSG=""
TRANSCRIPT_PATH=$(speak_field '.transcript_path // empty | strings')
TOOL_USE_ID=$(speak_field '.tool_use_id // empty | strings')
if [ -f "$TRANSCRIPT_PATH" ]; then
  if [ -n "$TOOL_USE_ID" ]; then
    i=0
    until grep -qF -- "\"$TOOL_USE_ID\"" "$TRANSCRIPT_PATH" 2>/dev/null; do
      i=$((i + 1)); [ "$i" -gt 30 ] && break
      sleep 0.05
    done
  fi
  SCAN=$(speak_turn_scan "$TRANSCRIPT_PATH" "$TOOL_USE_ID")
  if [ -n "$SCAN" ]; then
    OUT=$(jq -c --arg m "$(speak_marker)" --arg tid "$TOOL_USE_ID" '
      . as $s | ($m | tonumber? // null) as $m0
      | (if $m0 != null and $m0 >= $s.start then $m0 else $s.start end) as $mk
      | if $s.tl != null then {t: [$s.b[] | select(.n > $mk and .n <= $s.tl) | .t], m: $s.tl}
        elif $tid == "" then {t: [$s.b[] | select(.n > $mk) | .t], m: $s.n}
        else {t: [], m: $mk} end
      | .t |= join("\n\n")' <<<"$SCAN" 2>/dev/null)
    MSG=$(jq -r '.t // empty' <<<"$OUT" 2>/dev/null)
    speak_set_marker "$(jq -r '.m // 0' <<<"$OUT" 2>/dev/null)"
  fi
fi
[ -f "$SPOKEN_FILE" ] || speak_set_marker 0   # mark this turn as a voiced-quiz turn even without a transcript

# Questions with their options, as plain sentences: "Question? The options are: A. B. C."
QUESTIONS=$(speak_field '
  [.tool_input.questions[]? | objects
   | (.question | strings) as $q
   | [.options[]? | (.label? // .) | strings | sub("^\\s+"; "") | sub("[\\s.]+$"; "") | select(length > 0)] as $o
   | $q + (if ($o | length) > 0 then "\n\nThe options are: " + ($o | join(". ")) + "." else "" end)]
  | join("\n\n")')
MSG="${MSG:+$MSG

}$QUESTIONS"

MSG="${MSG:0:$((SPEAK_MAX_CHARS * 2))}"
TEXT=$(printf '%s' "$MSG" | bash "$HERE/speak-clean.sh" 2>/dev/null)
speak_spawn "${TEXT:0:$SPEAK_MAX_CHARS}" "$MY_SEQ"
exit 0
