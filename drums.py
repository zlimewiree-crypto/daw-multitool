"""Decide whether a bounce has drums (Beat) or not (Melody).

How it works, in plain words:
1. macOS's own sound classifier (native/sound_classifier.swift, no download) listens to every 3 seconds and says
   how sure it is that it hears a drum kit, snares, hi-hats, kicks or cymbals. It learned what drums sound like
   from a huge range of recordings, so an 808 line, vinyl crackle or a plucky melody doesn't fool it.
2. Two older measurements back it up: how noisy the hits are (snares and hats are noise) and how much 808
   sub-bass there is.
3. A small learned formula turns them into a 0–1 drum probability. Every measurement can only push toward
   "beat", so relearning from a few labels can't produce a formula where more drums means "melody".
   Without the classifier (it couldn't start) the previous detector's formula is used instead.
The weights come from the copies in test_exports/real plus altered versions of them (808 added to melodies,
hi-hats or 808 filtered out of beats, crackle, lo-fi; scripts/check_drums.py --stress) and from the user's own
labels. Results are cached so a file is only analysed once (until it changes).
"""
import json
import math
import os
import re
import shutil
import subprocess
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import librosa
import numpy as np

from app_paths import DATA_DIR, FROZEN, RESOURCE_DIR, atomic_json

ROOT = Path(__file__).parent
MODEL_FILE = DATA_DIR / "drum_model.json"
SHIPPED_MODEL_FILE = RESOURCE_DIR / "drum_model.json"
CACHE_FILE = DATA_DIR / ".drum_cache.json"
LABELS_FILE = DATA_DIR / "labels.json"
SR = 44100  # full quality: hi-hats live above 11 kHz, which 22 kHz analysis threw away
HOP = 512
FEATURE_VERSION = 4  # bump when features change so old cache entries are ignored
DETECTION_DEFAULTS = json.loads((RESOURCE_DIR / "config.example.json").read_text())["bounce_sorter"]

CLASSIFIER_SOURCE = ROOT / "native" / "sound_classifier.swift"
CLASSIFIER_BINARIES = (RESOURCE_DIR / "native" / "sound-classifier", ROOT / "build" / "native" / "sound-classifier")
DRUM_PARTS = ("snare_drum", "hi_hat", "bass_drum", "cymbal")
ML_FEATURES = ["ml_kit", "ml_parts", "ml_coverage"]
FEATURES = ["log_perc_p80", "snap_noise", "log_snap_share", "log_hat_hits", "log_kick_hits",
            "noise_grid", "kick_grid", "log_sub_share", *ML_FEATURES]
# Held out by source file on 13 labelled copies + 45 altered versions: 1 wrong / 12 unsure out of 58, where the
# previous detector (BASIC_FEATURES alone) got 24 wrong. With weights kept >= 0 the hat and snap shares drop out
# (crackle mimics them); they stay listed so the user's own labels can bring them back.
MODEL_FEATURES = ["ml_kit", "ml_parts", "snap_noise", "log_sub_share", "log_hat_hits", "log_snap_share"]
BASIC_FEATURES = ["log_hat_hits", "snap_noise", "log_snap_share", "log_sub_share"]  # the v3 detector

# Used until a model is learned. Only trusts the overall "hit" share.
DEFAULT_MODEL = {
    "source": "built-in default (not yet tuned on real bounces)",
    "means": {"log_perc_p80": math.log10(0.02)},
    "scales": {"log_perc_p80": 0.25},
    "weights": {"log_perc_p80": 2.0},
    "bias": 0.0,
}


def _grid_strength(env, min_s, max_s):
    """How strongly an envelope repeats at a steady interval (0 = random, 1 = perfectly regular)."""
    if len(env) < 2:
        return 0.0
    onset = np.maximum(0.0, np.diff(env))  # only rises count as hits
    onset = onset - onset.mean()
    # Zero-padding gives the same autocorrelation without quadratic work on long bounces.
    size = 1 << (2 * len(onset) - 1).bit_length()
    spectrum = np.fft.rfft(onset, n=size)
    ac = np.fft.irfft(spectrum * spectrum.conj(), n=size)[:len(onset)]
    lo, hi = int(min_s * SR / HOP), int(max_s * SR / HOP)
    if len(ac) <= hi or ac[0] <= 0:
        return 0.0
    return float(ac[lo:hi].max() / ac[0])


def classifier_path(build=True):
    """The sound-classifier helper: bundled with the app, or built once from native/ on a Mac with Apple's
    command line tools (source runs). None when neither works; detection then uses the measurements alone."""
    from multitool import platform
    if not platform.IS_MAC:
        return None  # the existing measurement-only model is used on Windows
    for path in CLASSIFIER_BINARIES:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    swiftc = shutil.which("swiftc")
    if not build or FROZEN or not swiftc or not CLASSIFIER_SOURCE.is_file():
        return None
    target = CLASSIFIER_BINARIES[-1]
    temporary = target.with_name(f".{target.name}-{os.getpid()}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([swiftc, "-O", str(CLASSIFIER_SOURCE), "-o", str(temporary)], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=600, check=True)
        os.replace(temporary, target)  # our own build output, never a user file
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        temporary.unlink(missing_ok=True)
    return target


def listen(path, classifier, timeout=300):
    """The classifier's drum measurements for one file: {} when it can't run (the other measurements still work).
    ml_kit / ml_parts: how sure it is of a drum kit / of snares, hats, kicks and cymbals, on average (log scale);
    ml_coverage: the share of 3-second windows with a drum kit in them."""
    if classifier is None:
        return {}
    try:
        result = subprocess.run([str(classifier), str(path)], stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout)
        answer = json.loads(result.stdout.splitlines()[0])
        windows, names = np.array(answer["windows"], dtype=float), answer["classes"]
        kit = windows[:, names.index("drum_kit")]
        parts = sum(windows[:, names.index(name)] for name in DRUM_PARTS)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        return {}
    if not len(kit) or not np.isfinite(windows).all():
        return {}
    return {"ml_kit": math.log10(float(kit.mean()) + 0.003), "ml_parts": math.log10(float(parts.mean()) + 0.003),
            "ml_coverage": float((kit >= 0.1).mean())}


def hear_music(path):
    """BPM and key from the audio, only for what the file name doesn't already say (the name is the reference)."""
    from multitool import music

    info = music.parse_name(Path(path).name)
    settings = music.music_settings()
    if (info.bpm and info.key) or not settings["detect_bpm_key"]:
        return None
    try:
        measured = music.analyse_audio(path, settings, need_bpm=not info.bpm, need_key=not info.key)
    except Exception:  # unreadable audio: the drum measurements above already decided what this file is
        return None
    return {"bpm": measured["bpm"], "key": measured["key"].short if measured["key"] else None,
            "key_confidence": measured["key_confidence"]}


def analyse(path, max_seconds=120, classifier=None):
    """Return a dict of drum features (plus heard BPM/key under "music" when the name lacks them), or None if
    the file is silent/unreadable."""
    try:
        y, _ = librosa.load(path, sr=SR, mono=True, duration=max_seconds)
    except Exception:
        return None
    if len(y) < SR or not np.isfinite(y).all() or np.max(np.abs(y)) < 1e-3:
        return None

    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    _, P = librosa.decompose.hpss(S, margin=(1.0, 3.0))  # strict split: only clear hits count as P
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    sub_band = (freqs >= 25) & (freqs <= 60)  # 808 / sub-bass: in nearly every beat, rare in melody loops
    kick_band = (freqs >= 30) & (freqs <= 120)
    snap_band = (freqs >= 2000) & (freqs <= 8000)  # snare crack, hat body
    hat_band = (freqs >= 8000) & (freqs <= 16000)  # hat sizzle (most mp3s keep up to 16 kHz)
    noisy_band = snap_band | hat_band

    P2, S2 = P**2, S**2
    total = S2.sum() + 1e-12
    p_all, s_all = P2.sum(0), S2.sum(0) + 1e-12

    # Hit share per 3-second window; the 80th percentile catches drums that start late
    win = max(1, int(3 * SR / HOP))
    windows = [p_all[i:i + win].sum() / s_all[i:i + win].sum() for i in range(0, len(p_all) - win + 1, win)]
    perc_p80 = float(np.percentile(windows, 80)) if windows else float(p_all.sum() / s_all.sum())

    # Noisiness of the hits in the snare/hat range (noise = drums, tones = plucks)
    snap_energy = P2[snap_band].sum(0)
    loud = snap_energy > 0.1 * np.percentile(snap_energy, 95)
    flatness = librosa.feature.spectral_flatness(S=P[snap_band] + 1e-10)[0]
    snap_noise = float(flatness[loud].mean()) if loud.any() else 0.0

    # Noisy-hit envelope: high-band hit energy, weighted by how noise-like each frame is.
    # Hats/snares repeat on a 1/16–1/2 note grid; crackle and plucked notes far less so.
    noisy_flat = librosa.feature.spectral_flatness(S=P[noisy_band] + 1e-10)[0]
    noise_env = P[noisy_band].sum(0) * noisy_flat
    kick_env = P[kick_band].sum(0)

    features = {
        "log_perc_p80": math.log10(perc_p80 + 1e-6),
        "snap_noise": snap_noise,
        "log_snap_share": math.log10(float(P2[snap_band].sum() / (S2[snap_band].sum() + 1e-12)) + 1e-6),
        "log_hat_hits": math.log10(float(P2[hat_band].sum() / total) + 1e-9),  # share of the whole mix
        "log_kick_hits": math.log10(float(P2[kick_band].sum() / total) + 1e-9),
        "noise_grid": _grid_strength(noise_env, 0.08, 1.0),
        "kick_grid": _grid_strength(kick_env, 0.25, 2.0),
        # whole spectrum, not just hits: an 808 is a sustained tone, so HPSS files it as "harmonic"
        "log_sub_share": math.log10(float(S2[sub_band].sum() / total) + 1e-9),
    }
    features.update(listen(path, classifier))
    heard = hear_music(path)
    if heard:
        features["music"] = heard
    return features


def _valid(model):
    try:
        names = set(model["weights"])
        values = [model["bias"], *(model[section][name] for section in ("weights", "means", "scales") for name in names)]
        return bool(names) and names <= set(FEATURES) and all(math.isfinite(v) for v in values) and \
            all(model["scales"][n] > 0 for n in names)
    except (KeyError, TypeError, AttributeError):
        return False


def _read_model(path):
    try:
        model = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict) or not _valid(model):
        return None
    if "basic" in model and not _valid(model["basic"]):
        model = {key: value for key, value in model.items() if key != "basic"}
    return model


def load_model():
    """The user's model when it was learned for the current measurements, else the one shipped with the app
    (an older user model can't use the classifier; its labels stay in labels.json for Learn), else built-in."""
    user = _read_model(MODEL_FILE)
    if user and user.get("feature_version", FEATURE_VERSION) >= FEATURE_VERSION:
        return user
    shipped = _read_model(SHIPPED_MODEL_FILE) if SHIPPED_MODEL_FILE != MODEL_FILE else None
    return shipped or user or DEFAULT_MODEL


def _usable(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _score(features, model):
    z = model["bias"]
    for name, w in model["weights"].items():
        z += w * (features[name] - model["means"][name]) / model["scales"][name]
    return 1 / (1 + math.exp(-max(-50, min(50, z))))


def drum_probability(features, model):
    """Logistic score: 0 = surely no drums, 1 = surely drums. Uses the model's fallback ("basic", without the
    classifier's measurements) when the classifier couldn't listen to this file."""
    for candidate in (model, model.get("basic")):
        if candidate and all(_usable(features.get(name)) for name in candidate["weights"]):
            return _score(features, candidate)
    raise ValueError("Audio measurements are incomplete or invalid; analyse this file again.")


def melody_name(name, settings=None):
    settings = settings or DETECTION_DEFAULTS
    def matches(words):
        return any(re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", name, re.IGNORECASE) for word in words if word)
    return matches(settings["melody_name_words"]) and not matches(settings["drum_name_words"])


def apply_name_hint(p, name, strength, settings=None):
    """A configurable melody word lowers drum odds; explicit drum names are exempt."""
    if strength <= 0 or not melody_name(name, settings):
        return p
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 1 / (1 + math.exp(-(math.log(p / (1 - p)) - strength)))


def classify_score(p, name, settings):
    adjusted = apply_name_hint(p, name, settings["loop_name_hint"], settings)
    if settings["loop_name_hint"] > 0 and p >= settings["beat_threshold"] and melody_name(name, settings):
        return "review", adjusted  # conflicting evidence needs an ear, not an automatic wrong move
    category = "beat" if adjusted >= settings["beat_threshold"] else "loop" if adjusted <= settings["loop_threshold"] else "review"
    return category, adjusted


def fit(X, y, C=3.0, sample_weight=None):
    """Logistic regression on standardised measurements with every weight >= 0 (more drum evidence can only mean
    "more likely a beat"), class-balanced and L2-regularised (strength 1/C). Returns (means, scales, weights, bias)."""
    from scipy.optimize import minimize

    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    means, scales = X.mean(0), X.std(0)
    scales = np.where(scales > 1e-9, scales, 1.0)
    Z = (X - means) / scales
    balance = np.where(y == 1, len(y) / (2 * max(1, y.sum())), len(y) / (2 * max(1, len(y) - y.sum())))
    if sample_weight is not None:
        balance = balance * np.asarray(sample_weight, dtype=float)

    def loss(theta):
        w, b = theta[:-1], theta[-1]
        z = Z @ w + b
        p = 1 / (1 + np.exp(-np.clip(z, -50, 50)))
        gradient = balance * (p - y)
        return (np.sum(balance * (np.logaddexp(0, z) - y * z)) + 0.5 / C * w @ w,
                np.append(Z.T @ gradient + w / C, gradient.sum()))
    result = minimize(loss, np.zeros(X.shape[1] + 1), jac=True, method="L-BFGS-B",
                      bounds=[(0, None)] * X.shape[1] + [(None, None)])
    return means, scales, result.x[:-1], float(result.x[-1])


def _as_model(names, fitted, source):
    means, scales, weights, bias = fitted
    return {"source": source, "means": dict(zip(names, means.tolist())), "scales": dict(zip(names, scales.tolist())),
            "weights": dict(zip(names, weights.tolist())), "bias": bias}


def _predict(fitted, X):
    means, scales, weights, bias = fitted
    return 1 / (1 + np.exp(-np.clip(((np.asarray(X) - means) / scales) @ weights + bias, -50, 50)))


def learn(rows, C=3.0, base=()):
    """rows: list of (features, 1 for beat / 0 for melody); `base`: more rows of the same kind that are always
    trained on but not scored (the altered copies shipped in drum_model.json, so a few labels can't undo them).
    Returns (model, held_out) where held_out[i] is row i's probability from a model that never saw it."""
    names = MODEL_FEATURES if all(_usable(f.get(n)) for f, _ in rows for n in MODEL_FEATURES) else BASIC_FEATURES
    try:
        X = np.array([[f[n] for n in names] for f, _ in rows], dtype=float)
    except (KeyError, TypeError, ValueError):
        raise ValueError("Training needs finite audio measurements and beat/melody labels.") from None
    y = np.array([label for _, label in rows])
    if not np.isfinite(X).all() or not set(y) <= {0, 1}:
        raise ValueError("Training needs finite audio measurements and beat/melody labels.")
    X, indices, inverse = np.unique(X, axis=0, return_index=True, return_inverse=True)
    unique_y = y[indices]
    if not np.array_equal(unique_y[inverse], y):
        raise ValueError("Identical audio measurements have conflicting labels. Correct those labels before learning.")
    y = unique_y
    if min(np.count_nonzero(y == label) for label in (0, 1)) < 2:
        raise ValueError("Learning needs at least two different examples of each type, not repeated copies.")
    known = {tuple(np.round(x, 9)) for x in X}  # a reference copy identical to a labelled file would leak its answer
    base_rows = [(f, label) for f, label in base if all(_usable(f.get(n)) for n in names) and label in (0, 1)
                 and tuple(np.round([f[n] for n in names], 9)) not in known]
    bX = np.array([[f[n] for n in names] for f, _ in base_rows], dtype=float).reshape(-1, len(names))
    by = np.array([label for _, label in base_rows], dtype=float)
    # each file is scored by a model trained without it (scaling included), so the blind test is honest
    held_out = np.array([_predict(fit(np.vstack([np.delete(X, i, 0), bX]), np.append(np.delete(y, i), by), C), X[i:i + 1])[0]
                         for i in range(len(y))])
    fitted = fit(np.vstack([X, bX]), np.append(y, by), C)
    extra = f" + {len(base_rows)} reference copies" if base_rows else ""
    model = _as_model(names, fitted, f"learned from {int(y.sum())} beats + {int(len(y) - y.sum())} melodies{extra}")
    return model, held_out[inverse]  # copies share a held-out score; none can leak into their own training fold


def save_model(model, keep_from=None):
    """Saves a learned model, keeping the current model's fallback ("basic") and reference rows ("training") so
    the next Learn starts from the same base. feature_version lets a newer app replace an outdated *shipped*
    model (app_paths.initialize_data)."""
    keep_from = keep_from if keep_from is not None else load_model()
    if set(model["weights"]) <= set(BASIC_FEATURES) and keep_from and not set(keep_from["weights"]) <= set(BASIC_FEATURES):
        # learned without the classifier (it couldn't run): that improves the fallback, not the main formula
        model = {**{key: keep_from[key] for key in ("source", "means", "scales", "weights", "bias")}, "basic": model}
    kept = {key: keep_from[key] for key in ("basic", "training") if key in keep_from and key not in model}
    atomic_json(MODEL_FILE, {**model, **kept, "feature_version": FEATURE_VERSION})


def reference_rows(model=None):
    """The altered copies the shipped model was trained on, as (features, label) rows for Learn."""
    model = model if model is not None else load_model()
    rows = model.get("training", []) if isinstance(model, dict) else []
    return [(features, label) for features, label in rows if isinstance(features, dict) and label in (0, 1)]


# ---------- labels the user gave in the window ----------
def load_labels():
    """{file path: {"label": "beat"|"loop", "features": {...}, "version": n}}"""
    try:
        with open(LABELS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_labels(labels):
    atomic_json(LABELS_FILE, labels, indent=1)


REAL_FOLDER = ROOT / "test_exports" / "real"  # copies the user dragged into beats/ and loops/
AUDIO_EXTS = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}


def labelled_rows(detector, real_folder=REAL_FOLDER, progress=None, *, include_saved=True, cancelled=lambda: False):
    """Every file the user labelled (copies in real_folder/beats|loops plus labels.json).
    Returns (rows, skipped); rows are (path, 1 = beat / 0 = melody, features)."""
    files = {}
    for label, sub in ((1, "beats"), (0, "loops")):
        d = Path(real_folder) / sub
        if d.is_dir():
            files.update({p: label for p in sorted(d.iterdir()) if p.suffix.lower() in AUDIO_EXTS})
    saved = load_labels() if include_saved else {}
    for path, entry in saved.items():
        files[Path(path)] = 1 if entry["label"] == "beat" else 0
    detector.analyse_many([p for p in files if p.exists()], progress, cancelled)

    rows, skipped = [], []
    for path, label in files.items():
        if cancelled():
            break
        entry = saved.get(str(path))
        if path.exists():
            feats = detector.features(path)
        elif entry and entry.get("version") == FEATURE_VERSION:
            feats = entry["features"]  # file was sorted/moved since labelling; use what we measured then
        else:
            skipped.append((path, "moved, and its saved measurements are outdated"))
            continue
        if feats is None:
            skipped.append((path, "silent or unreadable"))
            continue
        if detector._classifier and not all(_usable(feats.get(n)) for n in ML_FEATURES):
            # learning from it would retrain the detector without the classifier for every file
            skipped.append((path, "macOS's sound classifier couldn't read this format"))
            continue
        rows.append((path, label, feats))
    return rows, skipped


# ---------- cache ----------
def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
            return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_key(path, max_seconds=None, listened=True):
    """Changes when the file, the analysis length or the measurements change; "basic" entries (made while the
    classifier couldn't run) are measured again once it can."""
    st = Path(path).stat()
    seconds = max_seconds if max_seconds is not None else DETECTION_DEFAULTS["analysis_seconds"]
    return f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}|{seconds}|v{FEATURE_VERSION}{'' if listened else '|basic'}"


class DrumDetector:
    """Classifies files and remembers the features so re-scans are instant."""

    def __init__(self, beat_threshold=0.8, loop_threshold=0.2, name_hint=0.0, *, settings=None):
        self.model = load_model()
        self.settings = {**DETECTION_DEFAULTS, **(settings or {}), "beat_threshold": beat_threshold,
                         "loop_threshold": loop_threshold, "loop_name_hint": name_hint}
        if not 0 <= loop_threshold < beat_threshold <= 1:
            raise ValueError("Melody threshold must be below the beat threshold, between 0 and 1.")
        self.max_seconds = self.settings["analysis_seconds"]
        self.workers = max(1, min(int(self.settings["analysis_workers"]), max(1, (os.cpu_count() or 2) - 2)))
        self.cache = _load_cache()
        self.skipped = {}
        self._classifier = None

    @property
    def classifier(self):
        """Found (or built, in a source run) the first time a file needs listening to; False = unavailable."""
        if self._classifier is None:
            self._classifier = classifier_path() or False
        return self._classifier or None

    def _key(self, path):
        return _cache_key(path, self.max_seconds, listened=self._classifier is not False)

    def is_cached(self, path):
        try:
            return self._key(path) in self.cache
        except OSError:
            return False

    def analyse_many(self, paths, progress=None, cancelled=lambda: False):
        """Cache only the snapshot actually analysed; changing files never hide the rest of a batch."""
        paths = list(dict.fromkeys(Path(p) for p in paths))
        todo = {}
        self.skipped = {}
        for path in paths:
            try:
                key = self._key(path)
                if key not in self.cache:
                    todo[path] = key
            except OSError:
                self.skipped[path] = "missing or unreadable"
        done = len(paths) - len(todo)
        if not todo or cancelled():
            return
        classifier = self.classifier
        if classifier is None:  # unavailable after all: measure for the fallback formula under its own key
            todo = {path: self._key(path) for path in todo}
        pool = ProcessPoolExecutor(max_workers=min(self.workers, len(todo)))
        try:
            futures = {pool.submit(analyse, path, self.max_seconds, classifier): (path, key)
                       for path, key in todo.items()}
            while futures:
                if cancelled():
                    break
                finished, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in finished:
                    path, key = futures.pop(future)
                    try:
                        features = future.result()
                        if key == self._key(path):
                            self.cache[key] = features
                        else:
                            self.skipped[path] = "still being exported; preview again when finished"
                    except Exception:
                        self.skipped[path] = "analysis failed or file disappeared; preview again to retry"
                    done += 1
                    if progress:
                        progress(done, len(paths), path.name)
                    if done % 10 == 0:
                        self.save_cache()
        finally:
            if cancelled() and hasattr(pool, "terminate_workers"):
                pool.terminate_workers()
            else:
                pool.shutdown(wait=not cancelled(), cancel_futures=cancelled())
            self.save_cache()

    def features(self, path):
        key = self._key(path)
        if key not in self.cache:
            classifier = self.classifier
            key = self._key(path)
            features = analyse(path, self.max_seconds, classifier)
            if key != self._key(path):
                raise ValueError("This export is still changing. Preview it again when finished.")
            self.cache[key] = features
        return self.cache[key]

    def classify(self, path):
        """Return ("beat" | "loop" | "review", probability or None)."""
        feats = self.features(path)
        if feats is None:
            return "review", None
        try:
            return classify_score(drum_probability(feats, self.model), Path(path).name, self.settings)
        except (ValueError, KeyError, TypeError):
            return "review", None

    def save_cache(self):
        atomic_json(CACHE_FILE, self.cache)
