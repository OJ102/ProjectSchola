#!/bin/bash
# UserPromptSubmit hook: new input interrupts speech. Kills only the process recorded in our pid file
# (pid + start time must match), never other `say` processes, and bumps the sequence number so a reply
# still being prepared by speak.sh is dropped. Prints nothing (stdout here would be injected into the
# prompt) and always exits 0. Runs even when the flag is off, so leftover speech still stops.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"
cat >/dev/null
[ -d "$SPEAK_STATE_DIR" ] || exit 0
{ speak_lock && { speak_bump_seq; speak_stop; speak_unlock; }; } >/dev/null 2>&1
exit 0
