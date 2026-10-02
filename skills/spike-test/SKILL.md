---
name: spike-test
description: Minimal test for PreToolUse/PostToolUse hooks on AskUserQuestion
---

# Hook Spike Test

Testing whether AskUserQuestion fires PreToolUse and PostToolUse hooks.

Ask the user a multiple-choice question using AskUserQuestion, then show the answer and check if hooks logged it.

Use AskUserQuestion with:
- Single-select options: ["Option A", "Option B", "Option C"]
- No "Other" option needed

Repeat 2-3 times with different questions to confirm hooks fire consistently.
