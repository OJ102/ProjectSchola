#!/bin/bash
# Project Schola test suite. Plain bash 3.2 + jq. No audio: SPEAK_CMD is a recording stub.
# Usage: bash tests/run.sh [filter-substring]
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$ROOT/hooks"
FILTER="${1:-}"

# Hermetic env: drop anything from the caller's shell the hooks read, so results don't depend on who runs us.
for v in $(env | grep -oE '^(SPEAK_[A-Za-z0-9_]*|STUB_[A-Za-z0-9_]*|OBSIDIAN_[A-Za-z0-9_]*|CLAUDE_PLUGIN_[A-Za-z0-9_]*|VOICE_FLAG)='); do unset "${v%=}"; done
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
echo "suite: $(basename "$0") root=$ROOT bash=$BASH_VERSION hooks=$(ls "$ROOT"/hooks/*.sh | wc -l | tr -d ' ')"
PASS=0; FAIL=0; SKIP=0; FAILED=()

WORK=$(mktemp -d "${TMPDIR:-/tmp}/schola-tests.XXXXXX")
trap 'pkill -f "$WORK/" 2>/dev/null; rm -rf "$WORK"' EXIT

# --- stubs -------------------------------------------------------------------
STUB="$WORK/bin/speak-stub"; SLOW="$WORK/bin/slow-stub"
mkdir -p "$WORK/bin"
cat > "$STUB" <<'S'
#!/bin/bash
# Records whatever it is asked to speak: args, then stdin (if not a tty).
out="${SPEAK_OUT:?}"
{ [ $# -gt 0 ] && printf '%s' "$*"; [ -t 0 ] || cat; } >> "$out"
S
cat > "$SLOW" <<'S'
#!/bin/bash
echo $$ >> "${SPEAK_OUT:?}.pids"
{ [ $# -gt 0 ] && printf '%s' "$*"; [ -t 0 ] || cat; } >> "$SPEAK_OUT"
sleep 30
S
chmod +x "$STUB" "$SLOW"

# --- helpers -----------------------------------------------------------------
ok()   { PASS=$((PASS+1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  FAIL $1"; [ -n "$2" ] && printf '%s\n' "$2" | sed 's/^/         /'; }
skip() { SKIP=$((SKIP+1)); echo "  skip $1 ($2)"; }
want() { [ -z "$FILTER" ] || [[ "$1" == *"$FILTER"* ]]; }
need() { [ -f "$HOOKS/$1" ]; }

# Fresh per-test env: isolated HOME/TMPDIR so flag/pid files can't leak.
fresh() {
  T="$WORK/t$RANDOM$RANDOM"; mkdir -p "$T/home" "$T/tmp"
  export HOME="$T/home" TMPDIR="$T/tmp" SPEAK_OUT="$T/spoken" SPEAK_CMD="$STUB"
  export CLAUDE_PLUGIN_ROOT="$ROOT" CLAUDE_PLUGIN_DATA="$T/data" SPEAK_STATE_DIR="$T/state"
  mkdir -p "$CLAUDE_PLUGIN_DATA"; : > "$SPEAK_OUT"
}
# Wait (up to ~4s) for the async speaker to write something.
wait_spoken() { local i; for i in $(seq 1 40); do [ -s "$SPEAK_OUT" ] && { sleep 0.2; return 0; }; sleep 0.1; done; return 1; }
spoken() { tr -s ' \n\t' '   ' < "$SPEAK_OUT" | sed 's/^ //; s/ $//'; }

clean() { # run the cleaner on stdin text
  if need speak-clean.sh; then bash "$HOOKS/speak-clean.sh"; else return 99; fi
}

# Assertions on cleaner output
c_has()  { local n="$1" in="$2" s="$3" o; o=$(printf '%s' "$in" | clean); [ $? = 99 ] && { skip "$n" "no speak-clean.sh"; return; }
           if [[ "$o" == *"$s"* ]]; then ok "$n"; else bad "$n" "expected to contain: $s"$'\n'"got: $o"; fi; }
c_lacks(){ local n="$1" in="$2" s="$3" o; o=$(printf '%s' "$in" | clean); [ $? = 99 ] && { skip "$n" "no speak-clean.sh"; return; }
           if [[ "$o" != *"$s"* ]]; then ok "$n"; else bad "$n" "expected NOT to contain: $s"$'\n'"got: $o"; fi; }

# ============================================================================
echo "== static =="
if want static; then
  jq -e . "$HOOKS/hooks.json" >/dev/null 2>&1 && ok "hooks.json is valid JSON" || bad "hooks.json is valid JSON" "$(jq . "$HOOKS/hooks.json" 2>&1 | tail -2)"
  for f in "$HOOKS"/*.sh; do
    bash -n "$f" 2>"$WORK/syn" && ok "bash -n $(basename "$f")" || bad "bash -n $(basename "$f")" "$(cat "$WORK/syn")"
  done
  # every script referenced in hooks.json exists
  missing=$(jq -r '.. | strings | select(test("CLAUDE_PLUGIN_ROOT"))' "$HOOKS/hooks.json" | grep -o 'hooks/[A-Za-z0-9_.-]*' | sort -u | while read -r p; do [ -f "$ROOT/$p" ] || echo "$p"; done)
  [ -z "$missing" ] && ok "hooks.json references only existing scripts" || bad "hooks.json references only existing scripts" "missing: $missing"
  if command -v claude >/dev/null; then
    out=$(cd "$ROOT" && claude plugin validate . 2>&1); rc=$?
    { [ $rc = 0 ] && grep -q "Validation passed" <<<"$out"; } && ok "claude plugin validate ." || bad "claude plugin validate ." "$(tail -5 <<<"$out")"
  else skip "claude plugin validate ." "claude not on PATH"; fi
fi

# ============================================================================
echo "== md-log hook (regression) =="
if want md-log; then
  fresh; export OBSIDIAN_VAULT_PATH="$T/vault"
  TR="$T/tr.jsonl"
  cat > "$TR" <<'J'
{"type":"user","timestamp":"2026-10-11T10:00:00Z","message":{"role":"user","content":"<command-name>/teach</command-name><command-args>Fourier transforms</command-args>"}}
{"type":"assistant","timestamp":"2026-10-11T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"A Fourier transform splits a signal into frequencies."}]}}
{"type":"user","timestamp":"2026-10-11T10:01:00Z","message":{"role":"user","content":"why sines?"}}
J
  echo "{\"session_id\":\"abcdef1234\",\"transcript_path\":\"$TR\"}" | bash "$HOOKS/md-log-stop-hook.sh"; rc=$?
  LOG="$OBSIDIAN_VAULT_PATH/sessions/2026-10-11 Fourier transforms.md"
  [ $rc = 0 ] && ok "md-log exits 0" || bad "md-log exits 0" "rc=$rc"
  if [ -f "$LOG" ]; then ok "md-log writes named log"; else bad "md-log writes named log" "$(ls -R "$OBSIDIAN_VAULT_PATH" 2>&1)"; fi
  grep -q "splits a signal" "$LOG" 2>/dev/null && grep -q "why sines?" "$LOG" && grep -qx "session: abcdef1234" "$LOG" \
    && ok "md-log content has assistant+user+frontmatter" || bad "md-log content has assistant+user+frontmatter" "$(cat "$LOG" 2>&1)"
  echo "{\"session_id\":\"abcdef1234\",\"transcript_path\":\"$TR\"}" | bash "$HOOKS/md-log-stop-hook.sh"
  n=$(ls "$OBSIDIAN_VAULT_PATH/sessions" | wc -l | tr -d ' ')
  [ "$n" = 1 ] && ok "md-log idempotent (1 file after 2 runs)" || bad "md-log idempotent (1 file after 2 runs)" "files=$n"
  unset OBSIDIAN_VAULT_PATH
fi

# ============================================================================
echo "== cleaner =="
if want clean; then
  c_has   "clean: plain text kept"          "The derivative measures change."          "The derivative measures change."
  c_lacks "clean: bold markers removed"     "This is **important** stuff."             "**"
  c_has   "clean: bold word kept"           "This is **important** stuff."             "important"
  c_lacks "clean: italics markers removed"  "This is *very* _neat_."                   "*"
  c_lacks "clean: heading hashes removed"   $'## Big idea\nText'                        "#"
  c_has   "clean: heading text kept"        $'## Big idea\nText'                        "Big idea"
  c_lacks "clean: fenced code removed"      $'Before\n```python\nprint(\"secret_code\")\n```\nAfter' "secret_code"
  c_has   "clean: text after fence kept"    $'Before\n```python\nx=1\n```\nAfter'       "After"
  c_lacks "clean: inline code backticks"    'Call `foo()` now.'                         '`'
  c_lacks "clean: mermaid block removed"    $'See:\n```mermaid\ngraph TD; A-->B\n```\nDone' "graph TD"
  c_lacks "clean: table removed"            $'Intro\n| a | b |\n|---|---|\n| 1 | 2 |\nOutro' "|"
  c_has   "clean: text around table kept"   $'Intro\n| a | b |\n|---|---|\n| 1 | 2 |\nOutro' "Outro"
  c_lacks "clean: bare URL removed"         'Read https://example.com/x?y=1 later.'    "https://"
  c_lacks "clean: link URL removed"         'Read [the docs](https://example.com) now.' "example.com"
  c_has   "clean: link text kept"           'Read [the docs](https://example.com) now.' "the docs"
  c_lacks "clean: display LaTeX removed"    $'Formula:\n$$\\int_0^1 x\\,dx$$\nok'        '\int'
  c_lacks "clean: inline LaTeX removed"     'We have $e^{i\pi}+1=0$ here.'              '\pi'
  c_lacks "clean: obsidian embed removed"   'Look ![[diagram.svg]] here.'               "diagram.svg"
  c_lacks "clean: image md removed"         'Pic ![alt](img.png) here.'                 "img.png"
  c_lacks "clean: html tags removed"        'A <br> B <details>x</details>'             "<"
  c_has   "clean: bullet text kept"         $'- first point\n- second point'           "second point"
  c_lacks "clean: bullet dashes removed"    $'- first point\n- second point'           "- "
  c_lacks "clean: blockquote/callout marker" $'> [!note] Remember\n> this'              "[!note]"
  c_lacks "clean: svg block removed"        $'Look <svg viewBox="0 0 1 1"><circle r="1"/></svg> ok' "circle"
  c_lacks "clean: \\[..\\] LaTeX removed"     'Then \[ a^2+b^2 \] holds.'              'a^2'
  c_lacks "clean: \\(..\\) LaTeX removed"     'Then \( x_1 \) holds.'                  'x_1'
  c_has   "clean: \$5 and \$10 kept"           'Costs $5 and $10.'                        '$5 and $10'
  c_lacks "clean: emoji removed"            'Great job 🎉 really'                       '🎉'
  c_has   "clean: numbered list text kept"  $'1. alpha\n2. beta'                       "beta"
  c_has   "clean: snake_case word kept"     'the my_var name'                           "my"
  c_lacks "clean: tilde-fence removed"      $'A\n~~~\nhidden_tilde\n~~~\nB'           "hidden_tilde"
  c_lacks "clean: unclosed fence hides rest" $'A\n```\nleak_me'                         "leak_me"
  o=$(printf '' | clean); rc=$?
  if [ $rc = 99 ]; then skip "clean: empty in → empty out" "no speak-clean.sh"
  elif [ -z "$(printf '%s' "$o" | tr -d '[:space:]')" ]; then ok "clean: empty in → empty out"; else bad "clean: empty in → empty out" "got: $o"; fi
  o=$(printf '```\nonly code\n```' | clean); rc=$?
  if [ $rc = 99 ]; then skip "clean: code-only → empty" "no speak-clean.sh"
  elif [ -z "$(printf '%s' "$o" | tr -d '[:space:]')" ]; then ok "clean: code-only → empty"; else bad "clean: code-only → empty" "got: $o"; fi
fi

# ============================================================================
# Toggle: how voice is turned on. Builder must tell us the flag; we try a helper
# first (hooks/speak-toggle.sh on|off), else VOICE_FLAG env var path.
# Contract (builder inc 1): speech is on iff $SPEAK_STATE_DIR/speak.on exists.
voice_on()  { if need speak-toggle.sh; then bash "$HOOKS/speak-toggle.sh" on >/dev/null 2>&1; else mkdir -p "$SPEAK_STATE_DIR"; touch "$SPEAK_STATE_DIR/speak.on"; fi; }
voice_off() { if need speak-toggle.sh; then bash "$HOOKS/speak-toggle.sh" off >/dev/null 2>&1; else rm -f "$SPEAK_STATE_DIR/speak.on"; fi; }

stop_hook() { bash "$HOOKS/speak.sh"; }

echo "== Stop hook (speak.sh) =="
if want stop; then
  if ! need speak.sh; then skip "speak.sh tests" "hooks/speak.sh not present"; else
    fresh; voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Gradients point **uphill**. See `grad f`."}' | stop_hook; rc=$?
    [ $rc = 0 ] && ok "stop: exits 0" || bad "stop: exits 0" "rc=$rc"
    if wait_spoken; then
      s=$(spoken)
      [[ "$s" == *"Gradients point uphill"* && "$s" != *"**"* ]] && ok "stop: speaks cleaned last_assistant_message" || bad "stop: speaks cleaned last_assistant_message" "spoken: $s"
    else bad "stop: speaks cleaned last_assistant_message" "stub never called (SPEAK_OUT empty)"; fi

    fresh; voice_on
    # Contract: hook never returns a block decision, so stop_hook_active can't loop; the continued reply is still spoken once.
    out=$(jq -nc '{hook_event_name:"Stop",stop_hook_active:true,last_assistant_message:"Continued reply."}' | stop_hook); rc=$?
    { [ $rc = 0 ] && [ -z "$out" ]; } && ok "stop: stop_hook_active=true → exit 0, no stdout (no block decision)" || bad "stop: stop_hook_active=true → exit 0, no stdout (no block decision)" "rc=$rc stdout=$out"
    wait_spoken; [ "$(spoken)" = "Continued reply." ] && ok "stop: stop_hook_active=true → spoken exactly once" || bad "stop: stop_hook_active=true → spoken exactly once" "spoken: $(spoken)"

    fresh; voice_on
    TR="$T/tr.jsonl"
    cat > "$TR" <<'J'
{"type":"user","message":{"role":"user","content":"hi"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Older reply."}]}}
{"type":"user","message":{"role":"user","content":"more"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"x","name":"Read","input":{}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Fallback reply from transcript."}]}}
J
    jq -nc --arg p "$TR" '{hook_event_name:"Stop",stop_hook_active:false,transcript_path:$p}' | stop_hook
    if wait_spoken; then s=$(spoken)
      [[ "$s" == *"Fallback reply from transcript"* && "$s" != *"Older reply"* ]] && ok "stop: transcript fallback speaks latest text" || bad "stop: transcript fallback speaks latest text" "spoken: $s"
    else bad "stop: transcript fallback speaks latest text" "stub never called"; fi

    fresh; voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:""}' | stop_hook; rc=$?
    sleep 1; { [ $rc = 0 ] && [ -z "$(spoken)" ]; } && ok "stop: empty message → silent, exit 0" || bad "stop: empty message → silent, exit 0" "rc=$rc spoken: $(spoken)"

    fresh; voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"```\nx=1\n```"}' | stop_hook
    sleep 1; [ -z "$(spoken)" ] && ok "stop: code-only message → silent" || bad "stop: code-only message → silent" "spoken: $(spoken)"

    fresh; voice_on
    printf 'not json' | stop_hook; rc=$?
    [ $rc = 0 ] && ok "stop: garbage stdin → exit 0" || bad "stop: garbage stdin → exit 0" "rc=$rc"

    fresh; voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,transcript_path:"/nonexistent/x.jsonl"}' | stop_hook; rc=$?
    sleep 0.5; { [ $rc = 0 ] && [ -z "$(spoken)" ]; } && ok "stop: missing transcript → silent, exit 0" || bad "stop: missing transcript → silent, exit 0" "rc=$rc"

    fresh; voice_on
    out=$(jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Quiet stdout."}' | stop_hook)
    [ -z "$out" ] && ok "stop: no stdout" || bad "stop: no stdout" "stdout: $out"

    fresh; voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Costs $5 and $10 today."}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *'$5 and $10'* ]] && ok "stop: dollar amounts kept" || bad "stop: dollar amounts kept" "spoken: $(spoken)"

    fresh; voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Has a [[Wiki Page|nice label]] link."}' | stop_hook
    wait_spoken; s=$(spoken); [[ "$s" == *"nice label"* && "$s" != *"Wiki Page"* && "$s" != *"[["* ]] && ok "stop: wikilink → label" || bad "stop: wikilink → label" "spoken: $s"

    # transcript fallback where last assistant entry is a plain string content
    fresh; voice_on; TR="$T/tr2.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"q"}}' '{"type":"assistant","message":{"content":"String content reply."}}' > "$TR"
    jq -nc --arg p "$TR" '{stop_hook_active:false,transcript_path:$p}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *"String content reply"* ]] && ok "stop: transcript fallback (string content)" || bad "stop: transcript fallback (string content)" "spoken: $(spoken)"

    # transcript fallback: text split across two assistant entries after last tool call → both spoken
    fresh; voice_on; TR="$T/tr3.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"q"}}' \
      '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"a","name":"Bash","input":{}}]}}' \
      '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"a","content":"ok"}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Part one."}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Part two."}]}}' > "$TR"
    jq -nc --arg p "$TR" '{stop_hook_active:false,transcript_path:$p}' | stop_hook
    wait_spoken; s=$(spoken); [[ "$s" == *"Part one."*"Part two."* ]] && ok "stop: transcript fallback joins final turn" || bad "stop: transcript fallback joins final turn" "spoken: $s"

    # flag off by default → silent
    fresh
    jq -nc '{stop_hook_active:false,last_assistant_message:"Default off."}' | stop_hook
    sleep 1; [ -z "$(spoken)" ] && ok "stop: no flag → silent (default off)" || bad "stop: no flag → silent (default off)" "spoken: $(spoken)"

    # SPEAK_MAX_CHARS truncation
    fresh; voice_on; long=$(printf 'word %.0s' $(seq 1 3000))
    jq -nc --arg m "$long" '{stop_hook_active:false,last_assistant_message:$m}' | stop_hook
    wait_spoken; n=$(wc -c < "$SPEAK_OUT" | tr -d ' '); [ "$n" -le 6002 ] && [ "$n" -gt 100 ] && ok "stop: long text capped at SPEAK_MAX_CHARS ($n bytes)" || bad "stop: long text capped at SPEAK_MAX_CHARS" "bytes=$n"

    # a newer reply replaces (kills) the previous speech
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    jq -nc '{stop_hook_active:false,last_assistant_message:"First."}' | stop_hook; wait_spoken
    P1=$(head -1 "$SPEAK_OUT.pids" 2>/dev/null)
    jq -nc '{stop_hook_active:false,last_assistant_message:"Second."}' | stop_hook; sleep 0.8
    P2=$(sed -n 2p "$SPEAK_OUT.pids" 2>/dev/null)
    { [ -n "$P1" ] && ! kill -0 "$P1" 2>/dev/null && [ -n "$P2" ] && kill -0 "$P2" 2>/dev/null; } && ok "stop: new reply kills previous speech only" || bad "stop: new reply kills previous speech only" "P1=$P1 alive=$(kill -0 "$P1" 2>/dev/null && echo y || echo n) P2=$P2 alive=$(kill -0 "$P2" 2>/dev/null && echo y || echo n)"
    grep -q "^$P2 " "$SPEAK_STATE_DIR/speak.pid" 2>/dev/null && ok "stop: speak.pid records live speaker" || bad "stop: speak.pid records live speaker" "pidfile: $(cat "$SPEAK_STATE_DIR/speak.pid" 2>&1)"
    pkill -f "$SLOW" 2>/dev/null

    # hook must return promptly even if the speaker is slow (should not block Claude)
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    t0=$(date +%s)
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Slow speech."}' | stop_hook
    dt=$(( $(date +%s) - t0 ))
    [ $dt -le 5 ] && ok "stop: returns within 5s with slow speaker (${dt}s)" || bad "stop: returns within 5s with slow speaker" "took ${dt}s"
    pkill -f "$SLOW" 2>/dev/null
  fi
fi

# ============================================================================
echo "== interrupt (UserPromptSubmit) =="
if want interrupt; then
  INT=$(jq -r '.hooks.UserPromptSubmit[]?.hooks[]? | (.args // [] | join(" ")) + " " + .command' "$HOOKS/hooks.json" 2>/dev/null | grep -o 'hooks/[A-Za-z0-9_.-]*\.sh' | head -1)
  if [ -z "$INT" ] || ! need speak.sh; then skip "interrupt tests" "no UserPromptSubmit hook in hooks.json yet"; else
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    # unrelated long-running processes that must survive: a bystander slow-stub copy and a sleep
    cp "$SLOW" "$WORK/bin/bystander-stub"; SPEAK_OUT="$T/by" "$WORK/bin/bystander-stub" "bystander" & BY=$!
    sleep 30 & SL=$!
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"A very long lecture about entropy."}' | stop_hook
    wait_spoken
    SP=$(head -1 "$SPEAK_OUT.pids" 2>/dev/null)
    if [ -z "$SP" ] || ! kill -0 "$SP" 2>/dev/null; then bad "interrupt: speech running before prompt" "no live speaker pid (pids: $(cat "$SPEAK_OUT.pids" 2>/dev/null))"; else
      ok "interrupt: speech running before prompt"
      jq -nc '{hook_event_name:"UserPromptSubmit",prompt:"wait, stop"}' | bash "$ROOT/$INT"; rc=$?
      [ $rc = 0 ] && ok "interrupt: hook exits 0" || bad "interrupt: hook exits 0" "rc=$rc"
      sleep 0.5
      kill -0 "$SP" 2>/dev/null && bad "interrupt: own speech killed" "pid $SP still alive" || ok "interrupt: own speech killed"
      kill -0 "$BY" 2>/dev/null && ok "interrupt: unrelated speaker-like process survives" || bad "interrupt: unrelated speaker-like process survives" "bystander $BY died"
      kill -0 "$SL" 2>/dev/null && ok "interrupt: unrelated sleep survives" || bad "interrupt: unrelated sleep survives" "sleep $SL died"
    fi
    kill "$BY" "$SL" 2>/dev/null; wait "$BY" "$SL" 2>/dev/null; pkill -f "$SLOW" 2>/dev/null

    # no speech running: still exits 0 and kills nothing
    fresh; voice_on; sleep 30 & SL=$!
    jq -nc '{hook_event_name:"UserPromptSubmit",prompt:"hi"}' | bash "$ROOT/$INT"; rc=$?
    { [ $rc = 0 ] && kill -0 "$SL" 2>/dev/null; } && ok "interrupt: no speech → exit 0, nothing killed" || bad "interrupt: no speech → exit 0, nothing killed" "rc=$rc"
    # stale pid file pointing at an unrelated live process (pid reused, start time differs) must not kill it
    mkdir -p "$SPEAK_STATE_DIR"
    echo "$SL Mon Jan  1 00:00:00 2001" > "$SPEAK_STATE_DIR/speak.pid"
    jq -nc '{hook_event_name:"UserPromptSubmit",prompt:"hi"}' | bash "$ROOT/$INT"
    kill -0 "$SL" 2>/dev/null && ok "interrupt: stale pid (start-time mismatch) not killed" || bad "interrupt: stale pid (start-time mismatch) not killed" "sleep $SL was killed via stale pid file"
    echo "$SL" > "$SPEAK_STATE_DIR/speak.pid"   # pid with no start time
    jq -nc '{prompt:"hi"}' | bash "$ROOT/$INT"
    kill -0 "$SL" 2>/dev/null && ok "interrupt: pid file without start time not killed" || bad "interrupt: pid file without start time not killed" "sleep $SL killed"
    for junk in "garbage" "-1" "1 x" "0"; do echo "$junk" > "$SPEAK_STATE_DIR/speak.pid"; jq -nc '{prompt:"hi"}' | bash "$ROOT/$INT"; done
    kill -0 "$SL" 2>/dev/null && kill -0 $$ 2>/dev/null && ok "interrupt: junk pid files harmless" || bad "interrupt: junk pid files harmless" "something died"
    kill "$SL" 2>/dev/null; wait "$SL" 2>/dev/null

    # interrupt prints nothing (stdout would be injected into the prompt)
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    jq -nc '{stop_hook_active:false,last_assistant_message:"Talking."}' | stop_hook; wait_spoken
    out=$(jq -nc '{prompt:"x"}' | bash "$ROOT/$INT"); [ -z "$out" ] && ok "interrupt: no stdout" || bad "interrupt: no stdout" "stdout: $out"
    # speech still killed even if flag was turned off by removing it directly
    jq -nc '{stop_hook_active:false,last_assistant_message:"Talking again."}' | stop_hook; sleep 0.5
    P=$(tail -1 "$SPEAK_OUT.pids"); rm -f "$SPEAK_STATE_DIR/speak.on"
    jq -nc '{prompt:"x"}' | bash "$ROOT/$INT"; sleep 0.3
    kill -0 "$P" 2>/dev/null && bad "interrupt: kills own speech even with flag off" "pid $P alive" || ok "interrupt: kills own speech even with flag off"
    pkill -f "$SLOW" 2>/dev/null
  fi
fi

# ============================================================================
echo "== toggle =="
if want toggle; then
  if ! need speak-toggle.sh && [ -z "$VOICE_FLAG" ]; then skip "toggle tests" "no hooks/speak-toggle.sh and VOICE_FLAG unset"; elif ! need speak.sh; then skip "toggle tests" "no speak.sh"; else
    fresh; voice_off
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Off means quiet."}' | stop_hook
    sleep 1; [ -z "$(spoken)" ] && ok "toggle: off → Stop hook silent" || bad "toggle: off → Stop hook silent" "spoken: $(spoken)"
    voice_on
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"On means speak."}' | stop_hook
    wait_spoken && [[ "$(spoken)" == *"On means speak"* ]] && ok "toggle: on → Stop hook speaks" || bad "toggle: on → Stop hook speaks" "spoken: $(spoken)"
    voice_off
    : > "$SPEAK_OUT"
    jq -nc '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:"Off again."}' | stop_hook
    sleep 1; [ -z "$(spoken)" ] && ok "toggle: off again → silent" || bad "toggle: off again → silent" "spoken: $(spoken)"
    # status line badge
    voice_on; on=$(echo '{}' | bash "$HOOKS/status-line.sh" 2>&1)
    voice_off; off=$(echo '{}' | bash "$HOOKS/status-line.sh" 2>&1)
    [ "$on" != "$off" ] && ok "toggle: status-line badge differs on/off" || bad "toggle: status-line badge differs on/off" "on='$on' off='$off'"
    # toggle script itself
    fresh
    o=$(bash "$HOOKS/speak-toggle.sh" status); [ "$o" = "speech off" ] && ok "toggle: status default off" || bad "toggle: status default off" "got: $o"
    o=$(bash "$HOOKS/speak-toggle.sh" on 2>/dev/null); [ "$o" = "speech on" ] && [ -f "$SPEAK_STATE_DIR/speak.on" ] && ok "toggle: on creates flag" || bad "toggle: on creates flag" "got: $o"
    o=$(bash "$HOOKS/speak-toggle.sh" status); [ "$o" = "speech on" ] && ok "toggle: status on" || bad "toggle: status on" "got: $o"
    bash "$HOOKS/speak-toggle.sh" bogus >/dev/null 2>&1; rc=$?; [ $rc = 2 ] && ok "toggle: bad arg → exit 2" || bad "toggle: bad arg → exit 2" "rc=$rc"
    o=$(SPEAK_CMD="definitely-not-a-cmd-xyz" bash "$HOOKS/speak-toggle.sh" on 2>&1 >/dev/null); [[ "$o" == *warning* ]] && ok "toggle: warns when speak cmd missing" || bad "toggle: warns when speak cmd missing" "stderr: $o"
    # off stops in-progress speech
    export SPEAK_CMD="$SLOW"
    jq -nc '{stop_hook_active:false,last_assistant_message:"Long talk."}' | stop_hook; wait_spoken
    P=$(head -1 "$SPEAK_OUT.pids")
    # status line while speaking
    o=$(echo '{}' | bash "$HOOKS/status-line.sh"); [[ "$o" == *"🔊 speaking"* ]] && ok "status-line: 🔊 while speaking" || bad "status-line: 🔊 while speaking" "got: $o"
    bash "$HOOKS/speak-toggle.sh" off >/dev/null; sleep 0.3
    kill -0 "$P" 2>/dev/null && bad "toggle: off kills in-progress speech" "pid $P alive" || ok "toggle: off kills in-progress speech"
    o=$(echo '{}' | bash "$HOOKS/status-line.sh"); [[ "$o" != *speech* && "$o" != *speaking* ]] && ok "status-line: no badge when off" || bad "status-line: no badge when off" "got: $o"
    bash "$HOOKS/speak-toggle.sh" on >/dev/null 2>&1
    o=$(echo '{}' | bash "$HOOKS/status-line.sh"); [[ "$o" == *"🔈 speech on"* ]] && ok "status-line: 🔈 when on and idle" || bad "status-line: 🔈 when on and idle" "got: $o"
    pkill -f "$SLOW" 2>/dev/null
    for s in speak-on speak-off; do
      grep -q 'CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh' "$ROOT/skills/$s/SKILL.md" 2>/dev/null && ok "toggle: skills/$s calls speak-toggle.sh" || bad "toggle: skills/$s calls speak-toggle.sh" "no toggle call in SKILL.md"
    done
    for s in speak-on speak-off; do [ -f "$ROOT/skills/$s/SKILL.md" ] && ok "toggle: skills/$s exists" || bad "toggle: skills/$s exists" "missing skills/$s/SKILL.md"; done
  fi
fi

# ============================================================================
echo "== permissions (allowed-tools vs commands run) =="
# Literal glob match of each Bash(...) pattern against each command exactly as the skill writes it,
# and with ${CLAUDE_PLUGIN_ROOT} expanded. Claude Code's `*` is a wildcard like a shell glob.
perm_patterns() { awk 'NR==1&&/^---/{f=1;next} f&&/^---/{exit} f' "$1" | grep -o 'Bash([^)]*)' | sed 's/^Bash(//; s/)$//'; }
perm_commands() { # every line/inline span that invokes a hooks/*.sh script
  grep -o 'bash [^`]*hooks/[A-Za-z0-9_.-]*\.sh"\{0,1\}[^`]*' "$1" | sed 's/[[:space:]]*$//' | sort -u; }
perm_match() { local cmd="$1" pat; while IFS= read -r pat; do [ -n "$pat" ] || continue
    pat="${pat//\?/\\?}"; pat="${pat//\[/\\[}"
    [[ "$cmd" == $pat ]] && return 0; done <<<"$2"; return 1; }
if want perm; then
  for sk in speak-on speak-off teach; do
    f="$ROOT/skills/$sk/SKILL.md"; [ -f "$f" ] || { bad "perm: skills/$sk exists" "missing"; continue; }
    pats=$(perm_patterns "$f"); cmds=$(perm_commands "$f")
    [ -n "$cmds" ] || { bad "perm: $sk runs a hooks script" "no 'bash …hooks/*.sh' command found"; continue; }
    while IFS= read -r c; do
      e="${c//\$\{CLAUDE_PLUGIN_ROOT\}/$ROOT}"
      if perm_match "$c" "$pats" && perm_match "$e" "$pats"; then ok "perm: $sk allows: $c"
      else bad "perm: $sk allows: $c" "allowed-tools patterns: ${pats:-<none>}"$'\n'"command as written: $c"$'\n'"expanded: $e"; fi
    done <<<"$cmds"
  done
  # patterns must not be broader than the toggle (no bare Bash, no Bash(*), no Bash(bash *))
  for sk in speak-on speak-off teach; do
    f="$ROOT/skills/$sk/SKILL.md"; [ -f "$f" ] || continue
    broad=$(awk 'NR==1&&/^---/{f=1;next} f&&/^---/{exit} f' "$f" | grep -E 'allowed-tools:.*(Bash([,[:space:]]|$)|Bash\(\*\)|Bash\(bash \*\))')
    [ -z "$broad" ] && ok "perm: $sk allowed-tools not over-broad" || bad "perm: $sk allowed-tools not over-broad" "$broad"
  done
fi

echo "== docs =="
if want docs; then
  grep -q '^## Voice mode' "$ROOT/skills/teach/SKILL.md" && ok "docs: teach SKILL has Voice mode section" || bad "docs: teach SKILL has Voice mode section" "missing heading"
  for w in "Check question last" "Formulas in words" "Diagrams on screen only"; do
    grep -q "$w" "$ROOT/skills/teach/SKILL.md" && ok "docs: teach voice rule '$w'" || bad "docs: teach voice rule '$w'" "missing"
  done
  for w in "/speak-on" "/speak-off" "/voice" "SPEAK_CMD" "SPEAK_STATE_DIR" "headphones"; do
    grep -qi -- "$w" "$ROOT/README.md" && ok "docs: README mentions $w" || bad "docs: README mentions $w" "missing"
  done
  o=$(printf '## Step 2\nThe slope is **rise over run**.\n\n$$f(x)=2x$$\n\n```mermaid\ngraph TD; A-->B\n```\n\nWhat is the slope at x equals three?' | bash "$HOOKS/speak-clean.sh" | grep -v '^[[:space:]]*$' | tail -1)
  [ "$o" = "What is the slope at x equals three?" ] && ok "docs: sample voice reply ends on check question after cleaning" || bad "docs: sample voice reply ends on check question after cleaning" "last line: $o"
  refs=$(grep -rln "spike-test" "$ROOT" --exclude-dir=.git --exclude-dir=tests --exclude-dir=adversarial --exclude-dir=.obsidian 2>/dev/null)
  [ -z "$refs" ] && ok "docs: no references to removed skills/spike-test" || bad "docs: no references to removed skills/spike-test" "$refs"
fi

echo
echo "passed=$PASS failed=$FAIL skipped=$SKIP total=$((PASS+FAIL))"
[ $FAIL = 0 ] || { printf '  - %s\n' "${FAILED[@]}"; exit 1; }
