#!/bin/bash
# Shared settings for the speech-out hooks. Sourced, not executed.
# State lives in a fixed per-user dir (not $CLAUDE_PLUGIN_DATA) because the toggle skill and the
# status line run outside the hook environment and must see the same flag.
SPEAK_STATE_DIR="${SPEAK_STATE_DIR:-$HOME/.claude/learn-voice-teacher}"
SPEAK_FLAG="$SPEAK_STATE_DIR/speak.on"
SPEAK_PID_FILE="$SPEAK_STATE_DIR/speak.pid"   # "<pid> <process start time>" of our running speech
SPEAK_SEQ_FILE="$SPEAK_STATE_DIR/speak.seq"   # bumped by every reply, interrupt and toggle-off
SPEAK_LOCK="$SPEAK_STATE_DIR/speak.lock"
SPEAK_MAX_CHARS="${SPEAK_MAX_CHARS:-6000}"
case "$SPEAK_MAX_CHARS" in ''|*[!0-9]*) SPEAK_MAX_CHARS=6000 ;; esac

speak_enabled() { [ -f "$SPEAK_FLAG" ]; }

# Start time of a pid, used to make sure a recorded pid still belongs to the process we started.
speak_pid_start() { LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//; s/ *$//'; }

# Prints the recorded pid if it is still the process we started; fails otherwise.
speak_live_pid() {
  [ -f "$SPEAK_PID_FILE" ] && [ ! -L "$SPEAK_PID_FILE" ] || return 1
  local pid start
  read -r pid start < "$SPEAK_PID_FILE" 2>/dev/null
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "${#pid}" -le 9 ] && [ "$pid" -gt 1 ] && [ -n "$start" ] || return 1
  [ "$(speak_pid_start "$pid")" = "$start" ] || return 1
  echo "$pid"
}

# Descendants of a pid (children, grandchildren, ...), deepest last.
speak_descendants() {
  local c
  for c in $(pgrep -P "$1" 2>/dev/null); do echo "$c"; speak_descendants "$c"; done
}

# Kill our recorded speech and its descendants (so wrapper commands like `sh -c "piper | afplay"`
# stop too), and nothing else. The tree is collected before anything is signalled.
speak_stop() {
  local pid kids
  pid=$(speak_live_pid)
  rm -f "$SPEAK_PID_FILE"
  [ -n "$pid" ] || return 0
  kids=$(speak_descendants "$pid")
  kill -TERM "$pid" $kids 2>/dev/null
  return 0
}

# Mutual exclusion between overlapping hooks: mkdir is atomic. A lock older than 10s is stale.
speak_lock() {
  mkdir -p "$SPEAK_STATE_DIR" 2>/dev/null || return 1
  local i=0 m
  while ! mkdir "$SPEAK_LOCK" 2>/dev/null; do
    m=$(stat -f %m "$SPEAK_LOCK" 2>/dev/null || stat -c %Y "$SPEAK_LOCK" 2>/dev/null)
    if [ -n "$m" ] && [ $(( $(date +%s) - m )) -gt 10 ]; then rmdir "$SPEAK_LOCK" 2>/dev/null; continue; fi
    i=$((i + 1)); [ "$i" -gt 200 ] && return 1
    sleep 0.05
  done
}
speak_unlock() { rmdir "$SPEAK_LOCK" 2>/dev/null; }

speak_seq() { local s; { read -r s < "$SPEAK_SEQ_FILE"; } 2>/dev/null; case "$s" in ''|*[!0-9]*) s=0 ;; esac; echo "$s"; }
# Call with the lock held. Prints the new sequence number.
speak_bump_seq() { local s=$(( $(speak_seq) % 1000000000 + 1 )); echo "$s" > "$SPEAK_SEQ_FILE"; echo "$s"; }
