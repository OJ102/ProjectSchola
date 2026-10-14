"""Live Obsidian sync: rebuild the session note whenever the transcript changes.

usage: md-log-watch.py <transcript_path> <session_id> <parent_pid> <pid_file>

Started (daemonized) by md-log-watch.sh on SessionStart, stopped on SessionEnd.
The note itself is still rendered by md-log-stop-hook.sh, so the live note and the
end-of-turn note are byte-for-byte the same thing.

Watch logic follows gapmiss/claude-sessions (src/views/timeline-view.ts):
  - native file events (kqueue here, fs.watch there), falling back to stat polling;
  - a trailing debounce, so a burst of transcript writes costs one rebuild;
  - one rebuild right away on start, to catch anything written before we watched;
  - a full re-render each time (the file only grows, and rendering is cheap).
Differences: a max-wait cap so a long streaming turn still refreshes while it
streams, reopening the file if it is replaced, and exiting on our own when the
Claude Code process that started us is gone (no orphans if SessionEnd never runs).

Python stdlib only. Never prints: the hook's stdout would reach the model.
"""
import json
import os
import select
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(HERE, "md-log-stop-hook.sh")


def ms_env(name, default):
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        v = default
    return max(50, min(v, 60000)) / 1000.0


DEBOUNCE = ms_env("MD_LOG_DEBOUNCE_MS", 500)   # quiet time before a rebuild
MAX_WAIT = ms_env("MD_LOG_MAXWAIT_MS", 2000)   # longest a change waits during nonstop writes
POLL = ms_env("MD_LOG_POLL_MS", 1000)          # stat-poll interval (fallback, parent and file checks)


def pid_start(pid):
    """Process start time, so a reused pid is not mistaken for our parent."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, env={**os.environ, "LC_ALL": "C"}, timeout=5).stdout
    except Exception:
        return ""
    return out.strip()


class Watcher:
    def __init__(self, transcript, session_id, parent_pid):
        self.transcript = transcript
        self.payload = json.dumps({"session_id": session_id, "transcript_path": transcript,
                                   "hook_event_name": "LiveWatch"})
        self.parent_pid = parent_pid
        self.parent_start = pid_start(parent_pid) if parent_pid > 1 else ""
        self.kq = getattr(select, "kqueue", None) and select.kqueue()
        self.fd = None
        self.sig = None            # (inode, size, mtime) last seen
        self.first_change = None   # when the oldest unrendered change arrived
        self.last_change = None    # when the newest one arrived
        self.last_parent_check = 0.0
        self.last_reuse_check = time.monotonic()

    # --- file events -------------------------------------------------------
    def stat_sig(self):
        try:
            st = os.stat(self.transcript)
        except OSError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def open_kq(self):
        """(Re)arm kqueue on the current transcript file. Returns False if it isn't there yet."""
        if not self.kq:
            return False
        self.close_fd()
        try:
            self.fd = os.open(self.transcript, os.O_RDONLY | getattr(os, "O_EVTONLY", 0))
        except OSError:
            return False
        flags = (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_DELETE
                 | select.KQ_NOTE_RENAME | select.KQ_NOTE_ATTRIB)
        ev = select.kevent(self.fd, filter=select.KQ_FILTER_VNODE,
                           flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=flags)
        self.kq.control([ev], 0, 0)
        return True

    def close_fd(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def wait_events(self, timeout):
        """Block up to timeout seconds. True if the file may have changed."""
        if self.fd is not None:
            try:
                evs = self.kq.control(None, 8, timeout)
            except InterruptedError:
                return False
            if any(e.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME) for e in evs):
                self.close_fd()   # replaced or removed: rearm on the next pass
            if evs:
                return True
        else:
            time.sleep(timeout)
        # stat check: the polling fallback, and a safety net for missed or re-armed events
        sig = self.stat_sig()
        if sig != self.sig:
            if self.kq and sig and (self.sig is None or sig[0] != self.sig[0]):
                self.open_kq()
            return True
        return False

    # --- lifecycle ---------------------------------------------------------
    def parent_alive(self):
        if self.parent_pid <= 1:
            return True
        try:
            os.kill(self.parent_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        now = time.monotonic()
        if self.parent_start and now - self.last_reuse_check >= 30:   # pid reuse check, via ps
            self.last_reuse_check = now
            return pid_start(self.parent_pid) == self.parent_start
        return True

    def rebuild(self):
        self.sig = self.stat_sig()
        self.first_change = self.last_change = None
        if self.sig is None:
            return
        try:
            subprocess.run(["bash", RENDER], input=self.payload, text=True, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def run(self):
        self.open_kq()
        self.rebuild()   # catch up on anything written before we started
        while True:
            now = time.monotonic()
            if now - self.last_parent_check >= POLL:
                self.last_parent_check = now
                if not self.parent_alive():
                    if self.first_change is not None:
                        self.rebuild()
                    return
            if self.fd is None and self.kq:
                self.open_kq()   # transcript not created yet, or it was replaced
            if self.first_change is None:
                timeout = POLL
            else:
                due = min(self.last_change + DEBOUNCE, self.first_change + MAX_WAIT)
                timeout = max(0.0, min(due - now, POLL))
            if self.wait_events(timeout):
                t = time.monotonic()
                if self.first_change is None:
                    self.first_change = t
                self.last_change = t
            if self.first_change is not None:
                t = time.monotonic()
                if t >= self.last_change + DEBOUNCE or t >= self.first_change + MAX_WAIT:
                    self.rebuild()


def daemonize(pid_file):
    """Double fork so the hook returns at once and Claude Code can't reap us with it."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    tmp = f"{pid_file}.{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(f"{os.getpid()} {pid_start(os.getpid())}\n")
    os.replace(tmp, pid_file)


def main():
    if len(sys.argv) != 5:
        sys.exit(2)
    transcript, session_id, parent, pid_file = sys.argv[1:]
    try:
        parent_pid = int(parent)
    except ValueError:
        parent_pid = 0
    if os.environ.get("MD_LOG_FOREGROUND") != "1":
        daemonize(pid_file)
    me = os.getpid()

    def cleanup(*_):
        try:
            with open(pid_file) as f:
                if f.read().split()[:1] == [str(me)]:
                    os.unlink(pid_file)
        except OSError:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGHUP, cleanup)
    try:
        Watcher(transcript, session_id, parent_pid).run()
    finally:
        cleanup()


if __name__ == "__main__":
    main()
