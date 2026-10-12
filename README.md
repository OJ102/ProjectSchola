<div align="center">

# 🎓 Project Schola
### *The Autonomous Socratic AI Tutor for Claude Code*

**Stop drowning in static documentation and 20-hour video courses.**  
Schola transforms your terminal into a world-class, adaptive 1-on-1 private tutor. It finds the exact edge of your knowledge, drafts a transparent learning contract, builds intuition from first principles, and logs the entire journey directly into your Obsidian second brain.

[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-purple?style=flat-square&logo=anthropic)](https://claude.com/claude-code)
[![Platform macOS](https://img.shields.io/badge/Platform-macOS-black?style=flat-square&logo=apple)](https://apple.com)
[![Knowledge Base Obsidian](https://img.shields.io/badge/Notes-Obsidian-blueviolet?style=flat-square&logo=obsidian)](https://obsidian.md)
[![Test Suite](https://img.shields.io/badge/Tests-180%2B%20Passing-success?style=flat-square&logo=checkmarx)](tests/run.sh)
[![License MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](#)

[Why Schola](#-the-schola-difference) • [Features](#-features-built-for-deep-mastery) • [Architecture](#-how-it-works) • [Quickstart](#-2-minute-quickstart) • [Voice Mode](#-hands-free-voice-tutor) • [Obsidian Sync](#-the-live-obsidian-knowledge-base) • [Troubleshooting](#-troubleshooting)

---

</div>

## 💡 The Schola Difference

Most learning resources fail because they pitch to an imaginary "average" learner. Static articles move too slowly on fundamentals you already grasp, then accelerate recklessly over the exact concepts you miss. Chatbots compound the problem by nodding along, generating walls of unverified text, and never checking if you actually absorbed what was said.

**Project Schola flips the paradigm from passive consumption to verified active mastery:**

```
                                  ┌────────────────────────┐
                                  │   1. ADAPTIVE PROBE    │
                                  │ Calibrates boundary    │
                                  └───────────┬────────────┘
                                              │
                                              ▼
                                  ┌────────────────────────┐
                                  │   2. VISUAL BLUEPRINT  │
                                  │ Dependency map & plan  │
                                  └───────────┬────────────┘
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                           ▼
             ┌─────────────────────┐                     ┌─────────────────────┐
             │  3. FIRST-PRINCIPLE │ ◄── [Missed Quiz] ──┤   4. MULTI-CHOICE   │
             │     SCAFFOLDING     │                     │     MASTERY CHECK   │
             └──────────┬──────────┘ ──── [Passed Quiz] ─►└─────────────────────┘
                        │
                        ▼
             ┌─────────────────────┐
             │ 5. VAULT COMPILATION│
             │ Notes, Math, SVGs   │
             └─────────────────────┘
```

| Traditional Tutorials | Standard AI Chatbots | Project Schola |
| :--- | :--- | :--- |
| **Fixed Pacing**: One size fits none | **Rambling Explanations**: Dumps 2,000 words without pause | **Adaptive Calibration**: Finds where your knowledge ends before speaking |
| **No Accountability**: Illusion of competence from skimming | **Rhetorical Questions**: Doesn't verify your understanding | **Interactive Checks**: Real multi-choice dialogs that block until mastered |
| **Static Text**: Generic, uninspected diagrams | **Text-Only**: Cannot generate and verify custom visuals | **Autonomous Visualizers**: Generates, inspects, and embeds SVG/Mermaid visuals |
| **Manual Note-Taking**: Friction-heavy context switching | **Lost Ephemeral Chats**: History trapped in disposable chat windows | **Zero-Friction Vault**: Living Obsidian notes compiled automatically |

---

## ⚡ Features Built for Deep Mastery

### 🎯 1. Adaptive Probe → Plan → Teach Flywheel
Schola doesn't guess what you know. When you run `/teach <topic>`, it initiates a diagnostic questionnaire. Questions dynamically escalate in difficulty as you answer correctly and narrow in when you falter. Once your frontier is established, Schola presents a visual DAG dependency map of the concepts needed to reach your goal. Nothing is taught until you approve the roadmap.

### 🧩 2. Formative Multi-Choice Mastery Checks
No hand-waving, rhetorical questions, or trivia traps. Every micro-concept ends with a structured question via Claude Code's native `AskUserQuestion` interactive interface.
- Answer choices are engineered to eliminate superficial giveaway wording.
- A dedicated *"I haven't encountered this yet"* option guarantees genuine calibration without guessing.
- If you miss a question, Schola pivots, addresses the misconception with an alternative mental model, and re-evaluates before building higher-level abstractions on shaky ground.

### 🤖 3. Autonomous Visualizer Subagents
A picture is worth a thousand tokens. When visual intuition accelerates comprehension, Schola delegates to specialized subagents:
- **`mermaid-maker`**: Renders dependency graphs, state machines, sequence flows, and system architectures.
- **`svg-maker`**: Synthesizes geometric proofs, function plots, coordinate frames, and number lines.
- **Iterative Visual Inspection Loop**: Each diagram is rendered, visually verified, repaired if broken, and exported as a crisp image to your Obsidian `viz/` directory.

### 📓 4. Live Obsidian Knowledge Base Sync
Every exchange, mathematical proof in $\LaTeX$, code snippet, answered quiz, and generated diagram is automatically compiled into your Obsidian vault in real time via an idempotent transcript hook:
- Notes are created under `sessions/<date> <topic>.md`.
- No messy copy-pasting—your personal second brain grows richer with every study session.

### 🎙️ 5. Zero-Latency Hands-Free Voice Mode
Turn your terminal into a live conversational seminar with bi-directional voice:
- **Speech Out**: Natural spoken delivery via macOS `say`. Schola automatically strips Markdown, code fences, and tabular clutter, speaking formulas phonetically (e.g., $x^2$ becomes *"x squared"*).
- **Speech In**: Answer questions hands-free using Claude Code's native `/voice` dictation.
- **True Interruptibility**: Typing or speaking instantly halts the tutor mid-sentence—no runaway audio.

### 🧪 6. Test-Driven Engineering Rigor
Schola isn't a loose collection of prompts. It is an engineered system backed by **180+ automated regression tests** covering hook lifecycle events, interrupt handlers, concurrency locking, process tree reaping, and permission sandboxing.

---

## 🏗️ How It Works

Schola is organized into an extensible modular architecture:

```
├── skills/
│   ├── teach/        # Core Probe → Plan → Teach pedagogy engine (/teach)
│   ├── visualize/    # Autonomous subagent orchestrator for diagrams
│   ├── md-log/       # Obsidian session log manager
│   ├── speak-on/     # /speak-on: Activates voice output
│   └── speak-off/    # /speak-off: Deactivates voice output & halts speech
├── agents/
│   ├── researcher.md     # Web research and deep fact-checking agent
│   ├── mermaid-maker.md  # Self-healing Mermaid → PNG generator
│   └── svg-maker.md      # Self-healing vector SVG → PNG generator
├── hooks/
│   ├── md-log-stop-hook.sh   # Rebuilds clean Obsidian notes from session transcript
│   ├── speak*.sh             # Audio engine: clean, synthesize, interrupt, quiz voicing
│   └── status-line.sh        # Live status-line indicator (🔈/🔊)
└── tests/
    ├── run.sh        # Comprehensive test harness (180+ tests)
    └── spikes.sh     # Precision verification & edge-case test suite
```

---

## 🚀 2-Minute Quickstart

### Step 1: Install System Prerequisites

Schola requires macOS (for built-in speech synthesis) and a few lightweight CLI utilities for diagram rendering and JSON processing:

```bash
brew install mermaid-cli librsvg jq
```

* `mermaid-cli` (`mmdc`): Renders Mermaid diagrams to PNG.
* `librsvg` (`rsvg-convert`): Converts custom vector SVGs to high-resolution images.
* `jq`: Powers fast, robust hook data transformation.

---

### Step 2: Configure Your Obsidian Vault

Point Schola to your Obsidian vault directory (or create a new dedicated learning vault):

```bash
# Add to ~/.zshrc or ~/.bashrc for persistence:
export OBSIDIAN_VAULT_PATH=~/Obsidian/learn
mkdir -p "$OBSIDIAN_VAULT_PATH/viz"
```

Open [Obsidian](https://obsidian.md), choose **Open folder as vault**, and select `~/Obsidian/learn`. All session notes will seamlessly appear under `sessions/` and visual diagrams under `viz/`.

---

### Step 3: Launch Claude Code with Schola

Clone this repository and point Claude Code to the plugin:

```bash
git clone https://github.com/OJ102/ProjectSchola.git ~/learn
claude --plugin-dir ~/learn
```

---

### Step 4: Start Your First Mastery Session

Begin learning any topic under the sun:

```
/teach Eigenvalues and Eigenvectors
```
*(Or try `/teach Transformer Attention Mechanisms`, `/teach Rust Borrow Checker`, or `/teach Distributed Consensus`)*

1. **Answer the diagnostic questions** to pinpoint where your current knowledge boundaries lie.
2. **Review your customized dependency roadmap** and approve or tweak the lesson plan.
3. **Ascend the concept tree** with Socratic dialogue, visual aids, and interactive checks at each step.

---

## 🎙️ Hands-Free Voice Tutor

Experience learning without staring at text walls.

```
/speak-on    # Activate spoken voice output
/speak-off   # Deactivate voice output & silence immediate audio
```

- **Natural Speech Synthesis**: Code blocks, raw URLs, and tables remain visually pinned to your screen while explanations and math are spoken fluently.
- **Dictation Input**: Run Claude Code's native `/voice` command. Hold **Space** to speak, release, inspect the transcription, and hit **Enter**.
- 🎧 **Pro Tip**: Use **headphones** during voice mode to prevent your microphone from capturing the tutor's spoken replies during dictation.

### Voice Engine Configuration

Fine-tune speech behavior via environment variables in your shell configuration:

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `SPEAK_CMD` | `say` | Custom voice synthesis command. E.g.: `SPEAK_CMD="say -v Samantha -r 195"` |
| `SPEAK_STATE_DIR` | `~/.claude/learn-voice-teacher` | Path holding runtime lockfiles, flags, and process state |
| `SPEAK_MAX_CHARS` | `6000` | Maximum character threshold spoken per turn |

### Live Status-Line Indicator

Display real-time speech indicators in your Claude Code status bar (**🔈 speech on** when idle, **🔊 speaking** when audio is active). Add this snippet to your `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /path/to/learn/hooks/status-line.sh",
    "refreshInterval": 2000
  }
}
```
*(Replace `/path/to/learn` with the absolute path to your repository).*

---

## 📓 The Live Obsidian Knowledge Base

You never have to pause a learning session to jot down notes.

```
$OBSIDIAN_VAULT_PATH/
├── sessions/
│   └── 2026-10-12 Linear Regression.md   <-- Automatically rebuilt in real-time
└── viz/
    └── residual-sum-of-squares.png       <-- Rendered & visually verified diagrams
```

Because the session note is compiled cleanly from the execution transcript at the end of every reply, it provides an immaculate, chronological record containing:
- Mathematical proofs formatted in clean LaTeX blocks.
- Inline visual representations rendered by the subagent team.
- Exact quiz questions, the options provided, your recorded answer, and the accompanying explanation.

---

## 🛠️ Verification & Test Suite

Schola is built with uncompromising software engineering standards. Run the automated test suite locally to verify complete system integrity:

```bash
# Run the complete test suite (180+ tests)
bash tests/run.sh

# Run specific functional test suites
bash tests/run.sh md-log     # Obsidian log generation
bash tests/run.sh quiz       # Quiz voicing & UI mechanics
bash tests/run.sh toggle     # Voice toggle & status engine
bash tests/run.sh interrupt  # Audio interrupt & process lifecycle
```

---

## 🔍 Troubleshooting

<details>
<summary><b>Command <code>/teach</code> is not recognized</b></summary>

Ensure Claude Code was started with the `--plugin-dir` flag pointing directly to your repository clone containing `.claude-plugin/`:
```bash
claude --plugin-dir /path/to/learn
```
If a conflicting plugin shares the skill name, execute via the fully qualified namespace: `/learn-voice-teacher:teach`.
</details>

<details>
<summary><b>Session notes do not appear in Obsidian</b></summary>

1. Confirm `jq` is installed and reachable on your PATH (`which jq`).
2. Verify `OBSIDIAN_VAULT_PATH` was exported in the same terminal shell prior to launching `claude`.
3. Check the `sessions/` directory inside your vault. Notes are generated immediately following a completed assistant response or answered quiz question.
</details>

<details>
<summary><b>Diagrams are missing or show broken image links</b></summary>

Verify diagram dependencies:
```bash
which mmdc && which rsvg-convert
```
Ensure the target directory `$OBSIDIAN_VAULT_PATH/viz` exists so subagents can persist rendered PNGs.
</details>

<details>
<summary><b>No audio output during voice mode</b></summary>

1. Audio output requires macOS `say`. Verify your speech status:
   ```bash
   bash hooks/speak-toggle.sh status
   ```
2. If `SPEAK_CMD` is customized, verify the command binary exists in your `$PATH`.
</details>

<details>
<summary><b>Tutor audio triggers microphone feedback</b></summary>

Always wear headphones when pairing `/speak-on` with `/voice` dictation, or toggle audio off temporarily with `/speak-off`.
</details>

---

## 🤝 Credits & Philosophy

Project Schola was inspired by and adapted from the AI learning workflows pioneered by [Amos Blomqvist](https://github.com/amosblomqvist), showcased in [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU).

Architected and maintained by **[Om Jadhav](https://github.com/OJ102)**.

*Building at the intersection of agentic systems, cognitive science, and human potential.*  
If you have feedback, ideas, or want to collaborate, feel free to open an issue or connect!
