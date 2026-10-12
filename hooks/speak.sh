#!/bin/bash
# Stop hook: speak the teacher's final reply aloud (macOS `say`), when speech is toggled on.
# Never blocks Claude: emits nothing on stdout, always exits 0, and detaches the speech process.
# stop_hook_active: this hook never returns a block decision, so it cannot cause a Stop loop; when another
# hook made Claude continue, the new final message is still a fresh reply and is spoken (replacing any old one).
# Ordering: each run takes a sequence number on entry; after the (slow) cleaning it only speaks if no newer
# reply, question, answer, interrupt or toggle-off happened since (see speak_spawn).
# Dedupe: text blocks the question hook already spoke this turn are skipped.
# Env: SPEAK_CMD (default "say"; split on whitespace, never eval'd) receives the text on stdin.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"

INPUT=$(cat)
speak_enabled || exit 0
speak_enter || exit 0
SPOKEN_FILE=$(speak_spoken_file)

BLOCKS=()
MSG=$(speak_field '.last_assistant_message // empty | strings | sub("^\\s+"; "") | sub("\\s+$"; "")')
if [ -n "$MSG" ]; then
  BLOCKS=("$MSG")
else
  TRANSCRIPT_PATH=$(speak_field '.transcript_path // empty | strings')
  if [ -f "$TRANSCRIPT_PATH" ]; then
    while IFS= read -r -d '' b; do BLOCKS+=("$b"); done < <(speak_turn_texts "$TRANSCRIPT_PATH" final)
  fi
fi

MSG=""
for b in "${BLOCKS[@]}"; do
  speak_was_spoken "$b" && continue
  speak_mark_spoken "$b"
  MSG="${MSG:+$MSG

}$b"
done
# Everything here was already spoken by the question hook: leave that speech alone.
[ "${#BLOCKS[@]}" -gt 0 ] && [ -z "$MSG" ] && exit 0

# Cap before cleaning (cleaning cost grows with size), then again after.
MSG="${MSG:0:$((SPEAK_MAX_CHARS * 2))}"
TEXT=$(printf '%s' "$MSG" | bash "$HERE/speak-clean.sh" 2>/dev/null)
speak_spawn "${TEXT:0:$SPEAK_MAX_CHARS}" "$MY_SEQ"
exit 0
