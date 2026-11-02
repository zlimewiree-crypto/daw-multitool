"""Create fake (silent) audio exports for safely testing the organizer.

Usage:
    python scripts/make_test_files.py           # create test files
    python scripts/make_test_files.py --clean   # empty test_exports/ first
"""
import argparse
import shutil
import subprocess
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "test_exports"
RATE = 8000  # low sample rate keeps the fake files tiny

# (filename without extension, length in seconds)
FILES = [
    ("loop_dark pluck 140", 8),
    ("beat_mallsoft", 150),
    ("stem_808", 150),
    ("untagged short", 12),
    ("untagged long", 120),
    ("untagged middle", 60),
]


def make_wav(path: Path, seconds: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * RATE * seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="empty test_exports/ first")
    args = parser.parse_args()

    if args.clean and OUT.exists():
        shutil.rmtree(OUT)  # only ever deletes the fake test folder
    OUT.mkdir(exist_ok=True)

    has_ffmpeg = shutil.which("ffmpeg") is not None
    for name, seconds in FILES:
        wav = OUT / f"{name}.wav"
        make_wav(wav, seconds)
        if has_ffmpeg:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), str(OUT / f"{name}.mp3")],
                check=False,
            )
        print(f"made {wav.name} ({seconds}s)")

    if not has_ffmpeg:
        print("(ffmpeg not found, so only .wav files were made — `brew install ffmpeg` adds mp3s)")
    print(f"\nTest files are in: {OUT}")


if __name__ == "__main__":
    main()
