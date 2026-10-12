# Project Schola

**Project Schola** is an advanced, AI-driven personal learning ecosystem that adapts to your knowledge level in real-time. Built to transform how we learn complex topics, it uses autonomous agents to probe your understanding, build a customized curriculum, and teach interactively.

> **Developed by:** [Om Jadhav](https://github.com/OJ102)

## 🚀 Why Project Schola?

In a world overflowing with static tutorials, Project Schola stands out by offering a **dynamic, conversational learning experience**. As a developer passionate about AI and education tech, I built this system to showcase how Large Language Models can be orchestrated to act as personalized tutors.

### Key Innovations:
- **Adaptive Teaching Loop (Probe → Plan → Teach):** The system doesn't just lecture; it assesses your current knowledge edge first, creates a targeted learning plan, and then delivers bite-sized interactive lessons.
- **Multi-Agent Architecture:** Utilizes specialized sub-agents:
  - `researcher`: Fact-checks and gathers up-to-date information.
  - `svg-maker` & `mermaid-maker`: Dynamically generate visual aids and diagrams when concepts are better explained visually.
- **Live Markdown Sync:** Automatically captures the entire learning session into a well-formatted Obsidian markdown vault.
- **Voice Integration:** Built-in voice support for a seamless, hands-free conversational learning experience.

---

## 🛠️ Tech Stack & Architecture

- **Core Engine:** AI Agentic Workflows
- **Visuals Rendering:** Mermaid CLI (`mmdc`), SVG (`rsvg-convert`)
- **Note Management:** Live Obsidian integration via custom shell hooks and `jq`

## 📦 Installation & Setup

1. **Set your Obsidian vault path (one-time):**
   ```bash
   export OBSIDIAN_VAULT_PATH=~/Obsidian/learn
   mkdir -p ~/Obsidian/learn/viz
   ```
2. Open Obsidian and point it to `~/Obsidian/learn`
3. **Load the plugin:**
   ```bash
   claude --plugin-dir /path/to/ProjectSchola
   ```
4. **Start Learning:**
   In your session, simply type: `/teach Python` (or any topic of your choice)

### Optional: Status Line Indicator
Add the following to your settings to show a 🗒️ indicator when the markdown logger is active and a 🔈/🔊 speech badge:
```json
{
  "statusLine": {
    "command": "bash /path/to/ProjectSchola/hooks/status-line.sh",
    "refreshInterval": 2000
  }
}
```

## 🎙️ Voice mode

Talk to the teacher and hear it answer back.

- **Speech in:** use Claude Code's built-in `/voice` dictation. Hold **Space** to talk, release, then press **Enter** to send. It doesn't auto-submit, so you can fix the transcript first.
- **Speech out:** run `/speak-on` to have every reply read aloud with macOS `say`, and `/speak-off` to stop. Markdown, code, tables, links, LaTeX and diagrams are left out of what's spoken (they stay on screen). In a `/teach` session the teacher switches to short, spoken-style turns that end on the check question.
- **Interrupting:** submitting new input stops the reply that is being read. Only this plugin's own speech is stopped, never other `say` processes.
- **Use headphones** so the microphone doesn't pick up the teacher's voice.
- With the status line below enabled, it shows 🔈 when speech is on and 🔊 while speaking.

Speech state is kept in `~/.claude/learn-voice-teacher/` (override with `SPEAK_STATE_DIR`). `SPEAK_CMD` replaces `say`, for example `SPEAK_CMD="say -v Samantha -r 190"`. The text arrives on stdin.

## ⚙️ System Requirements

- **macOS** for speech out (`say`); a microphone for `/voice`
- **Dependencies:**
  ```bash
  brew install mermaid-cli librsvg jq
  ```

## 🧠 The Philosophy

This project is a testament to the power of **Agentic AI** in education. By breaking down the teaching process into modular skills (`skills/teach/`, `skills/visualize/`, `skills/md-log/`, `skills/speak-on/`, `skills/speak-off/`), the system remains extensible and highly customizable. 

Feel free to explore the code, test the teaching loop, and see how AI can be engineered to build intelligent, state-aware applications.

---

## 🤝 Credits

This project was originally inspired by and adapted from the AI learning system created by [Amos Blomqvist](https://github.com/amosblomqvist) and featured in the video [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU). 

---
*If you're interested in discussing AI, agentic workflows, or EdTech, feel free to connect!*
