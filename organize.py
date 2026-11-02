"""Sort the audio files currently in the export folder into Loops/, Beats/, Stems/, Unsorted/.

Usage:
    python organize.py --dry-run                      # show what would happen
    python organize.py                                # actually sort
    python organize.py --folder test_exports --dry-run  # try it on the fake test files
"""
import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from mutagen import File as MutagenFile

ROOT = Path(__file__).parent
TEMP_SUFFIXES = (".tmp", ".part", ".asd", ".reapeaks")


def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)


def is_audio(path, config):
    """True for real, finished-looking audio files (skips hidden and temp files)."""
    name = path.name.lower()
    if not path.is_file() or name.startswith(".") or name.endswith(TEMP_SUFFIXES):
        return False
    return path.suffix.lower() in config["extensions"]


def audio_length(path):
    """Length in seconds, or None if mutagen can't read the file."""
    try:
        audio = MutagenFile(path)
        return audio.info.length if audio else None
    except Exception:
        return None


def pick_category(path, config):
    """Tag words win first; if there's no tag, decide by length."""
    name = path.name.lower()
    for tag, folder in config["tags"].items():
        if name.startswith(tag.lower()):
            return folder
    length = audio_length(path)
    rules = config["length_rules"]
    if length is None:
        return config["unsorted_folder"]
    if length <= rules["loop_max_seconds"]:
        return config["tags"].get("loop_", "Loops")
    if length >= rules["beat_min_seconds"]:
        return config["tags"].get("beat_", "Beats")
    return config["unsorted_folder"]


def destination_folder(path, category, output, config):
    folder = output / category
    # Loops get a month subfolder (based on when the file was exported)
    if category == config["tags"].get("loop_", "Loops") and config.get("loops_by_month"):
        folder = folder / datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m")
    return folder


def unique_path(folder, name, taken):
    """Never overwrite: add ' (2)', ' (3)'... if the name is already used."""
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists() or target in taken:
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def finished_files(files, settle_seconds):
    """Keep only files whose size is non-zero and unchanged after waiting (export done)."""
    before = {f: f.stat().st_size for f in files}
    time.sleep(settle_seconds)
    ready = []
    for f in files:
        size = f.stat().st_size if f.exists() else -1
        if size > 0 and size == before[f]:
            ready.append(f)
        else:
            print(f"  skip (still exporting or empty): {f.name}")
    return ready


def log_move(output, src, dst):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(output / "_log.txt", "a") as log:
        log.write(f"{stamp} | {src} | {dst}\n")


def main():
    parser = argparse.ArgumentParser(description="Sort DAW exports into folders.")
    parser.add_argument("--dry-run", action="store_true", help="only print what would happen")
    parser.add_argument("--folder", help="sort this folder instead of watch_folder (output goes to <folder>/Sorted)")
    args = parser.parse_args()

    config = load_config()
    if args.folder:
        source = Path(args.folder).expanduser().resolve()
        output = source / "Sorted"
    else:
        source = Path(config["watch_folder"]).expanduser()
        output = Path(config["output_folder"]).expanduser()

    if not source.is_dir():
        print(f"Folder not found: {source}")
        return

    files = sorted(p for p in source.iterdir() if is_audio(p, config))
    print(f"{'DRY RUN — ' if args.dry_run else ''}{len(files)} audio file(s) in {source}")
    if not files:
        return
    files = finished_files(files, config["settle_seconds"])

    copy = config.get("mode") == "copy"
    taken = set()  # destinations already planned this run (matters for dry-run)
    for src in files:
        category = pick_category(src, config)
        folder = destination_folder(src, category, output, config)
        dst = unique_path(folder, src.name, taken)
        taken.add(dst)
        print(f"  {src.name}  ->  {dst.relative_to(output)}")
        if args.dry_run:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copy2(src, dst)
        else:
            shutil.move(src, dst)
        log_move(output, src, dst)

    if not args.dry_run:
        print(f"Done. Log: {output / '_log.txt'}")


if __name__ == "__main__":
    main()
