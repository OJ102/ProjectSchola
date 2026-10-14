---
name: md-log
description: Live Obsidian knowledge base sync. Automatically mirrors session transcripts to structured markdown notes in real time.
---

# md-log

Live mirror of the teaching session to Obsidian markdown notes (`$OBSIDIAN_VAULT_PATH/sessions/<date> <topic>.md`). Questions appear before answers, and no correct answer is ever leaked into the log.

## Setup

Set your Obsidian vault path in your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
export OBSIDIAN_VAULT_PATH=~/Obsidian/learn
```

(Default: `~/Obsidian/learn` if not set.)

## Live Watch Architecture

Live synchronization is powered by a daemonized watcher ([`hooks/md-log-watch.py`](file:///Users/omjadhav/Projects/learn/hooks/md-log-watch.py)) inspired by the file-watching architecture in `gapmiss/claude-sessions`:

- **Event Detection**: Uses macOS native `kqueue` vnode filters (`NOTE_WRITE`, `NOTE_EXTEND`, `NOTE_DELETE`, `NOTE_RENAME`) with a stat-polling fallback.
- **Debounced Compilation**: Changes trigger a trailing debounce window (`MD_LOG_DEBOUNCE_MS`, default 500ms) with a maximum delay cap (`MD_LOG_MAXWAIT_MS`, default 2000ms) to consolidate high-frequency writes during streaming turns.
- **Session Lifecycle**:
  - `SessionStart`: Spawns the background watcher daemon (`hooks/md-log-watch.sh start`).
  - `SessionEnd`: Signals the watcher to stop (`hooks/md-log-watch.sh stop`) and performs a final sync.
  - Self-Reaping: The daemon verifies the parent process ID and automatically terminates if Claude Code exits abruptly.
- **Concurrency & Idempotency**: Atomic writes and symlink locks (`$MD_LOG_STATE_DIR/<session>.lock`) ensure no race conditions between live compilation and end-of-turn hooks.

## Environment Variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `OBSIDIAN_VAULT_PATH` | `~/Obsidian/learn` | Target vault directory |
| `MD_LOG_LIVE` | `1` | Set to `0` to disable background watcher and compile only at turn end |
| `MD_LOG_DEBOUNCE_MS` | `500` | Quiescence window before compiling changes (milliseconds) |
| `MD_LOG_MAXWAIT_MS` | `2000` | Max delay before compiling active transcript streams (milliseconds) |
| `MD_LOG_STATE_DIR` | `~/.claude/learn-md-log` | Directory holding watcher PID and lock files |

