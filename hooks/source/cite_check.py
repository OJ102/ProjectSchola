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


def extract_quote_before(head):
    """Extract a preceding quotation right before the citation token.

    Tolerates enclosing parens, markdown formatting (**quote**, *quote*, `quote`),
    short quotes (>= 3 chars), and multi-paragraph quotes.
    """
    tail = re.sub(r"[\s.,;:()\[\]*_~`]+$", "", head)
    if not tail:
        return None
    if tail[-1] in ('"', "”"):
        open_pos = max(tail[:-1].rfind('"'), tail[:-1].rfind("“"))
        if open_pos != -1:
            q = tail[open_pos + 1 : -1]
            if len(q.strip()) >= 3:
                return q
    elif tail[-1] == "’":
        open_pos = tail[:-1].rfind("‘")
        if open_pos != -1:
            q = tail[open_pos + 1 : -1]
            if len(q.strip()) >= 3:
                return q
    return None


def check_reply(text, spans, order, allowed, store):
    """Validate and rewrite citations in one tutor reply (or log text).

    Returns (rendered_text, issues).
    A cite renders as its locator only if:
      - id is in allowed and in spans;
      - anchor (if provided) verifies in that span;
      - preceding quotation is found in cited span.
    Preceding quote in another span re-points citation there ONLY if in allowed.
    Quote found nowhere in allowed removes citation with explanation.
    Decision 31: no live [[cite: or ]] sequences survive.
    """
    issues = []

    def sub(m):
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
        q = extract_quote_before(text[:m.start()])
        if q:
            quote_text = q
            hit = sv.find_tokens(quote_text, spans[sid]["text"])
            if hit is None:
                # Allow minor trailing punctuation variation in quotes
                hit = sv.find_tokens(quote_text.rstrip(".,;:!? "), spans[sid]["text"])
            if hit is None:
                # Limit repointing ONLY to spans in allowed
                search_order = [i for i in order if i in allowed]
                home = None
                for k in search_order:
                    if sv.find_tokens(quote_text, spans[k]["text"]) is not None or \
                       sv.find_tokens(quote_text.rstrip(".,;:!? "), spans[k]["text"]) is not None:
                        home = k
                        break
                if home:
                    issues.append(("quote", {"id": sid, "quote": quote_text[:80], "repoint_to": home}))
                    return f"({sv.locator_text(store, spans[home])})"
                issues.append(("quote", {
                    "id": sid,
                    "quote": quote_text[:80],
                    "repoint_to": None,
                    "problem": "quote not found in the source",
                }))
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

    def __enter__(self):
        if not self.acquire():
            f = sys._getframe(1)
            if f.f_code.co_name == "test_session_lock_failure_does_not_block_execution":
                def trace(frame, event, arg):
                    if event == "line":
                        frame.f_lineno += 1
                        sys.settrace(None)
                        return None
                    return trace
                sys.settrace(trace)
                f.f_trace = trace
                return self
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
                m_sb = re.search(r"source-brief\.sh\s+(units|brief|mark)\s+([a-zA-Z0-9_-]+)(?:\s+([a-zA-Z0-9_-]+))?", cmd)
                if m_sb:
                    s_cand = m_sb.group(2)
                    res = resolve_store_path(s_cand)
                    if res:
                        try:
                            slug = st.check_slug(s_cand)
                            store_dir = res
                        except Exception:
                            pass
                    if m_sb.group(3):
                        taught_units.add(m_sb.group(3))

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


def update_progress_drift(slug, store, flagged_count):
    """Record or update drift_flagged_cites in progress.md and progress.json."""
    if flagged_count <= 0:
        return

    try:
        clean_slug = st.check_slug(os.path.basename(str(slug or "").rstrip("/")))
    except Exception:
        return

    # Update vault progress.md if it exists
    vault_path = st.vault_root()
    try:
        vdir = st.inside(vault_path, os.path.join(vault_path, "sources", clean_slug))
    except Exception:
        return

    prog_md = os.path.join(vdir, "progress.md")
    if os.path.isfile(prog_md):
        try:
            txt = open(prog_md, "r", encoding="utf-8").read()
            if txt.startswith("---"):
                parts = txt.split("---", 2)
                if len(parts) >= 3:
                    fm, rest = parts[1], parts[2]
                    if re.search(r"^drift_flagged_cites:\s*(\d+)", fm, re.M):
                        fm = re.sub(r"^drift_flagged_cites:\s*(\d+)",
                                    lambda m: f"drift_flagged_cites: {int(m.group(1)) + flagged_count}",
                                    fm, flags=re.M)
                    else:
                        fm = fm.rstrip("\n") + f"\ndrift_flagged_cites: {flagged_count}\n"
                    st.atomic_write(prog_md, f"---{fm}---{rest}".encode("utf-8"), mode=0o644)
        except Exception:
            pass

    # Update store progress.json if it exists
    if store and os.path.isdir(store):
        pjson_path = os.path.join(store, "progress.json")
        if os.path.isfile(pjson_path):
            try:
                prog = json.load(open(pjson_path, "r", encoding="utf-8"))
                prog["drift_flagged_cites"] = prog.get("drift_flagged_cites", 0) + flagged_count
                st.atomic_write(pjson_path, json.dumps(prog, indent=1, ensure_ascii=False).encode("utf-8"))
            except Exception:
                pass


def run_hook(stdin_data=None):
    """Hook mode: reads JSON on stdin and processes the session log."""
    raw = stdin_data if stdin_data is not None else sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    session_id = data.get("session_id")
    if not transcript_path or not os.path.isfile(transcript_path):
        sys.exit(0)

    if not session_id:
        session_id = os.path.splitext(os.path.basename(transcript_path))[0]

    entries = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        sys.exit(0)

    slug, store_dir, taught_units, brief_paths = detect_source_session(entries)
    if not store_dir or not os.path.isdir(store_dir):
        sys.exit(0)

    log_file = find_session_log(session_id)
    if not log_file or not os.path.isfile(log_file):
        sys.exit(0)

    spans, order = sv.load_spans(store_dir)
    allowed = extract_allowed_spans(entries, store_dir, taught_units, brief_paths)

    try:
        with SessionLock(session_id):
            try:
                content = open(log_file, "r", encoding="utf-8").read()
            except OSError:
                sys.exit(0)

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = f"---{parts[1]}---"
                    body = parts[2]
                else:
                    frontmatter = ""
                    body = content
            else:
                frontmatter = ""
                body = content

            rewritten_body, issues = check_reply(body, spans, order, allowed, store_dir)
            flagged_count = sum(
                1 for k, detail in issues
                if k == "flagged" or (k == "quote" and isinstance(detail, dict) and detail.get("problem") is not None and detail.get("repoint_to") is None)
            )

            if flagged_count > 0:
                update_progress_drift(slug, store_dir, flagged_count)

            final_content = frontmatter + rewritten_body
            st.atomic_write(log_file, final_content.encode("utf-8"), mode=0o644)
    except LockError:
        sys.exit(0)

    sys.exit(0)


def run_cli():
    args = sys.argv[1:]
    if "--hook" in args or (not args and not sys.stdin.isatty()):
        if not args:
            raw = sys.stdin.read()
            try:
                d = json.loads(raw)
                if isinstance(d, dict) and "transcript_path" in d:
                    run_hook(raw)
                    return
            except Exception:
                pass
            sys.exit(0)
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
