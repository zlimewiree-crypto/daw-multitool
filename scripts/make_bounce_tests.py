"""Create fake bounces (synth beats with drums, melody loops without) for testing the bounce sorter.

The expected answer is in each filename: "DRUMS" files should land in Finished Beats,
"MELODY" files in Melody Loops, "SILENT" in Needs Review.

Usage:
    python scripts/make_bounce_tests.py           # create test files in test_exports/bounces/
    python scripts/make_bounce_tests.py --clean   # empty that folder first
"""
import argparse
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

OUT = Path(__file__).resolve().parent.parent / "test_exports" / "bounces"
SR = 22050
BPM = 140
BEAT = 60 / BPM  # seconds per beat
rng = np.random.default_rng(7)

# A-minor-ish chord roots (Hz) for melodies
CHORDS = [[220.0, 261.6, 329.6], [174.6, 220.0, 261.6], [196.0, 246.9, 293.7], [164.8, 207.7, 246.9]]


def env(n, decay, attack=0.003):
    t = np.arange(n) / SR
    a = np.minimum(t / attack, 1.0)
    return a * np.exp(-t / decay)


def place(track, sound, start_s, gain=1.0):
    i = int(start_s * SR)
    j = min(len(track), i + len(sound))
    if i < len(track):
        track[i:j] += gain * sound[: j - i]


# ---------- drum sounds ----------
def kick():
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    freq = 50 + 110 * np.exp(-t / 0.03)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * env(n, 0.18, 0.001)


def snare():
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    return 0.7 * rng.standard_normal(n) * env(n, 0.07, 0.001) + 0.5 * np.sin(2 * np.pi * 190 * t) * env(n, 0.05, 0.001)


def hat():
    n = int(0.06 * SR)
    noise = np.diff(rng.standard_normal(n + 2), 2)  # crude high-pass
    return 0.25 * noise * env(n, 0.015, 0.0005)


# ---------- melodic sounds ----------
def tone(freq, seconds, decay, partials, rolloff, attack=0.004, inharmonic=None):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    ratios = inharmonic or range(1, partials + 1)
    for k, r in enumerate(ratios, start=1):
        if freq * r < SR / 2:
            out += np.sin(2 * np.pi * freq * r * t + rng.random() * 6) / k**rolloff * np.exp(-t * k / (decay * 6))
    return out * env(n, decay, attack)


def pluck(f):
    return tone(f, 0.4, 0.18, 30, 1.0, attack=0.002)


def pad(f):
    return tone(f, BEAT * 4, 8.0, 12, 2.0, attack=0.4) + tone(f * 1.004, BEAT * 4, 8.0, 12, 2.0, attack=0.4)


def piano(f):
    n = int(1.2 * SR)
    hammer = 0.05 * rng.standard_normal(n) * env(n, 0.004, 0.0005)
    return tone(f, 1.2, 0.7, 15, 1.5, attack=0.002) + hammer


def bell(f):
    return tone(f * 2, 0.8, 0.3, 0, 1.0, attack=0.001, inharmonic=[1, 2.76, 5.4, 8.93])


# ---------- arrangements ----------
def melody_track(bars, kind):
    track = np.zeros(int(bars * 4 * BEAT * SR) + SR)
    for bar in range(bars):
        chord = CHORDS[bar % 4]
        t0 = bar * 4 * BEAT
        if kind == "pad":
            for f in chord:
                place(track, pad(f), t0, 0.3)
        elif kind == "pluck":  # 16th-note arpeggio — sharp attacks, the hard case
            for i in range(16):
                place(track, pluck(chord[i % 3] * (2 if i % 8 > 3 else 1)), t0 + i * BEAT / 4, 0.4)
        elif kind == "piano":
            for i in range(8):
                place(track, piano(chord[i % 3]), t0 + i * BEAT / 2, 0.4)
        elif kind == "bell":
            for i in range(8):
                place(track, bell(chord[(i * 2) % 3]), t0 + i * BEAT / 2, 0.35)
    return track


def drum_track(bars, style, start_bar=0):
    track = np.zeros(int(bars * 4 * BEAT * SR) + SR)
    for bar in range(start_bar, bars):
        t0 = bar * 4 * BEAT
        if style == "trap":
            for b in (0, 1.5, 2.75):
                place(track, kick(), t0 + b * BEAT, 0.9)
            place(track, snare(), t0 + 2 * BEAT, 0.6)
            for i in range(16):
                place(track, hat(), t0 + i * BEAT / 4, 0.5 if i % 2 else 0.8)
        elif style == "boombap":
            for b in (0, 2.5):
                place(track, kick(), t0 + b * BEAT, 0.9)
            for b in (1, 3):
                place(track, snare(), t0 + b * BEAT, 0.6)
            for i in range(8):
                place(track, hat(), t0 + i * BEAT / 2 + (0.03 if i % 2 else 0), 0.7)
        elif style == "quiet":  # drums mixed low under a loud melody
            for b in (0, 2):
                place(track, kick(), t0 + b * BEAT, 0.35)
            place(track, snare(), t0 + 3 * BEAT, 0.2)
    return track


def normalize(x):
    return 0.89 * x / (np.max(np.abs(x)) + 1e-9)


CASES = {
    "DRUMS trap with pad": lambda: drum_track(16, "trap") + melody_track(16, "pad"),
    "DRUMS boombap with piano": lambda: drum_track(16, "boombap") + melody_track(16, "piano"),
    "DRUMS trap with plucks": lambda: drum_track(16, "trap") + melody_track(16, "pluck"),
    "DRUMS late intro bells": lambda: drum_track(16, "trap", start_bar=4) + melody_track(16, "bell"),
    "DRUMS quiet under piano": lambda: drum_track(16, "quiet") + melody_track(16, "piano"),
    "MELODY pluck arp 140": lambda: melody_track(8, "pluck"),
    "MELODY pad chords": lambda: melody_track(8, "pad"),
    "MELODY piano keys": lambda: melody_track(8, "piano"),
    "MELODY bells": lambda: melody_track(8, "bell"),
    "MELODY pluck and pad": lambda: melody_track(8, "pluck") + melody_track(8, "pad"),
    "SILENT export": lambda: np.zeros(SR * 10),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="empty test_exports/bounces/ first")
    args = parser.parse_args()
    if args.clean and OUT.exists():
        shutil.rmtree(OUT)  # only ever deletes the fake test folder
    OUT.mkdir(parents=True, exist_ok=True)

    has_ffmpeg = shutil.which("ffmpeg") is not None
    for i, (name, make) in enumerate(CASES.items()):
        audio = make()
        audio = normalize(audio) if np.any(audio) else audio
        wav = OUT / f"{name}.wav"
        sf.write(wav, audio, SR, subtype="PCM_16")
        # Every other file also as mp3 (then the wav is removed) so both formats get tested
        if has_ffmpeg and i % 2:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), str(wav.with_suffix(".mp3"))], check=True)
            wav.unlink()  # fake test file we just generated
        print(f"made {name}")
    print(f"\nTest bounces are in: {OUT}")


if __name__ == "__main__":
    main()
