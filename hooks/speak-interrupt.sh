#!/bin/bash
# UserPromptSubmit hook: new input interrupts speech. Kills only the process recorded in our pid file
# (pid + start time must match), never other `say` processes, bumps the sequence number so a reply still
# being prepared is dropped, and clears the per-turn record of spoken text. Runs even when the flag is off,
# so leftover speech still stops. While speech is on, its only stdout is a JSON additionalContext note
# telling the model so (the teach skill's Voice mode keys off it); otherwise it prints nothing.
# Always exits 0.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"
INPUT=$(cat)
[ -d "$SPEAK_STATE_DIR" ] || exit 0
rm -f "$(speak_spoken_file)" 2>/dev/null   # a new turn starts: forget what was spoken
speak_halt >/dev/null 2>&1
if speak_enabled; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Speech out is on (learn-voice-teacher): your replies and AskUserQuestion questions are read aloud. In /teach, follow the Voice mode section."}}'
fi
exit 0
