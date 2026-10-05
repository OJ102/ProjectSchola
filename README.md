# learn

[![video](assets/thumbnail.png)](https://www.youtube.com/watch?v=kzcI5F4tGiU)

My AI learning system from this video: [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU).

This is a personal system I built for myself, shared as-is. Originally built for the Pi coding agent, now **ported to Claude Code** with built-in voice support.

## What's in it

- `skills/teach/` — the teaching philosophy and the probe → plan → teach process
- `skills/visualize/` — adds a correct, minimal diagram to a lesson when an idea is clearer as a picture
- `skills/md-log/` — live markdown mirror of the session (questions logged before answers)
- `agents/` — `researcher` (verifies facts), `svg-maker` and `mermaid-maker` (create diagrams)
- `hooks/` — capture teaching session into markdown for Obsidian (live mirror)

## Install (Claude Code)

1. Set Obsidian vault path (one-time):
   ```bash
   export OBSIDIAN_VAULT_PATH=~/Obsidian/learn
   mkdir -p ~/Obsidian/learn/viz
   ```
2. Open Obsidian and point it to `~/Obsidian/learn`
3. Load the plugin:
   ```bash
   claude --plugin-dir /path/to/learn
   ```
4. In Claude Code session: `/teach Python` (or any topic)

**Optional: status line indicator**

Add to `.claude/settings.json` or `.claude/settings.local.json`:
```json
{
  "statusLine": {
    "command": "bash /path/to/learn/hooks/status-line.sh",
    "refreshInterval": 2000
  }
}
```

Shows 🗒️ indicator when md-log is active.

## Requirements

- **Claude Code** v2.1.76+ (for hook support)
- **macOS** (for `say` command in voice mode; requires a recent Claude Code version with voice support)
- `mmdc` (Mermaid CLI, for diagram rendering):
  ```bash
  brew install mermaid-cli
  ```
- `rsvg-convert` (for SVG rendering; usually via ImageMagick):
  ```bash
  brew install librsvg
  ```
- `jq` (for JSON parsing in hooks):
  ```bash
  brew install jq
  ```

## Features

- **Probe → plan → teach loop:** interactive session that finds your knowledge edge, plans around it, then teaches
- **Built-in voice** (Claude Code `/voice`): hold space to speak, listen to the teacher reply
- **Live markdown log:** session is mirrored to Obsidian-formatted markdown (`md-log` skill)
- **Diagrams:** system can generate dependency graphs (Mermaid) or geometric visuals (SVG)
- **Research verification:** researcher agent checks facts when needed

## Notes

The teaching skill is written for one learner (me). Edit `skills/teach/SKILL.md` to fit how you learn best.

**Pi version:** the original Pi configuration is still in `extensions/` and `.pi/`. This Claude Code port reuses `skills/` and `agents/` but replaces the Pi extensions with Claude Code hooks and built-in tools.
