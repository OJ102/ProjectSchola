# Project Schola

**A tutor that finds the edge of what you know, then teaches from there.**

Project Schola is a [Claude Code](https://claude.com/claude-code) plugin that turns your terminal into a one-on-one tutor. It quizzes you to find where your understanding runs out, shows you a plan, and builds the topic up from first principles, checking each step with a quiz before moving on. It draws diagrams when a picture helps, writes the whole session into your Obsidian vault as readable notes, and can read its replies aloud.

> **Built by** [Om Jadhav](https://github.com/OJ102)

---

## Why Schola

Most explanations are pitched at an imagined average reader. That's too slow if you already know the basics and too fast if you're missing a step. Facts you're simply told also fade quickly, because nothing ties them to what you already know.

Schola works the other way:

- **It finds your level before it teaches.** Graded quiz questions get harder when you answer correctly and close in when you miss, until it can say what you know and where that knowledge stops.
- **You see the plan first.** Before the first lesson you get the approach and a small dependency map of the ideas, from foundations to your goal. You can change it before it starts.
- **Each step is explained and checked.** Every idea comes with the reason you need it and a link to what came before, followed by a short quiz. A missed quiz means that step gets fixed before anything is built on it.
- **Your notes are written for you.** The session ends up in Obsidian with rendered math, embedded diagrams, and every question with the answer you gave.

## What you get

| | |
|---|---|
| 🎯 **Adaptive Probe → Plan → Teach loop** | The `/teach` skill finds your level, presents a plan for your OK, then teaches it one idea at a time. |
| ✅ **Real quizzes, not rhetorical questions** | Checks use Claude Code's multiple-choice `AskUserQuestion` dialog and the model grades them. Options are written so that wording doesn't give away the answer, and one slot is kept for "I haven't seen this yet". |
| 🔎 **Fact-checking built in** | A `researcher` subagent searches the web to map the topic before planning and to check facts the tutor isn't sure of. |
| 🖼️ **Diagrams that are checked before you see them** | The `mermaid-maker` (graphs, flows, sequences) and `svg-maker` (geometry, number lines, plots) subagents render each picture, look at the result and fix it, then save a PNG to your vault's `viz/` folder for embedding in the notes. |
| 📓 **Live Obsidian session log** | A hook rebuilds a clean Markdown copy of the session from the transcript after every reply and every answered quiz. Math, diagrams and Q&A all render in Obsidian. |
| 🔈 **Speech out** | `/speak-on` reads replies aloud with macOS `say`. Markdown, code and tables are skipped, formulas are spoken as words, and quiz questions are read along with their options. |
| 🎙️ **Speech in** | Answer by voice using Claude Code's built-in `/voice` dictation. |
| 🧪 **Tested** | 181 bash tests cover the hooks: log rebuilding, speech cleanup, interrupts, locking and permissions. |

## How it fits together

```
skills/
  teach/       the Probe → Plan → Teach method (/teach)
  visualize/   briefs a maker subagent and embeds the returned diagram
  md-log/      notes on the Obsidian session log
  speak-on/    /speak-on: turn speech out on
  speak-off/   /speak-off: turn speech out off and stop talking
agents/
  researcher.md     web research and fact-checking
  mermaid-maker.md  Mermaid → PNG, render-and-inspect loop
  svg-maker.md      hand-written SVG → PNG, render-and-inspect loop
hooks/
  md-log-stop-hook.sh   rebuilds the Obsidian log from the transcript
  speak*.sh             speech out: speak, clean, interrupt, toggle, quiz voicing
  status-line.sh        optional 🔈/🔊 status-line badge
tests/run.sh            the test suite
```

---

## 🤝 Credits

This project was originally inspired by and adapted from the AI learning system created by [Amos Blomqvist](https://github.com/amosblomqvist) and featured in the video [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU).

---

## How to use it

### 1. Requirements

- **Claude Code**
- **macOS**, for speech out (it uses the built-in `say` command). Everything else should work wherever the dependencies below are installed.
- A microphone if you want to use `/voice`
- **[Obsidian](https://obsidian.md)** for reading the session notes
- Command-line dependencies:
  ```bash
  brew install mermaid-cli librsvg jq
  ```
  `mermaid-cli` provides `mmdc` and `librsvg` provides `rsvg-convert`, which the diagram makers use. The log and speech hooks need `jq`.

### 2. One-time setup: your Obsidian vault

Choose a folder for the vault and create its `viz/` subfolder, where diagrams are saved:

```bash
export OBSIDIAN_VAULT_PATH=~/Obsidian/learn
mkdir -p "$OBSIDIAN_VAULT_PATH/viz"
```

Put the `export` line in your `~/.zshrc` (or `~/.bashrc`) so every new shell has it. If it isn't set, Schola uses `~/Obsidian/learn`.

Then open Obsidian, choose **Open folder as vault**, and select that folder. Session logs appear in `sessions/` and diagrams in `viz/`. Obsidian finds embedded images by filename anywhere in the vault, so diagrams show up inline in the notes.

### 3. Load the plugin

Clone this repo, then start Claude Code with the plugin directory:

```bash
claude --plugin-dir /path/to/learn
```

### 4. Your first session

```
/teach linear regression
```

Use any topic you like. The session runs in three phases:

1. **Probe.** A series of multiple-choice questions in Claude Code's question dialog. They get harder when you're right and narrow down when you're wrong, so expect several rounds, and expect some questions to stump you, since that's how it finds your limit. If you haven't seen something, pick the "haven't encountered this" option instead of guessing. It will also ask what you want to get out of the topic.
2. **Plan.** You get a short explanation of the approach and a Mermaid dependency map, with foundations at the top and your goal at the bottom. Nothing is taught until you approve it, so ask for changes here if the scope or starting point looks wrong.
3. **Teach.** It works through the map one idea at a time: why the idea matters, the idea itself, how it connects to what came before, then a quick quiz. Each answer is graded with an explanation. A miss means the idea gets reworked before the lesson moves on. Diagrams are added where a picture explains better than text.

### 5. The Obsidian log

You don't need to turn the log on. While the plugin is loaded, the log hook runs at the end of every reply, after every answered quiz, and when the session ends. Each time, it rewrites the session's note from the transcript:

```
$OBSIDIAN_VAULT_PATH/sessions/<date> <topic>.md
```

The topic comes from your `/teach` arguments. The note contains your messages, the tutor's replies, each quiz question with its options and **your** answer, and embedded diagrams. Because it is rebuilt from the transcript each time and never appended to, it stays consistent. You can keep it open in Obsidian while you learn.

### 6. Voice

**Speech out**, where the tutor talks:

- `/speak-on` turns it on and `/speak-off` turns it off (and stops any speech in progress).
- Replies are read with macOS `say`. Markdown, code blocks, tables, links and diagrams stay on screen and aren't read aloud. LaTeX and plain math are spoken as words, so $x^2$ is read as "x squared".
- During `/teach` the tutor switches to short spoken turns, one idea each, ending on the check question.
- When a quiz opens, its question is read aloud, then "The options are: …".
- Any new input interrupts speech, whether you type a message or answer a quiz. Only Schola's own speech is stopped, never other `say` processes.

**Speech in**, where you talk:

- Run Claude Code's built-in `/voice`. Hold **Space** while you speak, release it, check the transcript, then press **Enter** to send. Nothing is sent automatically, so you can fix transcription mistakes first.

🎧 **Tip: use headphones.** Otherwise the microphone can pick up the tutor's voice while you dictate.

**Tuning speech:**

| Variable | Default | Effect |
|---|---|---|
| `SPEAK_CMD` | `say` | Replaces the speech command and receives the text on stdin, e.g. `SPEAK_CMD="say -v Samantha -r 190"` |
| `SPEAK_STATE_DIR` | `~/.claude/learn-voice-teacher` | Where the on/off flag and speech state are kept |
| `SPEAK_MAX_CHARS` | `6000` | Longest text spoken from a single reply |

### 7. Optional: status-line badge

Add this to `~/.claude/settings.json` to show **🔈 speech on** while speech is enabled and **🔊 speaking** while it is talking:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /path/to/learn/hooks/status-line.sh",
    "refreshInterval": 2000
  }
}
```

Replace `/path/to/learn` with the absolute path to your clone.

### 8. Run the tests

```bash
bash tests/run.sh            # full suite (181 tests)
bash tests/run.sh md-log     # one group: clean, docs, interrupt, lock, md-log, perm, quiz, static, stop, toggle
```

The suite needs only bash and `jq`. A stub replaces `say`, so it makes no sound and leaves your real settings alone.

### 9. Troubleshooting

- **`/teach` isn't recognized.** Make sure you launched with `claude --plugin-dir /path/to/learn` and that the path points to the folder containing `.claude-plugin/`. If another plugin uses the same skill name, try the namespaced form `/learn-voice-teacher:teach`.
- **No session note in Obsidian.** Check that `jq` is installed (`which jq`) and that `OBSIDIAN_VAULT_PATH` was exported in the shell you started `claude` from. Look in the `sessions/` subfolder, not the vault root. The note is written when a reply finishes or a quiz is answered, not while a reply is still being generated.
- **Diagrams are missing or show as broken embeds.** Check `which mmdc` and `which rsvg-convert`, and make sure `viz/` exists inside the vault that Obsidian has open.
- **Nothing is spoken.** `/speak-on` prints a warning if the speech command can't be found, and speech out requires macOS `say`. To check the current state, run `bash /path/to/learn/hooks/speak-toggle.sh status`. If you set `SPEAK_CMD`, make sure its first word is a command on your `PATH`.
- **A long reply stops partway through.** Spoken text is capped at `SPEAK_MAX_CHARS` (6000 characters by default). Raise the cap or ask for shorter turns.
- **Speech won't stop.** Type anything or run `/speak-off`. Both stop Schola's current speech.
- **`/voice` hears the tutor.** Use headphones, or run `/speak-off` while you dictate.

---

*If you're interested in discussing AI, agentic workflows, or EdTech, feel free to connect!*
