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

  # Extract text content from assistant messages
  jq -r '
    select(.type == "assistant") |
    .message.content[] |
    select(.type == "text") |
    .text
  ' "$TRANSCRIPT_PATH" 2>/dev/null |
  # Clean up and format
  sed 's/^$/\n/g'

} > "$LOG_FILE"

exit 0
