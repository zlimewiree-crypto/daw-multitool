"""Find the best part of a beat for short videos: where the drums and 808 hit hardest.

Read-only: the audio is only loaded, never changed. Uses the same sound bands as the
organizer's detector (drums.py): drum hits are the "percussive" part above 2 kHz, the 808
is the 25–60 Hz sub-bass.
"""
import numpy as np

SR, HOP = 22050, 512  # half rate is plenty for a loudness map; keeps this ~1 s per track


def energy_map(y, sr=SR):
    """Per-frame score: drum-hit energy + 808 energy, each scaled 0..1 across the track."""
    import librosa

    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    _, P = librosa.decompose.hpss(S, margin=(1.0, 3.0))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    hits = (P[freqs >= 2000] ** 2).sum(0)
    sub = (S[(freqs >= 25) & (freqs <= 60)] ** 2).sum(0)
    scale = lambda x: x / (np.percentile(x, 99) + 1e-12)
    return np.minimum(scale(hits), 1) + np.minimum(scale(sub), 1)


def best_start(audio, seconds, settings, bpm=None):
    """Start time (s) of the strongest `seconds`-long window; 0 if the track is short or quiet. `bpm` (from the
    file name) fixes the beat grid the start snaps to, instead of estimating the tempo."""
    import librosa

    y, sr = librosa.load(str(audio), sr=SR, mono=True)
    total = len(y) / sr
    if total <= seconds + 1 or not np.isfinite(y).all() or np.max(np.abs(y)) < 1e-3:
        return 0.0
    score = energy_map(y, sr)
    width = int(seconds * sr / HOP)
    windows = np.convolve(score, np.ones(width), mode="valid")  # total score of every window
    start = float(np.argmax(windows) * HOP / sr)
    if settings.get("snippet_snap_to_beat", True):
        _, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP, units="time", **({"bpm": bpm} if bpm else {}))
        fits = [b for b in beats if b <= total - seconds]
        if len(fits):
            start = float(min(fits, key=lambda b: abs(b - start)))  # begin on a beat, not mid-hit
    return round(max(0.0, min(start, total - seconds)), 2)
