# DAW Multitool

A shared Python studio application containing Audio Organizer, Auto Poster, Project
Library and Varispeed. macOS additionally has a native audio device and pass-through bridge.

## Sharing and Windows

Mac and Windows development use the same repository and tool code. Windows source
startup, secure credentials, file opening and file playback have a platform layer;
native DAW capture, background scheduling and the virtual audio device remain Mac-only.
This is an initial Windows port, not a Windows installer or complete feature parity.
Read [Windows setup and collaboration](notes/WINDOWS.md) for commands and remaining work.

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
