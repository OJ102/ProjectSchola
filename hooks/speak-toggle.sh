#!/bin/bash
# Turn speech out on or off: speak-toggle.sh on|off|status
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"
case "${1:-status}" in
  on)
    mkdir -p -- "$SPEAK_STATE_DIR" && touch "$SPEAK_FLAG" || { echo "could not create $SPEAK_FLAG" >&2; exit 1; }
    command -v "$(read -r -a c <<<"${SPEAK_CMD:-say}"; echo "${c[0]}")" >/dev/null 2>&1 \
      || echo "warning: speech command '${SPEAK_CMD:-say}' not found (macOS 'say' is required)" >&2
    echo "speech on" ;;
  off)
    rm -f "$SPEAK_FLAG"
    speak_halt >/dev/null 2>&1
    echo "speech off" ;;
  status)
    speak_enabled && echo "speech on" || echo "speech off" ;;
  *) echo "usage: speak-toggle.sh on|off|status" >&2; exit 2 ;;
esac
