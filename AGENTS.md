# DAW Multitool — instructions for coding agents

## What this project is
A macOS application hosting independent studio tools: Audio Organizer and Auto Poster.
Further functions belong in separate adapters in `multitool/tools/`.
The shared registry drives the home screen and app packaging. See
`notes/ARCHITECTURE.md` before adding a tool. The organizer roadmap is retained;
do not expand or rewrite it unless the user asks.

Start every session by reading `notes/STATE.md` (project map) and the top entry of
`notes/HANDOFF.md` (what the last agent did). Open `notes/PLAN.md` only when you start
or finish a phase.

## Who you're working with
Two producers who are new to coding. Explain what you changed in plain language,
keep explanations short, and don't assume they know terminal or Git jargon.

## Tech stack
- Python 3.11+ on macOS
- Tkinter home screen and tool windows; PyInstaller is a build-only dependency
- Auto Poster: bundled FFmpeg, Requests, macOS Keychain via keyring, official social APIs
- `watchdog` (folder watching), `mutagen` (audio length/metadata)
- `librosa` + macOS SoundAnalysis (`native/sound_classifier.swift`, built with Apple's command line tools) for drum
  detection; BPM/key from file names first (`multitool/music.py`); `ffmpeg` (video), YouTube Data API
- All paths, thresholds and tag words come from `config.json` — never hardcode them.
  `config.example.json` is the template; `config.json` is gitignored.
- Use `app_paths.DATA_DIR` for writable app data; bundled runs use Application Support.
  Import tool dependencies lazily through their adapters; keep one window per tool.

## Hard rules (these protect real music files)
1. **Never delete audio files.** Moving is the most destructive thing allowed.
2. **Never overwrite.** If a destination name exists, add ` (2)`, ` (3)`, etc.
3. **Every new command starts with a dry-run mode** that prints what it *would* do.
4. **Log every move** to `_log.txt` (timestamp, from, to) so it can be undone.
5. **Wait for exports to finish** before moving: file size must be stable for a
   few seconds.
6. **Test with fake files only** — generate them with `scripts/make_test_files.py`
   into `test_exports/`. Never point tests at a real exports folder. Exception:
   `test_exports/real/` holds *copies* of real bounces the user labelled, used read-only
   to measure drum-detection accuracy.

## Workflow
- Work one phase at a time (see the `phase-workflow` skill).
- Keep functions small and named clearly; add a short comment on anything non-obvious.
- After a change, run it on test files and show the output.
- When a phase is done, tick its checkbox in `notes/PLAN.md`.

## Two agents, one project
Claude (Claude Code) and Codex both work in this folder and share these rules and the
skills in `.agents/skills/` (`.claude/skills` is a link to the same folder).
- **Builder** writes the code for a phase. **Reviewer** is the other agent and runs `cross-review`.
- Finish every session with the `handoff` skill. Start every session by reading the handoff.
- Never undo the other agent's work without saying why in the handoff.

## When an agent runs out of credits
Only one agent works at a time. When one stops (usage limit or credits), the user tells the other
**"take over"** (or "read the handoff and continue"):
1. Read `notes/LIVE_SESSION.md`. Claude Code rewrites it after every request, edit and reply
   (`scripts/live_session.py`, hooks in `.claude/settings.json`), so it has Claude's last requests,
   last words and changed files even when Claude stopped mid-task. If it says Claude is working and
   the last activity is under 10 minutes old, Claude may still be running: ask the user first.
2. Read the top entry of `notes/HANDOFF.md`, look at the changed files before building on them
   (an edit may be half done), and run the tests.
3. Codex keeps no automatic log, so when it takes over it first adds a one-line "In progress" entry
   at the top of `notes/HANDOFF.md` and keeps it current. Any project file change also stops Claude
   from resuming by itself when its limit resets.
4. Finish the request, then run the `handoff` skill.

## Token discipline
Follow the `lean-mode` skill at all times: read the map first, open only what you change,
use short replies.
