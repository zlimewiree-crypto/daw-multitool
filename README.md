# DAW Multitool

A macOS studio application containing Audio Organizer, Auto Poster, Project Library
and Varispeed, plus a native audio device and pass-through bridge.

## Sharing and Windows

This repository contains source code, not a Windows installer. You can edit it on
Windows, but platform-specific components must be ported before the app can run there.
Read [START-HERE-WINDOWS.txt](START-HERE-WINDOWS.txt) for the compatibility work required.

## Project documentation

- [Setup](notes/SETUP.md)
- [Architecture](notes/ARCHITECTURE.md)
- [Current state](notes/STATE.md)
- [Latest handoff](notes/HANDOFF.md)
- [Auto Poster](notes/AUTO_POSTER.md)

Use `config.example.json` as the configuration template. Personal settings, credentials,
music files, virtual environments and compiled builds are intentionally excluded.

Never delete or overwrite original audio. Preview file operations first and test only
with generated media in `test_exports/`; see [AGENTS.md](AGENTS.md).
