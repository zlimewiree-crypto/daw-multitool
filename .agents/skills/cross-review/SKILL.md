---
name: cross-review
description: Use when the user says "review", "check Codex's work", "check Claude's work", or after a phase is built and before it runs on real exports. One agent reviews the other's changes and reports problems without rewriting them.
---

# Cross-review

Whoever did NOT write the code reviews it. A second pair of eyes catches bugs
the builder is blind to.

## Steps
1. Read the top entry of `notes/HANDOFF.md` to see what changed.
2. Look at the change with `git diff HEAD~1` (or `git diff` if it isn't committed yet).
   Read a whole file only when the diff isn't enough to judge it.
3. Check the change against:
   - the phase's checkboxes in `notes/PLAN.md`
   - the `audio-file-safety` checklist (no deletes, no overwrites, dry-run, logging)
   - obvious bugs: wrong paths, unhandled missing folders, crashes on odd filenames
4. Run it on test files: `.venv/bin/python <script> --dry-run` against `test_exports/`.

## Output (keep it short)
```
VERDICT: ship it | fix first
1. [severity: high/med/low] file:line — problem → suggested fix
...
```
Report up to 5 findings, most severe first. Don't restyle working code, and don't make
the fixes yourself unless the user asks. The builder fixes them on the next turn.
