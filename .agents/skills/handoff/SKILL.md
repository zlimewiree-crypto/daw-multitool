---
name: handoff
description: Use at the END of any work session, or when the user says "handoff", "pass to Claude", "pass to Codex", or "switching agents". Writes a short note so the other agent can continue without re-reading the project.
---

# Handoff

Claude and Codex take turns on this project. The handoff note is how they pass
work without either one re-exploring the codebase (that wastes tokens).

## Write a handoff
1. Run `git status --short` and `git diff --stat` (NOT the full diff).
2. Add a new entry at the TOP of `notes/HANDOFF.md`, using this exact shape and staying under 12 lines:

```
## <YYYY-MM-DD HH:MM> — <Claude|Codex> → <Claude|Codex>
**Did:** one or two lines
**Files:** comma-separated paths that changed
**Tests:** what you ran + result (or "not run")
**Next:** the single next step, concrete
**Watch out:** anything risky or half-done (or "nothing")
```
3. Keep only the 5 newest entries in HANDOFF.md, and delete older ones.
4. Run the `sync-state` skill if the phase, file layout, or commands changed.
5. Suggest a commit message to the user.

## Read a handoff (start of session)
Read the top entry of `notes/HANDOFF.md` and `notes/STATE.md`, and nothing else
until the task needs it. If `notes/LIVE_SESSION.md` was updated after that entry, read it
too: Claude stopped mid-task (see "When an agent runs out of credits" in AGENTS.md).
Say in one line what you're picking up.
