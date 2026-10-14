#!/bin/bash
# Live Obsidian sync lifecycle. Reads the hook JSON on stdin.
#   md-log-watch.sh start   (SessionStart) start one watcher for this session's transcript
#   md-log-watch.sh stop    (SessionEnd)   stop it; md-log-stop-hook.sh writes the final note
# The watcher (md-log-watch.py) rebuilds the note through md-log-stop-hook.sh whenever the
# transcript changes. It also exits by itself when Claude Code is gone, so a missed
# SessionEnd leaves no orphan. Set MD_LOG_LIVE=0 to turn live sync off (the note then
# updates at the end of each reply, as before). Never prints: SessionStart stdout reaches the model.

STATE_DIR="${MD_LOG_STATE_DIR:-$HOME/.claude/learn-md-log}"
case "$STATE_DIR" in -*) STATE_DIR="./$STATE_DIR" ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"

INPUT=$(cat)
SESSION_ID=$(jq -r '.session_id // empty' <<<"$INPUT" 2>/dev/null)
TRANSCRIPT_PATH=$(jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)
[ -n "$SESSION_ID" ] || exit 0
# The id names a file: keep it to safe characters.
KEY=$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9_-' '_' | cut -c1-80)
PID_FILE="$STATE_DIR/$KEY.pid"

pid_start() { LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//; s/ *$//'; }

# Prints the watcher's pid if the pid file still names a live watcher we started.
live_watcher() {
  [ -f "$PID_FILE" ] && [ ! -L "$PID_FILE" ] || return 1
  local pid start
  read -r pid start < "$PID_FILE" 2>/dev/null
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "$pid" -gt 1 ] && [ -n "$start" ] && [ "$(pid_start "$pid")" = "$start" ] || return 1
  echo "$pid"
}

# The Claude Code process: our parent, skipping any shell it ran us through.
claude_pid() {
  [ -n "$MD_LOG_PARENT_PID" ] && { echo "$MD_LOG_PARENT_PID"; return; }
  local p=$PPID comm i
  for i in 1 2 3; do
    comm=$(ps -o comm= -p "$p" 2>/dev/null | sed 's/^ *//; s/ *$//')
    case "${comm##*/}" in sh|bash|zsh|dash|-sh|-bash|-zsh) p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ') ;; *) break ;; esac
    case "$p" in ''|*[!0-9]*) echo 0; return ;; esac
  done
  echo "$p"
}

case "${1:-}" in
  start)
    [ "${MD_LOG_LIVE:-1}" = 0 ] && exit 0
    [ -n "$TRANSCRIPT_PATH" ] || exit 0
    command -v python3 >/dev/null || exit 0
    mkdir -p -- "$STATE_DIR" 2>/dev/null || exit 0
    live_watcher >/dev/null && exit 0   # resume or compact: already watching
    python3 -I "$HERE/md-log-watch.py" "$TRANSCRIPT_PATH" "$SESSION_ID" "$(claude_pid)" "$PID_FILE" \
      </dev/null >/dev/null 2>&1
    ;;
  stop)
    pid=$(live_watcher) && kill -TERM "$pid" 2>/dev/null
    rm -f "$PID_FILE"
    ;;
esac
exit 0
