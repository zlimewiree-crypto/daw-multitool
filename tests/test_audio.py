"""DAW Multitool's audio device, bridge and Settings. Nothing is installed (the password prompt is a stand-in), no
audio device is opened and nothing plays: the device is loaded into a test program with a pretend Core Audio, the
bridge's tests use pretend audio callbacks, and the windows talk to a stand-in bridge."""
from datetime import datetime
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

import soundfile

from multitool.audio import bridge as audio_bridge, build, device

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="audio_test_", dir=ROOT / "test_exports"))
HAS_CLANG = subprocess.run(["/usr/bin/xcrun", "--find", "clang"], capture_output=True).returncode == 0


def archive():
    target = Path(tempfile.mkdtemp(prefix="daw-audio-tests-", dir="/private/tmp"))
    with (target / "_log.txt").open("a") as log:
        log.write(f"{datetime.now().isoformat()} | started | move | {BASE} | {target / BASE.name}\n")
        log.flush()
        os.fsync(log.fileno())
        shutil.move(str(BASE), str(target / BASE.name))
        log.write(f"{datetime.now().isoformat()} | done | move | {BASE} | {target / BASE.name}\n")


def setUpModule():
    unittest.addModuleCleanup(archive)  # test recordings are archived, never deleted


def fake_bundle(folder, version=1):
    contents = Path(folder) / device.DRIVER_NAME / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    with open(contents / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleVersion": str(version)}, handle)
    return contents.parent


def options(**changes):
    return {**device.settings(), "plugin_folder": str(BASE / "HAL"), "pass_through": False, "output_device": "",
            "output_left_channel": 0, "device_check_seconds": 0.01, **changes}


FOUND = {"default_output": "BuiltInHeadphoneOutputDevice", "devices": [
    {"uid": "BuiltInHeadphoneOutputDevice", "name": "External Headphones", "inputs": 0, "outputs": 2, "rate": 48000},
    {"uid": "BuiltInMicrophoneDevice", "name": "MacBook Pro Microphone", "inputs": 1, "outputs": 0, "rate": 44100},
    {"uid": "Scarlett 4i4 USB_UID", "name": "Scarlett 4i4", "inputs": 4, "outputs": 4, "rate": 48000},
    {"uid": device.DEVICE_UID, "name": "DAW Multitool", "inputs": 2, "outputs": 2, "rate": 48000}]}
WORKING = {"type": "status", "device": True, "input": True, "output": True, "output_name": "Scarlett 4i4",
           "output_missing": False, "left": 2, "in_rate": 44100, "out_rate": 48000, "speed": 0.9, "target": 0.75,
           "latency_ms": 21.4, "dropouts": 0, "level": 0.5, "recording": False, "recorded": 0, "microphone": "granted"}


class FakeBridge:
    """Stands in for the audio bridge: remembers what it was told."""
    def __init__(self, running=False, status=None):
        self.running, self.status = running, status or {}
        self.started, self.speeds, self.records, self.stops, self.listeners = [], [], [], 0, []

    def start(self, uid="", left=0):
        self.running = True
        self.started.append((uid, left))

    def stop(self):
        self.running, self.status = False, {}

    def set_output(self, uid, left=0):
        self.started.append((uid, left))

    def set_speed(self, speed):
        self.speeds.append(speed)

    def record(self, path):
        self.records.append(Path(path))
        return self.running

    def stop_record(self):
        self.stops += 1

    def listen(self, callback):
        self.listeners.append(callback)

    def unlisten(self, callback):
        self.listeners.remove(callback)

    def tell(self, event):
        for listener in list(self.listeners):
            listener(event)


@unittest.skipUnless(HAS_CLANG, "needs Apple's command line tools")
class NativeTests(unittest.TestCase):
    def test_the_device_passes_its_checks_without_being_installed(self):
        folder = BASE / "device-build"
        bundle = build.build_driver(folder)
        self.assertEqual(subprocess.run(["/usr/bin/codesign", "--verify", str(bundle)], capture_output=True).returncode, 0)
        with open(bundle / "Contents" / "Info.plist", "rb") as handle:
            info = plistlib.load(handle)
        factory = next(iter(info["CFPlugInFactories"]))
        self.assertEqual(info["CFPlugInTypes"]["443ABAB8-E7B3-491A-B985-BEB9187030DB"], [factory])  # an audio plug-in
        self.assertEqual(device.version(bundle), 1)
        harness = folder / "test-device"
        build.run("/usr/bin/xcrun", "clang", "-O1", "-std=gnu11", str(build.NATIVE / "audio_device" / "test_device.c"),
                  *build.frameworks("CoreAudio", "CoreFoundation"), "-o", str(harness))
        result = subprocess.run([str(harness), str(bundle / "Contents" / "MacOS" / "DAWMultitoolAudio")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), "ok", result.stdout)  # properties, varispeed clock, loopback, rates
        self.assertEqual(build.build_driver(folder), bundle)  # up to date: not rebuilt
        archive = build.pack_driver(folder)  # how the app carries it
        self.assertEqual(device.version(archive), 1)
        commands = device.install_commands(archive, options(plugin_folder=str(BASE / "zip HAL")))
        self.assertIn(f"/usr/bin/ditto -x -k {shlex.quote(str(archive))} {shlex.quote(str(BASE / 'zip HAL'))}", commands)
        unpacked = BASE / "unpacked"
        build.run("/usr/bin/ditto", "-x", "-k", str(archive), str(unpacked))  # what Install does, minus the system folder
        self.assertEqual(subprocess.run(["/usr/bin/codesign", "--verify", str(unpacked / device.DRIVER_NAME)],
                                        capture_output=True).returncode, 0)  # its own signature survives the trip

    def test_the_bridge_resamples_records_and_never_replaces(self):
        helper = build.build_bridge(BASE / "bridge-build")
        takes = BASE / "bridge-takes"
        takes.mkdir()
        result = subprocess.run([str(helper), "--test", str(takes)], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.stdout.strip(), "ok", result.stdout)  # pitch, speed, drift, glides, no dropouts
        info = soundfile.info(takes / "bridge test.wav")
        self.assertEqual((info.subtype, info.channels, info.samplerate, info.frames), ("FLOAT", 2, 48000, 94 * 512))
        check = subprocess.run([str(helper), "--self-check"], capture_output=True, text=True, timeout=60)
        self.assertEqual(check.stdout.strip(), "ok")


class DeviceTests(unittest.TestCase):
    def test_status_says_what_is_installed(self):
        settings = options(plugin_folder=str(BASE / "status-HAL"))
        self.assertEqual(device.status([], settings, bundle=BASE / "nothing").summary(), "Not installed")
        fake_bundle(BASE / "status-HAL", version=1)
        self.assertIn("hasn't loaded", device.status([], settings, bundle=BASE / "nothing").summary())
        self.assertEqual(device.status([device.DEVICE_UID], settings, bundle=BASE / "nothing").summary(), "Installed and ready")
        newer = fake_bundle(BASE / "status-newer", version=2)
        state = device.status([device.DEVICE_UID], settings, bundle=newer)
        self.assertTrue(state.update)
        self.assertIn("newer version", state.summary())

    def test_install_and_remove_are_dry_runs_unless_asked(self):
        runner = Mock()
        settings = options(plugin_folder=str(BASE / "Plug Ins's HAL"))  # a space and a quote get quoted
        source = fake_bundle(BASE / "shipped")
        with patch.object(device, "bundled_driver", return_value=source):
            ok, commands = device.install(runner=runner, options=settings)
        self.assertTrue(ok)
        runner.assert_not_called()
        target = str(BASE / "Plug Ins's HAL" / device.DRIVER_NAME)
        self.assertIn(f"chown -R root:wheel {shlex.quote(target)}", commands)
        self.assertIn("killall coreaudiod", commands)
        for line in commands.splitlines() + device.remove(options=settings)[1].splitlines():
            if line.startswith("rm -rf"):
                self.assertTrue(line.rstrip("'").endswith(device.DRIVER_NAME))  # only ever our own bundle
        runner = Mock(return_value=Mock(returncode=0, stderr=""))
        with patch.object(device, "bundled_driver", return_value=source):
            self.assertEqual(device.install(run=True, runner=runner, options=settings), (True, ""))
        script = runner.call_args[0][0]
        self.assertEqual(script[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("with administrator privileges", script[2])
        cancelled = Mock(return_value=Mock(returncode=1, stderr="execution error: User canceled. (-128)"))
        self.assertEqual(device.remove(run=True, runner=cancelled, options=settings), (False, "Cancelled."))

    def test_the_command_line_is_a_dry_run(self):
        with patch.object(device, "bundled_driver", return_value=fake_bundle(BASE / "cli")), \
                patch.object(device, "settings", return_value=options()), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(device.main(["install"]), 0)
        self.assertTrue(output.getvalue().startswith("DRY RUN"))


class FakeProcess:
    def __init__(self, lines):
        self.stdin, self.stdout = io.StringIO(), io.StringIO("".join(json.dumps(line) + "\n" for line in lines) + "nonsense\n")
        self.ended = None
        self.stdin.close = lambda: None  # keep what was written readable

    def poll(self):
        return self.ended

    def wait(self, timeout=None):
        self.ended = 0


class BridgeTests(unittest.TestCase):
    def test_commands_status_and_events(self):
        process = FakeProcess([WORKING, {"type": "recorded", "path": "/x.wav", "seconds": 2.5}])
        bridge = audio_bridge.Bridge(helper="/bin/audio-bridge", popen=Mock(return_value=process))
        heard = []
        bridge.listen(heard.append)
        bridge.set_speed(0.75)  # before it starts: sent once it does
        bridge.start("Pro Tools Audio Bridge 16_UID", 2)
        deadline = time.time() + 5
        while not any(event["type"] == "stopped" for event in heard) and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual([event["type"] for event in heard], ["status", "recorded", "stopped"])
        process.ended = None
        bridge.process = process
        bridge.record(BASE / "take.wav")
        with self.assertRaises(ValueError):
            bridge.record("two\nlines.wav")
        bridge.stop()
        self.assertEqual(process.stdin.getvalue().splitlines(),
                         ["output 2 Pro Tools Audio Bridge 16_UID", "speed 0.750000", f"record {BASE / 'take.wav'}", "quit"])
        self.assertFalse(bridge.running)

    def test_devices_are_only_read(self):
        runner = Mock(return_value=Mock(stdout=json.dumps(FOUND)))
        found = audio_bridge.list_devices(helper="/bin/audio-bridge", runner=runner)
        self.assertEqual(runner.call_args[0][0], ["/bin/audio-bridge", "--list"])
        self.assertEqual([d["name"] for d in audio_bridge.outputs(found)], ["External Headphones", "Scarlett 4i4"])
        broken = Mock(return_value=Mock(stdout="not json"))
        self.assertEqual(audio_bridge.list_devices(helper="/bin/audio-bridge", runner=broken)["devices"], [])
        self.assertEqual(audio_bridge.output_pairs(4), ["Outputs 1–2", "Outputs 3–4"])

    def test_pass_through_comes_back_only_when_it_was_on_and_installed(self):
        bridge = FakeBridge()
        self.assertFalse(audio_bridge.resume(bridge, options(pass_through=True, plugin_folder=str(BASE / "none"))))
        fake_bundle(BASE / "resume-HAL")
        self.assertFalse(audio_bridge.resume(bridge, options(plugin_folder=str(BASE / "resume-HAL"))))
        self.assertTrue(audio_bridge.resume(bridge, options(pass_through=True, plugin_folder=str(BASE / "resume-HAL"),
                                                            output_device="Scarlett 4i4 USB_UID", output_left_channel=2)))
        self.assertEqual(bridge.started, [("Scarlett 4i4 USB_UID", 2)])


def window():
    root = tk.Tk()
    root.withdraw()
    top = tk.Toplevel(root)
    top.withdraw()
    return root, top


def step(window):
    """One turn of a window's loop now (instead of waiting for its timer)."""
    window.root.after_cancel(window.poll_id)
    window.poll()


def settle(root, until, seconds=5):
    deadline = time.time() + seconds
    while not until() and time.time() < deadline:
        root.update()
        time.sleep(0.02)


class SettingsWindowTests(unittest.TestCase):
    def test_install_choose_an_output_and_pass_audio_through(self):
        from multitool import settings as settings_window
        root, top = window()
        self.addCleanup(root.destroy)
        bridge, runner = FakeBridge(), Mock(return_value=Mock(returncode=0, stderr=""))
        saved = {}
        with patch.object(audio_bridge, "settings", return_value=options()), \
                patch.object(audio_bridge, "remember", side_effect=lambda **values: saved.update(values)), \
                patch.object(device, "bundled_driver", return_value=fake_bundle(BASE / "settings-shipped")), \
                patch.object(settings_window.messagebox, "askokcancel", return_value=True):
            app = settings_window.SettingsWindow(top, bridge=bridge, devices=lambda: FOUND, runner=runner)
            settle(root, lambda: app.choices)
            self.assertEqual(app.device_state.get(), "Not installed")
            self.assertEqual(app.install_button.cget("text"), "Install…")
            self.assertEqual(app.output.get(), "The Mac's sound output (External Headphones)")
            self.assertNotIn("DAW Multitool", app.output_box.cget("values"))  # never back into itself
            app.output.set("Scarlett 4i4")
            app.output_changed()
            app.pair.set("Outputs 3–4")
            app.output_changed()
            self.assertEqual((saved["output_device"], saved["output_left_channel"]), ("Scarlett 4i4 USB_UID", 2))
            app.pass_on.set(True)
            app.toggle_pass_through()
            self.assertEqual(bridge.started[-1], ("Scarlett 4i4 USB_UID", 2))
            self.assertTrue(saved["pass_through"])
            bridge.status = WORKING
            root.update()
            step(app)
            self.assertIn("44.1 kHz in → Scarlett 4i4 at 48 kHz", app.flow.get())
            app.install()
            settle(root, lambda: not app.busy)
            self.assertIn("with administrator privileges", runner.call_args[0][0][2])  # macOS's own password prompt
            self.assertTrue(app.status.get().startswith("Installed."))
            bridge.tell({"type": "status", **WORKING, "microphone": "denied"})
            bridge.status = {**WORKING, "microphone": "denied"}
            step(app)
            self.assertIn("Microphone", app.problem.get())
            app.close()
        self.assertEqual(bridge.listeners, [])
        self.assertFalse(top.winfo_exists())

    def test_what_the_bridge_says_in_plain_words(self):
        from multitool.settings import flow_text, level_fraction
        self.assertEqual(flow_text({})[0], "Starting…")
        self.assertIn("install it above", flow_text({**WORKING, "device": False})[0])
        self.assertIn("isn't connected", flow_text({**WORKING, "output_missing": True})[0])
        self.assertEqual((level_fraction(0), level_fraction(1.0), round(level_fraction(0.001), 2)), (0.0, 1.0, 0.0))


class HomeTests(unittest.TestCase):
    def test_the_knob_opens_settings(self):
        from multitool.app import MultitoolApp
        root, top = window()
        self.addCleanup(root.destroy)
        with patch.object(MultitoolApp, "resume_audio"):
            app = MultitoolApp(top)
            top.after_cancel(app.resume_id)
            with patch.object(app, "open_tool") as opened:
                dial, label = app.knob.winfo_children()
                for part in (app.knob, label):  # a hidden canvas never gets pretend clicks, so the dial is checked below
                    part.event_generate("<Button-1>")
                root.update()
        self.assertTrue(dial.bind("<Button-1>"))
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(opened.call_args[0][0].module, "multitool.settings")
        self.assertIn("Settings", [w.cget("text") for w in app.knob.winfo_children() if isinstance(w, ttk.Label)])


class VarispeedDawTests(unittest.TestCase):
    def test_your_daw_follows_the_speed_and_records_through_the_bridge(self):
        from multitool.varispeed import core, ui
        root, top = window()
        self.addCleanup(root.destroy)
        takes = BASE / "daw-takes"
        settings = {**core.settings(), "speed": 1.0, "source": "daw", "recordings_folder": str(takes)}
        bridge = FakeBridge(running=True, status=dict(WORKING))
        with patch.object(ui, "update_config"), patch.object(core, "settings", return_value=settings), \
                patch.object(ui, "device_installed", return_value=True):
            app = ui.VarispeedApp(top, stream=Mock(), devices=lambda: ([("Speakers", 2)], "Speakers"), bridge=bridge)
            self.assertFalse(app.beat_section.winfo_ismapped() or app.beat_section.winfo_manager())
            app.set_target(0.75)
            self.assertEqual(bridge.speeds[-1], 0.75)  # the DAW's speed
            self.assertEqual(app.percent.text.get(), core.percent_text(0.9))  # where the device's clock is right now
            step(app)
            self.assertIn("Scarlett 4i4", app.daw_state.get())
            app.toggle_recording()
            self.assertEqual(bridge.records[0].parent, takes)
            self.assertTrue(bridge.records[0].name.startswith("Varispeed "))
            self.assertTrue(top.title().startswith("● Varispeed"))
            bridge.status = {**WORKING, "recording": True, "recorded": 65}
            step(app)
            self.assertEqual(app.record_state.get(), "1:05")
            app.toggle_recording()
            self.assertEqual(bridge.stops, 1)
            self.assertTrue(app.saving_take)
            take = takes / "Varispeed 2026-11-01 12.00.00.wav"
            soundfile.write(take, [[0.0, 0.0]] * 4800, 48000, subtype="FLOAT")
            bridge.tell({"type": "recorded", "path": str(take), "seconds": 0.1})
            settle(root, lambda: not app.saving_take)
            self.assertIn("Saved the take", app.status.get())
            app.source.set("files")
            app.source_changed()
            self.assertEqual(bridge.speeds[-1], 1.0)  # leaving Your DAW puts the DAW back to 100%
            self.assertTrue(app.beat_section.winfo_manager())
            app.close()
        self.assertEqual(bridge.listeners, [])

    def test_without_the_device_it_points_to_settings(self):
        from multitool.varispeed import core, ui
        root, top = window()
        self.addCleanup(root.destroy)
        settings = {**core.settings(), "source": "daw", "recordings_folder": str(BASE / "unused")}
        bridge = FakeBridge()
        with patch.object(ui, "update_config"), patch.object(core, "settings", return_value=settings), \
                patch.object(ui, "device_installed", return_value=False):
            app = ui.VarispeedApp(top, stream=Mock(), devices=lambda: ([], ""), bridge=bridge)
            step(app)
            self.assertEqual(app.daw_button.cget("text"), "Open Settings")
            self.assertFalse(bridge.running)  # nothing starts until the device is installed
            app.toggle_recording()
            self.assertIn("pass-through", app.record_problem.get())
            app.close()


if __name__ == "__main__":
    unittest.main()
