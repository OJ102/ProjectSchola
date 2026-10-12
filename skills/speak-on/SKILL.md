---
name: speak-on
description: Turn on speech out, so the teacher's replies are read aloud with macOS `say`. Use only when the user runs /speak-on.
disable-model-invocation: true
allowed-tools: Bash(bash *speak-toggle.sh on)
---

# speak-on

Run this command, then reply with one short line confirming speech is on:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/speak-toggle.sh" on
```

If it prints a warning that `say` is missing, tell the user that speech out needs macOS.

From now on your replies are spoken. If a `/teach` session is running, follow its **Voice mode** section.
Mention once that headphones stop the mic from hearing the teacher when `/voice` is in use, and that `/speak-off` turns speech off again.
