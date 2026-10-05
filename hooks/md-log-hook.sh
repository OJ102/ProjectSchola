#!/bin/bash
# md-log hook: capture AskUserQuestion and assistant messages for live md-log mirror to Obsidian vault
# Receives JSON on stdin from Claude Code hooks
# Appends questions, answers, and teacher responses to markdown file

# Obsidian vault path (set via env or default)
VAULT_PATH="${OBSIDIAN_VAULT_PATH:=${HOME}/Obsidian/learn}"
LOG_FILE="${VAULT_PATH}/session.md"
STATE_FILE="/tmp/md-log-state.json"

# Ensure vault and log file exist
mkdir -p "$VAULT_PATH"
[ ! -f "$LOG_FILE" ] && printf "# Teaching Session\n\n" > "$LOG_FILE"
[ ! -f "$STATE_FILE" ] && echo '{}' > "$STATE_FILE"

# Read JSON from stdin
INPUT=$(cat)

# Extract hook event and tool info
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
TOOL_USE_ID=$(echo "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null)

# PreToolUse: log the question (AskUserQuestion only)
if [ "$EVENT" = "PreToolUse" ] && [ "$TOOL" = "AskUserQuestion" ]; then
  # Extract first question text from tool_input
  QUESTION=$(echo "$INPUT" | jq -r '.tool_input.questions[0].question // empty' 2>/dev/null)

  if [ -n "$QUESTION" ]; then
    # Save pending question ID so we can match it with the answer
    jq --arg id "$TOOL_USE_ID" --arg q "$QUESTION" '.[$id] = {question: $q, answered: false}' "$STATE_FILE" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"

    # Append question to log
    {
      echo ""
      echo "> **Q:** $QUESTION"
    } >> "$LOG_FILE"
  fi

# PostToolUse: log the answer (AskUserQuestion only)
elif [ "$EVENT" = "PostToolUse" ] && [ "$TOOL" = "AskUserQuestion" ]; then
  # Extract the answer value from tool_response
  ANSWER=$(echo "$INPUT" | jq -r '.tool_response.answers | to_entries[0].value // empty' 2>/dev/null)

  if [ -n "$ANSWER" ]; then
    # Mark this tool_use as answered, remove from state
    jq --arg id "$TOOL_USE_ID" 'del(.[$id])' "$STATE_FILE" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"

    # Append answer to log (clean text, not raw JSON)
    echo "**A:** $ANSWER" >> "$LOG_FILE"
  fi
fi

exit 0
