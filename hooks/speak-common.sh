#!/bin/bash
# Shared settings for the speech-out hooks. Sourced, not executed.
# State lives in a fixed per-user dir (not $CLAUDE_PLUGIN_DATA) because the toggle skill and the
# status line run outside the hook environment and must see the same flag.
SPEAK_STATE_DIR="${SPEAK_STATE_DIR:-$HOME/.claude/learn-voice-teacher}"
SPEAK_FLAG="$SPEAK_STATE_DIR/speak.on"
SPEAK_PID_FILE="$SPEAK_STATE_DIR/speak.pid"   # "<pid> <process start time>" of our running speech
SPEAK_MAX_CHARS="${SPEAK_MAX_CHARS:-6000}"

speak_enabled() { [ -f "$SPEAK_FLAG" ]; }

# Start time of a pid, used to make sure a recorded pid still belongs to the process we started.
speak_pid_start() { LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//; s/ *$//'; }

# Kill our recorded speech process, and nothing else.
speak_stop() {
  [ -f "$SPEAK_PID_FILE" ] || return 0
  local pid start
  read -r pid start < "$SPEAK_PID_FILE" 2>/dev/null
  rm -f "$SPEAK_PID_FILE"
  case "$pid" in ''|*[!0-9]*) return 0 ;; esac
  [ "$pid" -gt 1 ] || return 0
  [ -n "$start" ] && [ "$(speak_pid_start "$pid")" = "$start" ] || return 0
  kill "$pid" 2>/dev/null
  return 0
}
