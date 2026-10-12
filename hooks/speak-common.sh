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

# Mutual exclusion between overlapping hooks: mkdir is atomic. A lock older than 10s is stale.
speak_lock() {
  mkdir -p -- "$SPEAK_STATE_DIR" 2>/dev/null || return 1
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

# ---- shared by the Stop and PreToolUse(AskUserQuestion) hooks -------------------------------------

# Hook JSON accessor. jq rejects lone UTF-16 surrogates (\ud800); retry with them replaced by U+FFFD.
speak_unsurrogate() { sed -E 's/\\u[dD][89a-fA-F][0-9a-fA-F]{2}/\\ufffd/g'; }
speak_field() { jq -r "$1" <<<"$INPUT" 2>/dev/null || speak_unsurrogate <<<"$INPUT" | jq -r "$1" 2>/dev/null; }

# Per-session record of what has already been spoken this turn (cksum of each trimmed text block),
# so Stop does not repeat text the question hook already voiced. Cleared on every user prompt.
speak_spoken_file() {
  local sid; sid=$(speak_field '.session_id // empty | strings' | tr -cd 'A-Za-z0-9_-' | cut -c1-64)
  echo "$SPEAK_STATE_DIR/spoken.${sid:-default}"
}
speak_hash() { printf '%s' "$1" | cksum | tr ' ' '-'; }
speak_was_spoken() { [ -f "$SPOKEN_FILE" ] && grep -qxF -- "$(speak_hash "$1")" "$SPOKEN_FILE" 2>/dev/null; }
speak_mark_spoken() { mkdir -p -- "$SPEAK_STATE_DIR" 2>/dev/null; speak_hash "$1" >> "$SPOKEN_FILE" 2>/dev/null; }

# Text blocks of the current turn (after the last real user prompt) as NUL-separated, trimmed strings.
# With "final", only the blocks after the last tool call (what the user sees as the closing reply).
speak_turn_texts() { # $1 = transcript path, $2 = all|final
  jq -Rjn --arg mode "$2" '
    reduce (inputs
            | (fromjson? // (gsub("\\\\u[dD][89a-fA-F][0-9a-fA-F]{2}"; "\\ufffd") | fromjson?))
            | objects | select((.isSidechain // false) | not)) as $e ([];
      if $e.type == "user" then
        (if ($e.message.content | type) == "string" then [] else . end)
      elif $e.type == "assistant" then
        ($e.message.content // []) as $c
        | if ($c | type) == "string" then . + [$c]
          elif ($c | type) == "array" then
            (if $mode == "final" and any($c[]; .type? == "tool_use") then [] else . end)
            + [$c[] | select(.type? == "text") | .text | strings]
          else . end
      else . end)
    | .[] | sub("^\\s+"; "") | sub("\\s+$"; "") | select(length > 0) | ., "\u0000"' "$1" 2>/dev/null
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
