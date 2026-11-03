"""BPM and key: the file name is the reference; audio detection fills the gaps. Synthetic audio only."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile

import sorter
from multitool import music
from multitool.music import Key, parse_name

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="music_test_", dir=ROOT / "test_exports"))


def archive():
    target = Path(tempfile.mkdtemp(prefix="daw-music-tests-", dir=tempfile.gettempdir()))
    with (target / "_log.txt").open("a") as log:
        log.write(f"{datetime.now().isoformat()} | started | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())
        shutil.move(str(BASE), str(target / BASE.name))
        log.write(f"{datetime.now().isoformat()} | done | move | {BASE} | {target / BASE.name}\n")


def setUpModule():
    unittest.addModuleCleanup(archive)  # fake audio is archived, never deleted


def beat(path, bpm=140, seconds=40, sr=22050):
    """A kick/hat pattern at `bpm` over an A minor chord (A, C, E) — fake audio for detection tests."""
    t = np.arange(int(seconds * sr)) / sr
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 261.63, 329.63, 440.0)) * 0.08
    period = 60 / bpm
    phase = np.mod(t, period)
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-phase * 30) * 0.6
    hat_phase = np.mod(t, period / 2)
    hat = np.random.default_rng(1).normal(0, 1, len(t)) * np.exp(-hat_phase * 120) * 0.15
    soundfile.write(path, (chord + kick + hat).astype(np.float32), sr)
    return path


class NameTests(unittest.TestCase):
    def test_the_producers_own_naming_styles(self):
        cases = {
            "infernal 155 fmin.mp3": (155, Key("F", True), "infernal"),
            "stereo 165 d#min.mp3": (165, Key("D#", True), "stereo"),
            "158 A# Min (ar22 x limewire).mp3": (158, Key("A#", True), None),
            "Night Drive 140bpm C# Minor.wav": (140, Key("C#", True), "Night Drive"),
            "bpm140 F#m dark.wav": (140, Key("F#", True), "dark"),
            "[FREE] Lights 152 Ebm.wav": (152, Key("D#", True), "Lights"),
            "Gb Maj 128 summer.wav": (128, Key("F#", False), "summer"),
            "@hauntshl 159 dmin +50cents.wav": (159, Key("D", True), None),
        }
        for name, (bpm, key, title) in cases.items():
            info = parse_name(name, remove=("ar22", "limewire"))
            self.assertEqual((info.bpm, info.key), (bpm, key), name)
            if title:
                self.assertEqual(info.title, title, name)

    def test_words_times_copies_and_dates_are_not_tempos_or_keys(self):
        for name in ("i am here 2.wav", "Lights 4 AM.wav", "them.wav", "beat (1).wav", "2026-10-31 idea.wav", "cmaj7 sketch.wav",
                     "sorry 808 loop.wav", "@hauntshl +50cents.wav"):
            info = parse_name(name)
            self.assertIsNone(info.key, name)
            self.assertIsNone(info.bpm, name)

    def test_key_spellings(self):
        self.assertEqual(Key("C#", True).name, "C# minor")
        self.assertEqual(Key("C#", True).short, "c#min")
        self.assertEqual(parse_name("x E-flat minor 90").key, Key("D#", True))
        self.assertEqual(music.label(Key("F", True), "name"), "fmin")
        self.assertEqual(music.label(154, "audio"), "≈154")
        self.assertEqual(music.label(None, None), "")


class AudioTests(unittest.TestCase):
    def test_tempo_folding_and_key_profiles(self):
        self.assertEqual(music.fold_tempo(77.5, 100, 200), 155)
        self.assertEqual(music.fold_tempo(310, 100, 200), 155)
        self.assertIsNone(music.fold_tempo(0, 100, 200))
        a_minor = np.roll(music.MINOR_PROFILE, 9)
        self.assertEqual(music.key_from_chroma(a_minor, 0.15)[0], Key("A", True))
        self.assertEqual(music.key_from_chroma(np.zeros(12))[0], None)

    def test_tempo_and_key_are_heard_from_audio(self):
        path = beat(BASE / "untitled idea.wav")
        heard = music.analyse_audio(path, {"analysis_seconds": 30})
        self.assertAlmostEqual(heard["bpm"], 140, delta=1.5)
        self.assertEqual(heard["key"], Key("A", True))

    def test_the_name_wins_and_only_gaps_are_heard(self):
        path = beat(BASE / "named 150 fmin.wav")
        with patch.object(music, "analyse_audio") as analyse:
            info = music.describe(path)
        analyse.assert_not_called()  # both in the name: the audio isn't even opened
        self.assertEqual((info["bpm"], info["bpm_source"], info["key"].short, info["key_source"]), (150, "name", "fmin", "name"))
        half = BASE / "half 150.wav"
        with patch.object(music, "analyse_audio", return_value={"bpm": 151.2, "key": Key("A", True), "key_confidence": .5}) as analyse:
            info = music.describe(half)
        self.assertEqual(analyse.call_args.kwargs, {"need_bpm": False, "need_key": True})
        self.assertEqual((info["bpm"], info["bpm_source"], info["key"].short, info["key_source"]), (150, "name", "amin", "audio"))
        self.assertEqual(music.describe(half, measured={"bpm": None, "key": "c#min"})["key"], Key("C#", True))
        self.assertIsNone(music.describe(BASE / "nothing.wav", listen=False)["bpm"])


class NamingTests(unittest.TestCase):
    def test_sorting_adds_only_the_missing_bpm_and_key(self):
        settings = {**sorter.DEFAULTS, "add_bpm_key": True, "add_date": False}
        heard = {"bpm": 154, "key": Key("C#", True)}
        self.assertEqual(sorter.new_name(Path("night drive.wav"), settings, heard), "night drive 154 c#min.wav")
        self.assertEqual(sorter.new_name(Path("night drive 150.wav"), settings, heard), "night drive 150 c#min.wav")
        self.assertEqual(sorter.new_name(Path("night 150 fmin.wav"), settings, heard), "night 150 fmin.wav")
        self.assertEqual(sorter.new_name(Path("night drive.wav"), {**settings, "add_bpm_key": False}, heard), "night drive.wav")
        self.assertEqual(sorter.new_name(Path("night drive.wav"), settings, None), "night drive.wav")


if __name__ == "__main__":
    unittest.main()
