#!/bin/bash
# Stop hook: speak the teacher's final reply aloud (macOS `say`), when speech is toggled on.
# Never blocks Claude: emits nothing on stdout, always exits 0, and detaches the speech process.
# stop_hook_active: this hook never returns a block decision, so it cannot cause a Stop loop; when another
# hook made Claude continue, the new final message is still a fresh reply and is spoken (replacing any old one).
# Ordering: each run takes a sequence number on entry; after the (slow) cleaning it only speaks if no newer
# reply, question, answer, interrupt or toggle-off happened since (see speak_spawn).
# Dedupe: blocks up to the per-turn marker (set by the question hook) were already voiced and are skipped.
# Env: SPEAK_CMD (default "say"; split on whitespace, never eval'd) receives the text on stdin.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"

INPUT=$(cat)
speak_enabled || exit 0
speak_enter || exit 0
SPOKEN_FILE=$(speak_spoken_file)

LAM=$(speak_field '.last_assistant_message // empty | strings | sub("^\\s+"; "") | sub("\\s+$"; "")')
TRANSCRIPT_PATH=$(speak_field '.transcript_path // empty | strings')
SCAN=""
[ -f "$TRANSCRIPT_PATH" ] && SCAN=$(speak_turn_scan "$TRANSCRIPT_PATH")
if [ -n "$SCAN" ]; then
  # After a voiced quiz (marker set this turn): every block past the marker. Otherwise: the closing reply,
  # i.e. blocks after the last tool call. last_assistant_message is added when the transcript doesn't end
  # with it yet (not flushed), and skipped when it is the already-voiced last block.
  MSG=$(jq -r --arg m "$(speak_marker)" --arg lam "$LAM" '
    . as $s | ($m | tonumber? // null) as $m0
    | (if $m0 != null and $m0 >= $s.start then $m0 else null end) as $mk
    | (if $mk != null then [$s.b[] | select(.n > $mk)] else [$s.b[] | select($s.lt == null or .n > $s.lt)] end)
    | [.[].t] as $texts
    | ($s.b | last) as $lastb
    | (if $lam == "" or ($lastb != null and $lastb.t == $lam) then $texts else $texts + [$lam] end)
    | join("\n\n")' <<<"$SCAN" 2>/dev/null)
  [ -f "$SPOKEN_FILE" ] && speak_set_marker "$(jq -r '.n' <<<"$SCAN")"
else
  MSG="$LAM"
fi
# Nothing new to say (e.g. all voiced by the question hook): leave the current speech alone.
[ -n "$MSG" ] || exit 0

# Cap before cleaning (cleaning cost grows with size), then again after.
MSG="${MSG:0:$((SPEAK_MAX_CHARS * 2))}"
TEXT=$(printf '%s' "$MSG" | bash "$HERE/speak-clean.sh" 2>/dev/null)
speak_spawn "${TEXT:0:$SPEAK_MAX_CHARS}" "$MY_SEQ"
exit 0
