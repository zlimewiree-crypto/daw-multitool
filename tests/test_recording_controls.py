"""Playback uses the app's permission, never AppleScript; no real keys, windows or projects touched."""
from pathlib import Path
import signal
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

from multitool.auto_poster import project
from multitool.auto_poster.storage import load_settings


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.services, self.foundation, self.graphics, self.proc = Mock(), Mock(), Mock(), Mock()
        self.services.AXIsProcessTrusted.return_value = True
        self.services.AXUIElementCreateSystemWide.return_value = 101
        self.foundation.CFStringCreateWithCString.return_value = 202
        def focused(system, attribute, output):
            output._obj.value = 303
            return 0
        def pid(application, output):
            output._obj.value = 404
            return 0
        def name(pid, buffer, size):
            buffer.value = b"Live"
            return len(buffer.value)
        self.services.AXUIElementCopyAttributeValue.side_effect = focused
        self.services.AXUIElementGetPid.side_effect = pid
        self.proc.proc_name.side_effect = name
        self.graphics.CGEventCreateKeyboardEvent.side_effect = [11, 12, 13, 14]
        def library(path):
            if "ApplicationServices" in path:
                return self.services
            if "CoreFoundation" in path:
                return self.foundation
            if "CoreGraphics" in path:
                return self.graphics
            return self.proc
        self.patcher = patch.object(project.ctypes, "CDLL", side_effect=library)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_home_then_space_use_native_events_to_the_focused_daw_only(self):
        with patch.object(project.subprocess, "run") as helper, patch.object(project.time, "sleep"):
            project.press_play("Live", [115])
        helper.assert_not_called()
        self.assertEqual(self.graphics.CGEventCreateKeyboardEvent.call_args_list,
                         [call(None, 115, True), call(None, 115, False), call(None, 49, True), call(None, 49, False)])
        self.assertEqual(self.graphics.CGEventPostToPid.call_args_list,
                         [call(404, 11), call(404, 12), call(404, 13), call(404, 14)])
        self.assertEqual(self.services.AXUIElementCopyAttributeValue.call_count, 2)
        for event in (11, 12, 13, 14):
            self.foundation.CFRelease.assert_any_call(event)

    def test_keyboard_way_checks_focus_then_types_the_keys(self):
        with patch.object(project.time, "sleep"):
            project.press_play("Live", [115], via="keyboard")
        self.assertEqual(self.graphics.CGEventPost.call_args_list, [call(0, 11), call(0, 12), call(0, 13), call(0, 14)])
        self.graphics.CGEventPostToPid.assert_not_called()
        self.assertEqual(self.services.AXUIElementCopyAttributeValue.call_count, 2)  # front app checked before each key

    def test_denied_access_or_other_front_app_sends_no_keys(self):
        for trusted, process, error in ((False, "Live", PermissionError), (True, "OsxFL", ValueError)):
            with self.subTest(trusted=trusted, process=process):
                self.services.AXIsProcessTrusted.return_value = trusted
                with self.assertRaises(error):
                    project.press_play(process)
                self.graphics.CGEventPostToPid.assert_not_called()

    def test_focus_change_after_home_never_sends_space(self):
        with patch.object(project, "_front_process", side_effect=[404, ValueError("not in front")]), \
             patch.object(project.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "not in front"):
                project.press_play("Live", [115])
        self.assertEqual(self.graphics.CGEventPostToPid.call_args_list, [call(404, 11), call(404, 12)])

    def test_unreadable_focus_and_failed_event_creation_fail_closed(self):
        self.services.AXUIElementCopyAttributeValue.side_effect = None
        self.services.AXUIElementCopyAttributeValue.return_value = -25211
        with self.assertRaisesRegex(PermissionError, "focused app"):
            project.press_play("Live")
        with patch.object(project, "_front_process", return_value=404):
            self.graphics.CGEventCreateKeyboardEvent.side_effect = [11, None]
            with self.assertRaisesRegex(PermissionError, "keypress"):
                project.press_play("Live")
        self.graphics.CGEventPostToPid.assert_not_called()
        self.foundation.CFRelease.assert_any_call(11)


class PlayFallbackTests(unittest.TestCase):
    def run_record(self, takes, settings=None):
        settings = {**load_settings(), "play_ways": {}, **(settings or {})}
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "beat.flp"
        path.write_bytes(b"fake project")
        with patch.object(project, "check_permissions"), patch.object(project, "_record", side_effect=takes) as record, \
             patch.object(project, "update_config") as saved:
            result = project.record_project(path, 5, Path(folder.name), settings)
        return result, [c.args[-1] for c in record.call_args_list], saved, settings

    def test_ignored_play_is_tried_the_other_way_and_remembered(self):
        result, ways, saved, settings = self.run_record([project.NotPlaying("frozen"), ("take.mov", 0.4)])
        self.assertEqual((result, ways), (("take.mov", 0.4), ["app", "keyboard"]))
        saved.assert_called_once_with("auto_poster", {"play_ways": {"OsxFL": "keyboard"}})
        _, ways, saved, _ = self.run_record([("take.mov", 0.4)], settings)
        self.assertEqual(ways, ["keyboard"])  # next time it goes straight to the way that worked
        saved.assert_not_called()


class RecordingLimitTests(unittest.TestCase):
    press = None

    def record(self, cancelled=lambda: False, warn=lambda text: None, **changes):
        self.capture = Mock(returncode=0)
        self.capture.poll.return_value = None
        self.capture.wait.side_effect = [subprocess.TimeoutExpired("fake recorder", 1), 0]
        self.reports = []
        settings = {**load_settings(), "recording_lead_seconds": 0, "recording_finish_seconds": 0.01}
        settings.update(changes)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name) / "fake.als"
        path.write_bytes(b"fake project")
        with patch.object(project, "check_permissions"), patch.object(project, "daw_windows", return_value=[]), \
             patch.object(project, "_open"), patch.object(project, "wait_for_project", return_value=(0, 0, 640, 480)), \
             patch.object(project, "press_play", side_effect=self.press or (lambda *args, **kw: project.time.monotonic())) as play, \
             patch.object(project.subprocess, "Popen", return_value=self.capture):
            self.play = play
            return project.record_project(path, 0.01, Path(self.folder.name), settings, cancelled, self.reports.append, warn)

    def test_stuck_recorder_is_interrupted_then_killed_and_playback_stopped(self):
        with self.assertRaisesRegex(ValueError, "didn't finish on time"):
            self.record()
        self.capture.send_signal.assert_called_once_with(signal.SIGINT)
        self.capture.kill.assert_called_once()
        self.assertEqual(self.capture.wait.call_count, 2)
        self.assertEqual(self.play.call_count, 2)  # start and stop

    def test_cancel_interrupts_capture_and_stops_playback(self):
        with self.assertRaises(project.Cancelled):
            self.record(cancelled=lambda: True)
        self.capture.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(self.play.call_count, 2)

    def test_a_failed_stop_says_the_daw_may_still_be_playing(self):
        presses, warnings = iter([project.time.monotonic, Mock(side_effect=ValueError("not in front"))]), []
        self.press = lambda *args, **kw: next(presses)()
        with self.assertRaises(project.Cancelled):
            self.record(cancelled=lambda: True, warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("may still be playing", warnings[0])

    def test_permissions_no_longer_request_system_events(self):
        from multitool.permissions import PERMISSIONS
        self.assertFalse(any("Automation" in permission.name for permission in PERMISSIONS))


if __name__ == "__main__":
    unittest.main()
