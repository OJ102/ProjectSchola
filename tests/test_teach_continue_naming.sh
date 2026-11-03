#!/bin/bash
# tests/test_teach_continue_naming.sh
# `/teach --continue <slug>` must name the session after the slug, not after the
# flag: the note used to be `<date> --continue <slug>.md` (#2).

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/hooks/md-log-stop-hook.sh"

PASS=0
FAIL=0
FAILED=()

ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  FAIL $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/         /'; }

# Git Bash without the symlink privilege needs .lnk symlinks for the hook's
# lock; on POSIX this branch does nothing.
case "$(uname -s)" in CYGWIN* | MINGW* | MSYS*) export MSYS=winsymlinks:lnk ;; esac

WORK=$(mktemp -d "${TMPDIR:-/tmp}/schola-continue-test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

run_hook() {
  # $1: command args written inside <command-args>, $2: session id
  local dir="$WORK/t$RANDOM$RANDOM"
  mkdir -p "$dir/vault/sessions"
  printf '%s\n' \
    "{\"type\":\"user\",\"timestamp\":\"2026-10-22T10:00:00Z\",\"message\":{\"role\":\"user\",\"content\":\"<command-name>/teach</command-name><command-args>$1</command-args>\"}}" \
    > "$dir/transcript.jsonl"
  # The env belongs to the hook, not to jq on the other side of the pipe.
  export OBSIDIAN_VAULT_PATH="$dir/vault" MD_LOG_STATE_DIR="$dir/state"
  jq -nc --arg tr "$dir/transcript.jsonl" --arg sid "$2" '{transcript_path:$tr, session_id:$sid}' \
    | bash "$HOOK" >/dev/null 2>&1
  echo "$dir/vault/sessions"
}

echo "== teach --continue session naming =="

# The reported bug: the flag used to leak into the file name.
SESSIONS=$(run_hook "--continue tides" "sess-continue-1")
if [ -f "$SESSIONS/2026-10-22 tides.md" ]; then
  ok "note is named after the slug, not the flag"
else
  bad "note is named after the slug, not the flag" "$(ls "$SESSIONS")"
fi
if grep -q '^topic: "tides"$' "$SESSIONS/2026-10-22 tides.md" 2>/dev/null; then
  ok "frontmatter topic carries the slug"
else
  bad "frontmatter topic carries the slug" "$(grep '^topic:' "$SESSIONS"/*.md 2>/dev/null)"
fi
if grep -q '^# tides$' "$SESSIONS/2026-10-22 tides.md" 2>/dev/null; then
  ok "heading carries the slug"
else
  bad "heading carries the slug" "$(grep '^# ' "$SESSIONS"/*.md 2>/dev/null)"
fi

# A plain topic is untouched by the flag stripping.
SESSIONS=$(run_hook "Tides" "sess-plain-1")
if [ -f "$SESSIONS/2026-10-22 Tides.md" ]; then
  ok "a plain /teach topic keeps its naming"
else
  bad "a plain /teach topic keeps its naming" "$(ls "$SESSIONS")"
fi

# Several leading flags are stripped in one pass.
SESSIONS=$(run_hook "--continue --verbose tides" "sess-multi-1")
if [ -f "$SESSIONS/2026-10-22 tides.md" ]; then
  ok "multiple leading flags are stripped"
else
  bad "multiple leading flags are stripped" "$(ls "$SESSIONS")"
fi

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { printf 'failed: %s\n' "${FAILED[@]}"; exit 1; }
exit 0
