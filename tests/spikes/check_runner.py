#!/usr/bin/env python3
"""Decision 25: librarian confinement. Runs spikes/brief_run.py against a stub `claude`
that records its argv, then checks the pinned flags. No model calls. Tester-owned.

usage: check_runner.py <repo-root> <workdir>
"""
import json, os, stat, subprocess, sys

ROOT, W = sys.argv[1], sys.argv[2]
SP = os.path.join(ROOT, 'spikes')
os.makedirs(os.path.join(W, 'bin'), exist_ok=True)
log = os.path.join(W, 'argv.jsonl')
stub = os.path.join(W, 'bin', 'claude')
open(stub, 'w').write(f'''#!/usr/bin/env python3
import json, os, sys
sys.stdin.read()
open({log!r}, 'a').write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
print(json.dumps({{"result": "## Core claims\\n- x\\n", "session_id": "stub", "usage": {{}}, "total_cost_usd": 0}}))
''')
os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)


def o(name, good, detail=''):
    print(f"ok {name}" if good else f"FAIL {name} :: {str(detail)[:400]}")


o("d25: no plugin agent file agents/librarian.md", not os.path.exists(os.path.join(ROOT, 'agents', 'librarian.md')),
  "agents/librarian.md exists (auto-delegatable, can be invoked unconfined)")
tracked = subprocess.run(['git', '-C', ROOT, 'ls-files', 'agents'], capture_output=True, text=True).stdout.split()
o("d25: librarian not a tracked plugin agent", not any('librarian' in t for t in tracked), tracked)

store = os.path.join(W, 'store')
os.makedirs(store, exist_ok=True)
open(os.path.join(store, 'spans.jsonl'), 'w').write(json.dumps({'id': 'p0001.1', 'segment': 'p0001', 'locator': '1', 'kind': 'para',
                                                               'text': 'Tides are driven by the moon and the sun together.',
                                                               'norm': 'tides are driven by the moon and the sun together.'}) + '\n')
unit = os.path.join(W, 'u.md')
open(unit, 'w').write('# Unit source: Synthetic\n[p0001.1] Tides are driven by the moon and the sun together.\n')
open(unit + '.ids', 'w').write('p0001.1\n')
env = dict(os.environ, PATH=os.path.join(W, 'bin') + os.pathsep + os.environ['PATH'])
r = subprocess.run([sys.executable, os.path.join(SP, 'brief_run.py'), store, unit, os.path.join(W, 'out')],
                   capture_output=True, text=True, env=env, timeout=120)
calls = [json.loads(l) for l in open(log)] if os.path.exists(log) else []
o("d25: brief_run invokes claude (stubbed)", calls, f"rc={r.returncode} {r.stderr[-300:]}")
for k, c in enumerate(calls):
    a = c['argv']
    tag = f"call {k + 1}"

    def val(flag):
        return a[a.index(flag) + 1] if flag in a and a.index(flag) + 1 < len(a) else None

    def vals(flag):  # variadic flag: values until the next --flag
        if flag not in a:
            return None
        i, v = a.index(flag) + 1, []
        while i < len(a) and not a[i].startswith('--'):
            v += a[i].split(',')
            i += 1
        return v
    o(f"d25 {tag}: --tools pinned to Read,Grep,Glob", sorted(vals('--tools') or []) == ['Glob', 'Grep', 'Read'], vals('--tools'))
    o(f"d25 {tag}: --strict-mcp-config present", '--strict-mcp-config' in a, a)
    mcp = val('--mcp-config')
    servers = None
    if mcp:
        try:
            servers = json.load(open(mcp)).get('mcpServers') if os.path.exists(mcp) else json.loads(mcp).get('mcpServers')
        except Exception as e:
            servers = f"unreadable: {e}"
    o(f"d25 {tag}: no MCP servers configured", not servers, servers)
    pm = val('--permission-mode')
    o(f"d25 {tag}: fixed --permission-mode (not bypass/acceptEdits)", pm in ('default', 'plan', 'dontAsk'), pm)
    o(f"d25 {tag}: no --dangerously-skip-permissions", '--dangerously-skip-permissions' not in a, a)
    st = val('--settings')
    S = {}
    try:
        S = json.load(open(st)) if st and os.path.exists(st) else (json.loads(st) if st else {})
    except Exception:
        pass
    deny = (S.get('permissions') or {}).get('deny', [])
    o(f"d25 {tag}: settings deny mcp__*", 'mcp__*' in deny, deny)
    hooks = json.dumps(S.get('hooks', {}))
    o(f"d25 {tag}: settings install the librarian guard hook", 'librarian' in hooks and 'PreToolUse' in hooks, hooks[:200])
    o(f"d25 {tag}: librarian defined inline via --agents", val('--agents') is not None, a)
    o(f"d25 {tag}: not invoked as plugin agent (--agent <plugin>:librarian)", not (val('--agent') or '').endswith(':librarian'), val('--agent'))
