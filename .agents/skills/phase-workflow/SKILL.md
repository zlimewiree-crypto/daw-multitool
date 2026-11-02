---
name: phase-workflow
description: Use when the user says "do phase N", "next phase", "what's next", or asks to build a feature from notes/PLAN.md. Keeps work to one small, tested step at a time for beginner coders.
---

# Phase workflow

The user is new to coding, so small, working steps beat big rewrites.

## Steps
1. **Read** `notes/PLAN.md` and find the phase. If the user didn't name one, pick
   the first unchecked phase and confirm it with them in one line.
2. **Plan out loud** in 3–6 bullets: what files you'll create or change and why.
   Wait for "ok"/"go" if the change touches more than 2 files.
3. **Build** the smallest version that works. No extra features.
4. **Test** using the `make-test-audio` skill: plumbing on synthetic files, accuracy with
   `scripts/check_drums.py` on `test_exports/real/`. Show the output.
5. **Explain** what changed in 2–4 plain-language sentences, plus the exact command
   to try it.
6. **Update** `notes/PLAN.md`: tick finished checkboxes, add anything learned to
   `notes/DECISIONS.md`.
7. **Hand off:** run the `handoff` skill and suggest a commit message
   (e.g. `phase 1: manual sorter with dry-run`). Then tell the user to have the
   other agent run `cross-review`.

## Don'ts
- Don't start the next phase without being asked.
- Don't add dependencies without saying why.
- Don't refactor working code that isn't part of this phase.
