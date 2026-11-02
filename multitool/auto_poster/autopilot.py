"""Autopilot: beats exported into a watched folder get their videos made without anyone pressing a button.

For each finished audio file that arrives in the folder after Autopilot was turned on:
1. it waits until the export has finished (the file hasn't changed for `settle_seconds`),
2. skips melody loops if asked (the organizer's drum detector decides; files it's unsure about are kept),
3. makes both videos with the saved Create settings: the chosen background (or the next one from a folder of
   them, or an automatic one), the best part for TikTok/Reels starting on the beat grid of the BPM in the name,
   the title, BPM and key on the video, and the producer tag,
4. writes what happened in autopilot.json so a file is never handled twice, and sends a notification.
It never posts. Finished pairs wait for the user to preview, approve and schedule them (TikTok requires a person
to choose each post's visibility and confirm it).
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, time as clock, timedelta
import fcntl
import json
from pathlib import Path
import threading
import time

from app_paths import DATA_DIR, atomic_json, load_section
from multitool import credits, music
from .render import VideoRequest, create_video, posting_formats
from .storage import save_preferences, social_caption

DEFAULT_OPTIONS = {
    "visual_kind": "auto",  # auto | image | video (a file, or a folder whose files take turns) | project
    "visual": "",
    "fit": "Fit",
    "start": 0.0,  # YouTube version: start and length (0 = to the end)
    "length": 0.0,
    "short_seconds": 30,  # TikTok/Reels version: the best N seconds; 0 = the same part as YouTube
    "overlay": True,  # title and "155 BPM · F minor · prod. you" on the video
    "waveform": True,
    "use_tag": False,
    "tag": "",
    "output": "",
    "title": "{title} ({credits}) | {bpm} {key}",
    "caption": "",
    "social_description": "",
    "hashtags": "",
    "skip_loops": True,
}
_LOCK = threading.Lock()


def ledger_file():
    return DATA_DIR / "autopilot.json"


@contextmanager
def ledger_lock():
    """One writer at a time, across threads and the background helper."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK, (DATA_DIR / ".autopilot.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


RETRIES, RETRY_AFTER = 3, 3600  # a failed file is tried again up to 3 times, an hour apart


def load_ledger():
    """{"since": when Autopilot was turned on, "folder": the folder it was turned on for,
    "files": {file key: {"state", "detail", "receipt", "when", "name", "attempts"}}}"""
    try:
        data = json.loads(ledger_file().read_text())
    except (OSError, ValueError):
        return {"since": None, "files": {}}
    if not isinstance(data, dict):
        return {"since": None, "files": {}}
    data.setdefault("since", None)
    data["files"] = data.get("files") if isinstance(data.get("files"), dict) else {}
    return data


def turn_on(folder=None, now=None):
    """From now on, new files in `folder` are handled; what's already there (the backlog) is left alone."""
    with ledger_lock():
        data = load_ledger()
        data["since"] = now if now is not None else time.time()
        data["folder"] = str(Path(folder).expanduser().resolve()) if folder else None
        atomic_json(ledger_file(), data)
    return data


def watching(folder, ledger):
    """True when Autopilot was turned on for this folder; a different folder must start fresh (turn_on)."""
    recorded = ledger.get("folder")
    return not recorded or not folder or Path(folder).expanduser().resolve() == Path(recorded)


def file_key(path, info=None):
    """The same export, unchanged: a re-export (new size or time) counts as new."""
    info = info or Path(path).stat()
    return f"{Path(path).resolve()}|{info.st_size}|{info.st_mtime_ns}"


def _stale(entry, now, hours=2):
    """A "working" claim from a run that stopped (app quit mid-render) is taken over after a while; a failed
    file is tried again (a setting may have been fixed meanwhile), a few times at most."""
    if entry.get("state") == "working":
        return now - entry.get("claimed", 0) > hours * 3600
    return (entry.get("state") == "failed" and entry.get("attempts", 1) < RETRIES
            and now - entry.get("claimed", 0) >= RETRY_AFTER)


def claim(path, now=None):
    """Marks a file as being worked on, so the app and the background helper never both make its videos.
    Returns the file's key, or None when another run has it (or it's gone)."""
    now = now if now is not None else time.time()
    with ledger_lock():
        data = load_ledger()
        try:
            key = file_key(path)
        except OSError:
            return None
        entry = data["files"].get(key)
        if entry and not _stale(entry, now):
            return None
        data["files"][key] = {"state": "working", "detail": "", "name": Path(path).name, "claimed": now,
                              "attempts": (entry or {}).get("attempts", 0) + 1,
                              "when": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")}
        atomic_json(ledger_file(), data)
    return key


def remember(path, entry, key=None, now=None):
    """Writes what happened to one file (under the key it was claimed with); returns the entry."""
    entry = {**entry, "name": Path(path).name, "when": datetime.fromtimestamp(now or time.time()).astimezone().isoformat(timespec="seconds")}
    with ledger_lock():
        data = load_ledger()
        claimed = data["files"].get(key, {}) if key else {}
        entry = {"claimed": claimed.get("claimed", now or time.time()), "attempts": claimed.get("attempts", 1), **entry}
        try:
            data["files"][key or file_key(path)] = entry
        except OSError:  # gone meanwhile: keep the record by name so the activity list still shows it
            data["files"][f"{Path(path).resolve()}|gone"] = entry
        atomic_json(ledger_file(), data)
    return entry


def new_files(folder, settings, ledger, now=None):
    """Finished audio files in `folder` (not its subfolders) made after Autopilot was turned on for it and not
    handled yet. "Made" is the file's creation time, so an export (or a copy) counts, but an older file moved or
    renamed into the folder stays part of the backlog. A file is finished when it hasn't changed for settle_seconds."""
    since = ledger.get("since")
    folder = Path(folder).expanduser() if folder else None
    if not since or not folder or not folder.is_dir() or not watching(folder, ledger):
        return []
    now = now if now is not None else time.time()
    found = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() not in settings["audio_extensions"]:
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        if not path.is_file() or info.st_size == 0:
            continue
        made = getattr(info, "st_birthtime", info.st_mtime)
        if made < since or now - info.st_mtime < settings["settle_seconds"]:
            continue
        entry = ledger["files"].get(file_key(path, info))
        if entry is None or _stale(entry, now):
            found.append(path)
    return found


def next_visual(folder, kind, settings, remember_choice=True):
    """The next image (or video) from a folder of backgrounds, taking turns across renders and app restarts."""
    folder = Path(folder).expanduser()
    extensions = settings["image_extensions"] if kind == "image" else settings["video_extensions"]
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in extensions)
    if not files:
        raise ValueError(f"The backgrounds folder {folder.name} has no {'images' if kind == 'image' else 'videos'} in it.")
    turns = settings.get("background_turns", {})
    last = turns.get(str(folder.resolve()))
    names = [p.name for p in files]
    chosen = files[(names.index(last) + 1) % len(files)] if last in names else files[0]
    if remember_choice:
        turns = {**turns, str(folder.resolve()): chosen.name}
        settings["background_turns"] = turns
        save_preferences({"background_turns": turns})
    return chosen


def describe(audio, credit_settings, listen=True):
    """Title (credited names removed), BPM and key for the texts. A key heard from the audio is only used when
    the settings allow it: a wrong key in a public title is worse than none."""
    people = [credit_settings.get("producer_name", ""), *credits.collaborators(Path(audio).name, credit_settings)]
    info = music.describe(Path(audio), remove=[p.lstrip("@") for p in people if p], listen=listen)
    key = info["key"] if info["key_source"] == "name" or load_section("music").get("heard_key_in_text") else None
    return {"title": info["title"], "bpm": f"{info['bpm']} BPM" if info["bpm"] else "", "key": key.name if key else "",
            "bpm_value": info["bpm"], "key_value": info["key"], "bpm_source": info["bpm_source"], "key_source": info["key_source"]}


def texts(audio, options, credit_settings, info):
    """The filled-in YouTube title/description, TikTok/Reels caption and the lines drawn on the video."""
    audio = Path(audio)
    extra = {"title": info["title"], "bpm": info["bpm"], "key": info["key"]}
    fill = lambda template: credits.fill(template, audio.name, audio.stem, credit_settings, extra)
    line = " · ".join(part for part in (info["bpm"], info["key"], credits.credit_line(audio.name, credit_settings)) if part)
    return {"title": fill(options["title"]) or info["title"], "caption": fill(options["caption"]),
            "social_caption": fill(social_caption(options["social_description"], options["hashtags"])),
            "overlay": tuple(part for part in (info["title"], line) if part) if options["overlay"] else ()}


def build_request(audio, options, settings, credit_settings, *, visual=None, info=None):
    """The VideoRequest for one beat from the Create options. `visual` overrides the background (a folder's
    turn, picked by the caller); project recording isn't done here (it takes over the DAW)."""
    check_options(options)
    audio = Path(audio).resolve()
    info = info or describe(audio, credit_settings)
    kind = options["visual_kind"]
    if visual is None and kind != "auto" and options["visual"]:  # project mode: the YouTube version's still image
        chosen = Path(options["visual"]).expanduser()
        visual = next_visual(chosen, "video" if kind == "video" else "image", settings) if chosen.is_dir() else chosen
    if kind == "auto":
        visual = None  # made automatically when rendering (project mode without a picture too)
    output = Path(options["output"] or settings["output_folder"]).expanduser().resolve()
    text = texts(audio, options, credit_settings, info)
    tag = Path(options["tag"]).expanduser().resolve() if options["use_tag"] and options["tag"] else None
    return VideoRequest(audio, Path(visual).expanduser().resolve() if visual else None, output, posting_formats(settings)[0],
                        options["fit"], float(options["start"]), float(options["length"]), text["title"], text["caption"], tag,
                        social_description=options["social_description"], hashtags=options["hashtags"],
                        social_caption=text["social_caption"], overlay=text["overlay"], waveform=bool(options["waveform"]),
                        music={key: info[key] for key in ("title", "bpm", "key")})


def check_options(options):
    """What can be checked before any file is touched, so a missing setting never uses up a new beat."""
    if options["visual_kind"] in ("image", "video") and not options["visual"]:
        raise ValueError("Choose a background image or video (or a folder of them), or switch Background to Automatic.")


def with_short_version(request, options, settings):
    """TikTok/Reels get the best `short_seconds` of the beat, starting on its beat grid (the BPM in the file
    name sets the grid when it's there). Worker thread: reads the audio."""
    seconds = float(options.get("short_seconds") or 0)
    if seconds <= 0:
        return replace(request, short_start=None, short_duration=0)
    from .snippet import best_start

    bpm = music.parse_name(request.audio.name).bpm
    return replace(request, short_start=best_start(request.audio, seconds, settings, bpm=bpm), short_duration=seconds)


def detector():
    """The organizer's drum detector, with its saved thresholds."""
    from drums import DrumDetector

    organizer = load_section("bounce_sorter")
    return DrumDetector(organizer["beat_threshold"], organizer["loop_threshold"], organizer["loop_name_hint"], settings=organizer)


def process(path, options, settings, credit_settings, *, cancelled=lambda: False, progress=lambda value: None,
            drums=None, report=lambda text: None):
    """One new file: skip it when it's a melody loop, else make both videos. Returns its ledger entry."""
    path = Path(path)
    if options.get("skip_loops", True):
        report(f"Listening for drums in {path.name}…")
        category, probability = (drums or detector()).classify(path)
        if category == "loop":
            return {"state": "skipped", "detail": "melody loop, no drums" + (f" ({probability:.0%})" if probability is not None else "")}
    report(f"Making the videos for {path.name}…")
    request = with_short_version(build_request(path, options, settings, credit_settings), options, settings)
    receipt = create_video(request, settings, cancelled, progress, formats=posting_formats(settings))
    return {"state": "ready", "detail": "videos ready to review", "receipt": str(receipt)}


def run_once(settings, *, cancelled=lambda: False, report=lambda text: None, notify=None):
    """The background helper's pass: handles every new file with the saved options (never posts).
    Returns the entries written."""
    if not settings.get("autopilot_enabled") or not settings.get("autopilot_folder"):
        return []
    options = {**DEFAULT_OPTIONS, **settings.get("autopilot_options", {})}
    if options["visual_kind"] == "project":
        options["visual_kind"] = "auto"  # recording a DAW needs someone at the Mac
    ledger = load_ledger()
    if not watching(settings["autopilot_folder"], ledger):
        turn_on(settings["autopilot_folder"])  # the folder changed: its existing files are the new backlog
        return []
    files = new_files(settings["autopilot_folder"], settings, ledger)
    if not files:
        return []
    try:
        check_options(options)
    except ValueError as error:
        report(f"Autopilot is waiting: {error}")
        return []
    credit_settings, drums, done = credits.load_settings(), None, []
    for path in files:
        if cancelled():
            break
        key = claim(path)
        if not key:
            continue  # the app (or another helper run) is already on it
        try:
            drums = drums or (detector() if options.get("skip_loops", True) else None)
            entry = process(path, options, settings, credit_settings, cancelled=cancelled, drums=drums, report=report)
        except Exception as error:  # one bad file never stops the others
            entry = {"state": "failed", "detail": str(error)}
        done.append(remember(path, entry, key))
    ready = [e for e in done if e["state"] == "ready"]
    if ready and notify:
        notify(f"{len(ready)} new beat video pair{'s' if len(ready) > 1 else ''} ready to review in Auto Poster.")
    return done


def posting_times(settings):
    """The daily times from the settings ("18:00"), as clock times, earliest first."""
    times = []
    for text in settings.get("post_times", []):
        try:
            times.append(datetime.strptime(text.strip(), "%H:%M").time())
        except ValueError:
            continue
    return sorted(set(times)) or [clock(18, 0)]


def next_free_times(count, settings, taken=(), now=None, days=120):
    """The next `count` daily posting times that no scheduled post already has, at least 10 minutes away."""
    now = now or datetime.now().astimezone()
    busy = {t.astimezone().replace(second=0, microsecond=0) for t in taken}
    found = []
    for offset in range(days):
        day = (now + timedelta(days=offset)).date()
        for at in posting_times(settings):
            when = datetime.combine(day, at).astimezone()
            if when >= now + timedelta(minutes=10) and when not in busy:
                found.append(when)
                if len(found) == count:
                    return found
    return found


def scheduled_times(entries):
    """When the posts still waiting in the queue go out."""
    times = []
    for entry in entries:
        if entry.get("state") in ("waiting", "posting"):
            try:
                times.append(datetime.fromisoformat(entry["due"]))
            except (KeyError, ValueError):
                continue
    return times


def activity(ledger, limit=50):
    """Newest first: (when, file name, what happened) for the Autopilot tab."""
    rows = sorted(ledger["files"].values(), key=lambda e: e.get("when", ""), reverse=True)[:limit]
    words = {"ready": "Videos ready to review", "skipped": "Skipped", "failed": "Failed", "working": "Working"}
    return [(e.get("when", "")[:16].replace("T", " "), e.get("name", ""),
             words.get(e.get("state"), e.get("state", "")) + (f": {e['detail']}" if e.get("detail") and e.get("state") != "ready" else ""))
            for e in rows]

