#!/bin/bash
# PostToolUse on AskUserQuestion, teach sessions only. Thinking is never shown or logged, so a table,
# example or grading explanation written only there never reaches the learner.
# - Always: remind the tutor to grade this answer in visible reply text.
# - Repair: if the question just answered was asked with no visible text since the previous answer
#   or prompt, tell the tutor to write out now what it drafted only in thinking.
# A nudge, not a block: PreToolUse can't see the reply text (assistant entries reach the transcript
# after PreToolUse runs), but by PostToolUse the question's message is there. Fails open.
python3 -I -c '
import json, re, sys
try:
    d = json.load(sys.stdin)
    path = d.get("transcript_path") or ""
except (ValueError, AttributeError):
    sys.exit(0)
TEACH_CMD = re.compile(r"<command-name>[^<]*teach</command-name>")
teach = False
seen_text = True        # visible text since the last anchor (a real prompt or a quiz answer)
bare_question = None    # text of the latest question if it was asked with no visible text before it

def is_answer(e):
    r = e.get("toolUseResult")
    return isinstance(r, dict) and isinstance(r.get("questions"), list)

try:
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("isSidechain"):
                continue
            msg = e.get("message")
            c = msg.get("content") if isinstance(msg, dict) else None
            if e.get("type") == "user":
                if isinstance(c, str) and not e.get("isMeta"):
                    if TEACH_CMD.search(c):
                        teach = True
                    if TEACH_CMD.search(c) or not c.lstrip().startswith("<"):
                        seen_text = False
                elif is_answer(e):
                    seen_text = False
                elif isinstance(c, list) and not e.get("isMeta") and \
                        any(isinstance(b, dict) and b.get("type") == "text" for b in c) and \
                        not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                    seen_text = False
            elif e.get("type") == "assistant" and isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and str(b.get("text") or "").strip():
                        seen_text = True
                    elif b.get("type") == "tool_use" and b.get("name") == "Skill" and \
                            str((b.get("input") or {}).get("skill") or "").split(":")[-1] == "teach":
                        teach = True
                    elif b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                        qs = (b.get("input") or {}).get("questions") or [{}]
                        bare_question = None if seen_text else str((qs[0] or {}).get("question") or "")[:200]
except OSError:
    sys.exit(0)
if not teach:
    sys.exit(0)
msg = ("Open your next reply with visible text grading this answer: right or wrong, the correct option, "
       "and 1-3 sentences on why (and why his choice is wrong, if it was). Your reply must begin with a "
       "text block; never send AskUserQuestion in a message with no text before it. Drafting a table, "
       "example or explanation in thinking is not writing it: he sees none of your thinking.")
if bare_question is not None:
    msg = ("The question just answered (" + json.dumps(bare_question) + ") was asked with NO visible text "
           "before it. The transcript shows no text block between the previous answer and that question: "
           "whatever grading, table or example you prepared exists only in your thinking. Nothing hid it; "
           "it was never written, and the AskUserQuestion dialog does not hide text that is written. "
           "First write all of it out now as visible text (a markdown table if you planned one). His answer "
           "was given without that content, so grade it lightly and ask that question again after the "
           "content. " + msg)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
'
