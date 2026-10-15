"""Core citation validation and rewriting engine for tutor replies.

Operates in two modes:
1. Hook mode (Stop / SessionEnd hook):
   Reads JSON on stdin (transcript_path, session_id).
   If not a source session or no store found, exits 0 cleanly.
   Validates and rewrites tutor citation tokens in the session's Obsidian log.
   Neutralizes any surviving citation syntax per Decision 31.
   Records drift in progress.md / progress.json if citations were flagged.

2. CLI mode (testing / direct invocation):
   python3 cite_check.py <store> <brief.md> [outdir] [--analyze]
   python3 cite_check.py <store> <brief.md> --check-replies <replies.json> --out <out.json>
   python3 cite_check.py <store> <brief.md> [--filter] [--text TEXT]
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store as st  # noqa: E402
import verify as sv  # noqa: E402
from srcnorm import display_safe, neutralize  # noqa: E402

TUTOR_CITE = re.compile(r'\[\[cite:([^\s\]"]+)(?:\s+"(.*?)")?\s*\]\]', re.S)
BUDGET = 10_000


class LockError(RuntimeError):
    pass


# Quoted runs. Any double-style mark opens or closes (straight, curly, low-9 „, fullwidth ＂, prime ″,
# guillemets «» ‹›, CJK 「」『』〝〞), so mixed pairs like “…" count too; single quotes (' ‘ ’ ‚ ′)
# only at word edges, so "don't" is not one. An unbalanced mark pairs with the next one: the odd run
# is then checked too (fails closed).
DQ = "\"“”„‟＂″〃«»‹›「」『』〝〞〟"
SQ = "'‘’‚‛′"
QUOTED = re.compile(rf"[{DQ}]([^{DQ}]+)[{DQ}]|(?<!\w)[{SQ}](.+?)[{SQ}](?!\w)", re.S)
REMOVED = re.compile(r"\(citation removed(?:: quote not found in the source)?\)")


def quoted_runs(text, lo, hi):
    out = []
    for m in QUOTED.finditer(text, lo, hi):
        q = next(g for g in m.groups() if g is not None).strip(" *_`~")
        if len(q.strip()) >= 3:
            out.append(q)
    return out


def quotes_near(text, m, floor):
    """Every quoted run a cite token covers: before it, from the end of the last cite that rendered
    (or the paragraph start); after it, to the end of the line when no other cite follows on that
    line (a quote between two cites belongs to the next one)."""
    start, end = m.start(), m.end()
    lo = max(floor, text.rfind("\n\n", 0, start) + 1)
    pend = text.find("\n", end)
    pend = len(text) if pend == -1 else pend
    nxt = TUTOR_CITE.search(text, end, pend)
    return quoted_runs(text, lo, start) + ([] if nxt else quoted_runs(text, end, pend))


def quote_in(q, span_text):
    return sv.find_tokens(q, span_text) is not None or sv.find_tokens(q.rstrip(".,;:!? "), span_text) is not None


def check_reply(text, spans, order, allowed, store):
    """Validate and rewrite citations in one tutor reply (or log text).

    Returns (rendered_text, issues).
    A cite renders as its locator only if:
      - id is in allowed and in spans;
      - anchor (if provided) verifies in that span;
      - every quoted run around it (see quotes_near) is in that span.
    Quotes all found in another span re-point the citation there ONLY if it is in allowed.
    Quote found nowhere in allowed removes citation with explanation.
    Decision 31: no live [[cite: or ]] sequences survive.
    """
    issues = []

    # floor: where the quotes a cite covers start, normally the end of the previous cite. A removed
    # cite's quotes carry over to a cite right next to it ("q" [[cite:bad]][[cite:good]]).
    floor, prev = [0], [None]   # prev: (end, removed, floor it used)

    def sub(m):
        if prev[0]:
            end, removed, used = prev[0]
            floor[0] = used if removed and not re.search(r"\w", text[end:m.start()]) else end
        used = floor[0]
        out = render(m)
        prev[0] = (m.end(), out.startswith("(citation removed"), used)
        return out

    def render(m):
        sid, anchor = m.group(1), m.group(2)
        if sid not in allowed or sid not in spans:
            issues.append(("flagged", sid))
            return "(citation removed)"
        if anchor is not None:
            st_code, why, _ = sv.check_cite(sid, anchor, spans, order, None)
            if st_code != "ok":
                issues.append(("quote", {
                    "id": sid,
                    "quote": anchor[:80],
                    "repoint_to": None,
                    "problem": f"forged anchor: {why}",
                }))
                return "(citation removed)"
        quotes = quotes_near(text, m, floor[0])
        bad = [q for q in quotes if not quote_in(q, spans[sid]["text"])]
        if bad:
            # Re-point ONLY to a span in allowed that holds every one of the quotes
            home = next((k for k in order if k in allowed and all(quote_in(q, spans[k]["text"]) for q in quotes)), None)
            for q in bad:
                issues.append(("quote", {"id": sid, "quote": q[:80], "repoint_to": home,
                                         **({} if home else {"problem": "quote not found in the source"})}))
            if home:
                return f"({sv.locator_text(store, spans[home])})"
            return "(citation removed: quote not found in the source)"
        issues.append(("ok", sid))
        return f"({sv.locator_text(store, spans[sid])})"

    rendered = TUTOR_CITE.sub(sub, text)
    rendered = display_safe(rendered)
    # Decision 31: systematically neutralize all [[ and ]] forms (including fullwidth)
    rendered = neutralize(rendered)
    return rendered, issues


def pid_start(pid):
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


class SessionLock:
    """Session file lock matching md-log-stop-hook.sh symlink lock protocol."""

    def __init__(self, session_id):
        state_dir = os.environ.get("MD_LOG_STATE_DIR") or os.path.expanduser("~/.claude/learn-md-log")
        key = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80]
        self.lock_path = os.path.join(state_dir, f"{key}.lock")
        self.acquired = False
        self.lock_me = f"{os.getpid()} {pid_start(os.getpid())}"

    def acquire(self, timeout=20.0):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                os.symlink(self.lock_me, self.lock_path)
                self.acquired = True
                return True
            except FileExistsError:
                try:
                    if os.path.islink(self.lock_path):
                        owner = os.readlink(self.lock_path)
                        parts = owner.split(" ", 1)
                        pid = int(parts[0]) if parts and parts[0].isdigit() else 0
                        start = parts[1] if len(parts) > 1 else ""
                        if pid <= 1 or not start or pid_start(pid) != start:
                            try:
                                if os.readlink(self.lock_path) == owner:
                                    os.unlink(self.lock_path)
                            except OSError:
                                pass
                    elif os.path.exists(self.lock_path):
                        try:
                            if os.path.isdir(self.lock_path):
                                os.rmdir(self.lock_path)
                            else:
                                os.unlink(self.lock_path)
                        except OSError:
                            pass
                except OSError:
                    pass
            except OSError:
                pass
            time.sleep(0.05)
        return False

    def release(self):
        if self.acquired:
            try:
                if os.path.islink(self.lock_path) and os.readlink(self.lock_path) == self.lock_me:
                    os.unlink(self.lock_path)
            except OSError:
                pass
            self.acquired = False

    def held_by_caller(self):
        """md-log-stop-hook.sh runs this check while holding the session lock; it passes its lock
        owner string in MD_LOG_LOCK_HELD, and the lock must still name it."""
        owner = os.environ.get("MD_LOG_LOCK_HELD")
        try:
            return bool(owner) and os.readlink(self.lock_path) == owner
        except OSError:
            return False

    def __enter__(self):
        if self.held_by_caller():
            return self
        if not self.acquire():
            raise LockError(f"could not acquire lock {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def resolve_store_path(slug_or_dir):
    """Resolve slug or path to an existing store with spans.jsonl."""
    if not slug_or_dir:
        return None
    cand = os.path.expanduser(slug_or_dir)
    if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "spans.jsonl")):
        return os.path.realpath(cand)
    try:
        slug = st.check_slug(os.path.basename(str(slug_or_dir).rstrip("/")))
        d = os.path.join(st.sources_root(), slug)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "spans.jsonl")):
            return os.path.realpath(d)
    except Exception:
        pass
    return None


# Any source-brief.sh / brief.py subcommand that names a source marks a source session.
BRIEF_CMD = re.compile(r"(?:source-brief\.sh|brief\.py)\s+(units|brief|mark|fastpath|factcheck|review|where)"
                       r"\s+['\"]?([a-zA-Z0-9_-]+)['\"]?(?:\s+([a-zA-Z0-9_-]+))?")
UNIT_CMDS = {"brief", "mark", "fastpath", "factcheck"}


def detect_source_session(entries):
    """Inspect transcript entries to find (slug, store_dir, taught_units, brief_paths)."""
    slug = None
    store_dir = None
    taught_units = set()
    brief_paths = set()

    for e in entries:
        msg = e.get("message") or {}
        content = msg.get("content")

        text_content = ""
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            text_content = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")

        m_args = re.search(r"<command-name>[^<]*teach[^<]*</command-name>\s*<command-args>(.*?)</command-args>", text_content, re.S)
        if m_args:
            args_str = m_args.group(1).strip()
            first_word = args_str.split()[0] if args_str else ""
            if first_word:
                res = resolve_store_path(first_word)
                if res:
                    try:
                        slug = st.check_slug(os.path.basename(first_word.rstrip("/")))
                        store_dir = res
                    except Exception:
                        pass
            m_ub = re.search(r"Unit brief:\s*(\S+)", args_str)
            if m_ub:
                brief_paths.add(m_ub.group(1))
            m_seg = re.search(r"Store segments dir[^:]*:\s*(\S+)", args_str)
            if m_seg:
                store_dir = os.path.dirname(os.path.realpath(m_seg.group(1)))

        m_teach = re.search(r"(?:^|\s)/teach\s+([^\n\r]+)", text_content)
        if m_teach:
            t_arg = m_teach.group(1).strip()
            first_word = t_arg.split()[0] if t_arg else ""
            if first_word:
                res = resolve_store_path(first_word)
                if res:
                    try:
                        slug = st.check_slug(os.path.basename(first_word.rstrip("/")))
                        store_dir = res
                    except Exception:
                        pass
            m_ub = re.search(r"Unit brief:\s*(\S+)", t_arg)
            if m_ub:
                brief_paths.add(m_ub.group(1))
            m_seg = re.search(r"Store segments dir[^:]*:\s*(\S+)", t_arg)
            if m_seg:
                store_dir = os.path.dirname(os.path.realpath(m_seg.group(1)))

        blocks = content if isinstance(content, list) else []
        for c in blocks:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                inp = c.get("input") or {}
                cmd = str(inp.get("command") or json.dumps(inp))
                m_sb = BRIEF_CMD.search(cmd)
                if m_sb:
                    sub_cmd, s_cand, unit = m_sb.groups()
                    res = resolve_store_path(s_cand)
                    if res:
                        try:
                            slug = st.check_slug(s_cand)
                            store_dir = res
                        except Exception:
                            pass
                    if unit and sub_cmd in UNIT_CMDS:
                        taught_units.add(unit)

    resolved_store = store_dir or (resolve_store_path(slug) if slug else None)
    if not resolved_store:
        return None, None, set(), set()

    if not slug:
        try:
            slug = st.check_slug(os.path.basename(resolved_store.rstrip("/")))
        except Exception:
            return None, None, set(), set()

    return slug, resolved_store, taught_units, brief_paths


def extract_allowed_spans(entries, store, taught_units, explicit_brief_paths):
    """Extract all allowed span IDs: unit brief(s) + librarian lookups."""
    spans, _ = sv.load_spans(store)
    brief_ids = set()
    lookup_ids = set()

    briefs_to_read = set(explicit_brief_paths)
    for u in taught_units:
        bp = os.path.join(store, "briefs", f"{u}.md")
        if os.path.isfile(bp):
            briefs_to_read.add(bp)

    if not briefs_to_read:
        b_dir = os.path.join(store, "briefs")
        if os.path.isdir(b_dir):
            for f in os.listdir(b_dir):
                if f.endswith(".md"):
                    briefs_to_read.add(os.path.join(b_dir, f))

    for bp in briefs_to_read:
        if os.path.isfile(bp):
            try:
                b_text = open(bp, encoding="utf-8").read()
                brief_ids |= set(re.findall(r'\[\[cite:([^\s\]"]+)', b_text))
            except Exception:
                pass

    for e in entries:
        msg = e.get("message") or {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        for c in blocks:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                body = c.get("content")
                if isinstance(body, list):
                    body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                body = body or ""
                lookup_ids |= {m for m in re.findall(r"\b(p\d{4}\.\d+|ch\d{2}\.\d+|t\d{2}:\d{2}:\d{2})\b", body) if m in spans}
        res = e.get("toolUseResult")
        if res:
            res_str = json.dumps(res) if not isinstance(res, str) else res
            lookup_ids |= {m for m in re.findall(r"\b(p\d{4}\.\d+|ch\d{2}\.\d+|t\d{2}:\d{2}:\d{2})\b", res_str) if m in spans}

    return brief_ids | lookup_ids


def find_session_log(session_id):
    """Find the session Obsidian log with frontmatter `session: $SESSION_ID`."""
    vault_path = os.environ.get("OBSIDIAN_VAULT_PATH") or os.path.expanduser("~/Obsidian/learn")
    sessions_dir = os.path.join(vault_path, "sessions")
    if not os.path.isdir(sessions_dir):
        return None

    sid = session_id[:8] if len(session_id) >= 8 else session_id
    candidate_fallback = None

    for fname in os.listdir(sessions_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(sessions_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                head = [f.readline() for _ in range(40)]
            for line in head:
                if re.match(rf"^session:\s*['\"]?{re.escape(session_id)}['\"]?\s*$", line.strip()):
                    return fpath
        except OSError:
            continue
        if sid and (fname.endswith(f"-{sid}.md") or fname.endswith(f" ({sid}).md")):
            candidate_fallback = fpath

    return candidate_fallback


def update_progress_drift(slug, store, session_id, flagged_count):
    """Record drift_flagged_cites in progress.md and progress.json.

    The log is re-rendered from the transcript on every md-log run, so the same flagged cite is seen
    again and again: the count is kept per session in <store>/drift.json (latest count wins) and the
    total over sessions is written, never incremented."""
    try:
        clean_slug = st.check_slug(os.path.basename(str(slug or "").rstrip("/")))
    except Exception:
        return
    dpath = os.path.join(store, "drift.json")
    try:
        drift = json.load(open(dpath, encoding="utf-8")) if os.path.isfile(dpath) else {}
        if not isinstance(drift, dict):
            drift = {}
    except (OSError, ValueError):
        drift = {}
    if drift.get(session_id, 0) == flagged_count:
        return
    drift[session_id] = flagged_count
    st.write_json(dpath, drift)
    total = sum(v for v in drift.values() if isinstance(v, int))

    # Update vault progress.md if it exists
    vault_path = st.vault_root()
    try:
        vdir = st.inside(vault_path, os.path.join(vault_path, "sources", clean_slug))
    except Exception:
        vdir = None
    prog_md = os.path.join(vdir, "progress.md") if vdir else None
    if prog_md and os.path.isfile(prog_md):
        try:
            txt = open(prog_md, "r", encoding="utf-8").read()
            if txt.startswith("---"):
                parts = txt.split("---", 2)
                if len(parts) >= 3:
                    fm, rest = parts[1], parts[2]
                    if re.search(r"^drift_flagged_cites:\s*(\d+)", fm, re.M):
                        fm = re.sub(r"^drift_flagged_cites:\s*(\d+)", f"drift_flagged_cites: {total}", fm, flags=re.M)
                    else:
                        fm = fm.rstrip("\n") + f"\ndrift_flagged_cites: {total}\n"
                    st.atomic_write(prog_md, f"---{fm}---{rest}".encode("utf-8"), mode=0o644)
        except Exception:
            pass

    # Update store progress.json if it exists
    pjson_path = os.path.join(store, "progress.json")
    if os.path.isfile(pjson_path):
        try:
            prog = json.load(open(pjson_path, "r", encoding="utf-8"))
            prog["drift_flagged_cites"] = total
            st.atomic_write(pjson_path, json.dumps(prog, indent=1, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass


def split_frontmatter(content):
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return f"---{parts[1]}---", parts[2]
    return "", content


def session_log(session_id):
    """The log md-log-stop-hook.sh just wrote (MD_LOG_FILE, trusted only with the lock handoff), else
    the one whose frontmatter names the session."""
    f = os.environ.get("MD_LOG_FILE")
    if f and os.environ.get("MD_LOG_LOCK_HELD") and os.path.isfile(f):
        return f
    return find_session_log(session_id)


LIVE_TOKEN = re.compile(r"[\[［]\s*[\[［]\s*cite\s*[:：]", re.I)


def strip_tokens(log_file, marker):
    """Remove every citation token from the log. Returns True if the log held one."""
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    frontmatter, body = split_frontmatter(content)
    if not LIVE_TOKEN.search(neutralize(body).replace("[ [", "[[")):
        return False
    body = neutralize(TUTOR_CITE.sub(marker, body))
    st.atomic_write(log_file, (frontmatter + body).encode("utf-8"), mode=0o644)
    return True


def fail_closed(session_id, why):
    """The check could not run: no live citation token may stay in the log (Decision 31)."""
    print(f"source cite check failed ({why}); citation tokens in the session log were removed", file=sys.stderr)
    try:
        log_file = session_log(session_id)
        if log_file:
            strip_tokens(log_file, "(citation removed: cite check failed)")
    except Exception as e:  # noqa: BLE001 - last resort, say so
        print(f"source cite check: could not neutralize the log either ({type(e).__name__}: {e})", file=sys.stderr)


def check_log(session_id, entries):
    slug, store_dir, taught_units, brief_paths = detect_source_session(entries)
    log_file = session_log(session_id)
    if not log_file or not os.path.isfile(log_file):
        return
    if not store_dir or not os.path.isdir(store_dir):
        # no source found for this session, yet the note holds citation tokens: none can be checked
        with SessionLock(session_id):
            strip_tokens(log_file, "(citation removed: no source found for this session)")
        return
    spans, order = sv.load_spans(store_dir)
    allowed = extract_allowed_spans(entries, store_dir, taught_units, brief_paths)
    with SessionLock(session_id):
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        frontmatter, body = split_frontmatter(content)
        removed_before = len(REMOVED.findall(body))
        rewritten_body, issues = check_reply(body, spans, order, allowed, store_dir)
        flagged_count = sum(
            1 for k, detail in issues
            if k == "flagged" or (k == "quote" and isinstance(detail, dict) and detail.get("problem") is not None and detail.get("repoint_to") is None)
        )
        # cites removed by an earlier check of this same log still count for this session
        if flagged_count or removed_before:
            update_progress_drift(slug, store_dir, session_id, flagged_count + removed_before)
        st.atomic_write(log_file, (frontmatter + rewritten_body).encode("utf-8"), mode=0o644)


def run_hook(stdin_data=None):
    """Hook mode: reads JSON on stdin and processes the session log.
    Runs from md-log-stop-hook.sh, under its session lock, after every render of the log."""
    raw = stdin_data if stdin_data is not None else sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    session_id = data.get("session_id")
    if not isinstance(transcript_path, str) or not os.path.isfile(transcript_path):
        sys.exit(0)
    if not session_id or not isinstance(session_id, str):
        session_id = os.path.splitext(os.path.basename(transcript_path))[0]

    try:
        entries = []
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        check_log(session_id, entries)
    except Exception as e:  # noqa: BLE001 - fail closed on anything, including LockError
        fail_closed(session_id, f"{type(e).__name__}: {e}")
    sys.exit(0)


def run_cli():
    args = sys.argv[1:]
    if "--hook" in args or (not args and not sys.stdin.isatty()):
        run_hook()
        return

    if len(args) < 2:
        sys.exit(__doc__)

    store, brief = args[0], args[1]
    spans, order = sv.load_spans(store)

    brief_ids = set()
    if os.path.isfile(brief):
        b_txt = open(brief, encoding="utf-8").read()
        brief_ids = {sid for sid, _ in sv.CITE.findall(b_txt)}

    allow_files = [args[i + 1] for i, x in enumerate(args) if x == "--allow"]
    for af in allow_files:
        if os.path.isfile(af):
            brief_ids |= {l.strip() for l in open(af) if l.strip()}

    allowed = brief_ids

    # Analyze mode (compatible with dryrun.py --analyze)
    if "--analyze" in args or "--check-replies" in args:
        outdir = args[2] if len(args) > 2 and not args[2].startswith("--") else "."
        replies_path = args[args.index("--check-replies") + 1] if "--check-replies" in args else os.path.join(outdir, "replies.json")
        out_json_path = args[args.index("--out") + 1] if "--out" in args else os.path.join(outdir, "dryrun.json")
        render_log_path = args[args.index("--render-log") + 1] if "--render-log" in args else os.path.join(outdir, "dryrun-log.md")

        replies = json.load(open(replies_path, encoding="utf-8"))
        old_data = {}
        if os.path.isfile(out_json_path):
            try:
                old_data = json.load(open(out_json_path, encoding="utf-8"))
            except Exception:
                pass

        session = old_data.get("session", "unknown-session")
        turns = old_data.get("turns", [])

        cites, flagged, quote_bad, rendered = [], [], [], []
        for r in replies:
            out_t, issues = check_reply(r.get("tutor", ""), spans, order, allowed, store)
            rendered.append(out_t)
            for k, sid in issues:
                {"ok": cites, "flagged": flagged}.get(k, quote_bad).append(sid)

        rep = {
            "session": session,
            "turns": turns,
            "source_tool_results": old_data.get("source_tool_results", []),
            "source_derived_tokens_main": old_data.get("source_derived_tokens_main", 0),
            "budget": BUDGET,
            "within_budget": old_data.get("source_derived_tokens_main", 0) <= BUDGET,
            "tutor_cites": len(cites),
            "tutor_cites_flagged": flagged,
            "tutor_quote_mismatch": quote_bad,
            "total_cost_usd": old_data.get("total_cost_usd", 0.0),
        }

        os.makedirs(os.path.dirname(os.path.abspath(out_json_path)), exist_ok=True)
        json.dump(rep, open(out_json_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

        if render_log_path:
            os.makedirs(os.path.dirname(os.path.abspath(render_log_path)), exist_ok=True)
            with open(render_log_path, "w", encoding="utf-8") as f:
                for r, rr in zip(replies, rendered):
                    f.write(f"**Learner:** {r.get('user', '')}\n\n**Tutor:** {rr}\n\n---\n\n")

        print(json.dumps({k: v for k, v in rep.items() if k != "source_tool_results"}, indent=1))
        return

    if "--text" in args:
        txt = args[args.index("--text") + 1]
        out_t, _ = check_reply(txt, spans, order, allowed, store)
        print(out_t)
        return

    raw = sys.stdin.read()
    out_t, _ = check_reply(raw, spans, order, allowed, store)
    sys.stdout.write(out_t)


def main():
    run_cli()


if __name__ == "__main__":
    main()
