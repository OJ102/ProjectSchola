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

MSG=""
TRANSCRIPT_PATH=$(speak_field '.transcript_path // empty | strings')
if [ -f "$TRANSCRIPT_PATH" ]; then
  while IFS= read -r -d '' b; do
    speak_was_spoken "$b" && continue
    speak_mark_spoken "$b"
    MSG="${MSG:+$MSG

}$b"
  done < <(speak_turn_texts "$TRANSCRIPT_PATH" all)
fi

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
