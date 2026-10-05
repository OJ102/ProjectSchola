#!/bin/bash
# md-log Stop hook: reconstruct session log from transcript JSONL
# Reads the full transcript at session end
# Rebuilds markdown log with Q&A and teacher responses

# Obsidian vault path
VAULT_PATH="${OBSIDIAN_VAULT_PATH:=${HOME}/Obsidian/learn}"
LOG_FILE="${VAULT_PATH}/session.md"

# Ensure vault exists
mkdir -p "$VAULT_PATH"

# Get transcript path from hook input
INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# Parse transcript and rebuild log
{
  echo "# Teaching Session"
  echo ""

  # Read transcript JSONL and extract messages
  jq -r '
    [.[] | select(.type == "message")] |
    .[] |
    if .role == "user" then
      "> **User:** \(.content)"
    elif .role == "assistant" then
      "> **Teacher:** \(.content)"
    else
      empty
    end
  ' "$TRANSCRIPT_PATH" 2>/dev/null

  # Also extract AskUserQuestion Q&A
  jq -r '
    [.[] | select(.type == "tool_result")] |
    .[] |
    select(.tool_name == "AskUserQuestion") |
    "**Q:** \(.tool_input.questions[0].question // "Question")\n**A:** \(.tool_response.answers[.tool_input.questions[0].question] // "Answer pending")"
  ' "$TRANSCRIPT_PATH" 2>/dev/null

} > "$LOG_FILE"

exit 0
