"""Detector/sorter regressions use synthetic files only; originals are never removed."""
from concurrent.futures import Future
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

import drums
import sorter

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="organizer_audit_test_", dir=ROOT / "test_exports"))


def archive():
    target = Path(tempfile.mkdtemp(prefix="daw-organizer-tests-", dir=tempfile.gettempdir()))
    with (target / "_log.txt").open("a") as log:
        log.write(f"{datetime.now().isoformat()} | started | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())
        shutil.move(str(BASE), str(target / BASE.name))
        log.write(f"{datetime.now().isoformat()} | done | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())


def setUpModule():
    # Registered here, not at import: unittest keeps ONE cleanup list for every module and
    # runs it when the first module finishes, which archived BASE before these tests ran.
    unittest.addModuleCleanup(archive)


def features(value=0.0):
    return {name: value for name in drums.FEATURES}


class DetectorTests(unittest.TestCase):
    def test_melody_names_cannot_be_confidently_sent_to_beats(self):
        settings = dict(sorter.DEFAULTS)
        for name in ("Loop.mp3", "roon mel 137.wav", "sample 1.wav", "melody.wav"):
            self.assertEqual(drums.classify_score(.999, name, settings)[0], "review")
        for name in ("drum loop.wav", "808 loop.wav", "hi hat loop.wav", "beatoff.wav"):
            self.assertEqual(drums.classify_score(.999, name, settings)[0], "beat")
        settings["loop_name_hint"] = 0
        self.assertEqual(drums.classify_score(.999, "loop.wav", settings)[0], "beat")

    def test_melody_words_are_configurable_and_do_not_match_unrelated_words(self):
        settings = {**sorter.DEFAULTS, "melody_name_words": ["custom"]}
        self.assertTrue(drums.melody_name("custom 123.wav", settings))
        self.assertFalse(drums.melody_name("customary.wav", settings))
        self.assertFalse(drums.melody_name("loop.wav", settings))

    def test_fft_grid_matches_original_autocorrelation(self):
        env = np.random.default_rng(7).random(2000)
        onset = np.maximum(0, np.diff(env))
        onset -= onset.mean()
        original = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
        low, high = int(.08 * drums.SR / drums.HOP), int(1.0 * drums.SR / drums.HOP)
        self.assertAlmostEqual(drums._grid_strength(env, .08, 1.0), original[low:high].max() / original[0], places=12)
        self.assertEqual(drums._grid_strength([], .08, 1.0), 0)

    def test_bad_saved_model_falls_back_safely(self):
        path, shipped = BASE / "bad-model.json", BASE / "shipped-model.json"
        good = {**drums.DEFAULT_MODEL, "source": "shipped", "feature_version": drums.FEATURE_VERSION}
        for data in ("[1]", "{}", "not json", json.dumps({**drums.DEFAULT_MODEL, "scales": {"log_perc_p80": 0}})):
            path.write_text(data)
            shipped.write_text(data)
            with patch.object(drums, "MODEL_FILE", path), patch.object(drums, "SHIPPED_MODEL_FILE", shipped):
                self.assertEqual(drums.load_model(), drums.DEFAULT_MODEL)
                shipped.write_text(json.dumps(good))
                self.assertEqual(drums.load_model()["source"], "shipped")  # a damaged own model falls back to the app's

    def test_an_outdated_own_model_gives_way_to_the_shipped_one(self):
        path, shipped = BASE / "own-model.json", BASE / "shipped-model.json"
        shipped.write_text(json.dumps({**drums.DEFAULT_MODEL, "source": "shipped", "feature_version": drums.FEATURE_VERSION}))
        with patch.object(drums, "MODEL_FILE", path), patch.object(drums, "SHIPPED_MODEL_FILE", shipped):
            path.write_text(json.dumps({**drums.DEFAULT_MODEL, "source": "mine", "feature_version": drums.FEATURE_VERSION - 1}))
            self.assertEqual(drums.load_model()["source"], "shipped")  # can't use the classifier: superseded
            path.write_text(json.dumps({**drums.DEFAULT_MODEL, "source": "mine", "feature_version": drums.FEATURE_VERSION}))
            self.assertEqual(drums.load_model()["source"], "mine")

    def test_without_the_classifier_the_basic_formula_is_used(self):
        model = {**drums.DEFAULT_MODEL, "weights": {"ml_kit": 1.0}, "means": {"ml_kit": 0.0}, "scales": {"ml_kit": 1.0},
                 "basic": drums.DEFAULT_MODEL}
        heard = {**features(-2.0), "ml_kit": 3.0}
        self.assertGreater(drums.drum_probability(heard, model), 0.9)
        unheard = {name: value for name, value in heard.items() if not name.startswith("ml_")}
        self.assertEqual(drums.drum_probability(unheard, model), drums.drum_probability(unheard, drums.DEFAULT_MODEL))
        with self.assertRaises(ValueError):  # no classifier measurements and no fallback formula
            drums.drum_probability(unheard, {**model, "basic": None})

    def test_learned_weights_never_turn_drum_evidence_into_melody_evidence(self):
        rng = np.random.default_rng(3)
        rows = []
        for label in (0, 1) * 12:
            f = features(0.0)
            f.update(ml_kit=label * 2 + rng.normal(0, .5), ml_parts=label + rng.normal(0, .5),
                     log_hat_hits=-label * 3 + rng.normal(0, .3))  # an anti-correlated measurement, like crackle
            rows.append((f, label))
        model, held_out = drums.learn(rows)
        self.assertTrue(all(weight >= 0 for weight in model["weights"].values()))
        self.assertEqual(model["weights"]["log_hat_hits"], 0)
        self.assertGreater(np.mean([p for p, (_, label) in zip(held_out, rows) if label]), 0.7)

    def test_reference_copies_train_along_but_never_leak_into_the_blind_test(self):
        rows = [(features(v), label) for v, label in ((-4, 0), (-3, 0), (-2, 0), (2, 1), (3, 1), (4, 1))]
        base = [(features(v), label) for v, label in ((-5, 0), (5, 1))] + [rows[0]]  # one is the same as a labelled file
        alone, held_alone = drums.learn(rows)
        together, held_together = drums.learn(rows, base=base)
        self.assertIn("2 reference copies", together["source"])  # the duplicate was left out
        self.assertEqual(len(held_together), len(rows))
        self.assertNotEqual(alone["weights"], together["weights"])
        self.assertEqual(len(drums.reference_rows({"training": [[features(1), 1], ["bad", 0], [features(2), 7]]})), 1)

    def test_a_model_learned_without_the_classifier_only_replaces_the_fallback(self):
        path = BASE / "learned-model.json"
        current = {**drums.DEFAULT_MODEL, "weights": {"ml_kit": 1.0}, "means": {"ml_kit": 0.0}, "scales": {"ml_kit": 1.0},
                   "source": "shipped", "training": [[features(1), 1]]}
        basic = {**drums.DEFAULT_MODEL, "weights": {"snap_noise": 2.0}, "means": {"snap_noise": 0.0},
                 "scales": {"snap_noise": 1.0}, "source": "mine"}
        with patch.object(drums, "MODEL_FILE", path):
            drums.save_model(basic, keep_from=current)
        saved = json.loads(path.read_text())
        self.assertEqual((saved["source"], saved["basic"]["source"], len(saved["training"])), ("shipped", "mine", 1))

    def test_classifier_output_becomes_measurements_and_failures_are_ignored(self):
        answer = json.dumps({"classes": ["drum_kit", "snare_drum", "hi_hat", "bass_drum", "cymbal"],
                             "windows": [[0.5, 0.1, 0.1, 0.1, 0.1], [0.0, 0.0, 0.0, 0.0, 0.0]]})
        with patch.object(drums.subprocess, "run", return_value=Mock(stdout=answer + "\n")):
            heard = drums.listen("beat.wav", Path("/bin/classifier"))
        self.assertAlmostEqual(heard["ml_kit"], np.log10(0.25 + 0.003))
        self.assertEqual(heard["ml_coverage"], 0.5)
        for broken in ("", "not json", json.dumps({"path": "x", "error": "unreadable"})):
            with patch.object(drums.subprocess, "run", return_value=Mock(stdout=broken)):
                self.assertEqual(drums.listen("beat.wav", Path("/bin/classifier")), {})
        self.assertEqual(drums.listen("beat.wav", None), {})

    def test_duplicate_audio_does_not_leak_into_its_held_out_fold(self):
        rows = [(features(v), label) for v, label in ((-4, 0), (-3, 0), (-2, 0), (2, 1), (3, 1), (4, 1))]
        first, scores = drums.learn(rows)
        repeated, repeated_scores = drums.learn([*rows, rows[0], rows[3]])
        np.testing.assert_allclose(scores, repeated_scores[:6])
        self.assertEqual(repeated_scores[6], scores[0])
        self.assertEqual(repeated_scores[7], scores[3])
        self.assertEqual(first, repeated)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            drums.learn([*rows, (rows[0][0], 1)])

    def test_nan_audio_is_unreadable_instead_of_a_confident_prediction(self):
        with patch.object(drums.librosa, "load", return_value=(np.full(drums.SR, np.nan), drums.SR)):
            self.assertIsNone(drums.analyse("fake.wav"))

    def test_cancellation_stops_workers_before_a_result_arrives(self):
        path = BASE / "pending.bin"
        path.write_bytes(b"synthetic metadata")
        pool = Mock()
        pool.submit.return_value = Future()
        stopped = [False]
        def wait(*args, **kwargs):
            stopped[0] = True
            return set(), set()
        with patch.object(drums, "_load_cache", return_value={}), patch.object(drums, "ProcessPoolExecutor", return_value=pool), \
             patch.object(drums, "wait", side_effect=wait), patch.object(drums.DrumDetector, "save_cache"):
            detector = drums.DrumDetector()
            detector.analyse_many([path], cancelled=lambda: stopped[0])
        pool.terminate_workers.assert_called_once()
        self.assertFalse(detector.is_cached(path))

    def test_changed_file_does_not_get_cached_under_its_new_snapshot(self):
        path = BASE / "changing.bin"
        path.write_bytes(b"before")
        pool = Mock()
        future = Future()
        def submit(*args):
            path.write_bytes(b"after with different content")
            future.set_result(features())
            return future
        pool.submit.side_effect = submit
        with patch.object(drums, "_load_cache", return_value={}), patch.object(drums, "ProcessPoolExecutor", return_value=pool), \
             patch.object(drums.DrumDetector, "save_cache"):
            detector = drums.DrumDetector()
            detector.analyse_many([path])
        self.assertFalse(detector.is_cached(path))
        self.assertIn("exported", detector.skipped[path])

    def test_one_uncached_export_does_not_hide_later_preview_rows(self):
        paths = [BASE / name for name in ("A.wav", "B.wav", "C.wav")]
        detector = Mock(skipped={paths[1]: "still exporting"})
        detector.is_cached.side_effect = lambda p: p != paths[1]
        detector.classify.return_value = ("loop", .01)
        detector.features.return_value = features()
        with patch.object(sorter, "find_bounces", return_value=(paths, [])), patch.object(sorter, "DrumDetector", return_value=detector), \
             patch.object(sorter, "load_labels", return_value={}):
            moves, skipped = sorter.plan(sorter.DEFAULTS)
        self.assertEqual([move.src for move in moves], [paths[0], paths[2]])
        self.assertEqual(skipped, [(paths[1], "still exporting")])


class SorterSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=BASE))
        spec = importlib.util.spec_from_file_location("fake_audio", ROOT / "scripts/make_test_files.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        self.audio = self.folder / "synthetic.wav"
        generator.make_wav(self.audio, 1)
        self.settings = {**sorter.DEFAULTS, "source_folder": str(self.folder), "output_folder": "", "min_seconds": 0,
                         "split_by_month": False, "settle_seconds": 0}

    def test_names_with_slashes_make_one_folder_and_traversal_is_rejected(self):
        self.settings["sorted_folder_name"] = "limewire beats/mels"
        root = sorter.sorted_root(self.settings)
        self.assertEqual(root.parent, self.folder)
        self.assertNotIn("/", root.name)
        for bad in ("..", "../outside", "/absolute", "bad\nname"):
            with self.assertRaises(ValueError):
                sorter.folder_name(bad)

    def test_existing_nested_or_case_aliased_outputs_are_not_rescanned(self):
        self.settings.update(sorted_folder_name="old/nested", include_subfolders=True)
        old = self.folder / "old/nested/Melodies"
        old.mkdir(parents=True)
        original = self.audio.read_bytes()
        shutil.copy2(self.audio, old / "already.wav")
        files, _ = sorter.find_bounces(self.settings)
        self.assertEqual(files, [self.audio])
        self.assertEqual(self.audio.read_bytes(), original)

    def test_copy_refuses_to_finalize_if_the_source_changes_mid_transfer(self):
        destination = self.folder / "copy.wav"
        destination.touch()
        copy = shutil.copyfileobj
        def changed(source, target):
            copy(source, target)
            with self.audio.open("ab") as stream:
                stream.write(b"test export still growing")
        with patch.object(sorter.shutil, "copyfileobj", side_effect=changed):
            with self.assertRaisesRegex(OSError, "original preserved"):
                sorter.copy_into(self.audio, destination)
        self.assertEqual(destination.stat().st_size, 0)
        self.assertTrue(self.audio.exists())
        self.assertTrue(list(self.folder.glob("*.partial")))

    def test_collision_and_move_journal_preserve_every_audio_file(self):
        move = sorter.PlannedMove(self.audio, "loop", .01, self.audio.name)
        folder = move.folder(self.settings)
        folder.mkdir(parents=True)
        existing = folder / self.audio.name
        existing.write_bytes(b"existing fake audio placeholder")
        original = self.audio.read_bytes()
        self.assertEqual(sorter.execute([move], self.settings, report=lambda _: None), 1)
        self.assertEqual(existing.read_bytes(), b"existing fake audio placeholder")
        self.assertEqual(move.destination.name, "synthetic (2).wav")
        self.assertEqual(move.destination.read_bytes(), original)
        log = (sorter.sorted_root(self.settings) / "_log.txt").read_text()
        self.assertIn(" | started | move | ", log)
        self.assertIn(" | done | move | ", log)
        self.assertEqual(sorter.load_sorted(self.settings)[0].path, move.destination)

    def test_case_alias_of_sorted_folder_is_ignored_on_case_insensitive_disks(self):
        self.settings["include_subfolders"] = True
        folder = sorter.sorted_root(self.settings) / "Melodies"
        folder.mkdir(parents=True)
        shutil.copy2(self.audio, folder / "already.wav")
        alias = folder.parent.with_name(folder.parent.name.lower())
        if not alias.exists():
            self.skipTest("case-sensitive test filesystem")
        self.settings["sorted_folder_name"] = alias.name
        files, _ = sorter.find_bounces(self.settings)
        self.assertEqual(files, [self.audio])
