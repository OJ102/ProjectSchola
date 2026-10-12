#!/bin/bash
# Status line: md-log indicator plus a speech badge (🔊 speaking / 🔈 speech on).
cat >/dev/null 2>&1   # status line input JSON is not needed

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/speak-common.sh"

PARTS=()

STATE_FILE="/tmp/md-log-state.json"
if [ -f "$STATE_FILE" ] && [ -s "$STATE_FILE" ]; then
  COUNT=$(jq 'length' "$STATE_FILE" 2>/dev/null || echo 0)
  case "$COUNT" in ''|*[!0-9]*) COUNT=0 ;; esac
  [ "$COUNT" -gt 0 ] && PARTS+=("🗒️ md-log active ($COUNT Q pending)")
fi

if speak_enabled; then
  BADGE="🔈 speech on"
  if [ -f "$SPEAK_PID_FILE" ]; then
    read -r PID START < "$SPEAK_PID_FILE" 2>/dev/null
    case "$PID" in ''|*[!0-9]*) ;; *)
      [ -n "$START" ] && [ "$(speak_pid_start "$PID")" = "$START" ] && BADGE="🔊 speaking" ;;
    esac
  fi
  PARTS+=("$BADGE")
fi

OUT=""
for p in "${PARTS[@]}"; do OUT="${OUT:+$OUT · }$p"; done
echo "$OUT"
