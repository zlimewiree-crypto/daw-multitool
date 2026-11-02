"""Auto Poster settings and local delivery receipts; credentials never go here."""
import json
import fcntl
from contextlib import contextmanager
from pathlib import Path
import re

from app_paths import atomic_json, load_section, update_config

NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}  # as people write them


def load_settings():
    return load_section("auto_poster")


def save_preferences(values):
    update_config("auto_poster", values)


def hashtag_line(text):
    """'typebeat, #rage  Producer' -> '#typebeat #rage #Producer' (adds missing #, drops repeats, keeps order)."""
    tags, seen = [], set()
    for word in re.split(r"[\s,]+", text):
        word = word.strip().lstrip("#")
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            tags.append("#" + word)
    return " ".join(tags)


def social_caption(description, hashtags):
    """The TikTok / Instagram caption: description, a blank line, then hashtags."""
    return "\n\n".join(part for part in (description.strip(), hashtag_line(hashtags)) if part)


def manifest(path):
    return json.loads(Path(path).read_text())


@contextmanager
def receipt_lock(path):
    """One writer per video, including across app processes; never wait behind a network transfer."""
    path = Path(path).resolve()
    with path.with_name(f".{path.name}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("This video is being updated in another window. Wait, then check its saved result.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def video_variant(record, format_name):
    """Select one saved version; retain old single-video receipts for previews/status checks."""
    if "variants" in record:
        try:
            return record["variants"][format_name]
        except KeyError:
            raise ValueError(f"This render is missing {format_name}. Create the videos again.") from None
    if record.get("format") and record["format"] != format_name:
        raise ValueError(f"This older render has no {format_name} version. Create the videos again.")
    return record


def save_delivery(path, platform, value):
    record = manifest(path)
    record.setdefault("delivery", {})[platform] = value
    atomic_json(path, record)
