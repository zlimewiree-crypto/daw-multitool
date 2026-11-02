"""Snippet finder, collab credits, producer tag and duplicate/loudness checks, on fake media only."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from multitool import credits
from multitool.auto_poster.render import VideoRequest, create_video, ffmpeg_binary, tag_times
from multitool.auto_poster.snippet import best_start
from multitool.auto_poster.storage import manifest
import subprocess
import sorter
from multitool.auto_poster.render import loudness, loudness_warnings
from multitool.auto_poster.storage import load_settings

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="features_test_", dir=ROOT / "test_exports"))
SR = 44100


def archive():
    """Move (never delete) the fake media out of the project, with a log, like the other test modules."""
    target = Path(tempfile.mkdtemp(prefix="daw-feature-tests-", dir="/private/tmp"))
    with (target / "_log.txt").open("a") as log:
        shutil.move(str(BASE), str(target / BASE.name))
        log.write(f"{datetime.now().isoformat(timespec='seconds')} | done | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())


def setUpModule():
    unittest.addModuleCleanup(archive)  # see test_organizer: register here, not at import


def fake_beat(path, seconds=60, drums_from=20):
    """A soft pad the whole time; hats every 1/4 s and an 808 thump every 1/2 s after `drums_from`."""
    t = np.arange(int(seconds * SR)) / SR
    y = 0.15 * np.sin(2 * np.pi * 330 * t)
    rng = np.random.default_rng(0)
    for start in np.arange(drums_from, seconds - 0.5, 0.25):
        i = int(start * SR)
        n = int(0.04 * SR)
        y[i:i + n] += 0.3 * rng.standard_normal(n) * np.exp(-np.linspace(0, 6, n))
    for start in np.arange(drums_from, seconds - 0.5, 0.5):
        i = int(start * SR)
        n = int(0.4 * SR)
        y[i:i + n] += 0.6 * np.sin(2 * np.pi * 45 * t[:n]) * np.exp(-np.linspace(0, 3, n))
    sf.write(path, y / np.max(np.abs(y)) * 0.8, SR)
    return path


class SnippetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.beat = fake_beat(BASE / "late drums.wav")
        cls.before = cls.beat.stat().st_mtime_ns

    def test_best_part_starts_where_the_drums_come_in(self):
        start = best_start(self.beat, 15, load_settings())
        self.assertGreaterEqual(start, 19)
        self.assertLessEqual(start, 45)  # the whole window fits inside the track
        self.assertEqual(self.beat.stat().st_mtime_ns, self.before)  # read-only

    def test_track_shorter_than_the_snippet_starts_at_zero(self):
        short = fake_beat(BASE / "short.wav", seconds=10, drums_from=2)
        self.assertEqual(best_start(short, 15, load_settings()), 0.0)


class CreditTests(unittest.TestCase):
    SETTINGS = {"producer_name": "kellan", "credit_format": "prod. {credits}",
                "ignore_words": ["limewire", "limeewiree", "prod", "loop", "mel"]}
    CASES = {
        "@hauntshl @limewire 159 dmin +50cents (1).mp3": ["@hauntshl"],
        "Cuddy gmin 159bpm prod. cari.mp3": ["cari"],
        "crank 170 prod hedez & 2ya.mp3": ["hedez", "2ya"],
        "forest man 154 (prod. deezy).wav": ["deezy"],
        "palm trees prod limewire x mas.mp3": ["mas"],
        "arsenal 155 c#min ar22.flp limewire.mp3": ["ar22"],
        "9-3-26 blendz 157bpm [kimbo] loop.mp3": ["kimbo"],
        "mel42Cmaj153@mo1zy.mp3": ["@mo1zy"],
        "zodiac (Prod. Deezy) 158 bpm.mp3": ["Deezy"],
        "pain 163 gmin.mp3": [],
        "beat prod kellan x @ar22.mp3": ["@ar22"],  # your own name is never listed twice
    }

    def test_collaborators_from_real_name_patterns(self):
        for name, expected in self.CASES.items():
            self.assertEqual(credits.collaborators(name, self.SETTINGS), expected, name)

    def test_credit_line_and_placeholders(self):
        title = credits.fill("{name} ({credits})", "crank 170 prod hedez & 2ya.mp3", "crank", self.SETTINGS)
        self.assertEqual(title, "crank (prod. kellan x hedez x 2ya)")
        alone = {**self.SETTINGS, "producer_name": ""}
        self.assertEqual(credits.credit_line("pain 163 gmin.mp3", alone), "")
        self.assertEqual(credits.fill("{name} ({credits})", "pain 163 gmin.mp3", "pain 163 gmin", alone), "pain 163 gmin")

    def test_configured_names_match_bare_filenames_without_markers(self):
        settings = {**self.SETTINGS, "known_producers": ["ar22", "purptokyo"]}
        for name in ("night ar22 140.wav", "AR22_beat.mp3", "night-ar22-demo.flac", "nightar22.wav"):
            self.assertEqual(credits.collaborators(name, settings), ["ar22"])
        self.assertEqual(credits.collaborators("purptokyo x ar22 140.wav", settings), ["purptokyo", "ar22"])
        self.assertEqual(credits.collaborators("night nobody 140.wav", settings), [])
        self.assertEqual(credits.collaborators("/ar22/night.wav", settings), [])  # filenames, not parent folders

    def test_explicit_and_automatic_credits_are_not_repeated_and_own_name_is_excluded(self):
        settings = {**self.SETTINGS, "known_producers": ["ar22", "@AR22", "Kellan"]}
        self.assertEqual(credits.collaborators("@ar22 ar22.flp kellan.wav", settings), ["@ar22"])
        self.assertEqual(credits.credit_line("kellan ar22.wav", settings), "prod. kellan x ar22")
        self.assertEqual(credits.fill("{name} ({credits})", "night ar22.wav", "night ar22", settings),
                         "night ar22 (prod. kellan x ar22)")

    def test_configured_names_are_literals_and_override_automatic_ignore_words(self):
        settings = {**self.SETTINGS, "known_producers": ["a+b", "x.y", "limewire"]}
        self.assertEqual(credits.collaborators("track a+b x.y limewire.wav", settings), ["a+b", "x.y", "limewire"])
        self.assertEqual(credits.collaborators("track aaab xzy.wav", settings), [])
        self.assertEqual(credits.producer_names("ar22, AR22; @ar22\npurptokyo, , mas"), ["ar22", "purptokyo", "mas"])

    def test_names_persist_without_changing_other_tools_settings(self):
        import app_paths
        import json
        with tempfile.TemporaryDirectory() as directory, patch.object(app_paths, "DATA_DIR", Path(directory)):
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"bounce_sorter": {"mode": "copy"}, "auto_poster": {"fps": 30}}))
            credits.save_settings({"producer_name": " kellan ", "known_producers": "ar22, purptokyo, AR22"})
            saved = credits.load_settings()
            self.assertEqual(saved["known_producers"], ["ar22", "purptokyo"])
            self.assertEqual(saved["producer_name"], "kellan")
            config = json.loads(path.read_text())
            self.assertEqual(config["bounce_sorter"], {"mode": "copy"})
            self.assertEqual(config["auto_poster"], {"fps": 30})

    def test_auto_poster_adopts_changes_from_another_window_without_overwriting_them(self):
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        old = {**self.SETTINGS, "known_producers": ["ar22"]}
        new = {**old, "known_producers": ["ar22", "mas"]}
        app.credit_values = old
        app.producer = Mock(get=Mock(return_value="kellan"))
        app.known_producers = Mock(get=Mock(return_value="ar22"))
        with patch.object(credits, "save_settings", return_value=new) as save:
            self.assertEqual(app.credit_settings(), new)
        save.assert_called_once_with({})
        app.known_producers.set.assert_called_once_with("ar22, mas")

    def test_organizer_refreshes_collaborators_without_changing_audio_category(self):
        from bounce_sorter_app import App
        app = App.__new__(App)
        app.credit_settings = {**self.SETTINGS, "known_producers": []}
        app.known_producers = Mock(get=Mock(return_value="ar22"))
        app.moves = {"row": sorter.PlannedMove(Path("collab ar22.wav"), "loop", .05, "collab ar22.wav")}
        app.refresh_row, app.status = Mock(), Mock()
        settings = {**app.credit_settings, "known_producers": ["ar22"]}
        with patch.object(credits, "save_settings", return_value=settings) as save:
            self.assertTrue(app.save_credits())
        save.assert_called_once_with({"known_producers": ["ar22"]})
        self.assertEqual(app.moves["row"].category, "loop")
        app.refresh_row.assert_called_once_with("row")

    def test_organizer_ticking_a_file_turns_sort_on_and_unticking_turns_it_off(self):
        from bounce_sorter_app import App
        app = App.__new__(App)
        app.busy, app.refresh_row, app.sort_btn = False, Mock(), Mock()
        app.moves = {"row": sorter.PlannedMove(Path("copy.wav"), "loop", .05, "copy.wav")}
        app.use = {"row": False}  # e.g. an exact copy, left unticked by Preview
        app.tree = Mock(identify_row=Mock(return_value="row"), identify_column=Mock(return_value="#1"),
                        column=Mock(return_value="use"))
        app.on_click(Mock(x=0, y=0))
        app.sort_btn.configure.assert_called_with(state="normal")
        app.on_click(Mock(x=0, y=0))
        app.sort_btn.configure.assert_called_with(state="disabled")

    def test_auto_poster_credit_column_marks_collabs_and_solo_files(self):
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.jobs = {"collab": {"audio": Path("night ar22.wav")}, "solo": {"audio": Path("night solo.wav")}}
        app.tracks = Mock()
        app.refresh_credits({**self.SETTINGS, "known_producers": ["ar22"]})
        self.assertEqual([call.args for call in app.tracks.set.call_args_list],
                         [("collab", "credits", "ar22"), ("solo", "credits", "Solo")])


class TagTests(unittest.TestCase):
    def test_tag_plays_at_the_chosen_times_and_originals_stay_untouched(self):
        settings = {**load_settings(), "formats": {"Test": [160, 284]}, "fps": 15, "preset": "ultrafast",
                    "crf": 28, "settle_seconds": 0.01, "tag_first_seconds": 2, "tag_every_seconds": 5}
        t = np.arange(12 * SR) / SR
        beat, tag = BASE / "quiet beat.wav", BASE / "my tag.wav"
        sf.write(beat, 0.05 * np.sin(2 * np.pi * 200 * t), SR)
        sf.write(tag, 0.5 * np.sin(2 * np.pi * 3000 * t[:int(0.8 * SR)]), SR)
        visual = BASE / "black.png"
        subprocess.run([ffmpeg_binary(settings), "-nostdin", "-n", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=64x64", "-frames:v", "1", str(visual)], check=True)
        before = {p: p.stat().st_mtime_ns for p in (beat, tag, visual)}
        self.assertEqual(tag_times(12, settings), [2, 7])
        receipt = create_video(VideoRequest(beat, visual, BASE / "tagged", "Test", tag=tag), settings, formats=("Test",))
        record = manifest(receipt)
        self.assertEqual(record["state"], "ready")
        import librosa
        y, sr = librosa.load(record["tagged_mp3"], sr=SR)
        band = lambda a, b: float(np.sqrt(np.mean(librosa.effects.preemphasis(y[int(a * sr):int(b * sr)]) ** 2)))
        self.assertGreater(band(2.1, 2.7), 5 * band(4.0, 4.6))  # tag at 2 s, nothing extra at 4 s
        self.assertGreater(band(7.1, 7.7), 5 * band(4.0, 4.6))  # and again at 7 s
        self.assertTrue(Path(record["variants"]["Test"]["video"]).is_file())
        self.assertEqual({p: p.stat().st_mtime_ns for p in before}, before)


    def test_stop_during_the_tagged_mp3_leaves_no_mp3(self):
        from multitool.auto_poster import render
        settings = {**load_settings(), "tag_first_seconds": 1, "tag_every_seconds": 3}
        t = np.arange(20 * SR) / SR
        beat, tag = BASE / "stop beat.wav", BASE / "stop tag.wav"
        sf.write(beat, 0.1 * np.sin(2 * np.pi * 200 * t), SR)
        sf.write(tag, 0.4 * np.sin(2 * np.pi * 3000 * t[:SR // 2]), SR)
        folder = BASE / "stopped"
        folder.mkdir()
        request = VideoRequest(beat, beat, folder, "Test", tag=tag)
        before = {p: render.snapshot(p) for p in (beat, tag)}
        with self.assertRaises(render.Cancelled):
            render._encode_tagged_mp3(request, settings, folder, 20, before, cancelled=lambda: True)
        self.assertEqual(list(folder.glob("*(tagged).mp3")), [])

class DuplicateAndLoudnessTests(unittest.TestCase):
    def test_copies_are_flagged_and_the_original_is_kept(self):
        folder = BASE / "dups"
        folder.mkdir()
        t = np.arange(6 * SR) / SR
        original = folder / "night 150 cmin.wav"
        sf.write(original, 0.3 * np.sin(2 * np.pi * 220 * t), SR)
        download = folder / "night 150 cmin (1).wav"
        shutil.copy2(original, download)  # byte-identical second download
        mp3 = folder / "night 150 cmin.mp3"
        subprocess.run([ffmpeg_binary(load_settings()), "-nostdin", "-n", "-loglevel", "error", "-i", str(original),
                        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
        other = folder / "different 160 gmin.wav"
        sf.write(other, 0.3 * np.sin(2 * np.pi * 330 * t), SR)
        moves = [sorter.PlannedMove(p, "loop", 0.1, p.name) for p in (download, mp3, original, other)]
        sorter.mark_duplicates(moves)
        flagged = {m.src.name: m.duplicate_of.name if m.duplicate_of else None for m in moves}
        self.assertEqual(flagged, {"night 150 cmin (1).wav": "night 150 cmin.wav", "night 150 cmin.mp3": "night 150 cmin.wav",
                                   "night 150 cmin.wav": None, "different 160 gmin.wav": None})
        kinds = {m.src.name: m.duplicate_kind for m in moves if m.duplicate_of}
        self.assertEqual(kinds, {"night 150 cmin (1).wav": "identical", "night 150 cmin.mp3": "same name"})
        self.assertTrue(all(p.exists() for p in (download, mp3, original, other)))  # nothing deleted

    def test_quiet_and_clipping_beats_get_warnings(self):
        settings = load_settings()
        t = np.arange(8 * SR) / SR
        quiet, hot = BASE / "quiet.wav", BASE / "hot.wav"
        sf.write(quiet, 0.03 * np.sin(2 * np.pi * 1000 * t), SR)
        sf.write(hot, 0.999 * np.sign(np.sin(2 * np.pi * 1000 * t)), SR)  # square wave at full scale
        q = loudness(quiet, 0, 8, settings)
        self.assertLess(q["lufs"], -25)
        self.assertTrue(any("quiet" in w for w in loudness_warnings(q, settings)))
        h = loudness(hot, 0, 8, settings)
        self.assertGreater(h["true_peak"], -1)
        self.assertTrue(any("clip" in w for w in loudness_warnings(h, settings)))



class ProjectModeTests(unittest.TestCase):
    """Project screen-recording mode: name matching, per-version visuals, and the record flow with the DAW mocked."""

    def test_finds_the_project_an_export_came_from(self):
        from multitool.auto_poster.project import find_project, project_index
        root = BASE / "projects"
        for name in ("FL/needa lead.flp", "FL/needa.flp", "FL/Backup/needa lead.flp",
                     "FL/needa lead (autosaved on 16-10-26 at 16h57).flp", "abl/dream Project/dream.als",
                     "a/b/c/d/deep.flp", "FL/notes.txt"):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"fake project")
        index = project_index({"daws": {".flp": {}, ".als": {}}, "project_folders": [str(root)], "project_search_depth": 3})
        self.assertEqual(sorted(p.relative_to(root).as_posix() for p in index),
                         ["FL/needa lead.flp", "FL/needa.flp", "abl/dream Project/dream.als"])  # no backups/autosaves/too deep
        for audio, expected in (("needa lead 140 fmin.wav", "needa lead.flp"), ("Needa_Lead (1).mp3", "needa lead.flp"),
                                ("needa 150.mp3", "needa.flp"), ("dream.wav", "dream.als"), ("deep.wav", None),
                                ("unrelated beat.wav", None)):
            match = find_project(audio, index)
            self.assertEqual(match.name if match else None, expected, audio)

    def test_vertical_version_uses_the_recording_and_youtube_keeps_the_image(self):
        from multitool.auto_poster.render import posting_formats
        ff = ffmpeg_binary(load_settings())
        folder = BASE / "project-render"
        folder.mkdir()
        audio, image, recording = folder / "beat.wav", folder / "still.png", folder / "recording.mov"
        quiet = dict(check=True, capture_output=True)
        subprocess.run([ff, "-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(audio)], **quiet)
        subprocess.run([ff, "-f", "lavfi", "-i", "color=c=black:s=64x36", "-frames:v", "1", str(image)], **quiet)
        # blue for the first second, red after: with vertical_start=1.5 the vertical video must be red
        subprocess.run([ff, "-f", "lavfi", "-i", "color=c=blue:s=64x36:d=5:r=30",
                        "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='gte(t,1)'", str(recording)], **quiet)
        before = {p: p.stat().st_mtime_ns for p in (audio, image, recording)}
        settings = {**load_settings(), "settle_seconds": 0, "formats": {"Wide": [64, 36], "Tall": [36, 64]},
                    "platform_formats": {"youtube": "Wide", "tiktok": "Tall", "instagram": "Tall"}}
        request = VideoRequest(audio, image, folder / "renders", "Wide", project=folder / "beat.flp",
                               vertical_visual=recording, vertical_start=1.5,
                               social_description="new beat", hashtags="rage", social_caption="new beat\n\n#rage")
        record = manifest(create_video(request, settings, formats=posting_formats(settings)))
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["vertical_visual"], str(recording))
        # the TikTok / Reels draft is saved at render time, so closing before Publish doesn't lose it
        self.assertEqual((record["social_description"], record["hashtags"], record["social_caption"]),
                         ("new beat", "rage", "new beat\n\n#rage"))

        def colour(video, area="scale=1:1"):
            pixel = subprocess.run([ff, "-ss", "0.5", "-i", video, "-frames:v", "1", "-vf", area + ",scale=1:1",
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
            return max(range(3), key=lambda i: pixel[i]) if max(pixel) > 60 else "black"

        self.assertEqual(colour(record["variants"]["Wide"]["video"]), "black")
        self.assertEqual(colour(record["variants"]["Tall"]["video"]), 0)  # red channel: started 1.5 s in, not at blue
        # top strip: a stretched, blurred copy of the recording instead of black bars
        self.assertEqual(colour(record["variants"]["Tall"]["video"], "crop=iw:ih/8:0:0"), 0)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in (audio, image, recording)})

    def recording_settings(self, **changes):
        return {**load_settings(), "project_load_seconds": 5, "project_settle_seconds": 0,
                "recording_lead_seconds": 0.2, **changes}

    def fake_capture(self, calls, source="testsrc2"):
        """Stands in for screencapture: runs as long as the real one would and leaves a 2 s clip
        (moving test pattern = DAW playing; a flat colour = DAW frozen)."""
        ff = ffmpeg_binary(load_settings())
        clip = BASE / f"fake capture {source}.mov"
        if not clip.exists():
            subprocess.run([ff, "-f", "lavfi", "-i", f"{source}=s=64x36:d=2:r=30", str(clip)], check=True, capture_output=True)
        real = subprocess.Popen

        class Capture:
            def __init__(self, args, **_):
                calls.append(args)
                shutil.copy(clip, args[-1])
                self.returncode, self.stderr, self.ends = 0, None, time.monotonic() + 2

            def poll(self):
                return 0 if time.monotonic() >= self.ends else None
        # FFmpeg probes still run for real; only screencapture is faked
        return lambda args, **kw: Capture(args) if args[0] == "/usr/sbin/screencapture" else real(args, **kw)

    @staticmethod
    def window(name, width=1200, height=800):
        return {"name": name, "bounds": {"X": 0, "Y": 44, "Width": width, "Height": height}}

    def record(self, path, windows, source="testsrc2", **settings):
        """record_project with the DAW, permissions and screencapture faked; returns (result, played, calls, opened)."""
        from multitool.auto_poster import project
        path.write_bytes(b"fake project")
        calls, played, self.ways = [], [], []
        with patch.object(project, "check_permissions"), patch.object(project, "_open") as opened, \
                patch.object(project, "daw_windows", side_effect=lambda _: next(windows)), \
                patch.object(project, "press_play", side_effect=lambda process, keys=(), via="app": played.append((process, list(keys))) or self.ways.append(via) or time.monotonic()), \
                patch.object(project, "update_config") as self.remembered, \
                patch.object(project.subprocess, "Popen", self.fake_capture(calls, source)):
            try:
                result = project.record_project(path, 1, BASE / "Screen recordings", self.recording_settings(**settings))
            except ValueError as error:
                result = error
        self.assertEqual(path.read_bytes(), b"fake project")  # never changed
        return result, played, calls, opened

    def test_record_presses_home_then_play_once_the_project_window_is_open(self):
        import itertools
        flp = BASE / "my beat.flp"
        windows = itertools.chain([[], [], [self.window("FL Studio 2026")]], itertools.repeat([self.window("my beat - FL Studio 2026")]))
        (recording, offset), played, calls, opened = self.record(flp, windows)
        opened.assert_any_call("FL Studio 2026", flp)
        self.assertEqual(played, [("OsxFL", [115]), ("OsxFL", [])])  # Home + play, then just stop
        self.assertIn("-R0,44,1200,800", calls[0])
        self.assertTrue(recording.is_file() and recording.parent == BASE / "Screen recordings")
        self.assertTrue(0 < offset < 1, offset)  # play was pressed ~0.2 s into the recording
        self.assertFalse(recording.with_suffix(".log").exists())  # the log is only kept when a recording fails
        self.remembered.assert_not_called()  # the usual way worked: nothing to remember

    def test_title_without_the_project_name_counts_once_it_changes(self):
        from multitool.auto_poster.project import project_window
        before = {"FL Studio 2026"}
        self.assertIsNone(project_window("my beat.flp", [self.window("FL Studio 2026")], before))  # old project still up
        self.assertIsNone(project_window("my beat.flp", [self.window("Loading", 300, 200)], before))  # splash ignored
        self.assertEqual(project_window("my beat.flp", [self.window("Untitled 3 - FL Studio 2026")], before)["name"],
                         "Untitled 3 - FL Studio 2026")
        self.assertEqual(project_window("my_beat.flp", [self.window("x"), self.window("My Beat", 500, 400)])["name"], "My Beat")

    def test_frozen_recording_means_the_daw_did_not_play(self):
        import itertools
        result, played, _, _ = self.record(BASE / "frozen.als", itertools.repeat([self.window("frozen")]), source="color")
        self.assertRegex(str(result), "didn't start playing")
        self.assertEqual(len(played), 4)  # play + stop, then once more the other way
        self.assertEqual(self.ways, ["app", "app", "keyboard", "keyboard"])
        self.remembered.assert_not_called()
        self.assertEqual(list((BASE / "Screen recordings").glob("frozen-*")), [])  # failed takes aren't kept

    def test_record_never_presses_play_if_the_project_does_not_open(self):
        import itertools
        result, played, calls, _ = self.record(BASE / "slow set.als", itertools.repeat([]), project_load_seconds=0.2)
        self.assertRegex(str(result), "didn't open")
        self.assertEqual((played, calls), ([], []))  # no key pressed, nothing recorded


if __name__ == "__main__":
    unittest.main()
