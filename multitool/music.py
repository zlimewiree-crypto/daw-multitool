"""Tempo (BPM) and key of a beat, shared by the organizer and Auto Poster.

Producers usually put them in the file name ("infernal 155 fmin.mp3", "Night 140bpm C# Minor.wav"), so the name
is the reference: whatever it states is used as-is. Only what the name leaves out is worked out from the audio
(read-only), and those values are marked as heard ("audio") rather than named.
"""
from dataclasses import dataclass
import re

from app_paths import load_section

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"}
_ACCIDENTAL = r"(?:#|♯|b|♭|-?sharp|-?flat)"
_MODE = r"(?:major|minor|maj|min|m)"
# "fmin", "F#m", "C# Minor", "a#min", "Bb maj", "E-flat minor": a letter, maybe sharp/flat, then the mode
KEY = re.compile(rf"(?<![A-Za-z0-9#])([A-Ga-g])\s?((?i:{_ACCIDENTAL}))?\s?((?i:{_MODE}))(?![A-Za-z0-9])")
BPM_WORD = re.compile(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*-?\s*bpm(?![a-z])|bpm\s*-?\s*(\d{2,3})(?!\d)", re.I)
NUMBER = re.compile(r"(?<![\w.#+@])(\d{2,3})(?![\w.%])")


@dataclass(frozen=True)
class Key:
    root: str  # "C#" (sharps; flats are spelled as their sharp)
    minor: bool

    @property
    def name(self):
        """'C# minor' for titles and captions."""
        return f"{self.root} {'minor' if self.minor else 'major'}"

    @property
    def short(self):
        """'c#min' / 'cmaj', the way producers write it in file names."""
        return f"{self.root.lower()}{'min' if self.minor else 'maj'}"


def _key_from(letter, accidental, mode):
    root = letter.upper()
    accidental = (accidental or "").lower().lstrip("-")
    if accidental in ("#", "♯", "sharp"):
        root += "#"
    elif accidental in ("b", "♭", "flat"):
        root = _FLATS.get(root + "b", root)
        if root not in NOTES:  # "Cb" etc. were mapped above; anything else is not a key
            return None
    root = _FLATS.get(root, root)
    if root not in NOTES:
        return None
    return Key(root, not mode.lower().startswith("maj"))


@dataclass(frozen=True)
class NameInfo:
    bpm: int | None
    key: Key | None
    title: str  # the name without BPM, key, copy numbers and credits: "infernal"


def _bpm_in_range(value, low=55, high=230):
    return low <= value <= high


def parse_name(text, remove=()):
    """BPM, key and a clean title from a file name or stem. `remove`: words to drop from the title as well
    (collaborator names already credited elsewhere)."""
    stem = re.sub(r"\.(wav|mp3|aiff?|flac|m4a|aac|ogg|opus)$", "", text, flags=re.I)
    cleaned = stem
    bpm = None
    for match in BPM_WORD.finditer(stem):
        value = float(match[1] or match[2])
        if _bpm_in_range(value):
            bpm = round(value)
            cleaned = cleaned.replace(match[0], " ")
            break
    key = None
    for match in KEY.finditer(stem):
        letter, accidental, mode = match.groups()
        bare_m = mode.lower() == "m"
        # "am"/"em" are also words and "4 AM" is a time: a bare "m" must be lower case, and counts only with a
        # sharp/flat, a capital letter or a BPM beside it
        if bare_m and (mode == "M" or not (accidental or letter.isupper() or _beside_number(stem, match))):
            continue
        if accidental and accidental.lower() == "b" and letter.lower() == "b" and mode.lower() == "m" and not letter.isupper():
            continue  # "bbm" in lower case is too likely to be something else
        found = _key_from(letter, accidental, mode)
        if found:
            key = found
            cleaned = cleaned.replace(match[0], " ", 1)
            break
    if bpm is None:
        candidates = [m for m in NUMBER.finditer(cleaned) if _bpm_in_range(int(m[1])) and not _copy_number(cleaned, m)]
        if candidates:
            bpm = int(candidates[0][1])
            cleaned = cleaned[:candidates[0].start()] + " " + cleaned[candidates[0].end():]
    return NameInfo(bpm, key, clean_title(cleaned, remove) or clean_title(stem, ()) or stem.strip())


def _beside_number(text, match):
    before, after = text[:match.start()].rstrip(), text[match.end():].lstrip()
    return bool(re.search(r"\d{2,3}\s*(bpm)?$", before, re.I) or re.match(r"\d{2,3}(?!\d)", after))


def _copy_number(text, match):
    """'(1)', '(2)' after a browser's duplicate download, or 'v2'-style versions: not a tempo."""
    return text[max(0, match.start() - 1):match.start()] == "(" and text[match.end():match.end() + 1] == ")"


def clean_title(text, remove=()):
    """The beat's name for a video title: no copy numbers, @handles, prod./credit brackets, [FREE] tags,
    leftover separators or the names in `remove`."""
    text = re.sub(r"\(\s*\d+\s*\)", " ", text)  # " (1)" browser copies
    text = re.sub(r"[+-]?\d+\s*cents?\b|\ba4\d\d\b", " ", text, flags=re.I)  # tuning notes: "+50cents", "a432"
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[-.]\d{1,2}[-.]\d{2,4}\b", " ", text)  # dates the organizer may add
    text = re.sub(r"@[\w.]+", " ", text)
    text = re.sub(r"[\(\[]\s*(prod|produced|ft|feat|w)\b[^\)\]]*[\)\]]", " ", text, flags=re.I)
    text = re.sub(r"\bprod\b\.?\s*(by\s+)?.*$", " ", text, flags=re.I)
    text = re.sub(r"[\(\[]\s*free\s*[\)\]]|\bfree\b", " ", text, flags=re.I)
    for word in remove:
        if word:
            text = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", " ", text, flags=re.I)
    text = re.sub(r"[\(\[]\s*(x|&|,|\+|\s)*[\)\]]", " ", text)  # brackets left empty
    text = re.sub(r"(\s(x|&|w/|\+)\s*)+$", " ", text, flags=re.I)  # a dangling "x" after removed names
    text = re.sub(r"[_]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -_.,|·+&").strip()


# ---------- from the audio ----------
# Key profiles: the scale's notes, with the tonic and fifth heard most. Tested against Krumhansl-Kessler on the
# labelled copies in test_exports/real (named keys as the answer): 10/14 right versus 6/14.
MAJOR_PROFILE = [2, 0, 1, 0, 1, 1, 0, 1.5, 0, 1, 0, 1]
MINOR_PROFILE = [2, 0, 1, 1.5, 0, 1, 0, 1.5, 1, 0, 1, 0]


def key_from_chroma(chroma, minor_bias=0.0):
    """(Key, confidence 0..1) from a 12-value pitch-class profile. `minor_bias` favours minor keys, which almost
    all trap and drill beats use: without it a minor key is often read as its relative major (C# minor -> E major)."""
    import numpy as np

    chroma = np.asarray(chroma, dtype=float)
    if chroma.shape != (12,) or not np.isfinite(chroma).all() or chroma.std() < 1e-9:
        return None, 0.0
    scores = []
    for minor, profile in ((False, MAJOR_PROFILE), (True, MINOR_PROFILE)):
        for shift in range(12):
            r = float(np.corrcoef(chroma, np.roll(profile, shift))[0, 1]) + (minor_bias if minor else 0.0)
            scores.append((r, Key(NOTES[shift], minor)))
    scores.sort(key=lambda item: item[0], reverse=True)
    (best, key), (second, _) = scores[0], scores[1]
    return key, max(0.0, min(1.0, (best - second) * 5))


def fold_tempo(bpm, low, high):
    """Doubles or halves a tempo until it sits in [low, high): 77.5 -> 155 for a trap beat heard in half time."""
    if not bpm or bpm <= 0 or high < 2 * low:
        return bpm or None
    while bpm < low:
        bpm *= 2
    while bpm >= high:
        bpm /= 2
    return bpm


def music_settings(overrides=None):
    """The "music" section of config.json (tempo range, minor-key preference, ...), with optional overrides."""
    return {**load_section("music"), **(overrides or {})}


def analyse_audio(path, settings=None, *, need_bpm=True, need_key=True):
    """{'bpm': float | None, 'key': Key | None, 'key_confidence': 0..1} measured from the audio (read-only)."""
    import librosa
    import numpy as np

    settings = music_settings(settings)
    seconds = settings["analysis_seconds"]
    y, sr = librosa.load(str(path), sr=22050, mono=True, duration=seconds + 15)
    result = {"bpm": None, "key": None, "key_confidence": 0.0}
    if len(y) < sr * 4 or not np.isfinite(y).all() or np.max(np.abs(y)) < 1e-3:
        return result
    if len(y) > sr * 30:
        y = y[sr * 10:]  # skip the intro: the groove and the harmony have settled after it
    harmonic, percussive = librosa.effects.hpss(y)
    if need_bpm:
        hop = 256
        envelope = librosa.onset.onset_strength(y=percussive, sr=sr, hop_length=hop, aggregate=np.median)
        rough = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=envelope, sr=sr, hop_length=hop,
                                                           start_bpm=settings["bpm_center"]))[0])
        tempo = pick_tempo(envelope, sr / hop, rough, settings["bpm_center"])
        low, high = settings["bpm_range"]
        result["bpm"] = fold_tempo(tempo, low, high)
    if need_key:
        # the melody's range (C3-C7): 808 glides and kick tails below it blur which note is home
        chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, hop_length=2048, bins_per_octave=36,
                                            fmin=librosa.note_to_hz("C3"), n_octaves=4)
        result["key"], result["key_confidence"] = key_from_chroma(chroma.mean(1), settings["key_minor_bias"])
    return result


def _autocorrelation(envelope):
    import numpy as np

    e = np.asarray(envelope, dtype=float) - np.mean(envelope)
    size = 1 << (2 * len(e) - 1).bit_length()
    spectrum = np.fft.rfft(e, n=size)
    ac = np.fft.irfft(spectrum * spectrum.conj(), n=size)[:len(e)]
    return ac / (ac[0] + 1e-12)


def _peak(ac, lag):
    """(interpolated lag, height) of the autocorrelation peak within 3% of `lag`; None past the end."""
    lo, hi = max(1, int(lag * 0.97)), int(lag * 1.03) + 2
    if hi >= len(ac) - 1:
        return None
    i = lo + int(ac[lo:hi].argmax())
    a, b, c = ac[i - 1], ac[i], ac[i + 1]
    curve = a - 2 * b + c
    return i + (0.5 * (a - c) / curve if curve else 0.0), b


def pick_tempo(envelope, frames_per_second, rough, center=150):
    """The tempo whose beat, two beats and bar all repeat strongly, favouring tempos near `center`. Checks the
    rough estimate's double, half and 3:2 relatives, the usual mistakes on triplet hi-hats (165 heard as 110),
    then reads the winner to a fraction of a BPM from its 1-, 2- and 4-beat peaks."""
    import math

    if not rough or len(envelope) < 64:
        return rough or None
    ac = _autocorrelation(envelope)
    best = None
    for factor in (1, 2, 0.5, 1.5, 2 / 3, 4 / 3, 0.75):
        candidate = rough * factor
        if not 50 <= candidate <= 240:
            continue
        lag = 60 * frames_per_second / candidate
        peaks = [_peak(ac, lag * k) for k in (1, 2, 4)]
        heights = [p[1] for p in peaks if p]
        if not heights:
            continue
        score = sum(heights) / len(heights) * math.exp(-0.5 * (math.log2(candidate / center) / 0.5) ** 2)
        if best is None or score > best[0]:
            best = (score, lag, peaks)
    if best is None:
        return rough
    _, lag, peaks = best
    readings = [p[0] / k for p, k in zip(peaks, (1, 2, 4)) if p]
    fine = sum(readings[-2:]) / len(readings[-2:])  # the longer lags are the most precise
    return 60 * frames_per_second / fine if abs(fine - lag) < lag * 0.03 else 60 * frames_per_second / lag


def describe(path, settings=None, *, measured=None, remove=(), listen=True):
    """Name first, audio for the rest: {'bpm', 'bpm_source', 'key', 'key_source', 'title'}.
    `measured` reuses an earlier analyse_audio result (cached by the caller; keys may be "c#min" text);
    listen=False never opens the audio (only the name and `measured` are used)."""
    settings = music_settings(settings)
    info = parse_name(getattr(path, "name", str(path)), remove)
    result = {"title": info.title, "bpm": info.bpm, "bpm_source": "name" if info.bpm else None,
              "key": info.key, "key_source": "name" if info.key else None}
    if (info.bpm and info.key) or not settings["detect_bpm_key"]:
        return result
    if measured is None:
        if not listen:
            return result
        measured = analyse_audio(path, settings, need_bpm=not info.bpm, need_key=not info.key)
    if isinstance(measured.get("key"), str):
        measured = {**measured, "key": parse_name(measured["key"]).key}
    if not info.bpm and measured.get("bpm"):
        result.update(bpm=round(measured["bpm"]), bpm_source="audio")
    if not info.key and measured.get("key"):
        result.update(key=measured["key"], key_source="audio")
    return result


def label(value, source):
    """What a table shows: '155' as named, '≈154' when it was heard."""
    if value is None:
        return ""
    text = value.short if isinstance(value, Key) else str(value)
    return text if source == "name" else f"≈{text}"
