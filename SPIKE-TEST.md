# Phase 1 Spike Tests

Two critical unknowns gate the Phase 1 port design. Run both spikes before proceeding.

## Spike 1: Do hooks fire for AskUserQuestion?

**Goal:** Confirm whether `PreToolUse` and `PostToolUse` hooks fire when `AskUserQuestion` is called. This determines how to build md-log.

**Setup:**
```bash
cd /Users/omjadhav/Documents/--Projects--/learn
rm -f /tmp/hook-spike.log
claude --plugin-dir . &
# Wait for Claude Code to start
```

**Test:**
1. Start a new session (this loads the plugin)
2. Type: `/teach Python`
3. Answer the probe questions when asked
4. When a quiz appears (via `AskUserQuestion`), pick an answer
5. Wait for the teacher's reply
6. Repeat for 2-3 questions

**Check result:**
```bash
cat /tmp/hook-spike.log
```

If hooks fired, you'll see JSON objects in the log (one per hook event). If the file is empty or doesn't exist, hooks did not fire, and md-log must rebuild from the transcript.

**What to report:**
- Did `/tmp/hook-spike.log` get created?
- How many lines (one line = one hook event)?
- For each line, does it contain `"tool_name": "AskUserQuestion"`?

---

## Spike 2: Does MCP elicitation form render as a quiz UI?

**Goal:** Test whether an MCP server can return an elicitation form with a select enum and note field, and whether it renders as an interactive quiz UI in Claude Code.

**Setup:**
The `.mcp.json` is already configured to run `quiz-mcp-spike.js`. No extra setup needed.

**Test:**
1. Start Claude Code with the plugin:
   ```bash
   claude --plugin-dir /Users/omjadhav/Documents/--Projects--/learn
   ```
2. In a session, type:
   ```
   Call the quiz tool with a test question: "What is 2+2?" with options ["3", "4", "5"] and correctIndex=1
   ```
3. Observe what appears on screen.

**What to report:**
- Did the quiz tool get called successfully?
- Did an interactive form appear, or just text output?
- If a form appeared, could you select an option and submit it?
- Did the form have a note/comment field?

---

## If spikes fail

- **Spike 1 (no hooks):** md-log will rebuild from the transcript JSONL instead of using hooks. This works but may lose question-before-answer ordering if the transcript doesn't preserve it.
- **Spike 2 (no form):** the quiz MCP server will fall back to `AskUserQuestion` + a `quiz_grade(selected)` tool instead of an elicitation form. Still graded and shuffled server-side, just less polished UI.

Both fallbacks are acceptable; they just change Phase 2's implementation details.

**When you've run both spikes, report the results and we'll move to Phase 1 port steps.**
