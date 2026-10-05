#!/bin/bash
# md-log message_end hook: capture teacher responses
# Receives JSON on stdin from Claude Code hooks
# Appends assistant messages (teacher explanations) to markdown file

# Obsidian vault path
VAULT_PATH="${OBSIDIAN_VAULT_PATH:=${HOME}/Obsidian/learn}"
LOG_FILE="${VAULT_PATH}/session.md"

# Ensure log file exists
mkdir -p "$VAULT_PATH"
[ ! -f "$LOG_FILE" ] && printf "# Teaching Session\n\n" > "$LOG_FILE"

# Read JSON from stdin
INPUT=$(cat)

# Extract message content
ROLE=$(echo "$INPUT" | jq -r '.message.role // empty' 2>/dev/null)

# Only log assistant (teacher) messages
if [ "$ROLE" = "assistant" ]; then
  CONTENT=$(echo "$INPUT" | jq -r '.message.content // empty' 2>/dev/null)

  if [ -n "$CONTENT" ]; then
    # Strip markdown code blocks and keep only prose
    # (Don't log tool calls, just the teaching text)
    CLEAN_CONTENT=$(echo "$CONTENT" | sed '/^```/,/^```/d')

    # Only log if there's substance (not just whitespace or tool calls)
    if echo "$CLEAN_CONTENT" | grep -qv '^[[:space:]]*$'; then
      {
        echo ""
        echo "> **Teacher:** $CLEAN_CONTENT"
      } >> "$LOG_FILE"
    fi
  fi
fi

exit 0
