"""Varispeed: speed a beat up or slow it down like tape, so the pitch moves with the speed (+12 semitones =
twice as fast, -12 = half speed). The originals are only read; each copy is a new file, never overwriting one.

    python -m multitool.varispeed.core BEAT.wav --speed "+2 st"          # dry run: prints the new name
    python -m multitool.varispeed.core BEAT.wav --speed 112% --run       # saves the copy
"""
import math
import os
from pathlib import Path
import re
import subprocess
import uuid

import numpy as np

from app_paths import load_section
from multitool import music


def settings():
    return load_section("varispeed")


def speed_from_semitones(semitones):
    return 2 ** (semitones / 12)


def semitones_from_speed(speed):
    return 12 * math.log2(speed)


def clamp(speed, options=None):
    options = options or settings()
    return min(max(speed, options["min_speed"]), options["max_speed"])


def percent_text(speed):
    """'112.2%': the big readout."""
    return f"{speed * 100:.1f}%"


def semitone_text(semitones):
    """'0 st', '+2 st', '−2.53 st': whole semitones without decimals, a real minus sign."""
    if abs(semitones) < 0.005:
        return "0 st"
    exact = abs(semitones - round(semitones)) < 0.005
    return ("−" if semitones < 0 else "+") + (f"{abs(semitones):.0f}" if exact else f"{abs(semitones):.2f}") + " st"


def readout(speed):
    """'112.2% · +2 st' for status lines."""
    return f"{percent_text(speed)} · {semitone_text(semitones_from_speed(speed))}"


def semitone_limits(options=None):
    options = options or settings()
    return semitones_from_speed(options["min_speed"]), semitones_from_speed(options["max_speed"])


def slider_semitones(position, options=None):
    """The slider runs -1..1 with 100% in the middle: the left half covers the slowest speed to 100%, the right
    half 100% to the fastest. It snaps to 100% close to the middle."""
    low, high = semitone_limits(options)
    position = 0.0 if abs(position) < 0.015 else max(-1.0, min(1.0, position))
    return position * (-low if position < 0 else high)


def slider_position(speed, options=None):
    low, high = semitone_limits(options)
    st = semitones_from_speed(speed)
    return st / -low if st < 0 else st / high


def set_semitones(semitones, options=None):
    low, high = semitone_limits(options)
    return speed_from_semitones(min(max(semitones, low), high))


def nudge(speed, semitones, options=None):
    """Up or down by some semitones from where it is now; float fuzz is rounded away so repeated bumps land
    on exact semitones. Stops at the limits."""
    return set_semitones(round(semitones_from_speed(speed) * 1000) / 1000 + semitones, options)


def edited_number(text, unit):
    """A number typed into a readout: a leading + or − (the typographic minus too), the unit and a comma
    decimal point are all accepted. None when it isn't a number."""
    cleaned = (text.lower().replace(unit.lower(), "").replace("\u2212", "-").replace(",", ".").replace("+", "")).strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def glide_seconds(start, target, rate, options=None):
    """How long a glide takes. Like a tape motor it changes 1/speed at a steady rate: at most
    max_rise_semitones_per_period going up and max_fall going down per period of `period_frames`, so an octave
    takes about 0.37 s down and 0.74 s up at 48 kHz, and never less than min_glide_seconds."""
    options = options or settings()
    per_period = options["max_rise_semitones_per_period" if target > start else "max_fall_semitones_per_period"]
    rate_per_frame = math.log(2) / 12 * per_period / options["period_frames"]
    return max(options["min_glide_seconds"], abs(1 / start - 1 / target) / (rate_per_frame * rate))


def parse_amount(text):
    """A typed speed: "112%", "1.5x", "+2 st", "-3 semitones", "+2" (semitones) or "85" (a percentage)."""
    text = text.strip().lower().replace("−", "-").replace(",", ".")
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(%|x|×|st|semi|semis|semitones?)?", text)
    if not match:
        raise ValueError("Type a speed like 112%, 1.5x or +2 st.")
    value, unit = float(match[1]), match[2]
    if unit == "%" or (unit is None and abs(value) > 24):
        speed = value / 100
    elif unit in ("x", "×"):
        speed = value
    else:
        speed = speed_from_semitones(value)
    if speed <= 0:
        raise ValueError("The speed must be above 0%.")
    return speed


def whole(semitones, tolerance=0.02):
    """The whole number of semitones, when the speed is (almost) exactly one; else None."""
    nearest = round(semitones)
    return nearest if abs(semitones - nearest) <= tolerance else None


def transpose(key, steps):
    return music.Key(music.NOTES[(music.NOTES.index(key.root) + steps) % 12], key.minor)


def changed(info, speed):
    """The BPM and key at this speed: {"bpm", "key", "key_exact"} (key_exact False between two keys)."""
    st = semitones_from_speed(speed)
    steps = whole(st)
    key = info.get("key")
    return {"bpm": round(info["bpm"] * speed) if info.get("bpm") else None,
            "key": transpose(key, steps if steps is not None else round(st)) if key else None,
            "key_exact": steps is not None}


def percent_label(speed):
    """'112%' or '112.2%': whole percentages stay short."""
    value = speed * 100
    return f"{value:.0f}%" if abs(value - round(value)) < 0.05 else f"{value:.1f}%"


def restyle(match, key):
    """The new key written the way the name wrote the old one: "C# Minor" -> "D Minor", "fmin" -> "gmin",
    "Ebm" -> "Fm" (sharps, since names mix both)."""
    letter, _, mode = match.groups()
    root = key.root if letter.isupper() else key.root.lower()
    spaced = " " if " " in match[0].strip() else ""
    if (key.minor and mode.lower().startswith("m") and not mode.lower().startswith("maj")) or \
            (not key.minor and mode.lower().startswith("maj")):
        word = mode  # same mode: keep its spelling ("Minor", "min", "m")
    else:
        word = ("minor" if key.minor else "major") if len(mode) > 3 else ("min" if key.minor else "maj")
        word = word.capitalize() if mode[:1].isupper() else word
    return f"{root}{spaced}{word}"


def new_name(path, speed, info, extension=".wav"):
    """"infernal 155 fmin.mp3" at +2 st -> "infernal 174 gmin (varispeed 112%).wav": BPM and key written in the
    name are updated (a key between two keys gets no key), anything else is kept."""
    path = Path(path)
    stem, now = path.stem, changed(info, speed)
    named = music.parse_name(path.name)
    if named.bpm and now["bpm"]:
        stem = re.sub(rf"(?<!\d){named.bpm}(?!\d)", str(now["bpm"]), stem, count=1)
    if named.key:
        match = next((m for m in music.KEY.finditer(stem)
                      if music.parse_name(m[0] + " 100").key == named.key), None)
        if match:
            replacement = restyle(match, now["key"]) if now["key"] and now["key_exact"] else ""
            stem = (stem[:match.start()] + replacement + stem[match.end():]).strip()
            stem = re.sub(r"\s{2,}", " ", stem)
    return f"{stem} (varispeed {percent_label(speed)}){extension}"


def describe(path):
    """BPM and key from the name (else heard), for the list and the new name."""
    return music.describe(Path(path))


def ffmpeg():
    from multitool.auto_poster.render import ffmpeg_binary
    from multitool.auto_poster.storage import load_settings
    return ffmpeg_binary(load_settings())


def sample_rate(path):
    """The file's own sample rate (copies keep it), read from FFmpeg's description of the file."""
    result = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=60)
    match = re.search(r"Audio:.*?(\d{4,6}) Hz", result.stderr)
    if not match:
        raise ValueError(f"Couldn't read the audio in {Path(path).name}.")
    return int(match[1])


def decode(path, rate=None):
    """(stereo float32 samples, sample rate) for any format FFmpeg reads; the file is only read."""
    rate = rate or sample_rate(path)
    result = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-vn",
                             "-ac", "2", "-ar", str(rate), "-f", "f32le", "-"], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=600)
    if result.returncode or not result.stdout:
        raise ValueError(f"Couldn't read the audio in {Path(path).name}.")
    return np.frombuffer(result.stdout, dtype=np.float32).reshape(-1, 2).copy(), rate


def varispeed(samples, rate, speed):
    """Tape-style: the same samples played `speed` times faster, resampled back to `rate` at studio quality
    (soxr, very high quality). Faster is shorter and higher; slower is longer and lower."""
    import soxr
    if abs(speed - 1) < 1e-9:
        return samples.copy()
    return soxr.resample(samples, rate * speed, rate, quality="VHQ").astype(np.float32)


def save(path, speed, folder, options=None, info=None, cancelled=lambda: False):
    """Writes one varispeed copy into `folder` and returns its path. The name never replaces an existing file:
    " (2)", " (3)" are added; the copy is written under a temporary name first, then linked into place."""
    import soundfile

    options = options or settings()
    path, folder = Path(path), Path(folder).expanduser()
    info = info or describe(path)
    samples, rate = decode(path)
    if cancelled():
        return None
    out = varispeed(samples, rate, speed)
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 1.0:  # resampling can overshoot a mastered beat by a hair; never write a clipped copy
        out = out / peak
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".varispeed-{uuid.uuid4().hex}.partial.wav"
    soundfile.write(partial, out, rate, subtype=options["sample_format"])
    try:
        name = new_name(path, speed, info)
        stem, suffix = Path(name).stem, Path(name).suffix
        for number in range(1, 1000):
            target = folder / (name if number == 1 else f"{stem} ({number}){suffix}")
            try:
                os.link(partial, target)  # fails if the name exists: nothing is ever overwritten
                return target
            except FileExistsError:
                continue
        raise ValueError(f"Too many copies named {name} in {folder}.")
    finally:
        partial.unlink(missing_ok=True)  # only our own temporary copy


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Save a varispeed copy of a beat (default: dry run, saves nothing).")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--speed", required=True, help='e.g. 112%%, 1.5x or "+2 st"')
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true", help="save the copy")
    args = parser.parse_args()
    options = settings()
    speed = clamp(parse_amount(args.speed), options)
    folder = args.output or Path(options["output_folder"]).expanduser()
    info = describe(args.audio)
    print(f"{args.audio.name} at {readout(speed)} -> {folder / new_name(args.audio, speed, info)}")
    if args.run:
        print(f"Saved {save(args.audio, speed, folder, options, info)}")
    else:
        print("DRY RUN: nothing saved. Add --run to save the copy.")


if __name__ == "__main__":
    main()
