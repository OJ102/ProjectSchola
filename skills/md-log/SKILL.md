---
name: md-log
description: Manage the live markdown log of the teaching session. Use /md-log to start logging, /md-unlog to stop.
---

# md-log

Live mirror of the teaching session to a markdown file. Questions appear before answers. No correct answer is ever leaked into the log.

## Commands

### /md-log [filename]

Start (or restart) the markdown log file. If no filename given, uses the default `session.md` in `~/.claude/projects/<project>/`.

The log file is created in Obsidian vault format. It captures:
- User prompts (as blockquotes)
- Teacher replies (as prose)
- Questions and answers (Q before A, no grading revealed)
- Diagrams (as wikilink embeds)

If the file already exists, appends to it.

### /md-unlog

Stop logging. The file remains and is not deleted.

## Behavior

- **Question before answer:** the hook logs the question on `PreToolUse`, then the user's answer on `PostToolUse`. This ordering is preserved in the markdown.
- **No answer leak:** the correct answer is NEVER written to the log. Only the question and the user's selected answer appear.
- **Live append-only:** each turn is appended as it happens. No re-rendering or back-fill.
- **Restore after restart:** if Claude Code session restarts, the log file persists and new appends continue from where it left off.

## Format

Log uses Obsidian markdown:
- User prompt: `> **User:** question text`
- Teacher reply: plain prose
- Question: `> **Q:** question text`
- Answer: `**A:** user's choice`
- Diagram embed: `![[viz-name.png|500]]`
- LaTeX: inline `$x^2$` and display `$$x^2$$`

## Implementation note

The hooks (`PreToolUse`, `PostToolUse` on `AskUserQuestion`) pipe to `md-log-hook.sh`, which reads the hook input JSON and appends the question/answer to the log file. A state file tracks pending questions by tool_use_id to ensure question-before-answer ordering.
