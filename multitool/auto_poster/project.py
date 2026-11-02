"""Find the FL Studio / Ableton project an export came from, open it, press play and screen-record its window.
Project files are only opened, never saved, moved or changed by this module."""
import ctypes
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

from app_paths import update_config
from .render import Cancelled, ffmpeg_binary, media_info

# DAW backups/internals; Pro Tools keeps recordings in "Audio Files" and copies of the session in "Session File Backups"
SKIP_FOLDERS = {"backup", "samples", "ableton project info", "session file backups", "audio files", "fade files",
                "video files", "clip groups", "node_modules", ".venv"}
AUTOSAVE = re.compile(r"\((autosaved|overwritten)\b|\.bak\.\d+\.\w+$", re.IGNORECASE)  # FL/Ableton autosaves, Pro Tools .bak.001


def _key(name):
    """'Needa_Lead (1)' -> 'needa lead', so export and project names compare loosely."""
    name = re.sub(r"\s*\(\d+\)\s*$", "", name.casefold())
    return re.sub(r"[\s_\-.]+", " ", name).strip()


def project_index(settings):
    """{project file: match key} for the configured folders; DAW backups and autosaves are skipped.
    Keys are worked out once here, not again for every export being matched."""
    extensions = tuple(settings["daws"])
    found = []
    for root in settings["project_folders"]:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for folder, dirs, files in os.walk(root):
            depth = len(Path(folder).relative_to(root).parts)
            dirs[:] = [] if depth >= settings["project_search_depth"] else [
                d for d in dirs if not d.startswith(".") and d.casefold() not in SKIP_FOLDERS]
            found += [Path(folder) / f for f in files
                      if f.lower().endswith(extensions) and not f.startswith(".") and not AUTOSAVE.search(f)]
    return {project: _key(project.stem) for project in found}


def find_project(audio, index):
    """Same name first, else the longest project name the export starts with
    ('needa lead 140 fmin.wav' -> 'needa lead.flp'); the newest file wins a tie. None if nothing fits."""
    name = _key(Path(audio).stem)
    scored = []
    for project, key in index.items():
        if len(key) < 3:
            continue
        rank = 2 if key == name else 1 if name.startswith(key + " ") or key.startswith(name + " ") else 0
        try:
            if rank:
                scored.append(((rank, len(key), project.stat().st_mtime), project))
        except OSError:
            continue
    return max(scored)[1] if scored else None


def daw_for(project, settings):
    try:
        return settings["daws"][Path(project).suffix.lower()]
    except KeyError:
        raise ValueError(f"{Path(project).name} isn't a project type set up here ({', '.join(settings['daws'])}).") from None


def check_permissions():
    """Screen Recording (to see and record the DAW window) and Accessibility (to press play)."""
    graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    if not graphics.CGPreflightScreenCaptureAccess():
        graphics.CGRequestScreenCaptureAccess()  # macOS shows its prompt once
        raise PermissionError("Allow Screen Recording for DAW Multitool in System Settings → Privacy & Security, "
                              "then quit and reopen DAW Multitool.")
    services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    if not services.AXIsProcessTrusted():
        subprocess.Popen(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
        raise PermissionError("Allow Accessibility for DAW Multitool in System Settings → Privacy & Security "
                              "so it can press play in your DAW, then try again.")


# CoreGraphics lists window names and positions with Screen Recording access alone (no Accessibility needed)
WINDOWS_JS = """function run(argv) {
  ObjC.import("CoreGraphics");
  const all = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo(
    $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0)));
  return JSON.stringify(all.filter(w => w.kCGWindowOwnerName === argv[0] && w.kCGWindowLayer === 0)
    .map(w => ({name: w.kCGWindowName || "", bounds: w.kCGWindowBounds})));
}"""

def daw_windows(process):
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", WINDOWS_JS, process],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("Couldn't look at your DAW's windows: " + result.stderr.strip()[-200:])
    return json.loads(result.stdout or "[]")


def _area(window):
    return window["bounds"]["Width"] * window["bounds"]["Height"]


def project_window(project, windows, before=()):
    """The DAW window showing this project, or None while it is still loading. Its title names the project;
    for a DAW whose title doesn't, the main window counts once its title differs from every title before opening."""
    name = _key(Path(project).stem)
    named = [w for w in windows if name in _key(w["name"])]
    if named:
        return max(named, key=_area)
    main = max((w for w in windows if w["bounds"]["Width"] >= 400 and w["bounds"]["Height"] >= 300), key=_area, default=None)
    return main if main and main["name"] and main["name"] not in before else None


def wait_for_project(project, process, before, settings, cancelled, report=lambda text: None):
    """Bounds (x, y, width, height) once the project's window title has held still for project_settle_seconds,
    so plugins and samples have loaded and the window has stopped resizing."""
    deadline = time.monotonic() + settings["project_load_seconds"]
    title, since = None, 0
    while True:
        if cancelled():
            raise Cancelled("Stopped before recording the project.")
        report(f"Waiting for {Path(project).name} to load…")
        window = project_window(project, daw_windows(process), before)
        now = time.monotonic()
        if window is None or window["name"] != title:
            title, since = window and window["name"], now
        elif now - since >= settings["project_settle_seconds"]:
            return tuple(int(window["bounds"][k]) for k in ("X", "Y", "Width", "Height"))
        if now > deadline:
            raise ValueError(f"{Path(project).name} didn't open. If your DAW asked to save another project, "
                             "answer it, then try again.")
        _wait(0.5, cancelled)


def _front_process(process):
    """Resolve the focused app through our own Accessibility grant, without an AppleScript helper."""
    services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    if not services.AXIsProcessTrusted():
        raise PermissionError("macOS hasn't given this running copy of DAW Multitool Accessibility access. "
                              "Quit and reopen the app after enabling it in System Settings → Privacy & Security → "
                              "Accessibility. If it is already on, remove the old entry and add the current app again.")
    services.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
    services.AXUIElementCopyAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    services.AXUIElementGetPid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    system = services.AXUIElementCreateSystemWide()
    attribute = foundation.CFStringCreateWithCString(None, b"AXFocusedApplication", 0x08000100)  # UTF-8
    focused, pid = ctypes.c_void_p(), ctypes.c_int()
    try:
        error = services.AXUIElementCopyAttributeValue(system, attribute, ctypes.byref(focused))
        if error or not focused.value or services.AXUIElementGetPid(focused, ctypes.byref(pid)):
            raise PermissionError("macOS couldn't identify the focused app through Accessibility. "
                                  "Quit and reopen DAW Multitool after checking its Accessibility permission.")
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        library.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        name = ctypes.create_string_buffer(1024)
        if library.proc_name(pid.value, name, len(name)) <= 0 or name.value.decode() != process:
            raise ValueError("Your DAW wasn't in front, so play wasn't pressed. Don't click other apps while it records.")
        return pid.value
    finally:
        for item in (focused.value, attribute, system):
            if item:
                foundation.CFRelease(item)


PLAY_WAYS = ("app", "keyboard")  # keys sent straight to the DAW, or as if typed on the keyboard


class NotPlaying(ValueError):
    """The recording shows a frozen window: the DAW didn't react to play."""


def press_play(process, keys=(), via="app"):
    """Send Home/Space from this app's Accessibility identity, only while the DAW is the focused app.
    "app" posts to the DAW's PID; "keyboard" posts like a real keypress (some apps only read keys from there),
    right after the same focus check."""
    graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    graphics.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    graphics.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    graphics.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    graphics.CGEventPostToPid.argtypes = [ctypes.c_int, ctypes.c_void_p]
    graphics.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    for index, key in enumerate((*keys, 49)):
        if index:
            time.sleep(0.2)
        pid = _front_process(process)  # re-check between Home and Space; a focus change never redirects a key
        down = graphics.CGEventCreateKeyboardEvent(None, key, True)
        up = graphics.CGEventCreateKeyboardEvent(None, key, False)
        try:
            if not down or not up:
                raise PermissionError("macOS couldn't create the playback keypress. Check DAW Multitool's Accessibility access.")
            for event in (down, up):
                graphics.CGEventSetFlags(event, 0)
                if via == "keyboard":
                    graphics.CGEventPost(0, event)  # kCGHIDEventTap: reaches the focused app, checked just above
                else:
                    graphics.CGEventPostToPid(pid, event)
        finally:
            for event in (down, up):
                if event:
                    foundation.CFRelease(event)
    return time.monotonic()


def _open(app, project=None):
    """`open -a` launches the app if needed, brings it to the front and loads the project."""
    try:
        subprocess.run(["/usr/bin/open", "-a", app, *([str(project)] if project else [])],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=30)
    except subprocess.CalledProcessError:
        raise ValueError(f"Couldn't open {app}. Check its name under auto_poster → daws in config.json.") from None


def _wait(seconds, cancelled):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancelled():
            raise Cancelled("Stopped before recording the project.")
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def remember_play_way(process, way, settings):
    """Next time, press play the way that worked for this DAW straight away."""
    ways = {**settings.get("play_ways", {}), process: way}
    settings["play_ways"] = ways
    update_config("auto_poster", {"play_ways": ways})


def record_project(project, seconds, folder, settings, cancelled=lambda: False, report=lambda text: None,
                   warn=lambda text: None):
    """Open the project, press play and record only its window for `seconds` of playback.
    Returns (recording, offset): offset is how far into the recording the song started.
    If the DAW ignored play, records once more pressing play the other way, and remembers the way that worked."""
    project = Path(project)
    if not seconds or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Couldn't read the beat's length. Choose a finished audio export, then try again.")
    if not project.is_file():
        raise ValueError(f"Project file not found: {project}")
    daw = daw_for(project, settings)
    report("Checking Screen Recording and Accessibility permissions…")
    check_permissions()
    known = settings.get("play_ways", {}).get(daw["process"], PLAY_WAYS[0])
    ways = [known] + [way for way in PLAY_WAYS if way != known]
    for attempt, way in enumerate(ways):
        try:
            result = _record(project, seconds, folder, settings, cancelled, report, warn, daw, way)
        except NotPlaying:
            if attempt == len(ways) - 1:
                raise
            report("Playback didn't start; trying another way to press play…")
            continue
        if way != known:
            remember_play_way(daw["process"], way, settings)
        return result


def _record(project, seconds, folder, settings, cancelled, report, warn, daw, way):
    """One take: open the project, press play `way`, record its window. NotPlaying if the picture never moves."""
    before = {w["name"] for w in daw_windows(daw["process"])}  # titles that can't be the new project yet
    report(f"Opening {daw['app']}…")
    _open(daw["app"], project)
    bounds = wait_for_project(project, daw["process"], before, settings, cancelled, report)
    _open(daw["app"])  # back in front in case something else was clicked meanwhile
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{project.stem[:70]}-{uuid.uuid4().hex[:12]}.mov"  # a new name every time; never overwrites
    lead = settings["recording_lead_seconds"]
    capture_seconds = math.ceil(lead + seconds + 2)
    grace = settings["recording_finish_seconds"]
    report("Starting screen recording…")
    log = target.with_suffix(".log")
    with log.open("xb") as errors:  # a pipe can fill and leave screencapture waiting forever
        process = subprocess.Popen(["/usr/sbin/screencapture", "-x", "-v", f"-V{capture_seconds}",
                                    "-R" + ",".join(map(str, bounds)), str(target)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
        deadline = time.monotonic() + capture_seconds + grace
        played, shown = None, None
        try:
            _wait(lead, cancelled)
            if process.poll() is not None:
                raise ValueError(f"Screen recorder stopped before playback. Check Screen Recording access; details: {log}")
            report("Starting playback in the DAW…")
            played = press_play(daw["process"], daw.get("start_keys", ()), via=way)
            while process.poll() is None:
                if cancelled():
                    raise Cancelled("Stopped while recording the project. The project file was not changed.")
                now = time.monotonic()
                if now > deadline:
                    raise ValueError(f"Screen recording didn't finish on time and was stopped. Details: {log}")
                elapsed = min(int(seconds), int(now - played))
                message = (f"Recording {elapsed // 60}:{elapsed % 60:02d} / {int(seconds) // 60}:{int(seconds) % 60:02d} "
                           "(full beat)…" if now - played < seconds else "Finishing the screen recording…")
                if message != shown:
                    report(message)
                    shown = message
                time.sleep(0.1)
        finally:
            ended = time.monotonic()
            if process.poll() is None:
                try:
                    process.send_signal(signal.SIGINT)  # keep what screencapture recorded
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace)
            if played:
                try:
                    press_play(daw["process"], via=way)  # stop playback
                except (OSError, ValueError, subprocess.SubprocessError):
                    warn(f"{daw['app']} may still be playing: click it and press Space to stop.")
    if process.returncode or not target.is_file():
        raise ValueError("Screen recording failed: " + log.read_text(errors="replace")[-300:] + f" Details: {log}")
    report("Checking the completed recording…")
    duration = media_info(target, settings)["duration"] or 0
    # screencapture exits right after its last frame, so its first frame was `duration` seconds before that
    offset = max(0.0, played - (ended - duration))
    if duration < offset + seconds - 0.5:
        raise ValueError(f"The recording of {project.name} stopped early ({duration:.0f} s). Try again.")
    if not _moving(target, offset, seconds, settings):
        for made in (target, log):  # this take's own files, made above under fresh names
            made.unlink(missing_ok=True)
        raise NotPlaying(f"{daw['app']} didn't start playing in the recording of {project.name}. "
                         "Check it isn't waiting on a dialog, then try again.")
    log.unlink(missing_ok=True)  # only kept when a recording fails, to say why
    return target, offset


def _moving(recording, offset, seconds, settings):
    """True if the picture changes after play was pressed (playhead, meters); a frozen window means no playback."""
    def frame(at):
        return subprocess.run([ffmpeg_binary(settings), "-v", "error", "-ss", f"{at:.2f}", "-i", str(recording),
                               "-frames:v", "1", "-vf", "scale=96:54,format=gray", "-f", "rawvideo", "-"],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=settings["probe_timeout"]).stdout
    first, last = frame(offset + 0.3), frame(offset + max(0.6, min(seconds, 4) - 0.3))
    if not first or len(first) != len(last):
        return True  # couldn't sample; don't block a render on the check itself
    return sum(abs(a - b) for a, b in zip(first, last)) / len(first) > 0.2
