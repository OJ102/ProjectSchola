#!/bin/bash
# UserPromptSubmit hook: new input interrupts speech. Kills only the process recorded in our pid file
# (pid + start time must match), never other `say` processes. Prints nothing (stdout here would be
# injected into the prompt) and always exits 0. Runs even when the flag is off, so that turning speech
# off mid-sentence and then typing still stops it; with no pid file it does nothing.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"
cat >/dev/null
speak_stop >/dev/null 2>&1
exit 0
