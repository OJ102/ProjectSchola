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
PASS=0; FAIL=0; SKIP=0; WARN=0; FAILED=(); WARNED=()

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
txt=$({ [ $# -gt 0 ] && printf '%s' "$*"; [ -t 0 ] || cat; })
printf '%s' "$txt" >> "$SPEAK_OUT"
printf '%s %s\n' "$$" "$(printf '%s' "$txt" | head -c 40 | tr '\n' ' ')" >> "$SPEAK_OUT.map"
sleep 30 & SL=$!
trap 'kill $SL 2>/dev/null; exit 0' TERM INT HUP
wait $SL
S
chmod +x "$STUB" "$SLOW"

# --- helpers -----------------------------------------------------------------
ok()   { PASS=$((PASS+1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  FAIL $1"; [ -n "$2" ] && printf '%s\n' "$2" | sed 's/^/         /'; }
skip() { SKIP=$((SKIP+1)); echo "  skip $1 ($2)"; }
warn() { WARN=$((WARN+1)); WARNED+=("$1"); echo "  WARN $1"; [ -n "$2" ] && printf '%s\n' "$2" | sed 's/^/         /'; }
load1() { sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}'; }
LOAD_WARN=8   # lead's rule: latency misses above this 1-min load average are warnings, not failures
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

now_ms() { perl -MTime::HiRes=time -e 'printf "%d\n", time*1000'; }
lstart_of() { LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//; s/ *$//'; }
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
  # math is read in words, not deleted (builder 02efcfa)
  c_has   "clean: \$x^2\$ → x squared"        'The derivative of $x^2$ is $2x$.'          "x squared"
  c_has   "clean: x<y → less than"          'if x<y then'                               "x less than y"
  c_has   "clean: a>b → greater than"       'when a>b we stop'                          "a greater than b"
  c_has   "clean: 3 * 4 → times"            '3 * 4 = 12'                                "3 times 4"
  c_has   "clean: = in LaTeX → equals"      'so $a=b$ holds'                            "a equals b"
  c_has   "clean: C# → C sharp"             'Use C# daily'                              "C sharp"
  c_lacks "clean: brackets removed"         'see [note] here'                           "["
  c_has   "clean: bracket text kept"        'see [note] here'                           "note"
  c_lacks "clean: known HTML tag stripped"  'a <b>bold</b> word'                        "<b>"
  c_has   "clean: x<y>z not eaten as tag"   'and x<y>z ok'                              "less than y greater than z"
  c_lacks "clean: say [[slnc]] command defused ([[)" '[[[[slnc 30000]]]] hello'          "[["
  c_lacks "clean: say [[slnc]] command defused (]])" '[[[[slnc 30000]]]] hello'          "]]"
  c_has   "clean: quadratic formula in words" '$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$' "square root of b squared minus 4ac, over 2a"
  c_has   "clean: nested frac"              '$\frac{\frac{1}{2}}{3}$'                  "1 over 2, over 3"
  c_has   "clean: f(x) = x^2 plain"         'f(x) = x^2'                                "x squared"
  c_has   "clean: x ** 2 → power"           'x ** 2'                                    "to the power of"
  c_has   "clean: 2**10 → power"            '2**10'                                     "to the power of"
  c_has   "clean: a*b*c → times"            'a*b*c'                                     "a times b times c"
  o=$(printf '**bold**' | clean); [ "$(printf '%s' "$o" | tr -d '[:space:]')" = bold ] && ok "clean: **bold** → bold (not power)" || bad "clean: **bold** → bold (not power)" "got: $o"
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

    # (4) newer reply that cleans to nothing still stops the old speech
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    jq -nc '{stop_hook_active:false,last_assistant_message:"Old talk."}' | stop_hook; wait_spoken
    P1=$(head -1 "$SPEAK_OUT.pids")
    jq -nc '{stop_hook_active:false,last_assistant_message:"```\ncode only\n```"}' | stop_hook; sleep 0.8
    kill -0 "$P1" 2>/dev/null && bad "stop: empty newer reply stops old speech" "pid $P1 alive" || ok "stop: empty newer reply stops old speech"
    pkill -f "$SLOW" 2>/dev/null

    # (3) speaker's children die with it (e.g. a wrapper that runs say as a child)
    fresh; voice_on; KID="$WORK/bin/kid-stub"
    printf '#!/bin/bash\ncat >> "$SPEAK_OUT"; sleep 31 & echo $! >> "$SPEAK_OUT.kids"; wait\n' > "$KID"; chmod +x "$KID"   # no TERM trap on purpose: this tests the hook's descendant kill
    export SPEAK_CMD="$KID"
    jq -nc '{stop_hook_active:false,last_assistant_message:"Wrapped talk."}' | stop_hook; wait_spoken; sleep 0.3
    C=$(head -1 "$SPEAK_OUT.kids" 2>/dev/null)
    jq -nc '{prompt:"x"}' | bash "$HOOKS/speak-interrupt.sh" >/dev/null; sleep 0.5
    { [ -n "$C" ] && ! kill -0 "$C" 2>/dev/null; } && ok "interrupt: speaker's child process killed too" || bad "interrupt: speaker's child process killed too" "child=$C alive=$(kill -0 "$C" 2>/dev/null && echo y || echo n)"
    pkill -f "$KID" 2>/dev/null; [ -n "$C" ] && kill "$C" 2>/dev/null

    # (2) slow-to-clean reply A, then quick reply B. Timing-independent end state: B is the last thing
    # spoken, and exactly one speaker (B's) is left alive. If A finished cleaning first, speaking A briefly is fine.
    alive_speakers() { local n=0 p; for p in $(cat "$SPEAK_OUT.pids" 2>/dev/null); do kill -0 "$p" 2>/dev/null && n=$((n+1)); done; echo $n; }
    big=$(printf '*a* %.0s' $(seq 1 30000))
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    jq -nc --arg m "OLD-REPLY-A $big" '{stop_hook_active:false,last_assistant_message:$m}' | stop_hook &
    APID=$!; sleep 0.2
    jq -nc '{stop_hook_active:false,last_assistant_message:"NEW-REPLY-B"}' | stop_hook
    wait "$APID"; sleep 0.8
    n=$(alive_speakers); read -r LP LS < "$SPEAK_STATE_DIR/speak.pid" 2>/dev/null
    LT=$(awk -v p="$LP" '$1==p{ $1=""; print substr($0,2) }' "$SPEAK_OUT.map")
    { [ "$n" = 1 ] && kill -0 "$LP" 2>/dev/null && [ "$(lstart_of "$LP")" = "$LS" ] && [[ "$LT" == NEW-REPLY-B* ]]; } \
      && ok "stop: stale slower reply never outlives newer one" || bad "stop: stale slower reply never outlives newer one" "live speakers: $n | pidfile: $LP '$LS' | that speaker's text: ${LT:0:40}"
    # interrupt / speak-off while A is still cleaning → no speech left running
    for how in interrupt off; do
      fresh; voice_on; export SPEAK_CMD="$SLOW"
      jq -nc --arg m "LONG-REPLY $big" '{stop_hook_active:false,last_assistant_message:$m}' | stop_hook &
      APID=$!; sleep 0.2
      if [ $how = off ]; then bash "$HOOKS/speak-toggle.sh" off >/dev/null; else jq -nc '{prompt:"x"}' | bash "$HOOKS/speak-interrupt.sh" >/dev/null; fi
      wait "$APID"; sleep 0.5; n=$(alive_speakers)
      [ "$n" = 0 ] && ok "stop: $how during cleaning → no speech left running" || bad "stop: $how during cleaning → no speech left running" "live speakers: $n (pids: $(tr '\n' ' ' < "$SPEAK_OUT.pids"))"
      pkill -f "$SLOW" 2>/dev/null
    done

    # (6) lone surrogate escape in hook JSON doesn't silence the reply
    fresh; voice_on
    printf '%s' '{"stop_hook_active":false,"last_assistant_message":"Surrogate \ud800 still spoken."}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *"still spoken"* ]] && ok "stop: lone \\ud800 in JSON still spoken" || bad "stop: lone \\ud800 in JSON still spoken" "spoken: $(spoken)"

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
      jq -nc '{hook_event_name:"UserPromptSubmit",prompt:"wait, stop"}' | bash "$ROOT/$INT" >/dev/null; rc=$?
      [ $rc = 0 ] && ok "interrupt: hook exits 0" || bad "interrupt: hook exits 0" "rc=$rc"
      sleep 0.5
      kill -0 "$SP" 2>/dev/null && bad "interrupt: own speech killed" "pid $SP still alive" || ok "interrupt: own speech killed"
      kill -0 "$BY" 2>/dev/null && ok "interrupt: unrelated speaker-like process survives" || bad "interrupt: unrelated speaker-like process survives" "bystander $BY died"
      kill -0 "$SL" 2>/dev/null && ok "interrupt: unrelated sleep survives" || bad "interrupt: unrelated sleep survives" "sleep $SL died"
    fi
    kill "$BY" "$SL" 2>/dev/null; wait "$BY" "$SL" 2>/dev/null; pkill -f "$SLOW" 2>/dev/null

    # no speech running: still exits 0 and kills nothing
    fresh; voice_on; sleep 30 & SL=$!
    jq -nc '{hook_event_name:"UserPromptSubmit",prompt:"hi"}' | bash "$ROOT/$INT" >/dev/null; rc=$?
    { [ $rc = 0 ] && kill -0 "$SL" 2>/dev/null; } && ok "interrupt: no speech → exit 0, nothing killed" || bad "interrupt: no speech → exit 0, nothing killed" "rc=$rc"
    # stale pid file pointing at an unrelated live process (pid reused, start time differs) must not kill it
    mkdir -p "$SPEAK_STATE_DIR"
    echo "$SL Mon Jan  1 00:00:00 2001" > "$SPEAK_STATE_DIR/speak.pid"
    jq -nc '{hook_event_name:"UserPromptSubmit",prompt:"hi"}' | bash "$ROOT/$INT" >/dev/null
    kill -0 "$SL" 2>/dev/null && ok "interrupt: stale pid (start-time mismatch) not killed" || bad "interrupt: stale pid (start-time mismatch) not killed" "sleep $SL was killed via stale pid file"
    echo "$SL" > "$SPEAK_STATE_DIR/speak.pid"   # pid with no start time
    jq -nc '{prompt:"hi"}' | bash "$ROOT/$INT" >/dev/null
    kill -0 "$SL" 2>/dev/null && ok "interrupt: pid file without start time not killed" || bad "interrupt: pid file without start time not killed" "sleep $SL killed"
    for junk in "garbage" "-1" "1 x" "0"; do echo "$junk" > "$SPEAK_STATE_DIR/speak.pid"; jq -nc '{prompt:"hi"}' | bash "$ROOT/$INT" >/dev/null; done
    kill -0 "$SL" 2>/dev/null && kill -0 $$ 2>/dev/null && ok "interrupt: junk pid files harmless" || bad "interrupt: junk pid files harmless" "something died"
    kill "$SL" 2>/dev/null; wait "$SL" 2>/dev/null

    # interrupt prints nothing (stdout would be injected into the prompt)
    fresh; voice_on; export SPEAK_CMD="$SLOW"
    jq -nc '{stop_hook_active:false,last_assistant_message:"Talking."}' | stop_hook; wait_spoken
    out=$(jq -nc '{prompt:"x"}' | bash "$ROOT/$INT")
    { [ "$(printf '%s\n' "$out" | grep -c .)" = 1 ] && printf '%s' "$out" | jq -e '.hookSpecificOutput.hookEventName == "UserPromptSubmit" and (.hookSpecificOutput.additionalContext | type == "string" and length > 0)' >/dev/null 2>&1; } \
      && ok "interrupt: speech on → exactly one JSON line with additionalContext" || bad "interrupt: speech on → exactly one JSON line with additionalContext" "stdout: $out"
    voice_off; out=$(jq -nc '{prompt:"x"}' | bash "$ROOT/$INT"); voice_on
    [ -z "$out" ] && ok "interrupt: speech off → no stdout" || bad "interrupt: speech off → no stdout" "stdout: $out"
    # speech still killed even if flag was turned off by removing it directly
    jq -nc '{stop_hook_active:false,last_assistant_message:"Talking again."}' | stop_hook; sleep 0.5
    P=$(tail -1 "$SPEAK_OUT.pids"); rm -f "$SPEAK_STATE_DIR/speak.on"
    jq -nc '{prompt:"x"}' | bash "$ROOT/$INT" >/dev/null; sleep 0.3
    kill -0 "$P" 2>/dev/null && bad "interrupt: kills own speech even with flag off" "pid $P alive" || ok "interrupt: kills own speech even with flag off"
    pkill -f "$SLOW" 2>/dev/null
  fi
fi

# ============================================================================
echo "== lock =="
if want lock; then
  if ! need speak-common.sh || ! grep -q 'speak.lock' "$HOOKS/speak-common.sh"; then skip "lock tests" "no speak.lock"; else
    lock_case() { # $1 = name, $2 = setup command run with the lock path
      fresh; voice_on; export SPEAK_CMD="$SLOW"
      jq -nc '{stop_hook_active:false,last_assistant_message:"Locked talk."}' | stop_hook; wait_spoken; sleep 0.2
      local P; P=$(tail -1 "$SPEAK_OUT.pids"); local L="$SPEAK_STATE_DIR/speak.lock"
      rm -rf "$L"; eval "$2"
      local t0 t1; t0=$(now_ms); jq -nc '{prompt:"x"}' | bash "$HOOKS/speak-interrupt.sh" >/dev/null; t1=$(now_ms); sleep 0.3
      local dt=$((t1-t0))
      local la; la=$(load1)
      if kill -0 "$P" 2>/dev/null; then bad "lock: $1 → speech stopped" "speaker $P still alive (took ${dt}ms, load $la)"
      else ok "lock: $1 → speech stopped"; fi
      if [ $dt -le 1000 ]; then ok "lock: $1 → interrupt ≤1s (${dt}ms, load $la)"
      elif awk -v l="$la" -v m="$LOAD_WARN" 'BEGIN{exit !(l+0 > m+0)}'; then warn "lock: $1 → interrupt ≤1s" "took ${dt}ms at load average $la (> $LOAD_WARN)"
      else bad "lock: $1 → interrupt ≤1s" "took ${dt}ms at load average $la (≤ $LOAD_WARN)"; fi
      pkill -f "$SLOW" 2>/dev/null
    }
    lock_case "stale symlink to dead pid" 'ln -s "99999 Mon Jan  1 00:00:00 2001" "$L"'
    lock_case "plain dir at lock path"   'mkdir "$L"'
    lock_case "dir with future mtime"    'mkdir "$L"; touch -t 203501010000 "$L"'
    sleep 30 & OWN=$!
    lock_case "live foreign owner"       'ln -s "$OWN $(lstart_of $OWN)" "$L"'
    kill -0 "$OWN" 2>/dev/null && ok "lock: live owner process not killed" || bad "lock: live owner process not killed" "owner $OWN died"
    # Stop (async hook) with a live foreign lock: design waits ≤10s per acquisition, then gives up silently.
    # Bound 25s = two acquisitions + slack; it must not hang forever or speak through the lock.
    fresh; voice_on; ln -s "$OWN $(lstart_of $OWN)" "$SPEAK_STATE_DIR/speak.lock"
    t0=$(now_ms); jq -nc '{stop_hook_active:false,last_assistant_message:"Blocked."}' | stop_hook; dt=$(( $(now_ms) - t0 ))
    { [ $dt -le 25000 ] && [ -z "$(spoken)" ]; } && ok "lock: Stop with live foreign lock gives up silently (${dt}ms ≤25s)" || bad "lock: Stop with live foreign lock gives up silently ≤25s" "took ${dt}ms spoken: $(spoken)"
    [ "$(readlink "$SPEAK_STATE_DIR/speak.lock")" = "$OWN $(lstart_of $OWN)" ] && ok "lock: live owner's lock left intact" || bad "lock: live owner's lock left intact" "lock now: $(readlink "$SPEAK_STATE_DIR/speak.lock" 2>&1)"
    # after a lock-holding hook finishes, the lock is released
    fresh; voice_on
    jq -nc '{stop_hook_active:false,last_assistant_message:"Release me."}' | stop_hook; wait_spoken
    [ ! -e "$SPEAK_STATE_DIR/speak.lock" ] && [ ! -L "$SPEAK_STATE_DIR/speak.lock" ] && ok "lock: released after Stop" || bad "lock: released after Stop" "$(ls -l "$SPEAK_STATE_DIR/speak.lock" 2>&1)"
    kill "$OWN" 2>/dev/null; wait "$OWN" 2>/dev/null
  fi
fi

echo "== quiz (AskUserQuestion) =="
if want quiz; then
  if ! need speak-question.sh || ! need speak-answered.sh; then skip "quiz tests" "no speak-question.sh / speak-answered.sh"; else
    QH() { bash "$HOOKS/speak-question.sh"; }; AH() { bash "$HOOKS/speak-answered.sh"; }
    wiring=$(jq -r '[.hooks.PreToolUse[]? | select(.matcher=="AskUserQuestion") | .hooks[] | select((.args|join(" "))|test("speak-question")) | .async] | tostring' "$HOOKS/hooks.json")
    [ "$wiring" = "[true]" ] && ok "quiz: speak-question wired PreToolUse(AskUserQuestion), async" || bad "quiz: speak-question wired PreToolUse(AskUserQuestion), async" "got: $wiring"
    wiring=$(jq -r '[.hooks.PostToolUse[]? | select(.matcher=="AskUserQuestion") | .hooks[] | select((.args|join(" "))|test("speak-answered"))] | length' "$HOOKS/hooks.json")
    [ "$wiring" = 1 ] && ok "quiz: speak-answered wired PostToolUse(AskUserQuestion)" || bad "quiz: speak-answered wired PostToolUse(AskUserQuestion)" "count: $wiring"

    QIN() { jq -nc --arg p "$TR" '{session_id:"sess-q1",transcript_path:$p,hook_event_name:"PreToolUse",tool_name:"AskUserQuestion",
            tool_input:{questions:[{question:"What is the slope of y equals 2x?",options:[{label:"Two"},{label:"One half "}]}]}}'; }
    fresh; voice_on; TR="$T/quiz.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"older prompt"}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"OLD-TURN text."}]}}' \
      '{"type":"user","message":{"content":"<command-name>/teach</command-name><command-args>slope</command-args>"}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Teaching: slope is **rise over run**."}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion","input":{}}]}}' > "$TR"
    out=$(QIN | QH); rc=$?
    { [ $rc = 0 ] && [ -z "$out" ]; } && ok "quiz: question hook exit 0, no stdout" || bad "quiz: question hook exit 0, no stdout" "rc=$rc out=$out"
    wait_spoken; s=$(spoken)
    [[ "$s" == *"Teaching: slope is rise over run."*"What is the slope of y equals 2x?"*"The options are: Two. One half."* ]] \
      && ok "quiz: speaks turn text, then question, then options" || bad "quiz: speaks turn text, then question, then options" "spoken: $s"
    [[ "$s" != *OLD-TURN* ]] && ok "quiz: earlier turns not spoken" || bad "quiz: earlier turns not spoken" "spoken: $s"
    [[ "$s" != *"**"* && "$s" != *AskUserQuestion* && "$s" != *"{"* ]] && ok "quiz: spoken text is cleaned" || bad "quiz: spoken text is cleaned" "spoken: $s"

    # tool_result does not reset the turn; final text spoken once by Stop; teaching text not repeated
    printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"q1","content":"Two"}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Correct, well done."}]}}' >> "$TR"
    : > "$SPEAK_OUT"
    jq -nc --arg p "$TR" '{session_id:"sess-q1",transcript_path:$p,stop_hook_active:false,last_assistant_message:"Correct, well done."}' | stop_hook
    wait_spoken; s=$(spoken)
    [ "$s" = "Correct, well done." ] && ok "quiz: Stop speaks only the unspoken final text" || bad "quiz: Stop speaks only the unspoken final text" "spoken: $s"
    : > "$SPEAK_OUT"
    jq -nc --arg p "$TR" '{session_id:"sess-q1",transcript_path:$p,stop_hook_active:false,last_assistant_message:"Correct, well done."}' | stop_hook
    sleep 1; [ -z "$(spoken)" ] && ok "quiz: repeated Stop with same text → silent" || bad "quiz: repeated Stop with same text → silent" "spoken: $(spoken)"
    # a different session's Stop is not deduped against sess-q1
    jq -nc --arg p "$TR" '{session_id:"sess-other",stop_hook_active:false,last_assistant_message:"Correct, well done."}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *"Correct, well done."* ]] && ok "quiz: dedupe is per session" || bad "quiz: dedupe is per session" "spoken: $(spoken)"
    # UserPromptSubmit clears the record
    jq -nc '{session_id:"sess-q1",prompt:"next"}' | bash "$HOOKS/speak-interrupt.sh" >/dev/null
    left=$(ls "$SPEAK_STATE_DIR" | grep '^spoken\.sess-q1' )
    [ -z "$left" ] && ok "quiz: UserPromptSubmit clears spoken record" || bad "quiz: UserPromptSubmit clears spoken record" "left: $left"

    # a block identical to one voiced earlier in the turn, appearing later, is still spoken (position, not content)
    fresh; voice_on; TR="$T/quiz-rep.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"quiz me twice"}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Correct."}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion","input":{}}]}}' > "$TR"
    QIN | QH; wait_spoken
    printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"q1","content":"Two"}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Correct."}]}}' >> "$TR"
    : > "$SPEAK_OUT"
    jq -nc --arg p "$TR" '{session_id:"sess-q1",transcript_path:$p,stop_hook_active:false,last_assistant_message:"Correct."}' | stop_hook
    wait_spoken; [ "$(spoken)" = "Correct." ] && ok "quiz: repeated identical later block still spoken" || bad "quiz: repeated identical later block still spoken" "spoken: '$(spoken)'"

    # --- marker (93295f8): tool_use_id present → marker = that line, holds a single integer
    fresh; voice_on; TR="$T/qm.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"go"}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Marker teaching."}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"tu-m1","name":"AskUserQuestion","input":{}}]}}' > "$TR"
    jq -nc --arg p "$TR" '{session_id:"sm",transcript_path:$p,tool_use_id:"tu-m1",tool_name:"AskUserQuestion",tool_input:{questions:[{question:"Ready?"}]}}' | QH
    wait_spoken; m=$(cat "$SPEAK_STATE_DIR/spoken.sm" 2>/dev/null)
    [[ "$(spoken)" == "Marker teaching."*"Ready?"* ]] && ok "marker: tool_use found → text then question" || bad "marker: tool_use found → text then question" "spoken: $(spoken)"
    [ "$m" = 3 ] && ok "marker: spoken.<sid> is the tool_use line (3)" || bad "marker: spoken.<sid> is the tool_use line (3)" "content: '$m'"
    # tool_use line appears late (0.5s) but within the 1.5s wait → text still voiced
    fresh; voice_on; TR="$T/qlate.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"go"}}' '{"type":"assistant","message":{"content":[{"type":"text","text":"Late teaching."}]}}' > "$TR"
    ( sleep 0.5; printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"tu-late","name":"AskUserQuestion","input":{}}]}}' >> "$TR" ) &
    jq -nc --arg p "$TR" '{session_id:"sl",transcript_path:$p,tool_use_id:"tu-late",tool_input:{questions:[{question:"Late?"}]}}' | QH; wait
    wait_spoken; [[ "$(spoken)" == "Late teaching."*"Late?"* ]] && ok "marker: tool_use appearing at 0.5s is waited for" || bad "marker: tool_use appearing at 0.5s is waited for" "spoken: $(spoken)"
    # tool_use never appears → only the questions, returns ≤3s, marker 0/absent
    fresh; voice_on; TR="$T/qnever.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"go"}}' '{"type":"assistant","message":{"content":[{"type":"text","text":"Unseen teaching."}]}}' > "$TR"
    t0=$(now_ms); jq -nc --arg p "$TR" '{session_id:"sn",transcript_path:$p,tool_use_id:"tu-never",tool_input:{questions:[{question:"Never?"}]}}' | QH; dt=$(( $(now_ms)-t0 ))
    wait_spoken; m=$(cat "$SPEAK_STATE_DIR/spoken.sn" 2>/dev/null)
    { [ "$(spoken)" = "Never?" ] && [ $dt -le 3000 ] && { [ -z "$m" ] || [ "$m" -le 1 ] 2>/dev/null; }; } && ok "marker: missing tool_use → questions only, ${dt}ms, marker '${m:-absent}' (≤ turn start)" \
      || bad "marker: missing tool_use → questions only, ≤3s, marker ≤ turn start (line 1)" "spoken: '$(spoken)' took ${dt}ms marker '$m'"
    # …and Stop afterwards still voices the unmarked teaching text
    printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"Final words."}]}}' >> "$TR"; : > "$SPEAK_OUT"
    jq -nc --arg p "$TR" '{session_id:"sn",transcript_path:$p,stop_hook_active:false,last_assistant_message:"Final words."}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *"Unseen teaching."*"Final words."* ]] && ok "marker: Stop picks up text the quiz couldn't mark" || bad "marker: Stop picks up text the quiz couldn't mark" "spoken: $(spoken)"
    # last_assistant_message newer than the transcript is appended
    fresh; voice_on; TR="$T/qlam.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"go"}}' '{"type":"assistant","message":{"content":[{"type":"text","text":"Transcript part."}]}}' > "$TR"
    jq -nc --arg p "$TR" '{session_id:"sa",transcript_path:$p,stop_hook_active:false,last_assistant_message:"Live only part."}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *"Live only part."* ]] && ok "marker: last_assistant_message not yet in transcript is spoken" || bad "marker: last_assistant_message not yet in transcript is spoken" "spoken: $(spoken)"

    # --- turn boundaries (a78012b)
    fresh; voice_on; TR="$T/img.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"first question"}}' \
      '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Read","input":{}}]}}' \
      '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"OLD REPLY previous turn."}]}}' \
      '{"type":"user","message":{"content":[{"type":"text","text":"what is this?"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"iVBO"}}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"NEW REPLY picture."}]}}' > "$TR"
    jq -nc '{session_id:"si",prompt:"what is this?"}' | bash "$HOOKS/speak-interrupt.sh" >/dev/null
    jq -nc --arg p "$TR" '{session_id:"si",transcript_path:$p,stop_hook_active:false,last_assistant_message:"NEW REPLY picture."}' | stop_hook
    wait_spoken; [ "$(spoken)" = "NEW REPLY picture." ] && ok "turn: text+image prompt is a turn boundary" || bad "turn: text+image prompt is a turn boundary" "spoken: $(spoken)"
    fresh; voice_on; TR="$T/imgonly.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"q"}}' '{"type":"assistant","message":{"content":[{"type":"text","text":"OLD REPLY."}]}}' \
      '{"type":"user","message":{"content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"iVBO"}}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Image reply."}]}}' > "$TR"
    jq -nc --arg p "$TR" '{session_id:"si2",transcript_path:$p,stop_hook_active:false}' | stop_hook
    wait_spoken; [ "$(spoken)" = "Image reply." ] && ok "turn: image-only prompt is a turn boundary (fallback)" || bad "turn: image-only prompt is a turn boundary (fallback)" "spoken: $(spoken)"
    fresh; voice_on; TR="$T/meta.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"explain"}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"PART-ONE."}]}}' \
      '{"type":"user","isMeta":true,"message":{"content":"<system-reminder>meta</system-reminder>"}}' \
      '{"type":"user","isMeta":true,"message":{"content":[{"type":"text","text":"meta array"}]}}' \
      '{"type":"assistant","message":{"content":[{"type":"text","text":"PART-TWO."}]}}' > "$TR"
    jq -nc --arg p "$TR" '{session_id:"sm2",transcript_path:$p,stop_hook_active:false,last_assistant_message:"PART-TWO."}' | stop_hook
    wait_spoken; s=$(spoken); [[ "$s" == *"PART-ONE."*"PART-TWO."* && "$s" != *meta* ]] && ok "turn: meta user entries mid-turn are not boundaries" || bad "turn: meta user entries mid-turn are not boundaries" "spoken: $s"
    # >1MB turn (60 × 20KB text + tool rounds) still speaks the final reply
    fresh; voice_on; TR="$T/huge.jsonl"; X=$(head -c 20000 /dev/zero | tr '\0' x)
    { jq -nc '{type:"user",message:{content:"build it"}}'
      for i in $(seq 1 60); do
        jq -nc --arg t "step $i $X" --arg id "t$i" '{type:"assistant",message:{content:[{type:"text",text:$t},{type:"tool_use",id:$id,name:"Read",input:{}}]}}'
        jq -nc --arg id "t$i" '{type:"user",message:{content:[{type:"tool_result",tool_use_id:$id,content:"ok"}]}}'
      done
      jq -nc '{type:"assistant",message:{content:[{type:"text",text:"FINAL REPLY done."}]}}'; } > "$TR"
    kb=$(( $(wc -c < "$TR") / 1024 ))
    jq -nc --arg p "$TR" '{session_id:"sh",transcript_path:$p,stop_hook_active:false,last_assistant_message:"FINAL REPLY done."}' | stop_hook
    wait_spoken; [ "$(spoken)" = "FINAL REPLY done." ] && ok "turn: ${kb}KB turn still speaks final reply" || bad "turn: ${kb}KB turn still speaks final reply" "spoken: $(spoken | cut -c1-80)"
    jq -nc --arg p "$TR" '{session_id:"sh2",transcript_path:$p,stop_hook_active:false}' | stop_hook
    wait_spoken; [[ "$(spoken)" == *"FINAL REPLY done."* ]] && ok "turn: ${kb}KB turn, transcript fallback" || bad "turn: ${kb}KB turn, transcript fallback" "spoken: $(spoken | cut -c1-80)"

    # answered hook stops the question speech
    fresh; voice_on; export SPEAK_CMD="$SLOW"; TR="$T/quiz2.jsonl"
    printf '%s\n' '{"type":"user","message":{"content":"go"}}' '{"type":"assistant","message":{"content":[{"type":"text","text":"Think first."}]}}' > "$TR"
    QIN | QH; wait_spoken; P=$(tail -1 "$SPEAK_OUT.pids")
    out=$(jq -nc '{session_id:"sess-q1",tool_name:"AskUserQuestion",tool_input:{},tool_response:{}}' | AH); rc=$?; sleep 0.3
    { [ $rc = 0 ] && [ -z "$out" ] && ! kill -0 "$P" 2>/dev/null; } && ok "quiz: answered hook stops speech, exit 0, no stdout" || bad "quiz: answered hook stops speech, exit 0, no stdout" "rc=$rc out=$out speaker $P alive=$(kill -0 "$P" 2>/dev/null && echo y || echo n)"
    pkill -f "$SLOW" 2>/dev/null
    # answered hook with no state dir / garbage stdin
    fresh; out=$(printf 'garbage' | AH); rc=$?
    { [ $rc = 0 ] && [ -z "$out" ] && [ ! -d "$SPEAK_STATE_DIR" ]; } && ok "quiz: answered hook no-op without state" || bad "quiz: answered hook no-op without state" "rc=$rc out=$out dir=$(ls -d "$SPEAK_STATE_DIR" 2>&1)"
    # flag off → question hook silent; garbage → exit 0
    fresh; TR="$T/q3.jsonl"; printf '%s\n' '{"type":"user","message":{"content":"go"}}' > "$TR"
    QIN | QH; sleep 1; [ -z "$(spoken)" ] && ok "quiz: speech off → question hook silent" || bad "quiz: speech off → question hook silent" "spoken: $(spoken)"
    fresh; voice_on; printf 'not json' | QH; rc=$?; [ $rc = 0 ] && ok "quiz: question hook garbage stdin → exit 0" || bad "quiz: question hook garbage stdin → exit 0" "rc=$rc"
    # questions only (no transcript)
    fresh; voice_on; TR=/nonexistent
    QIN | QH; wait_spoken; [[ "$(spoken)" == "What is the slope of y equals 2x?"*"Two. One half."* ]] && ok "quiz: questions spoken without transcript" || bad "quiz: questions spoken without transcript" "spoken: $(spoken)"
  fi
fi

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
    o=$(echo '{}' | bash "$HOOKS/status-line.sh"); [[ "$o" == *"🔊 speaking"* ]] && ok "status-line: 🔊 while speaking" || bad "status-line: 🔊 while speaking" "got: '$o' | pidfile: $(cat "$SPEAK_STATE_DIR/speak.pid" 2>&1) | P=$P alive=$(kill -0 "$P" 2>/dev/null && echo y || echo n) lstart-now: $(LC_ALL=C ps -o lstart= -p "$P" 2>&1) | flag: $(ls "$SPEAK_STATE_DIR" 2>&1 | tr '\n' ' ')"
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
perm_commands() { # every line/inline span in the body (not frontmatter) that invokes a hooks/*.sh script
  awk 'NR==1&&/^---/{f=1;next} f&&/^---/{f=0;next} !f' "$1" | grep -o 'bash [^`]*hooks/[A-Za-z0-9_.-]*\.sh"\{0,1\}[^`]*' | sed 's/[[:space:]]*$//' | sort -u; }
perm_match() { local cmd="$1" pat; while IFS= read -r pat; do [ -n "$pat" ] || continue
    pat="${pat//\?/\\?}"; pat="${pat//\[/\\[}"
    [[ "$cmd" == $pat ]] && return 0; done <<<"$2"; return 1; }
if want perm; then
  for sk in speak-on speak-off; do
    f="$ROOT/skills/$sk/SKILL.md"; [ -f "$f" ] || { bad "perm: skills/$sk exists" "missing"; continue; }
    pats=$(perm_patterns "$f"); cmds=$(perm_commands "$f")
    if [ -z "$cmds" ]; then
      [ "$sk" = teach ] && { skip "perm: teach speech-status check" "teach no longer runs a hooks script"; continue; }
      bad "perm: $sk runs a hooks script" "no 'bash …hooks/*.sh' command found"; continue; fi
    while IFS= read -r c; do
      e="${c//\$\{CLAUDE_PLUGIN_ROOT\}/$ROOT}"
      # Docs (code.claude.com/docs/en/skills): in plugin skills ${CLAUDE_PLUGIN_ROOT} is substituted in the body
      # AND in allowed-tools Bash rules. So check as written, and with both sides expanded.
      epats="${pats//\$\{CLAUDE_PLUGIN_ROOT\}/$ROOT}"
      if perm_match "$c" "$pats" && perm_match "$e" "$epats"; then ok "perm: $sk allows: $c"
      else bad "perm: $sk allows: $c" "allowed-tools patterns: ${pats:-<none>}"$'\n'"command as written: $c"$'\n'"expanded: $e"; fi
    done <<<"$cmds"
  done
  # patterns must not approve other code. Claude Code: "Bash rules match the whole command text, with * standing
  # in for any text" (code.claude.com/docs/en/permissions), so a * right after `bash` can absorb `-c '<code>'`.
  for pair in "speak-on:on" "speak-off:off"; do
    sk="${pair%%:*}"; arg="${pair#*:}"; f="$ROOT/skills/$sk/SKILL.md"; [ -f "$f" ] || continue
    pats=$(perm_patterns "$f"); hit=""
    # (a different script at another path can't be excluded portably, since the plugin root varies; not tested)
    for evil in "bash -c 'touch /tmp/pwned' \"\${CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh\" $arg" \
                "bash --rcfile /tmp/evil -i \"\${CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh\" $arg" \
                "bash \"/tmp/evil.sh\" \"\${CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh\" $arg" \
                "bash \"/tmp/evil.sh\" \"/x/hooks/speak-toggle.sh\" $arg"; do
      perm_match "$evil" "$pats" && hit="$hit"$'\n'"matches: $evil"
    done
    [ -z "$hit" ] && ok "perm: $sk pattern rejects injected commands" || bad "perm: $sk pattern rejects injected commands" "patterns: $pats$hit"
  done
  # patterns must not be broader than the toggle (no bare Bash, no Bash(*), no Bash(bash *))
  for sk in speak-on speak-off teach; do   # teach included: it must not grant broad Bash either
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
echo "passed=$PASS failed=$FAIL warned=$WARN skipped=$SKIP total=$((PASS+FAIL+WARN))"
[ $WARN = 0 ] || printf '  ~ %s\n' "${WARNED[@]}"
[ $FAIL = 0 ] || { printf '  - %s\n' "${FAILED[@]}"; exit 1; }
