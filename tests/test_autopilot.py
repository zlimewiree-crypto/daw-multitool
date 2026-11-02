"""Autopilot: new beats in a watched folder get videos made automatically; nothing is ever posted by it.
Fake audio and images only; rendering, the drum detector and settings writes are stand-ins where named."""
import contextlib
from datetime import datetime, timedelta
import io
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile

from multitool import music
from multitool.auto_poster import autopilot
from multitool.auto_poster.storage import load_settings

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="autopilot_test_", dir=ROOT / "test_exports"))


def archive():
    target = Path(tempfile.mkdtemp(prefix="daw-autopilot-tests-", dir="/private/tmp"))
    with (target / "_log.txt").open("a") as log:
        log.write(f"{datetime.now().isoformat()} | started | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())
        shutil.move(str(BASE), str(target / BASE.name))
        log.write(f"{datetime.now().isoformat()} | done | move | {BASE} | {target / BASE.name}\n")


def setUpModule():
    unittest.addModuleCleanup(archive)  # fake media is archived, never deleted


def wav(path, seconds=2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * 22050)) / 22050
    soundfile.write(path, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 22050)
    return path


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=BASE))
        self.data = Path(tempfile.mkdtemp(dir=BASE))
        patcher = patch.object(autopilot, "DATA_DIR", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = {**load_settings(), "settle_seconds": 3, "post_times": ["18:00"]}
        self.credits = {"producer_name": "kellan", "known_producers": ["ar22"], "ignore_words": [], "credit_format": "prod. {credits}"}

    def test_only_finished_files_that_arrive_after_turning_on_are_picked_up(self):
        old = wav(self.folder / "backlog 140 fmin.wav")
        ledger = autopilot.turn_on(now=time.time() + 5)
        self.assertEqual(autopilot.new_files(self.folder, self.settings, ledger, now=time.time() + 60), [])  # the backlog stays
        ledger = autopilot.turn_on(now=time.time() - 60)
        new = wav(self.folder / "new 150 cmin.wav")
        (self.folder / "notes.txt").write_text("not audio")
        self.assertEqual(autopilot.new_files(self.folder, self.settings, ledger, now=time.time()), [])  # still exporting
        found = autopilot.new_files(self.folder, self.settings, ledger, now=time.time() + 10)
        self.assertEqual(set(found), {old, new})  # the "old" file was written after turning on too
        autopilot.remember(new, {"state": "ready", "detail": "videos ready to review"})
        self.assertNotIn(new, autopilot.new_files(self.folder, self.settings, autopilot.load_ledger(), now=time.time() + 10))
        wav(new, seconds=3.0)  # exported again: a new version counts as new
        self.assertIn(new, autopilot.new_files(self.folder, self.settings, autopilot.load_ledger(), now=time.time() + 10))
        self.assertEqual(autopilot.activity(autopilot.load_ledger())[0][1:], ("new 150 cmin.wav", "Videos ready to review"))

    def test_turned_off_or_no_folder_means_nothing_happens(self):
        self.assertEqual(autopilot.new_files(self.folder, self.settings, {"since": None, "files": {}}), [])
        self.assertEqual(autopilot.new_files("", self.settings, {"since": 1, "files": {}}), [])
        self.assertEqual(autopilot.run_once({**self.settings, "autopilot_enabled": False, "autopilot_folder": str(self.folder)}), [])

    def test_texts_use_the_name_and_never_a_guessed_key(self):
        info = autopilot.describe(self.folder / "infernal 155 fmin ar22.mp3", self.credits)
        self.assertEqual((info["title"], info["bpm"], info["key"]), ("infernal", "155 BPM", "F minor"))
        text = autopilot.texts(self.folder / "infernal 155 fmin ar22.mp3", autopilot.DEFAULT_OPTIONS, self.credits, info)
        self.assertEqual(text["title"], "infernal (prod. kellan x ar22) | 155 BPM F minor")
        self.assertEqual(text["overlay"], ("infernal", "155 BPM · F minor · prod. kellan x ar22"))
        heard = {"bpm": 151.6, "key": music.Key("A", True), "key_confidence": 0.9}
        with patch.object(music, "analyse_audio", return_value=heard):
            info = autopilot.describe(wav(self.folder / "untitled idea.wav"), {**self.credits, "producer_name": "", "known_producers": []})
        self.assertEqual((info["bpm"], info["key"], info["key_value"]), ("152 BPM", "", music.Key("A", True)))
        text = autopilot.texts(self.folder / "untitled idea.wav", {**autopilot.DEFAULT_OPTIONS, "overlay": False}, {**self.credits, "producer_name": "", "known_producers": []}, info)
        self.assertEqual((text["title"], text["overlay"]), ("untitled idea | 152 BPM", ()))

    def test_a_folder_of_backgrounds_takes_turns(self):
        backgrounds = self.folder / "backgrounds"
        backgrounds.mkdir()
        for name in ("b.png", "a.png", ".hidden.png", "clip.mp4"):
            (backgrounds / name).write_bytes(b"x")
        settings = dict(self.settings)
        with patch.object(autopilot, "save_preferences") as saved:
            picks = [autopilot.next_visual(backgrounds, "image", settings).name for _ in range(3)]
        self.assertEqual(picks, ["a.png", "b.png", "a.png"])
        self.assertEqual(saved.call_args.args[0], {"background_turns": {str(backgrounds.resolve()): "a.png"}})
        with self.assertRaisesRegex(ValueError, "no videos"):
            autopilot.next_visual(self.folder, "video", settings, remember_choice=False)

    def test_requests_follow_the_create_options(self):
        beat = wav(self.folder / "night 140 fmin.wav")
        request = autopilot.build_request(beat, autopilot.DEFAULT_OPTIONS, self.settings, self.credits)
        self.assertIsNone(request.visual)  # automatic: made while rendering
        self.assertTrue(request.waveform)
        self.assertEqual(request.overlay[0], "night")
        with self.assertRaisesRegex(ValueError, "Choose a background"):
            autopilot.build_request(beat, {**autopilot.DEFAULT_OPTIONS, "visual_kind": "image", "visual": ""}, self.settings, self.credits)
        with patch("multitool.auto_poster.snippet.best_start", return_value=12.5) as best:
            short = autopilot.with_short_version(request, {"short_seconds": 30}, self.settings)
        self.assertEqual((short.short_start, short.short_duration), (12.5, 30))
        self.assertEqual(best.call_args.kwargs, {"bpm": 140})  # the name's BPM sets the beat grid
        same = autopilot.with_short_version(request, {"short_seconds": 0}, self.settings)
        self.assertIsNone(same.short_start)

    def test_melody_loops_are_skipped_and_beats_get_videos(self):
        loop, beat = wav(self.folder / "pad loop.wav"), wav(self.folder / "beat 150 cmin.wav")
        drums = Mock()
        drums.classify.return_value = ("loop", 0.03)
        with patch.object(autopilot, "create_video") as render:
            entry = autopilot.process(loop, autopilot.DEFAULT_OPTIONS, self.settings, self.credits, drums=drums)
        self.assertEqual(entry["state"], "skipped")
        render.assert_not_called()
        drums.classify.return_value = ("review", 0.6)  # unsure files still get videos; the user reviews them anyway
        with patch.object(autopilot, "create_video", return_value=self.folder / "post.json") as render, \
             patch("multitool.auto_poster.snippet.best_start", return_value=4.0):
            entry = autopilot.process(beat, autopilot.DEFAULT_OPTIONS, self.settings, self.credits, drums=drums)
        self.assertEqual((entry["state"], entry["receipt"]), ("ready", str(self.folder / "post.json")))
        self.assertEqual(render.call_args.args[0].short_start, 4.0)

    def test_the_background_pass_records_every_file_and_notifies_once(self):
        autopilot.turn_on(now=time.time() - 60)
        beats = [wav(self.folder / f"beat {n} 150 cmin.wav") for n in (1, 2)]
        for path in beats:
            os.utime(path, (time.time() - 30, time.time() - 30))
        settings = {**self.settings, "autopilot_enabled": True, "autopilot_folder": str(self.folder),
                    "autopilot_options": {"skip_loops": False, "visual_kind": "project"}}
        outcomes = iter([{"state": "ready", "receipt": "r1"}, RuntimeError("disk full")])
        def process(path, options, *args, **kwargs):
            self.assertEqual(options["visual_kind"], "auto")  # never takes over the DAW unattended
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        notify = Mock()
        with patch.object(autopilot, "process", side_effect=process):
            done = autopilot.run_once(settings, notify=notify)
        self.assertEqual([e["state"] for e in done], ["ready", "failed"])
        self.assertIn("disk full", done[1]["detail"])
        notify.assert_called_once()
        self.assertEqual(autopilot.new_files(self.folder, settings, autopilot.load_ledger()), [])

    def test_a_file_is_claimed_once_and_a_stopped_claim_is_taken_over(self):
        beat = wav(self.folder / "claimed 150 cmin.wav")
        key = autopilot.claim(beat, now=1000)
        self.assertTrue(key)
        self.assertIsNone(autopilot.claim(beat, now=1060))  # the helper and the app never both render it
        self.assertEqual(autopilot.claim(beat, now=1000 + 3 * 3600), key)  # that run stopped hours ago
        autopilot.remember(beat, {"state": "ready"}, key)
        self.assertIsNone(autopilot.claim(beat, now=1000 + 30 * 3600))  # done stays done

    def test_a_new_beat_becomes_a_ready_video_pair_from_start_to_finish(self):
        """No stand-ins for the work itself: best part, automatic background, text, waveform and both renders."""
        from multitool.auto_poster import render
        autopilot.turn_on(now=time.time() - 60)
        beat = wav(self.folder / "finale 150 cmin.wav", seconds=6.0)
        os.utime(beat, (time.time() - 30, time.time() - 30))
        settings = {**self.settings, "formats": {"Landscape 16:9": [160, 90], "Portrait 9:16": [90, 160]}, "fps": 15,
                    "preset": "ultrafast", "crf": 30, "settle_seconds": 0.01, "output_folder": str(self.folder / "videos"),
                    "autopilot_enabled": True, "autopilot_folder": str(self.folder),
                    "autopilot_options": {"skip_loops": False, "short_seconds": 2}}
        with patch.object(render, "DATA_DIR", self.data), patch.object(autopilot.credits, "load_settings", return_value=self.credits):
            done = autopilot.run_once(settings)
        self.assertEqual([entry["state"] for entry in done], ["ready"], done)
        from multitool.auto_poster.storage import manifest, video_variant
        record = manifest(done[0]["receipt"])
        wide, tall = video_variant(record, "Landscape 16:9"), video_variant(record, "Portrait 9:16")
        self.assertAlmostEqual(wide["duration"], 6, delta=0.2)
        self.assertEqual(tall["duration"], 2)
        self.assertEqual(record["music"], {"title": "finale", "bpm": "150 BPM", "key": "C minor"})
        self.assertTrue(record["title"].startswith("finale (prod. kellan) | 150 BPM C minor"))
        self.assertEqual(list((self.data / "video-text").glob("*")), [])  # the on-video text files were tidied away
        self.assertTrue(beat.exists())  # the beat itself is only read

    def test_project_mode_keeps_the_chosen_still_for_youtube(self):
        beat, still = wav(self.folder / "night 140 fmin.wav"), self.folder / "cover.png"
        still.write_bytes(b"x")
        options = {**autopilot.DEFAULT_OPTIONS, "visual_kind": "project", "visual": str(still)}
        self.assertEqual(autopilot.build_request(beat, options, self.settings, self.credits).visual, still.resolve())
        options["visual"] = ""
        self.assertIsNone(autopilot.build_request(beat, options, self.settings, self.credits).visual)  # automatic instead

    def test_a_different_folder_starts_fresh_and_missing_settings_touch_nothing(self):
        other = Path(tempfile.mkdtemp(dir=BASE))
        autopilot.turn_on(self.folder, now=time.time() - 60)
        wav(other / "older 150 cmin.wav")
        self.assertFalse(autopilot.watching(other, autopilot.load_ledger()))
        self.assertEqual(autopilot.new_files(other, self.settings, autopilot.load_ledger(), now=time.time() + 10), [])
        settings = {**self.settings, "autopilot_enabled": True, "autopilot_folder": str(other)}
        self.assertEqual(autopilot.run_once(settings), [])
        self.assertTrue(autopilot.watching(other, autopilot.load_ledger()))  # the helper started fresh there
        autopilot.turn_on(other, now=time.time() - 60)
        new = wav(other / "new 150 cmin.wav")
        os.utime(new, (time.time() - 30, time.time() - 30))  # finished exporting
        broken = {**settings, "autopilot_options": {"visual_kind": "image", "visual": "", "skip_loops": False}}
        report = Mock()
        with patch.object(autopilot, "process") as process:
            self.assertEqual(autopilot.run_once(broken, report=report), [])
        process.assert_not_called()
        self.assertIn("Choose a background", report.call_args.args[0])
        self.assertEqual(autopilot.load_ledger()["files"], {})  # nothing was claimed or used up

    def test_failed_files_are_retried_a_few_times_an_hour_apart(self):
        beat = wav(self.folder / "flaky 150 cmin.wav")
        for attempt in range(1, autopilot.RETRIES + 1):
            key = autopilot.claim(beat, now=1000 + attempt * 4000)
            self.assertTrue(key, attempt)
            autopilot.remember(beat, {"state": "failed", "detail": "disk full"}, key)
            self.assertIsNone(autopilot.claim(beat, now=1000 + attempt * 4000 + 60))  # not again within the hour
        self.assertIsNone(autopilot.claim(beat, now=10 ** 9))  # given up after the last try

    def test_next_free_posting_times_skip_taken_and_past_slots(self):
        now = datetime(2026, 11, 1, 13, 0).astimezone()
        settings = {"post_times": ["18:00", "12:00", "nonsense"]}
        taken = [datetime(2026, 11, 1, 18, 0).astimezone()]
        slots = autopilot.next_free_times(3, settings, taken, now=now)
        self.assertEqual([(s.day, s.hour) for s in slots], [(2, 12), (2, 18), (3, 12)])
        self.assertEqual(autopilot.next_free_times(1, {"post_times": []}, now=now)[0].hour, 18)
        entries = [{"state": "waiting", "due": "2026-11-02T12:00:00+00:00"}, {"state": "posted", "due": "2026-11-03T12:00:00+00:00"}]
        self.assertEqual(len(autopilot.scheduled_times(entries)), 1)
        self.assertGreater(slots[0] - now, timedelta(minutes=10))


if __name__ == "__main__":
    unittest.main()


class AutopilotWindowTests(unittest.TestCase):
    """The Auto Poster window's side of Autopilot, with stand-ins for the window, files and rendering."""
    def app(self):
        import queue
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.root, app.busy, app.events, app.jobs, app.status = Mock(), False, queue.Queue(), {}, Mock()
        app.settings = {**load_settings(), "autopilot_check_seconds": 30}
        app.cancel = Mock(is_set=Mock(return_value=False))
        rows = iter(["row1", "row2"])
        app.tracks = Mock(insert=Mock(side_effect=lambda *args, **kwargs: next(rows)))
        app.autopilot_on, app.skip_loops = Mock(get=Mock(return_value=True)), Mock(get=Mock(return_value=True))
        app.autopilot_folder = Mock(get=Mock(return_value=str(BASE)))
        app.options = Mock(return_value={**autopilot.DEFAULT_OPTIONS, "visual_kind": "project"})
        app.credit_settings, app.refresh_credits = Mock(return_value={}), Mock()
        app.run_worker = lambda work: work()  # the background step runs at once
        return app

    def events(self, app):
        found = []
        while not app.events.empty():
            found.append(app.events.get())
        return found

    def test_new_beats_are_claimed_rendered_and_announced(self):
        app = self.app()
        first, second = BASE / "one 150 cmin.wav", BASE / "two 140 fmin.wav"
        with patch.object(autopilot, "new_files", return_value=[first, second]), \
             patch.object(autopilot, "claim", side_effect=["key1", None]), \
             patch.object(autopilot, "detector", return_value=Mock()), \
             patch.object(autopilot, "remember") as remembered, \
             patch.object(autopilot, "process", return_value={"state": "ready", "receipt": "/fake/post.json"}) as process, \
             patch("multitool.auto_poster.ui.schedule.notify") as notify:
            app.autopilot_tick()
        self.assertEqual(process.call_count, 1)  # the second file was already claimed by the helper
        self.assertEqual(process.call_args.args[1]["visual_kind"], "auto")  # never takes over the DAW unattended
        remembered.assert_called_once_with(first, {"state": "ready", "receipt": "/fake/post.json"}, "key1")
        self.assertEqual(self.events(app), [("rendered", "row1", Path("/fake/post.json")), ("drop", "row2"), ("autopilot", 1)])
        notify.assert_called_once()
        self.assertTrue(app.jobs["row1"]["autopilot"])

    def test_nothing_happens_while_off_or_busy(self):
        app = self.app()
        app.autopilot_on.get.return_value = False
        with patch.object(autopilot, "new_files") as new_files:
            app.autopilot_tick()
            app.autopilot_on.get.return_value, app.busy = True, True
            app.autopilot_tick()
        new_files.assert_not_called()
        self.assertEqual(app.root.after.call_count, 2)  # it keeps checking on its timer

    def test_the_background_helper_makes_videos_before_posting(self):
        from multitool.auto_poster import schedule
        settings = {**load_settings(), "autopilot_enabled": True}
        with patch("multitool.auto_poster.storage.load_settings", return_value=settings), \
             patch.object(autopilot, "run_once", return_value=[{"name": "beat.wav", "state": "ready", "detail": ""}]) as run, \
             patch.object(schedule, "due_now", return_value=[]), \
             patch("multitool.auto_poster.publish.pending", return_value=[]), \
             patch("multitool.auto_poster.results.receipts", return_value=[]), \
             patch.object(schedule, "load", return_value=[]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(schedule.run_from_helper(), 0)
        run.assert_called_once()
        with patch("multitool.auto_poster.storage.load_settings", return_value={**settings, "autopilot_enabled": False}), \
             patch.object(autopilot, "run_once") as run, patch.object(schedule, "due_now", return_value=[]), \
             patch("multitool.auto_poster.publish.pending", return_value=[]), \
             patch("multitool.auto_poster.results.receipts", return_value=[]), patch.object(schedule, "load", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            schedule.run_from_helper()
        run.assert_not_called()
