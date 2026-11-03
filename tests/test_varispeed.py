"""Varispeed: tape-style speed changes. Fake tones only; nothing plays through the speakers (the sound output
is a stand-in) and the real settings file is never written."""
import contextlib
from datetime import datetime
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile

from multitool import music
from multitool.varispeed import core
from multitool.varispeed.player import Recorder, TapePlayer, latest_take, output_pairs

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="varispeed_test_", dir=ROOT / "test_exports"))


def archive():
    target = Path(tempfile.mkdtemp(prefix="daw-varispeed-tests-", dir=tempfile.gettempdir()))
    with (target / "_log.txt").open("a") as log:
        log.write(f"{datetime.now().isoformat()} | started | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())
        shutil.move(str(BASE), str(target / BASE.name))
        log.write(f"{datetime.now().isoformat()} | done | move | {BASE} | {target / BASE.name}\n")


def setUpModule():
    unittest.addModuleCleanup(archive)  # fake audio is archived, never deleted


def tone(path, frequency=440.0, seconds=2.0, rate=44100):
    t = np.arange(int(seconds * rate)) / rate
    wave = (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
    soundfile.write(path, np.column_stack([wave, wave]), rate)
    return path


def pitch(samples, rate):
    spectrum = np.abs(np.fft.rfft(samples[:, 0] * np.hanning(len(samples))))
    return np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(spectrum)]


class SpeedTests(unittest.TestCase):
    def test_speeds_semitones_and_what_people_type(self):
        self.assertAlmostEqual(core.speed_from_semitones(12), 2.0)
        self.assertAlmostEqual(core.semitones_from_speed(0.5), -12)
        typed = {"112%": 1.12, "1.5x": 1.5, "+2 st": 2 ** (2 / 12), "-3 semitones": 2 ** (-3 / 12), "+2": 2 ** (2 / 12),
                 "85": 0.85, "−12": 0.5}
        for text, speed in typed.items():
            self.assertAlmostEqual(core.parse_amount(text), speed, places=6, msg=text)
        for bad in ("fast", "", "0%", "-200%"):
            with self.assertRaises(ValueError, msg=bad):
                core.parse_amount(bad)
        self.assertEqual(core.clamp(3.0, {"min_speed": 0.5, "max_speed": 2.0}), 2.0)
        self.assertEqual(core.readout(2 ** (2 / 12)), "112.2% · +2 st")
        self.assertEqual(core.readout(1.0), "100.0% · 0 st")

    def test_the_new_bpm_and_key_and_a_name_in_the_same_style(self):
        info = music.describe("infernal 155 fmin.mp3", listen=False)
        now = core.changed(info, core.speed_from_semitones(2))
        self.assertEqual((now["bpm"], now["key"], now["key_exact"]), (174, music.Key("G", True), True))
        self.assertFalse(core.changed(info, 1.2)["key_exact"])  # 120% sits between two keys
        names = {("infernal 155 fmin.mp3", 2): "infernal 174 gmin (varispeed 112.2%).wav",
                 ("Night Drive 140bpm C# Minor.wav", 1): "Night Drive 148bpm D Minor (varispeed 105.9%).wav",
                 ("Lights 152 Ebm.wav", 2): "Lights 171 Fm (varispeed 112.2%).wav",
                 ("untitled.wav", 12): "untitled (varispeed 200%).wav"}
        for (name, semitones), expected in names.items():
            speed = core.speed_from_semitones(semitones)
            self.assertEqual(core.new_name(name, speed, music.describe(name, listen=False)), expected)
        self.assertEqual(core.new_name("idea 150 fmin.wav", 1.2, music.describe("idea 150 fmin.wav", listen=False)),
                         "idea 180 (varispeed 120%).wav")  # no key written when it's between two


class SaveTests(unittest.TestCase):
    def test_a_copy_is_shorter_and_higher_and_nothing_is_overwritten(self):
        source = tone(BASE / "tone 120 amin.wav")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        out = BASE / "copies"
        speed = core.speed_from_semitones(2)
        options = {**core.settings(), "sample_format": "PCM_24"}
        first = core.save(source, speed, out, options)
        self.assertEqual(first.name, "tone 135 bmin (varispeed 112.2%).wav")
        samples, rate = soundfile.read(first, dtype="float32")
        self.assertEqual(rate, 44100)  # the copy keeps the original's sample rate
        self.assertAlmostEqual(len(samples) / rate, 2 / speed, delta=0.01)
        self.assertAlmostEqual(pitch(samples, rate), 440 * speed, delta=2)
        second = core.save(source, speed, out, options)
        self.assertEqual(second.name, "tone 135 bmin (varispeed 112.2%) (2).wav")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)  # the original is only read
        self.assertEqual([p.name for p in out.iterdir() if p.name.startswith(".")], [])  # no leftovers

    def test_the_command_line_is_a_dry_run_unless_told_to_save(self):
        source = tone(BASE / "dry 140 fmin.wav", seconds=0.5)
        out = BASE / "dry-copies"
        with patch.object(sys, "argv", ["core", str(source), "--speed", "-12 st", "--output", str(out)]), \
             contextlib.redirect_stdout(io.StringIO()) as printed:
            core.main()
        self.assertIn("DRY RUN", printed.getvalue())
        self.assertIn("dry 70 fmin (varispeed 50%).wav", printed.getvalue())
        self.assertFalse(out.exists())


class OriginalRulesTests(unittest.TestCase):
    """The behaviour copied from the original Varispeed: readouts, slider, nudges and glide timing."""
    def test_readouts_slider_and_nudges(self):
        self.assertEqual([core.semitone_text(x) for x in (0, 2, -2.5312, 12, -0.004)], ["0 st", "+2 st", "−2.53 st", "+12 st", "0 st"])
        self.assertEqual(core.percent_text(2 ** (2 / 12)), "112.2%")
        self.assertEqual(core.slider_semitones(0.01), 0)  # snaps to 100% near the middle
        self.assertEqual((core.slider_semitones(-0.5), core.slider_semitones(1)), (-6, 12))
        self.assertAlmostEqual(core.slider_position(0.5), -1)
        self.assertAlmostEqual(core.semitones_from_speed(core.nudge(core.speed_from_semitones(1.9999999), 1)), 3)
        self.assertEqual(core.nudge(1.9, 12), 2.0)  # stops at 200%
        self.assertEqual((core.edited_number(" −2,5 st", "st"), core.edited_number("+112.2%", "%")), (-2.5, 112.2))
        self.assertIsNone(core.edited_number("fast", "%"))

    def test_glides_take_as_long_as_a_tape_motor(self):
        options = core.settings()
        self.assertAlmostEqual(core.glide_seconds(1.0, 0.5, 48000, options), 0.369, places=3)  # an octave down
        self.assertAlmostEqual(core.glide_seconds(0.5, 1.0, 48000, options), 0.739, places=3)  # up is half as quick
        self.assertEqual(core.glide_seconds(1.0, 1.001, 48000, options), 0.1)  # never shorter than this


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.source = np.column_stack([np.arange(48000, dtype=np.float32)] * 2)
        self.stream = Mock()
        self.opened = []
        def stream(rate, callback, device=None, channel_map=None):
            self.opened.append((device, channel_map))
            return self.stream
        self.player = TapePlayer(self.source, 1000, core.settings(), stream)

    def test_normal_speed_plays_the_samples_as_they_are(self):
        self.player.open()
        self.player.playing = True
        np.testing.assert_allclose(self.player.render(100), self.source[:100])
        self.stream.start.assert_called_once()

    def test_while_stopped_the_speed_is_there_at_once(self):
        self.player.set_speed(2.0)
        self.player.open()
        self.player.playing = True
        np.testing.assert_allclose(self.player.render(50)[:, 0], np.arange(0, 100, 2))

    def test_a_glide_follows_the_tape_curve_and_runs_on_while_paused(self):
        self.player.open()
        self.player.set_speed(0.5)  # an octave down
        frames = int(core.glide_seconds(1.0, 0.5, 1000) * 1000)
        self.player.render(frames // 2)  # paused: silence, but the glide carries on
        self.assertAlmostEqual(1 / self.player.speed, 1.5, places=2)  # halfway in time = halfway in 1/speed
        self.player.render(frames)
        self.assertAlmostEqual(self.player.speed, 0.5)

    def test_the_end_stops_playback_and_outputs_go_where_chosen(self):
        self.player.open(device="Interface", left_channel=2, channels=6)
        self.assertEqual(self.opened, [("Interface", [-1, -1, 0, 1, -1, -1])])
        self.player.playing = True
        self.player.seek(47.99)
        block = self.player.render(50)
        self.assertFalse(self.player.playing)
        self.assertEqual(float(np.abs(block[20:]).max()), 0.0)
        out = np.zeros((4, 6), dtype=np.float32)
        self.player.channel_map = [2, 3]
        self.player.callback(out, 4, None, Mock(output_underflow=True))
        self.assertEqual(self.player.dropouts, 1)
        self.player.close()
        self.stream.close.assert_called_once()
        self.assertEqual(output_pairs(6), ["Outputs 1–2", "Outputs 3–4", "Outputs 5–6"])


class RecorderTests(unittest.TestCase):
    def test_takes_are_float_wavs_named_by_time_and_never_replaced(self):
        folder = BASE / "takes"
        when = datetime(2026, 11, 1, 18, 30, 5)
        first, second = Recorder(folder, 48000), Recorder(folder, 48000)
        self.assertEqual(first.start(now=when).name, "Varispeed 2026-11-01 18.30.05.wav")
        self.assertEqual(second.start(now=when).name, "Varispeed 2026-11-01 18.30.05 (2).wav")
        first.push(np.full((4800, 2), 0.25, dtype=np.float32))
        path, seconds = first.stop()
        second.stop()
        info = soundfile.info(path)
        self.assertEqual((info.subtype, info.channels, info.samplerate, seconds), ("FLOAT", 2, 48000, 0.1))
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(latest_take(folder)[0].parent, folder)  # only the newest take is shown
        self.assertIsNone(latest_take(BASE / "nothing here"))


class WindowTests(unittest.TestCase):
    def test_type_the_speed_play_record_and_turn_the_output_off(self):
        source = tone(BASE / "window 150 cmin.wav", seconds=1.0)
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        window = tk.Toplevel(root)  # tools open in their own window, like in the app
        window.withdraw()
        stream = Mock(latency=0.012)
        from multitool.varispeed import ui
        options = {**core.settings(), "speed": 1.0, "output_folder": str(BASE / "window-copies"),
                   "recordings_folder": str(BASE / "window-takes"), "output_device": "", "source": "files"}
        with patch.object(ui, "update_config") as saved, patch.object(core, "settings", return_value=options):
            app = ui.VarispeedApp(window, stream=lambda rate, callback, device=None, channel_map=None: stream,
                                  devices=lambda: ([("Speakers", 2), ("Interface", 6)], "Speakers"))
            self.assertEqual(app.device.get(), "Speakers")  # starts with the Mac's current output
            with patch.object(ui.filedialog, "askopenfilenames", return_value=[str(source)]):
                app.add_files()
            app.percent.edit()
            app.percent.typed.set("112.2")
            app.percent.commit()
            self.assertAlmostEqual(app.target, 1.122)
            app.semis.edit()
            app.semis.typed.set("+2")
            app.semis.commit()
            self.assertEqual(app.percent.text.get(), "112.2%")
            row = app.tree.get_children()[0]
            self.assertEqual(app.tree.set(row, "saves"), "window 168 dmin (varispeed 112.2%).wav")
            deadline = time.time() + 20
            while app.player is None and time.time() < deadline:  # loading happens in the background
                root.update()
                time.sleep(0.05)
            app.toggle_play()
            stream.start.assert_called_once()
            app.toggle_recording()
            self.assertTrue(window.title().startswith("● Varispeed 112%"))  # like the original's menu bar label
            app.player.render(4800)
            app.toggle_recording()
            while app.saving_take and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertEqual(len(list((BASE / "window-takes").glob("Varispeed *.wav"))), 1)
            app.output_on.set(False)
            app.output_changed()
            self.assertFalse(app.player.running)
            app.toggle_play()
            self.assertIn("Turn on Output to device", app.status.get())
            app.close()
        saved.assert_called()  # remembered through the stand-in, never the real settings file
        self.assertFalse(window.winfo_exists())


if __name__ == "__main__":
    unittest.main()
