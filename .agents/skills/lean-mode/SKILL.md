---
name: lean-mode
description: Always-on token and code discipline for this project. Use for every coding task, especially when the user says "be quick", "save tokens", "lean", "ponytail", "simplest solution" or "short answer".
---

# Lean mode

Tokens are limited on both the ChatGPT and Claude plans, so every read, every line of
code and every reply should earn its place. Code rules adapted from the ponytail skill
(github.com/DietrichGebert/ponytail, MIT).

## Reading
- Start from `notes/STATE.md` and the top of `notes/HANDOFF.md`, not by scanning the repo.
- Search (`rg`/grep) before opening files, and open only the files you will change.
- Read files once. Don't re-read a file you just wrote.
- Never read `test_exports/`, `.venv/`, `.git/`, `.obsidian/`, `.drum_cache.json` or audio files.
- Use `git diff --stat` first, and a full diff only when it's needed.

## Writing code: the smallest complete change
Before writing, list every place the change must reach (callers, config, scripts, notes).
Then take the first option that fully works:
1. Does it need to exist? Skip features or options nobody asked for, and name them in one line.
2. Already in this project? Reuse it (`sorter.py`, `drums.py` helpers, `config.json` settings).
3. Python standard library, or an installed package (see `requirements.txt`)? Use it.
   Never add a dependency for a few lines.
4. Otherwise, write the minimum code that works.

- Prefer editing an existing file over creating a new one. Deletion beats addition.
- No wrappers, abstractions or "for later" code. Comment only the *why*, in one line.
- Bug fix: grep every caller first, then fix the root cause once.
- A shortcut with a known limit gets a comment: `# shortcut: <limit>, <when to upgrade>`.
- New logic (a branch, loop, parser, or new script) leaves one small check, such as an assert,
  a test run in the reply, or a `--dry-run` output.
- **Never cut:** the audio-file-safety rules (dry-run, no overwrite, logging, wait for
  export), error handling that prevents data loss, or anything the user asked for.

## Doing
- Run commands with quiet flags, and pipe long output through `tail -20`.
- Long output goes to a file in the scratch/temp folder; read only the lines you need from it.
  This project's usual commands:
  - Tests: `.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3` (full output only if one fails)
  - Build: `.venv/bin/python scripts/build_app.py --run > /tmp/build.log 2>&1; tail -1 /tmp/build.log`
  - Preview: `.venv/bin/python sorter.py > /tmp/preview.txt 2>&1`, then `grep -c` / `grep` what you need
- Big files (`multitool/auto_poster/ui.py`, `bounce_sorter_app.py`, `tests/test_auto_poster.py`):
  `grep -n "def \|<keyword>"` first, then read only that line range.
- Don't run the same check twice. After a passing run, don't re-run it to "confirm".
- Agent-to-agent text (handoffs, progress lines) follows the `caveman` skill.

## Replying
- Lead with the result. Two to four sentences of explanation, max.
- Show the command to try it, not the whole file you wrote.
- No recap of steps the user already saw. No closing pleasantries.
- End with one line naming what you skipped or didn't check, and any risk.
