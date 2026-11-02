"""Report held-out drum detection on labelled copies; --run explicitly saves a passing model.

    python scripts/check_drums.py                  # report/propose only (default dry-run)
    python scripts/check_drums.py --stress         # also test altered copies (808 added, hats filtered out, ...)
    python scripts/check_drums.py --stress --run   # save only if no real copy is held-out wrong

--stress makes altered versions of every labelled copy once, in test_exports/stress/ (the copies are only read,
nothing is ever deleted): melodies get an 808 line, vinyl crackle or both; beats lose their 808 or hi-hats, get
crackle or a lo-fi filter. Each source file and all its versions are held out together, so a score never comes
from a model that heard another version of the same beat. A saved model keeps the previous detector as its
fallback ("basic") and the measured rows ("training") so Learn in the app starts from the same base.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from drums import (BASIC_FEATURES, MODEL_FEATURES, REAL_FOLDER, DrumDetector, _as_model, _predict, _usable, classify_score,
                   drum_probability, fit, labelled_rows, load_model, save_model)
from sorter import load_settings

STRESS_FOLDER = ROOT / "test_exports" / "stress"
VARIANT = re.compile(r" \[[^\]]+\]$")
BEAT_VARIANTS = {
    "no808": "highpass=f=160,highpass=f=160,highpass=f=160",
    "nohats": "lowpass=f=2500,lowpass=f=2500",
    "lofi": "highpass=f=90,lowpass=f=7000,volume=-6dB",
}
CRACKLE = "anoisesrc=color=brown:amplitude={amp}:d=100[n];aevalsrc='if(lt(random(0),0.0004),0.5*(random(1)-0.5),0)':s=44100:d=100[c]"


def make_stress_copies(folder=REAL_FOLDER, target=STRESS_FOLDER):
    """Altered versions of each labelled copy, made once (existing files are reused, never replaced)."""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    made = []
    for sub in ("beats", "loops"):
        (target / sub).mkdir(parents=True, exist_ok=True)
        for source in sorted((Path(folder) / sub).glob("*")):
            if source.suffix.lower() not in (".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"):
                continue
            bpm = next((int(n) for n in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", source.stem) if 55 <= int(n) <= 230), 150)
            if sub == "beats":
                jobs = {name: ["-af", chain, "-t", "100"] for name, chain in BEAT_VARIANTS.items()}
                jobs["crackle"] = ["-filter_complex", CRACKLE.format(amp=0.03) + ";[0:a]atrim=0:100[a];[a][n][c]amix=inputs=3:normalize=0"]
            else:
                pulse, bar = round(60 / bpm * 2, 4), round(60 / bpm * 4, 4)
                bass = "aevalsrc='0.55*sin(2*PI*(44+20*exp(-25*mod(t,{p})))*t)*exp(-1.6*mod(t,{p}))':s=44100:d=100[b]"
                jobs = {
                    "808": ["-filter_complex", bass.format(p=pulse) + ";[0:a]atrim=0:100[a];[a][b]amix=inputs=2:normalize=0,alimiter=limit=0.95"],
                    "crackle": ["-filter_complex", CRACKLE.format(amp=0.03) + ";[0:a]atrim=0:100[a];[a][n][c]amix=inputs=3:normalize=0"],
                    "808+crackle": ["-filter_complex", bass.format(p=bar) + ";anoisesrc=color=brown:amplitude=0.025:d=100[n];"
                                    "[0:a]atrim=0:100[a];[a][b][n]amix=inputs=3:normalize=0,alimiter=limit=0.95"],
                }
            for name, arguments in jobs.items():
                out = target / sub / f"{source.stem} [{name}].mp3"
                if not out.exists():  # -n: FFmpeg itself refuses to overwrite
                    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-n", "-i", str(source), *arguments, str(out)],
                                   check=True)
                made.append(out)
    return made


def report(rows, probs, settings, title):
    right = wrong = review = 0
    print(f"\n== {title}")
    for (path, label, _), probability in zip(rows, probs):
        category, score = classify_score(probability, path.name, settings)
        truth = "beat" if label else "loop"
        right += category == truth
        wrong += category not in (truth, "review")
        review += category == "review"
        if category != truth:
            print(f"  {'REVIEW' if category == 'review' else 'WRONG '} {truth:4} score={score:.2f}  {path.name}")
    n = len(rows)
    print(f"  right {right}/{n}   WRONG {wrong}/{n}   needs review {review}/{n}")
    return wrong


def held_out_by_source(rows, names, C=3.0):
    """Each source file and all its altered versions are scored by a model trained without any of them."""
    X = np.array([[f[n] for n in names] for _, _, f in rows], dtype=float)
    y = np.array([label for _, label, _ in rows], dtype=float)
    groups = np.array([VARIANT.sub("", path.stem) for path, _, _ in rows])
    held = np.zeros(len(rows))
    for group in np.unique(groups):
        test = groups == group
        held[test] = _predict(fit(X[~test], y[~test], C), X[test])
    return held


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", type=Path, default=REAL_FOLDER)
    parser.add_argument("--stress", action="store_true", help="also train and test on altered copies (test_exports/stress)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="save only a model with no held-out wrong predictions on real copies")
    mode.add_argument("--dry-run", "--no-save", action="store_true", help="report without saving (the default)")
    parser.add_argument("--include-saved-labels", action="store_true", help="also read files explicitly labelled in the app")
    args = parser.parse_args()
    settings = load_settings()
    detector = DrumDetector(settings["beat_threshold"], settings["loop_threshold"], settings["loop_name_hint"], settings=settings)
    if args.stress:
        print(f"altered copies: {len(make_stress_copies(args.folder))} in {STRESS_FOLDER.relative_to(ROOT)}")
    progress = lambda i, n, name: print(f"\ranalysing {i}/{n}", end="", flush=True)
    rows, skipped = labelled_rows(detector, args.folder, include_saved=args.include_saved_labels, progress=progress)
    if args.stress:
        stress, more = labelled_rows(detector, STRESS_FOLDER, include_saved=False, progress=progress)
        rows, skipped = rows + stress, skipped + more
    print()
    for path, why in skipped:
        print(f"  skipped ({why}): {path.name}")
    n_beats = sum(row[1] for row in rows)
    print(f"{n_beats} beats, {len(rows) - n_beats} melodies   (model: {detector.model['source']})")
    current = load_model()
    report(rows, [drum_probability(f, current) for _, _, f in rows], settings,
           "Current model (may already know these files; held-out scores below are the useful check)")
    if current.get("basic"):
        report(rows, [drum_probability({k: v for k, v in f.items() if not k.startswith("ml_")}, current) for _, _, f in rows],
               settings, "Its fallback without the sound classifier (the previous detector)")
    minimum = settings["minimum_training_per_class"]
    if n_beats < minimum or len(rows) - n_beats < minimum:
        print(f"Need at least {minimum} different beats and {minimum} melodies to learn.")
        return
    names = MODEL_FEATURES if all(_usable(f.get(n)) for _, _, f in rows for n in MODEL_FEATURES) else BASIC_FEATURES
    if names is BASIC_FEATURES:
        print("The sound classifier didn't run (native/sound_classifier.swift needs Apple's command line tools).")
    held = held_out_by_source(rows, names)
    report(rows, held, settings, "Learned model, each source file held out with all its versions")
    real_wrong = report([r for r in rows if not VARIANT.search(r[0].stem)], [h for r, h in zip(rows, held) if not VARIANT.search(r[0].stem)],
                        settings, "...the real copies alone")
    X = np.array([[f[n] for n in names] for _, _, f in rows], dtype=float)
    y = np.array([label for _, label, _ in rows], dtype=float)
    model = _as_model(names, fit(X, y), f"learned from {int(y.sum())} beats + {int(len(y) - y.sum())} melodies "
                                        f"({sum(not VARIANT.search(p.stem) for p, _, _ in rows)} real, the rest altered copies)")
    print("weights:", {name: round(weight, 2) for name, weight in model["weights"].items()})
    if not args.run:
        print("DRY RUN: existing model kept. Use --run to save a passing candidate.")
    elif real_wrong:
        print("Existing model kept: candidate made wrong predictions on real copies. Correct/add labels first.")
    else:
        basic = current.get("basic") or (current if set(current["weights"]) <= set(BASIC_FEATURES) else None)
        if basic:
            model["basic"] = {key: basic[key] for key in ("source", "means", "scales", "weights", "bias")}
        model["training"] = [[{n: f[n] for n in set(MODEL_FEATURES) | set(BASIC_FEATURES) if _usable(f.get(n))}, label]
                             for _, label, f in rows]
        model["origin"] = "shipped"
        save_model(model, keep_from={})
        print("Saved drum_model.json.")


if __name__ == "__main__":
    main()
