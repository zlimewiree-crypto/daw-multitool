# Mac and Windows development

Both versions live on `main`. Work on short-lived feature branches and combine reviewed
changes through pull requests. Each developer uses their own clone; do not share a live
working folder or virtual environment between computers.

## Windows setup

Use Python 3.12 with Tcl/Tk installed. From PowerShell in the project folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -X utf8 main.py --dry-run
.venv\Scripts\python.exe -X utf8 main.py --self-check
.venv\Scripts\python.exe -X utf8 main.py
```

The last command opens the app. The dry run lists tools without creating settings or
opening windows. Normal startup creates personal `config.json` when missing; do not
copy your partner's personal configuration or credentials. `config.example.json` is
shared; `config.windows.json` overrides Windows fonts and project folders before personal
settings are applied. DAW display names use Windows file associations. Set an
absolute `.exe` path in personal `auto_poster.daws` settings to choose a specific DAW.

Source runs store settings in the project folder. Packaged Windows runs will use
`%LOCALAPPDATA%/DAW Multitool`; Mac bundles retain Application Support. Tests can use
`DAW_MULTITOOL_DATA_DIR` to isolate settings.

## Platform boundary

`multitool/platform.py` owns file locks, app-data paths, open/reveal actions, credential
backend selection and desktop conventions. Shared tools call these helpers. Mac-native
capture/audio modules stay in place behind platform checks. New Windows implementations
should preserve those interfaces and leave Mac behaviour covered by tests.

| Feature | Windows source port |
| --- | --- |
| Home, tool windows, settings | Supported |
| Organizer preview/sort/learning | Uses existing measurement-only detector; accuracy is not equivalent to Apple's classifier |
| Project Library | Scan/link/sort; open through configured executable or file association |
| Auto Poster image/video rendering | Shared FFmpeg renderer with Windows font defaults |
| Social accounts | Windows secure credential backend; live sign-in/posting still needs user testing |
| Scheduled posts / Autopilot | Keep Auto Poster open; closed-app helper and OS notifications pending |
| Varispeed beat files | Shared decoder, playback, output mapping and copy rendering |
| DAW screen recording | Pending; unavailable in Windows UI |
| Virtual audio device / whole-DAW Varispeed | Pending; unavailable in Windows UI |
| Windows executable/installer | Pending; current build scripts still package macOS |

## Checks before sharing

```powershell
.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
```

On Mac, run the same checks using `.venv/bin/python`. Native Core Audio tests run only
on Mac with Apple's command line tools; portable tests also cover the Mac command
routes with stand-ins. Synthetic media lives in `test_exports/` and is archived to the
system temporary folder after tests. No real accounts or audio hardware are needed.

Use `scripts/make_test_files.py` only in a fresh synthetic test area; never use its
`--clean` switch on a folder containing labelled copies. Never test against Downloads.

## Working together

1. Start each change from an up-to-date `main` on a named feature branch.
2. Keep shared logic in the existing modules; put OS differences at the platform boundary.
3. Commit code, default templates, tests and notes. Keep `config.json`, audio, credentials,
   `.venv`, `build` and `dist` local (already ignored).
4. Push the feature branch and open a pull request. The other developer tests their OS
   and reviews the changes before merging.
5. Each developer updates their local `main` after the merge.

The first handoff branch adds the platform foundation. It does not replace the Mac's
native driver with a Windows driver. Full parity needs separate work and real listening
tests on both systems.
