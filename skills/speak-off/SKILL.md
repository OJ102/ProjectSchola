---
name: speak-off
description: Turn off speech out and stop any reply that is being read aloud. Use only when the user runs /speak-off.
disable-model-invocation: true
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh" off)
---

# speak-off

Run this command, then reply with one short line confirming speech is off:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh" off
```

Go back to normal (non-voice) formatting.
