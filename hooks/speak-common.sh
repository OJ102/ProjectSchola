#!/bin/bash
# Shared settings for the speech-out hooks. Sourced, not executed.
# State lives in a fixed per-user dir (not $CLAUDE_PLUGIN_DATA) because the toggle skill and the
# status line run outside the hook environment and must see the same flag.
SPEAK_STATE_DIR="${SPEAK_STATE_DIR:-$HOME/.claude/learn-voice-teacher}"
case "$SPEAK_STATE_DIR" in -*) SPEAK_STATE_DIR="./$SPEAK_STATE_DIR" ;; esac   # never parsed as an option
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

# Mutual exclusion between overlapping hooks. The lock is a symlink whose target is the owner's
# "pid lstart": creating it is atomic, and a lock whose owner is gone (killed hook, reused pid) is broken
# at once, so no age or clock is involved. Anything else at that path (e.g. an old lock directory) has no
# owner and is cleared too. $1 = tries at 50ms (default 200 = 10s).
speak_lock() {
  mkdir -p -- "$SPEAK_STATE_DIR" 2>/dev/null || return 1
  local tries="${1:-200}" i=0 owner pid start
  SPEAK_LOCK_ME="$$ $(speak_pid_start $$)"
  while ! ln -s "$SPEAK_LOCK_ME" "$SPEAK_LOCK" 2>/dev/null; do
    if [ -L "$SPEAK_LOCK" ]; then
      owner=$(readlink "$SPEAK_LOCK" 2>/dev/null)
      read -r pid start <<<"$owner"
      case "$pid" in ''|*[!0-9]*) pid=0 ;; esac
      if [ "$pid" -le 1 ] || [ -z "$start" ] || [ "$(speak_pid_start "$pid")" != "$start" ]; then
        [ "$(readlink "$SPEAK_LOCK" 2>/dev/null)" = "$owner" ] && rm -f "$SPEAK_LOCK"
      fi
    elif [ -e "$SPEAK_LOCK" ]; then
      rmdir "$SPEAK_LOCK" 2>/dev/null || mv -f "$SPEAK_LOCK" "$SPEAK_LOCK.stale.$$" 2>/dev/null
    fi
    i=$((i + 1)); [ "$i" -ge "$tries" ] && return 1
    sleep 0.05
  done
}
speak_unlock() { [ "$(readlink "$SPEAK_LOCK" 2>/dev/null)" = "$SPEAK_LOCK_ME" ] && rm -f "$SPEAK_LOCK"; return 0; }

# Stop speech now and invalidate replies still being prepared. Used on the synchronous paths (prompt,
# quiz answer, toggle-off), so it waits for the lock at most ~0.5s; without the lock, stopping is still
# safe because speak_stop only kills a pid whose start time matches.
speak_halt() {
  if speak_lock 10; then speak_bump_seq >/dev/null; speak_stop; speak_unlock
  else speak_bump_seq >/dev/null; speak_stop; fi
}

speak_seq() { local s; { read -r s < "$SPEAK_SEQ_FILE"; } 2>/dev/null; case "$s" in ''|*[!0-9]*) s=0 ;; esac; echo "$s"; }
# Call with the lock held. Prints the new sequence number.
speak_bump_seq() { local s=$(( $(speak_seq) % 1000000000 + 1 )); echo "$s" > "$SPEAK_SEQ_FILE"; echo "$s"; }

# ---- shared by the Stop and PreToolUse(AskUserQuestion) hooks -------------------------------------

# Hook JSON accessor. jq rejects lone UTF-16 surrogates (\ud800); retry with them replaced by U+FFFD.
speak_unsurrogate() { sed -E 's/\\u[dD][89a-fA-F][0-9a-fA-F]{2}/\\ufffd/g'; }
speak_field() { jq -r "$1" <<<"$INPUT" 2>/dev/null || speak_unsurrogate <<<"$INPUT" | jq -r "$1" 2>/dev/null; }

# Per-session marker of how far through the transcript speech has got this turn: the line number of the
# last entry already voiced. Position, not content, so a repeated "Correct." later in the turn is still
# spoken. Its presence also means a quiz was voiced this turn. Cleared on every user prompt.
speak_spoken_file() {
  local sid; sid=$(speak_field '.session_id // empty | strings' | tr -cd 'A-Za-z0-9_-' | cut -c1-64)
  echo "$SPEAK_STATE_DIR/spoken.${sid:-default}"
}
speak_marker() { local m; { read -r m < "$SPOKEN_FILE"; } 2>/dev/null; case "$m" in ''|*[!0-9]*) return 1 ;; esac; echo "$m"; }
speak_set_marker() { mkdir -p -- "$SPEAK_STATE_DIR" 2>/dev/null; echo "$1" > "$SPOKEN_FILE" 2>/dev/null; }

# Scan the transcript ($1) for the current turn (after the last real user prompt). Prints one JSON object:
#   {start: line of that prompt, n: last line, lt: line of the last tool call, tl: line of tool_use id $2,
#    b: [{n: line, t: trimmed text}] for each assistant text block of the turn}
speak_turn_scan() {
  jq -Rcn --arg tid "${2:-}" '
    reduce inputs as $raw ({n: 0, start: 0, lt: null, tl: null, b: []};
      .n += 1
      | ($raw | (fromjson? // (gsub("\\\\u[dD][89a-fA-F][0-9a-fA-F]{2}"; "\\ufffd") | fromjson?))) as $e
      | if ($e | type) != "object" or ($e.isSidechain // false) then .
        elif $e.type == "user" then
          (if ($e.message.content | type) == "string" then .b = [] | .lt = null | .tl = null | .start = .n else . end)
        elif $e.type == "assistant" then
          .n as $n | ($e.message.content // []) as $c
          | if ($c | type) == "string" then .b += [{n: $n, t: $c}]
            elif ($c | type) == "array" then
              .b += [$c[] | select(.type? == "text") | .text | strings | {n: $n, t: .}]
              | (if any($c[]; .type? == "tool_use") then .lt = $n else . end)
              | (if $tid != "" and any($c[]; .type? == "tool_use" and .id? == $tid) then .tl = $n else . end)
            else . end
        else . end)
    | .b |= map(.t |= (sub("^\\s+"; "") | sub("\\s+$"; "")) | select(.t | length > 0))' "$1" 2>/dev/null
}

# Speak TEXT (already cleaned) if sequence number SEQ is still current and speech is on.
# Stops our previous speech first, even when TEXT is empty. Never blocks, never prints.
speak_spawn() { # $1 = text, $2 = seq
  local text="$1" seq="$2" tmp pid start cmd
  read -r -a cmd <<<"${SPEAK_CMD:-say}"
  [ "${#cmd[@]}" -gt 0 ] || cmd=(say)
  speak_lock || return 0
  if [ "$(speak_seq)" != "$seq" ] || ! speak_enabled; then speak_unlock; return 0; fi
  speak_stop
  if [[ $text =~ [^[:space:]] ]] && tmp=$(mktemp "$SPEAK_STATE_DIR/speak.XXXXXX"); then
    printf '%s\n' "$text" > "$tmp"
    exec 3<"$tmp"
    rm -f "$tmp"
    nohup "${cmd[@]}" <&3 >/dev/null 2>&1 &
    pid=$!
    exec 3<&-
    start=$(speak_pid_start "$pid")
    [ -n "$start" ] && printf '%s %s\n' "$pid" "$start" > "$SPEAK_PID_FILE"
  fi
  speak_unlock
}

# Take a new sequence number (call on hook entry, before any slow work).
speak_enter() { speak_lock || return 1; MY_SEQ=$(speak_bump_seq); speak_unlock; }
