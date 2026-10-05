#!/bin/bash
# Status line hook: show md-log indicator when logging is active

STATE_FILE="/tmp/md-log-state.json"

# Check if md-log is active (state file exists and is not empty)
if [ -f "$STATE_FILE" ] && [ -s "$STATE_FILE" ]; then
  # Count pending questions
  COUNT=$(jq 'length' "$STATE_FILE" 2>/dev/null || echo 0)
  if [ "$COUNT" -gt 0 ]; then
    echo "🗒️ md-log active ($COUNT Q pending)"
  fi
else
  echo ""
fi
