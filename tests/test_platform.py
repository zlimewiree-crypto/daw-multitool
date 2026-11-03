"""OS boundaries and Windows startup; no real accounts, projects or audio devices."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import app_paths
from multitool import platform
from multitool.windows_credentials import WindowsVault


class PlatformTests(unittest.TestCase):
    def test_lock_contention_and_release_even_when_holder_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lock"
            with path.open("a") as first, path.open("a") as second:
                platform.lock_file(first)
                with self.assertRaises(BlockingIOError):
                    platform.lock_file(second, blocking=False)
                platform.unlock_file(first)
                platform.lock_file(second, blocking=False)
            with path.open("a") as third:
                platform.lock_file(third, blocking=False)
                platform.unlock_file(third)

    def test_windows_defaults_and_personal_override(self):
        with patch.object(platform, "IS_WINDOWS", True):
            defaults = app_paths.default_config()
            self.assertIn("%WINDIR%", defaults["auto_poster"]["overlay_fonts"][0])
            with tempfile.TemporaryDirectory() as folder:
                config = Path(folder) / "config.json"
                config.write_text(json.dumps({"auto_poster": {"overlay_fonts": ["my-font.ttf"]}}))
                self.assertEqual(app_paths.load_section("auto_poster", config)["overlay_fonts"], ["my-font.ttf"])
        with patch.object(platform, "IS_WINDOWS", False):
            self.assertEqual(app_paths.default_config(), json.loads(
                (app_paths.RESOURCE_DIR / "config.example.json").read_text(encoding="utf-8")))

    def test_windows_data_directory_uses_local_appdata(self):
        with patch.object(platform, "IS_WINDOWS", True), patch.dict(os.environ, {"LOCALAPPDATA": "user-data"}):
            self.assertEqual(platform.user_data_dir(), Path("user-data") / "DAW Multitool")

    def test_open_routes_and_arguments_are_not_shell_commands(self):
        path = Path("project & song.flp").resolve()
        with patch.object(platform, "IS_MAC", True), patch.object(platform.subprocess, "run") as run:
            platform.open_path(path, "FL Studio")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-a", "FL Studio", str(path)])
        with patch.object(platform, "IS_MAC", False), patch.object(platform, "IS_WINDOWS", True), \
                patch.object(os, "startfile", create=True) as start:
            platform.open_path(path, "FL Studio")
            start.assert_called_once_with(str(path))
            with self.assertRaises(FileNotFoundError):
                platform.open_path(path, "missing-daw.exe")

    def test_windows_helpers_cannot_launch_mac_install_or_capture(self):
        from multitool.audio import bridge, device
        from multitool.auto_poster import project, schedule
        with patch.object(platform, "IS_MAC", False), patch.object(subprocess, "run") as run:
            self.assertIsNone(bridge.helper_path())
            self.assertIsNone(device.bundled_driver())
            self.assertFalse(bridge.resume(options={"pass_through": True}))
            for action in (device.install, device.remove, project.check_permissions, schedule.remove_agent):
                with self.assertRaisesRegex(OSError, "Windows support is pending"):
                    action()
            with self.assertRaises(OSError):
                schedule.install_agent({})
            run.assert_not_called()

    def test_windows_multichannel_playback_maps_only_the_selected_pair(self):
        import numpy as np
        import sounddevice
        from multitool.varispeed.player import default_stream
        def stereo(out, frames, time, status):
            out[:, 0], out[:, 1] = 0.25, 0.75
        with patch.object(platform, "IS_MAC", False), patch.object(sounddevice, "OutputStream") as stream, \
                patch.object(sounddevice, "CoreAudioSettings") as mac_settings:
            default_stream(48000, stereo, channel_map=[-1, -1, 0, 1, -1, -1])
            options = stream.call_args.kwargs
            out = np.ones((4, 6))
            options["callback"](out, 4, None, None)
            np.testing.assert_allclose(out, [[0, 0, .25, .75, 0, 0]] * 4)
            self.assertEqual(options["channels"], 6)
            mac_settings.assert_not_called()

    def test_file_preview_closes_output_after_the_track_ends(self):
        import numpy as np
        from multitool.varispeed import player
        stream = Mock()
        with patch.object(player.core, "decode", return_value=(np.zeros((100, 2)), 48000)), \
                patch.object(player, "default_stream", return_value=stream):
            preview = player.FilePreview("fake.wav")
            self.assertIsNone(preview.poll())
            preview.player.render(200)
            self.assertEqual(preview.poll(), 0)
            preview.terminate()
            stream.close.assert_called_once()

    @unittest.skipUnless(platform.IS_WINDOWS, "Windows UI smoke test")
    def test_all_tool_windows_open_on_windows_with_isolated_settings(self):
        code = '''
import time
import tkinter as tk
from unittest.mock import Mock, patch
import app_paths
from multitool.app import MultitoolApp, SETTINGS
from multitool.tools import TOOLS
from multitool import platform
app_paths.initialize_data()
folder = str(app_paths.DATA_DIR)
app_paths.update_config("bounce_sorter", {"source_folder": folder, "output_folder": folder})
app_paths.update_config("auto_poster", {"project_folders": [], "output_folder": folder, "autopilot_enabled": False})
app_paths.update_config("project_library", {"export_folders": [], "extra_project_folders": []})
errors = []
root = tk.Tk()
root.withdraw()
root.report_callback_exception = lambda *error: errors.append(str(error))
with patch.object(platform, "credential_backend", return_value=Mock(get_password=Mock(return_value=None))), \
     patch("multitool.varispeed.ui.output_devices", return_value=([("Test output", 2)], "Test output")), \
     patch("multitool.app.messagebox.showerror", side_effect=AssertionError):
    host = MultitoolApp(root)
    for tool in (*TOOLS, SETTINGS):
        host.open_tool(tool)
    assert len(host.windows) == 5, host.status.get()
    assert host.windows["auto_poster"][1].agent_button.instate(["disabled"])
    assert host.windows["varispeed"][1].source.get() == "files"
    deadline = time.monotonic() + 10
    while any(getattr(controller, "busy", False) for _, controller in host.windows.values()):
        root.update()
        assert time.monotonic() < deadline, "Tool did not finish startup"
        time.sleep(.02)
    root.update()
    assert not errors, errors
    host.close()
print("Home, Organizer, Auto Poster, Library, Varispeed and Settings opened and closed.")
'''
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True,
                                    env={**os.environ, "DAW_MULTITOOL_DATA_DIR": folder}, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class FakeVault:
    def __init__(self):
        self.values = {}
        self.reject_manifest = False

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        if len(value.encode("utf-16-le")) > 2560:
            raise ValueError("Credential too large")
        if self.reject_manifest and service == "test-service":
            raise OSError("Vault write failed")
        self.values[service, username] = value

    def delete_password(self, service, username):
        del self.values[service, username]


class WindowsCredentialTests(unittest.TestCase):
    def test_large_logins_round_trip_rotate_and_delete(self):
        backend = FakeVault()
        vault = WindowsVault(backend)
        value = json.dumps({"token": "sample-token-" * 1500, "name": "producer 🎵"})
        vault.set_password("test-service", "accounts", value)
        self.assertEqual(vault.get_password("test-service", "accounts"), value)
        old_chunks = set(backend.values) - {("test-service", "accounts")}
        vault.set_password("test-service", "accounts", "replacement")
        self.assertEqual(vault.get_password("test-service", "accounts"), "replacement")
        self.assertFalse(old_chunks & set(backend.values))
        vault.delete_password("test-service", "accounts")
        self.assertEqual(backend.values, {})

    def test_failed_rotation_keeps_previous_login_and_removes_staged_chunks(self):
        backend = FakeVault()
        vault = WindowsVault(backend)
        vault.set_password("test-service", "accounts", "previous login")
        before = dict(backend.values)
        backend.reject_manifest = True
        with self.assertRaises(OSError):
            vault.set_password("test-service", "accounts", "new login" * 900)
        self.assertEqual(backend.values, before)
        self.assertEqual(vault.get_password("test-service", "accounts"), "previous login")
