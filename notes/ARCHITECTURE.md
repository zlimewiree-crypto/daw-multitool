# DAW Multitool structure

One macOS application opens separate windows for each tool. The organizer is the first
tool; Auto Poster is the second; Project Library is the third. Future tools do not need to change the organizer or the home screen.

## Run and build

- Double-click `Run DAW Multitool.command` during development.
- `.venv/bin/python main.py --dry-run` lists tools without opening windows or changing files.
- `.venv/bin/python -m unittest discover -s tests -v` checks the host and settings.
- Install build tools: `.venv/bin/python -m pip install -r requirements-build.txt`.
- `.venv/bin/python scripts/build_app.py` previews the build command.
- `.venv/bin/python scripts/build_app.py --run` builds `dist/DAW Multitool.app`.
- `"dist/DAW Multitool.app/Contents/MacOS/DAW Multitool" --self-check` checks the packaged imports and worker process.

Builds include Python, Tk and the organizer's Python dependencies. Each build gets its
own dated folder; `dist/DAW Multitool.app` points to the latest build. Builds target
the architecture of the build Mac. They are locally signed, without distribution
certificates or notarization. macOS `afplay` handles listening when `ffplay` is absent.
Builds reuse `build/app` and `build/cache`; a file lock serializes builds sharing that
cache. Older dated outputs are retained. The imageio-ffmpeg hook packages one executable.

## File map

```text
main.py                         application entry; frozen-worker support
app_paths.py                    bundled resources versus writable user data
multitool/app.py                home screen and tool-window lifecycle
multitool/tools/__init__.py     the TOOLS registry
multitool/tools/organizer.py    adapter for the existing organizer
multitool/tools/auto_poster.py  adapter for video creation and posting
multitool/tools/project_library.py  adapter for the Project Library
multitool/tools/varispeed.py   adapter for Varispeed
multitool/varispeed/           tape-style speed changes: core.py (maths, names, soxr render, CLI), player.py (live), ui.py
multitool/settings.py          Settings window (home screen knob, ⌘,): audio device and pass-through
multitool/audio/               DAW Multitool audio device: build.py, device.py (install/remove), bridge.py (helper process)
native/audio_device/           the Core Audio plug-in (C) + its Info.plist + test_device.c (loads it with a pretend host)
native/audio_bridge.m          device input → resampled to an output pair; records; --list/--self-check/--test
multitool/library/              project scan / export linking (core.py) and window (ui.py)
multitool/auto_poster/          rendering, Keychain/OAuth, API publishers, receipts, scheduler, results and window
bounce_sorter_app.py            organizer window, still runnable on its own
sorter.py, drums.py             existing organizer engine
DAW Multitool.spec              packaging; imports tools from the registry
scripts/build_app.py            preview/build command
requirements-build.txt         optional packaging dependencies
```

## Add a tool

1. Add a module such as `multitool/tools/my_tool.py` with `open_tool(window)`.
2. Build the tool's Tk interface in that provided window. Return its controller;
   the controller must have `close()`, which cleans up its work and destroys its window.
3. Register it once in `multitool/tools/__init__.py`:

```python
Tool("my_tool", "My Tool", "What the tool does.", "multitool.tools.my_tool"),
```

The home screen and app builder both read this registry. Import expensive dependencies
inside `open_tool` so they load only when the tool opens. Use ordinary Python imports
inside the adapter so packaging can discover the dependencies. An optional
`self_check()` function can verify a tool's dependencies without launching its window.

Use background workers for long tasks. A working controller exposes `busy`, `cancel`
(a threading Event), and `status` (a Tk StringVar); the host requests cancellation and
waits for the task to finish before closing. Update Tk widgets on the main thread.
Tools that touch audio must keep the same preview, collision, settling and logging rules.

## Settings and saved data

Source runs keep existing `config.json`, labels, model and cache in the project folder.
Bundled runs use `~/Library/Application Support/DAW Multitool/`. Only a default config
and model ship with the application; personal labels, paths, caches and audio are excluded.
Defaults are copied only when missing, so app upgrades preserve existing user settings.
The bundled app starts with defaults; project-specific settings are not imported automatically.

Give each new tool its own section in `config.json` (the organizer uses `bounce_sorter`).
Save it through `app_paths.update_config`: its thread/process lock covers reading,
merging and atomically writing the whole config, so tools cannot discard each other's
sections. `app_paths.atomic_json` is the shared durable writer for receipts, labels and models.
Use `app_paths.DATA_DIR` for writable data and `app_paths.RESOURCE_DIR` for shipped assets.
`DAW_MULTITOOL_DATA_DIR` can redirect data during tests. Audio destinations remain controlled
by the organizer's settings. Numba's bundled cache also lives in application data;
librosa is packaged as source so cached audio functions can find their source files.

Packaging references: [PyInstaller spec files](https://pyinstaller.org/en/stable/spec-files.html),
[runtime paths](https://pyinstaller.org/en/stable/runtime-information.html), and
[multiprocessing](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing).

Auto Poster additionally bundles the imageio-ffmpeg binary and uses Requests plus the
explicit macOS Keychain backend from keyring. Its defaults are in config.example.json;
API credentials stay in Keychain and rendered media/receipts go to the chosen output
folder. Account prerequisites and API references are in [[AUTO_POSTER]].

Auto Poster v2 receipts store a `variants` map (format name → file, snapshot, dimensions)
and shared metadata/delivery results. The GUI and default render CLI create both formats
from `platform_formats` in config; each publisher selects its assigned version and records
that format beside the remote ID. A pair becomes ready only after both encodes finish.
Legacy single-video receipts support previews/known-ID checks, but new uploads require a pair.
