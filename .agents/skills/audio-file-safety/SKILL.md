---
name: audio-file-safety
description: Use whenever writing or changing code that moves, renames, copies or reads the user's audio exports (mp3/wav). Enforces dry-run, no-overwrite, logging and wait-for-export-to-finish rules.
---

# Audio file safety

These files are someone's music. Losing one is the worst possible bug. Any code
that touches audio files must follow every rule below.

## Checklist (verify each before saying you're done)
- [ ] Has a `--dry-run` flag that prints planned moves without touching anything.
- [ ] Never calls delete/unlink/rmtree on audio files.
- [ ] Destination collisions get a numbered suffix: `name (2).mp3`, never overwrite.
- [ ] Every real move is appended to `_log.txt` as `timestamp | from | to`.
- [ ] Before moving, the file size is checked twice ~2–3 seconds apart and must be
      unchanged and non-zero (DAWs write exports gradually).
- [ ] Skips hidden/temp files (names starting with `.` or ending in `.tmp`,
      `.part`, `.asd`, `.reapeaks`).
- [ ] Uses `shutil.move` / `pathlib` — no shell `mv`.
- [ ] Paths come from `config.json`, not hardcoded.

## Undo
If you add a mover, also make sure `_log.txt` contains enough to reverse it. An
`undo` command reads the last batch from the log and moves files back (still
never overwriting).

## Testing
Only test on files in `test_exports/`: synthetic files from the scripts there, or the user's
labelled copies in `test_exports/real/` (read-only). See the `make-test-audio` skill.
If asked to run against a real folder, do a dry-run first and show the output
before doing it for real.
