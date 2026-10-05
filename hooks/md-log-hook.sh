#!/bin/bash
# md-log hook: capture AskUserQuestion calls for live md-log mirror to Obsidian vault
# Receives JSON on stdin from Claude Code hooks
# Appends to markdown file in Obsidian vault

# Obsidian vault path (set via env or default)
VAULT_PATH="${OBSIDIAN_VAULT_PATH:=${HOME}/Obsidian/learn}"
LOG_FILE="${VAULT_PATH}/session.md"
STATE_FILE="/tmp/md-log-state.json"

# Ensure vault and log file exist
mkdir -p "$VAULT_PATH"
[ ! -f "$LOG_FILE" ] && echo "# Teaching Session\n\n" > "$LOG_FILE"

# Ensure state file exists
[ ! -f "$STATE_FILE" ] && echo '{}' > "$STATE_FILE"

# Read JSON from stdin
INPUT=$(cat)

# Extract hook event and tool info
EVENT=$(echo "$INPUT" | grep -o '"hook_event_name":"[^"]*' | cut -d'"' -f4)
TOOL=$(echo "$INPUT" | grep -o '"tool_name":"[^"]*' | cut -d'"' -f4)
TOOL_USE_ID=$(echo "$INPUT" | grep -o '"tool_use_id":"[^"]*' | cut -d'"' -f4)

# Only process AskUserQuestion
[ "$TOOL" != "AskUserQuestion" ] && exit 0

# PreToolUse: log the question
if [ "$EVENT" = "PreToolUse" ]; then
  QUESTIONS=$(echo "$INPUT" | grep -o '"questions":\[.*\]' | head -1)
  if [ -n "$QUESTIONS" ]; then
    # Extract first question text
    QUESTION=$(echo "$INPUT" | grep -o '"question":"[^"]*' | head -1 | cut -d'"' -f4)

    # Save pending question ID for this tool_use
    jq --arg id "$TOOL_USE_ID" --arg q "$QUESTION" '.[$id] = {question: $q, answered: false}' "$STATE_FILE" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"

    # Append question to log (if file exists)
    if [ -f "$LOG_FILE" ]; then
      echo "" >> "$LOG_FILE"
      echo "> **Q:** $QUESTION" >> "$LOG_FILE"
    fi
  fi

# PostToolUse: log the answer
elif [ "$EVENT" = "PostToolUse" ]; then
  ANSWERS=$(echo "$INPUT" | grep -o '"answers":{[^}]*}' | head -1)
  if [ -n "$ANSWERS" ]; then
    # Extract the answer value (don't leak the correct answer)
    ANSWER=$(echo "$INPUT" | grep -o '"answers":{[^}]*}' | sed 's/.*: "\([^"]*\)".*/\1/')

    # Mark this tool_use as answered, remove from state
    jq --arg id "$TOOL_USE_ID" 'del(.[$id])' "$STATE_FILE" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"

    # Append answer and teacher's response placeholder
    if [ -f "$LOG_FILE" ]; then
      echo "**A:** $ANSWER" >> "$LOG_FILE"
      echo "" >> "$LOG_FILE"
      echo "> **Teacher:** (response pending)" >> "$LOG_FILE"
    fi
  fi
fi

exit 0
