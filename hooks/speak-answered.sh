#!/bin/bash
# PostToolUse(AskUserQuestion) hook: answering a quiz is new input, so it interrupts our speech the same
# way a typed prompt does (UserPromptSubmit does not fire for quiz answers). Prints nothing, exits 0.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"
cat >/dev/null
[ -d "$SPEAK_STATE_DIR" ] || exit 0
speak_halt >/dev/null 2>&1
exit 0
