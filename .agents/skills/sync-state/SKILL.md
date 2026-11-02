---
name: sync-state
description: Use when the project's phase, file layout, or run commands change, or when the user says "update state". Keeps notes/STATE.md as a tiny project map so agents don't have to explore the codebase.
---

# Sync state

`notes/STATE.md` is the one file any agent reads to understand the project fast.
It must stay under 40 lines.

Rewrite it with exactly these sections:
```
# State
**Phase:** N — name (status)
**Run it:** the 1–3 commands that matter now
## Files
- path — one-line purpose   (only files that exist, skip notes/ and tests)
## Known issues
- ... (or "none")
```
Base it on what is actually in the repo (`ls`, `git ls-files`), not on memory.
Don't copy the content of PLAN.md here. Link to it instead: "see [[PLAN]]".
