"""Bounce sorter engine: find bounces, decide beat/loop, plan names, then move safely.

The window (bounce_sorter_app.py) uses this, but it also runs on its own:
    python sorter.py                     # preview only (nothing moves)
    python sorter.py --folder test_exports/bounces
    python sorter.py --run               # actually sort, using the saved settings
"""
import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mutagen import File as MutagenFile

from app_paths import DATA_DIR, RESOURCE_DIR, load_section, update_config
from drums import DrumDetector, load_labels

ROOT = Path(__file__).parent
CONFIG_FILE = DATA_DIR / "config.json"
TEMP_SUFFIXES = (".tmp", ".part", ".asd", ".reapeaks", ".crdownload", ".download")

DEFAULTS = json.loads((RESOURCE_DIR / "config.example.json").read_text())["bounce_sorter"]


# ---------- settings ----------
def load_settings():
    """The bounce_sorter section of config.json, with defaults for anything missing."""
    return load_section("bounce_sorter", CONFIG_FILE)


def save_settings(settings):
    update_config("bounce_sorter", settings, CONFIG_FILE)


def output_root(settings):
    return Path(settings["output_folder"] or settings["source_folder"]).expanduser().resolve()


def sorted_root(settings):
    """The named folder that holds Beats/, Melodies/ and Needs Review/."""
    base = output_root(settings)
    name = folder_name(settings["sorted_folder_name"].strip() or DEFAULTS["sorted_folder_name"])
    folder = (base / name).resolve()
    if folder == base or not folder.is_relative_to(base):
        raise ValueError("Your sorted folder must be inside the output folder.")
    return folder


def folder_name(value):
    """Treat names as one folder; Finder displays POSIX ':' as '/' on macOS."""
    value = value.strip()
    if not value or Path(value).is_absolute() or any(part in (".", "..") for part in value.split("/")) or any(c in value for c in ("\0", "\n", "\r")):
        raise ValueError("Enter a folder name, without an absolute path or '.' / '..' components.")
    return value.replace("/", ":" if sys.platform == "darwin" else "-")


def previous_sorted_root(settings):
    """Keep existing nested exports discoverable; never rename/migrate the user's folders."""
    base = output_root(settings)
    previous = (base / settings["sorted_folder_name"]).resolve()
    return previous if previous != base and previous.is_relative_to(base) and previous.is_dir() else None


def inside_sorted(path, roots):
    for root in roots:
        if root is None:
            continue
        if root in path.parents:
            return True
        # Path spelling can differ on the default case-insensitive macOS filesystem.
        if root.exists() and any(parent.exists() and parent.samefile(root) for parent in path.parents):
            return True
    return False


# ---------- finding files ----------
def created_time(path):
    """When the file was made (macOS birth time; falls back to modified time)."""
    st = path.stat()
    return datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))


def audio_seconds(path):
    try:
        audio = MutagenFile(path)
        return audio.info.length if audio is not None else None  # untagged WAVs are falsy, so check None
    except Exception:
        return None


def find_bounces(settings):
    """Files in the source folder that pass every filter, plus a list of (file, reason) skips."""
    source = Path(settings["source_folder"]).expanduser().resolve()
    already_sorted = sorted_root(settings)
    old_sorted = previous_sorted_root(settings)
    exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in settings["extensions"]}
    contains = [w.strip().lower() for w in settings["name_contains"].split(",") if w.strip()]
    excludes = [w.strip().lower() for w in settings["name_excludes"].split(",") if w.strip()]
    newest_ok = time.time() - settings["only_recent_days"] * 86400

    candidates = source.rglob("*") if settings["include_subfolders"] else source.iterdir()
    found, skipped = [], []
    for p in candidates:
        name = p.name.lower()
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if p.is_symlink() or inside_sorted(p, (already_sorted, old_sorted)) or any(part.startswith(".") for part in p.relative_to(source).parts):
            continue  # already sorted, or inside a hidden folder
        if name.startswith(".") or name.endswith(TEMP_SUFFIXES):
            continue
        if contains and not any(w in name for w in contains):
            continue
        if any(w in name for w in excludes):
            continue
        if settings["only_recent_days"] and created_time(p).timestamp() < newest_ok:
            continue
        secs = audio_seconds(p)
        if secs is not None and secs < settings["min_seconds"]:
            skipped.append((p, f"shorter than {settings['min_seconds']}s"))
            continue
        found.append(p)
    return sorted(found), skipped


# ---------- planning ----------
@dataclass
class PlannedMove:
    src: Path
    category: str  # "beat" | "loop" | "review"
    probability: float | None  # drum probability, None if silent/unreadable
    new_name: str
    destination: Path | None = None
    features: dict | None = None
    duplicate_of: Path | None = None  # another file in this preview holds the same audio
    duplicate_kind: str = ""  # "identical" (same bytes) or "same name" (only a guess: listen to check)
    music: dict | None = None  # {"bpm", "bpm_source", "key", "key_source"}: from the name, or heard ("audio")

    @property
    def path(self):
        return self.destination or self.src

    def folder(self, settings):
        key = {"beat": "beats_folder_name", "loop": "loops_folder_name", "review": "review_folder_name"}[self.category]
        folder = sorted_root(settings) / folder_name(settings[key])
        if folder.resolve() == sorted_root(settings) or not folder.resolve().is_relative_to(sorted_root(settings)):
            raise ValueError("Category folders must be inside your sorted folder.")
        if settings["split_by_month"] and self.category != "review":
            made = created_time(self.src)
            folder = folder / str(made.year) / made.strftime("%m %B")  # "10 October" sorts in month order
        return folder


def music_info(path, features=None):
    """BPM and key for a file: what its name says, else what the analysis heard (cached in its features)."""
    from multitool import music

    return music.describe(path, measured=(features or {}).get("music") or {}, listen=False)


def bpm_key_suffix(path, info):
    """' 154 c#min' style text with only what the name is missing; '' when there's nothing to add."""
    from multitool import music

    named = music.parse_name(path.name)
    parts = []
    if not named.bpm and info and info.get("bpm"):
        parts.append(str(round(info["bpm"])))
    if not named.key and info and info.get("key"):
        parts.append(info["key"].short)
    return " " + " ".join(parts) if parts else ""


def new_name(path, settings, info=None):
    """The name a file gets when sorted: optionally the date it was made and the BPM/key its name lacks."""
    stem = path.stem
    if settings.get("add_bpm_key"):
        stem += bpm_key_suffix(path, info)
    if not settings["add_date"]:
        return stem + path.suffix
    stamp = created_time(path).strftime(settings["date_format"])
    if stamp in stem:
        return stem + path.suffix  # date already there; don't add it twice
    if settings["date_position"] == "end":
        return f"{stem} {stamp}{path.suffix}"
    return f"{stamp} {stem}{path.suffix}"


def plan(settings, progress=None, cancelled=lambda: False):
    """Analyse every bounce and return (planned moves, skipped). Moves nothing."""
    files, skipped = find_bounces(settings)
    detector = DrumDetector(settings["beat_threshold"], settings["loop_threshold"], settings["loop_name_hint"], settings=settings)
    detector.analyse_many(files, progress, cancelled)  # the slow part, spread over all CPU cores
    labels = load_labels()
    moves = []
    for path in files:
        if cancelled():
            break
        if not detector.is_cached(path):
            skipped.append((path, detector.skipped.get(path, "file changed during analysis; preview again when finished")))
            continue
        category, prob = detector.classify(path)
        if str(path) in labels:
            category = labels[str(path)]["label"]  # the user's own label always wins
        features = detector.features(path)
        info = music_info(path, features)
        moves.append(PlannedMove(path, category, prob, new_name(path, settings, info), features=features, music=info))
    mark_duplicates(moves)
    return moves, skipped


# ---------- duplicates (flagged for review only; nothing is ever deleted) ----------
COPY_SUFFIX = re.compile(r"\s*\(\d+\)$")  # "beat (1)" = the browser's second download of "beat"


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _keeper(group):
    """The copy to keep: no " (1)" suffix, then the biggest (wav over mp3), then the oldest."""
    return min(group, key=lambda m: (bool(COPY_SUFFIX.search(m.src.stem)), -m.src.stat().st_size, created_time(m.src)))


def mark_duplicates(moves, length_tolerance=0.2):
    """Set duplicate_of on every extra copy: byte-identical files, or the same name (ignoring
    " (1)" and the extension) with the same length. Reads files only; moves nothing."""
    present = [m for m in moves if m.src.is_file()]  # a file removed mid-preview is simply not compared
    by_size = {}
    for m in present:
        by_size.setdefault(m.src.stat().st_size, []).append(m)
    groups = []
    for same_size in by_size.values():
        if len(same_size) > 1:  # only files of equal size can be byte-identical; hash just those
            by_hash = {}
            for m in same_size:
                try:
                    by_hash.setdefault(_sha1(m.src), []).append(m)
                except OSError:
                    pass  # unreadable right now: not flagged
            groups += [(g, "identical") for g in by_hash.values() if len(g) > 1]
    by_name = {}
    for m in present:
        by_name.setdefault(COPY_SUFFIX.sub("", m.src.stem).lower(), []).append(m)
    for same_name in by_name.values():
        if len(same_name) > 1:
            first = _keeper(same_name)
            target = audio_seconds(first.src)
            matching = [m for m in same_name if target is not None and (m is first or (
                (length := audio_seconds(m.src)) is not None and abs(length - target) <= length_tolerance))]
            if len(matching) > 1:
                groups.append((matching, "same name"))
    for group, kind in groups:  # identical groups come first, so they win over a same-name guess
        keep = _keeper(group)
        for m in group:
            if m is not keep and m.duplicate_of is None:
                m.duplicate_of, m.duplicate_kind = keep.src, kind


def load_sorted(settings):
    roots = dict.fromkeys(root for root in (sorted_root(settings), previous_sorted_root(settings)) if root is not None)
    return [move for root in roots for move in _load_sorted_root(settings, root)]


def _load_sorted_root(settings, root):
    """Reopen completed transfers for review, without scanning or moving originals."""
    categories = {(root / folder_name(settings[key])).resolve(): cat for key, cat in (
        ("beats_folder_name", "beat"), ("loops_folder_name", "loop"), ("review_folder_name", "review"))}
    reviewed = {}
    log_path = root / "_log.txt"
    if not log_path.exists():
        return []
    for line in log_path.read_text().splitlines():
        parts = line.split(" | ", 4)
        if len(parts) != 5 or parts[1] != "done":
            continue
        path = Path(parts[4]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        category = next((cat for folder, cat in categories.items() if path.is_relative_to(folder)), None)
        if category and path.is_file():
            reviewed[path] = PlannedMove(path, category, None, path.name, destination=path, music=music_info(path))
    return list(reviewed.values())


# ---------- moving safely ----------
def reserve_destination(folder, name):
    """Atomically claim a free name (adds ' (2)', ' (3)'...). Never overwrites, even if two runs race."""
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError("The new filename must not contain folders; use a date format without slashes.")
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while True:
        target = folder / (name if n == 1 else f"{stem} ({n}){suffix}")
        try:
            os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY))  # empty placeholder
            return target
        except FileExistsError:
            n += 1


def snapshot(path):
    """(size, modified time), or None if the file is gone."""
    try:
        st = path.stat()
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def journal(log, status, action, src, dst):
    """Write one log line and force it to disk, so the log survives a crash mid-move."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"{stamp} | {status} | {action} | {src} | {dst}\n")
    log.flush()
    os.fsync(log.fileno())


def copy_into(src, dst):
    """Copy via a hidden '.partial' file, so an interrupted copy never sits at the final name."""
    partial = dst.with_name(f".{dst.name}.{uuid.uuid4().hex}.partial")
    try:
        before = snapshot(src)
        with src.open("rb") as source, partial.open("xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        if before is None or snapshot(src) != before or partial.stat().st_size != before[0]:
            raise OSError("Source changed during copying; original and unfinished copy were kept.")
        shutil.copystat(src, partial)
        os.replace(partial, dst)  # replaces only our own empty placeholder
    except Exception as error:
        raise OSError(f"Copy failed; original preserved. Incomplete copy, if any: {partial}") from error


def execute(moves, settings, report=print, cancelled=lambda: False):
    """Move (or copy) each planned file. Every change is journalled to <output>/_log.txt:
    a 'started' line before touching the file and a 'done' or 'failed' line after."""
    if settings["mode"] not in ("move", "copy"):
        raise ValueError(f"mode must be 'move' or 'copy', not {settings['mode']!r}")
    out = sorted_root(settings)
    out.mkdir(parents=True, exist_ok=True)

    # Wait once for the whole batch, then require each file to be unchanged right before its own move
    before = {m.src: snapshot(m.src) for m in moves}
    deadline = time.monotonic() + settings["settle_seconds"]
    while time.monotonic() < deadline:
        if cancelled():
            report("Stopped before moving any files.")
            return 0
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    done = 0
    # Open the log before touching anything, so a log problem stops us before any move
    with open(out / "_log.txt", "a") as log:
        for m in moves:
            if m.destination is not None:
                continue  # review rows must never be sorted a second time
            if cancelled():
                report("Stopped. Files not yet sorted were left where they are.")
                break
            snap = snapshot(m.src)
            if snap is None or snap[0] == 0 or snap != before[m.src]:
                report(f"skip (missing, empty or still being written): {m.src.name}")
                continue
            folder = m.folder(settings)
            folder.mkdir(parents=True, exist_ok=True)
            dst = reserve_destination(folder, m.new_name)
            action = settings["mode"]
            journal(log, "started", action, m.src, dst)
            try:
                if action == "move":
                    try:
                        os.replace(m.src, dst)  # replaces only our own empty placeholder
                    except OSError as e:
                        if e.errno != errno.EXDEV:
                            raise
                        action = "copy"  # different drive: copy and leave the original in place
                if action == "copy":
                    copy_into(m.src, dst)
            except Exception as e:
                if dst.exists() and dst.stat().st_size == 0:
                    dst.unlink()  # remove only our empty placeholder, never audio
                journal(log, "failed", action, m.src, dst)
                report(f"FAILED {m.src.name}: {e}")
                continue
            m.destination = dst
            journal(log, "done", action, m.src, dst)
            done += 1
            report(f"{action}: {m.src.name} -> {dst.relative_to(out)}")
    return done


# ---------- command line ----------
def describe(m, settings):
    pct = "  ?" if m.probability is None else f"{m.probability * 100:3.0f}"
    dest = m.folder(settings).relative_to(sorted_root(settings)) / m.new_name
    dup = (f"   (same file as {m.duplicate_of.name}: left unchecked in the window)" if m.duplicate_kind == "identical"
           else f"   (maybe a copy of {m.duplicate_of.name}: listen to check)" if m.duplicate_of else "")
    return f"[{pct}% drums] {m.src.name}  ->  {dest}{dup}"


def main():
    parser = argparse.ArgumentParser(description="Sort bounces into Finished Beats / Melody Loops.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="actually move files (default is preview only)")
    mode.add_argument("--dry-run", action="store_true", help="preview only (the default)")
    parser.add_argument("--folder", help="look in this folder instead of the saved source folder")
    args = parser.parse_args()

    settings = load_settings()
    if args.folder:
        settings["source_folder"] = args.folder
        settings["output_folder"] = ""
    print(f"{'' if args.run else 'PREVIEW (nothing moves) — '}{Path(settings['source_folder']).expanduser()}")
    moves, skipped = plan(settings, progress=lambda i, n, name: print(f"\r  analysing {i}/{n}", end="", flush=True))
    print()
    for m in moves:
        print("  " + describe(m, settings))
    for p, why in skipped:
        print(f"  skipped {p.name}: {why}")
    if args.run and moves:
        print(f"\n{execute(moves, settings, report=lambda s: print('  ' + s))} file(s) sorted.")


if __name__ == "__main__":
    main()
