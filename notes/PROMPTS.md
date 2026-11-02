# Prompt library

Copy-paste these. In Codex call a skill with `$name`, in Claude Code with `/name`.

## Start a phase
> $phase-workflow do phase 1

## Fix something
> This happened: [paste the error or what went wrong]. Expected: [what you wanted].
> Find the cause, explain it in one sentence, then fix it.

## Understand code
> Explain what organize.py does like I'm a producer, not a programmer. 5 bullets max.

## Before running on real exports
> $audio-file-safety check organize.py against the checklist, then do a dry-run
> on ~/Music/Exports and show me the output. Don't move anything yet.

## Fresh test files
> $make-test-audio --clean, then run the sorter on test_exports with --dry-run.

## When it gets messy
> Summarize what we changed this session, list anything half-finished, and update
> notes/PLAN.md and notes/DECISIONS.md.

## Tandem
> Read the handoff and continue.

> take over       (the other agent ran out of credits; works in Codex and in Claude)

> $handoff        (end of a Codex session)
> /handoff        (end of a Claude session)

> /cross-review   (Claude checks Codex's last phase)

> Fix the findings from the last review. Then run the handoff.
