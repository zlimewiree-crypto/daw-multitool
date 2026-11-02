"""Accounts setup wizard: steps, links, copy buttons and keys filled in from what's copied or downloaded.
Runs in a hidden window with a stand-in clipboard and a temporary Downloads folder; nothing real is read or sent."""
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from multitool.auto_poster import setup_guide
from multitool.auto_poster.accounts import APIError, APP_KEYS, PLATFORMS
from multitool.auto_poster.storage import load_settings

SITES = {"youtube": ("console.cloud.google.com",), "instagram": ("developers.facebook.com", "help.instagram.com", "www.facebook.com"),
         "tiktok": ("developers.tiktok.com",)}


def widgets(parent, kind):
    for child in parent.winfo_children():
        if isinstance(child, kind):
            yield child
        yield from widgets(child, kind)


def button(window, text):
    return next(b for b in widgets(window, ttk.Button) if b.cget("text") == text)


class GuideTests(unittest.TestCase):
    def test_every_link_is_a_secure_page_on_that_platforms_own_site(self):
        settings = load_settings()
        for platform in PLATFORMS:
            links = [step.url for step in setup_guide.steps(platform, settings) if step.url]
            self.assertTrue(links, platform)
            for url in links:
                self.assertEqual(urlparse(url).scheme, "https", url)
                self.assertIn(urlparse(url).hostname, SITES[platform], url)
            boxes = [key for key, _, _ in setup_guide.FIELDS[platform]]
            self.assertEqual(boxes[:2], list(APP_KEYS[platform][:2]))  # the boxes save the keys sign-in reads

    def test_copied_keys_are_recognised_only_where_the_step_asks_for_them(self):
        r = setup_guide.recognise
        self.assertEqual(r("youtube", " 12-ab.apps.googleusercontent.com\n", "key"), ("client_id", "12-ab.apps.googleusercontent.com"))
        self.assertEqual(r("youtube", "GOCSPX-abcdefghijklmnopqrst", "final"), ("client_secret", "GOCSPX-abcdefghijklmnopqrst"))
        self.assertIsNone(r("youtube", "GOCSPX-abcdefghijklmnopqrst", "enable"))  # not a key step
        self.assertEqual(r("instagram", "1234567890123456", "config"), ("config_id", "1234567890123456"))
        self.assertEqual(r("instagram", "1234567890123456", "key"), ("app_id", "1234567890123456"))
        self.assertEqual(r("instagram", "0a" * 16, "key"), ("app_secret", "0a" * 16))
        self.assertIsNone(r("instagram", "1234567890123456", "final"))  # App ID or Configuration ID? paste it
        self.assertEqual(r("tiktok", "sbawabc123456789", "key"), ("client_key", "sbawabc123456789"))
        self.assertEqual(r("tiktok", "aw" + "B" * 30, "key"), ("client_secret", "aw" + "B" * 30))
        for junk in ("DAW Multitool", "http://127.0.0.1:8765/callback/tiktok", "video.publish, video.list", "", "x" * 300):
            for platform in PLATFORMS:
                self.assertIsNone(r(platform, junk, "key"), (platform, junk))

    def test_googles_downloaded_key_file_is_found_and_web_keys_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(setup_guide, "DOWNLOADS", Path(folder)):
            self.assertIsNone(setup_guide.google_json())
            desktop, web = Path(folder) / "client_secret_1-a.apps.googleusercontent.com.json", Path(folder) / "client_secret_2.json"
            desktop.write_text(json.dumps({"installed": {"client_id": "1-a.apps.googleusercontent.com", "client_secret": "GOCSPX-x"}}))
            web.write_text(json.dumps({"web": {"client_id": "2"}}))
            os.utime(desktop, (time.time() - 60, time.time() - 60))  # the web one is newer but can't be used
            keys, found = setup_guide.google_json()
            self.assertEqual((keys["client_id"], found), ("1-a.apps.googleusercontent.com", desktop))
            self.assertIsNone(setup_guide.google_json(newer_than=time.time()))  # an old download isn't picked up

    def test_pasted_keys_in_the_wrong_box_are_caught_before_connecting(self):
        problem = setup_guide.key_problem
        good = {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-fake"}
        self.assertEqual(problem("youtube", good), "")
        self.assertIn("Client secret box", problem("youtube", {"client_id": "x", "client_secret": good["client_id"]}))
        self.assertIn("ends in .apps.googleusercontent.com", problem("youtube", {**good, "client_id": "my-project-123"}))
        self.assertIn("space", problem("tiktok", {"client_key": "aw12 34", "client_secret": "s"}))
        self.assertIn("only numbers", problem("instagram", {"app_id": "abc", "app_secret": "f" * 32}))
        self.assertIn("App ID in the App secret box", problem("instagram", {"app_id": "123", "app_secret": "123"}))
        self.assertEqual(problem("instagram", {"app_id": "1234567890", "app_secret": "0a" * 16, "config_id": ""}), "")

    def test_common_setup_mistakes_become_a_message_and_the_page_that_fixes_them(self):
        settings = load_settings()
        off = APIError("Platform rejected the request (HTTP 403, accessNotConfigured). YouTube Data API v3 has not been "
                       "used in project 123456789 before or it is disabled.", code="accessNotConfigured")
        message, url = setup_guide.fix_for("youtube", off, settings)
        self.assertIn("step 2", message)
        self.assertEqual(url, "https://console.cloud.google.com/apis/library/youtube.googleapis.com?project=123456789")
        message, url = setup_guide.fix_for("youtube", APIError("declined", code="access_denied"), settings)
        self.assertIn("test user", message)
        self.assertEqual(url, setup_guide.AUDIENCE_PAGE)
        message, url = setup_guide.fix_for("tiktok", APIError("bad", code="invalid_client"), settings)
        self.assertIn(f"step {setup_guide.number('tiktok', settings, 'key')}", message)
        self.assertIsNone(setup_guide.fix_for("youtube", APIError("Connection interrupted."), settings))
        for platform in PLATFORMS:  # every step a message refers to exists
            self.assertTrue(setup_guide.waiting(platform, settings))
            self.assertGreater(setup_guide.number(platform, settings, "key"), 1)

    def test_tiktok_asks_only_for_posting(self):
        settings = load_settings()  # video.list needs the Display API, and private posts have no stats to read
        self.assertEqual(settings["tiktok_scopes"], ["video.publish"])
        products = next(step for step in setup_guide.steps("tiktok", settings) if step.id == "products")
        self.assertEqual(products.copy, "video.publish")
        self.assertNotIn("Display API", products.text)
        self.assertIn("“scope”", setup_guide.waiting("tiktok", settings))

    def test_youtube_reconnect_date_is_seven_days_after_connecting(self):
        from datetime import datetime
        now = time.time()
        self.assertEqual(setup_guide.reconnect_by({"connected_at": now}),
                         datetime.fromtimestamp(now + 7 * 86400).astimezone())
        self.assertIsNone(setup_guide.reconnect_by({"name": "older login"}))


class WizardTests(unittest.TestCase):
    """The real setup window, hidden. The clipboard is a stand-in so the user's own clipboard is never touched."""
    def setUp(self):
        from multitool.auto_poster.ui import AutoPosterApp
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.copied = [""]
        self.root.clipboard_get = lambda: self.copied[0]
        self.root.clipboard_clear, self.root.clipboard_append = Mock(), Mock()
        app = self.app = AutoPosterApp.__new__(AutoPosterApp)
        app.root, app.settings, app.setup_windows = self.root, {**load_settings(), "setup_steps": {}}, {}
        app.accounts = Mock(app_keys=Mock(return_value={}))
        app.setup_connect, app.remember_setup_step = Mock(), Mock()  # never writes the real settings file
        downloads = tempfile.TemporaryDirectory()
        self.addCleanup(downloads.cleanup)
        self.downloads = Path(downloads.name)
        patcher = patch.object(setup_guide, "DOWNLOADS", self.downloads)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open(self, platform):
        wizard = self.app.show_setup(platform)
        wizard.window.withdraw()
        self.addCleanup(wizard.close)
        return wizard

    def copy(self, wizard, text):
        self.copied[0] = text
        wizard.poll()

    def test_one_step_per_page_with_its_page_and_copy_buttons(self):
        for platform in PLATFORMS:
            wizard = self.open(platform)
            steps = setup_guide.steps(platform, self.app.settings)
            for number, step in enumerate(steps, start=1):
                self.assertEqual(wizard.progress.get(), f"Step {number} of {len(steps)}")
                if step.url:
                    with patch("multitool.auto_poster.setup_window.webbrowser.open") as browser:
                        button(wizard.window, "Open this page ↗").invoke()
                    browser.assert_called_once_with(step.url)
                copies = [text for _, _, text in step.fields if text] + ([step.copy] if step.copy else [])
                self.root.clipboard_append.reset_mock()
                for widget, text in zip([b for b in widgets(wizard.window, ttk.Button) if b.cget("text") == "Copy"], copies):
                    widget.invoke()
                    self.root.clipboard_append.assert_called_with(text)
                if number < len(steps):
                    wizard.forward.invoke()
            self.assertEqual(wizard.forward.cget("text"), f"Connect {setup_guide.NAMES[platform]}")
            wizard.forward.invoke()
            self.app.setup_connect.assert_called_with(platform, wizard.fields, wizard.note)
            wizard.back.invoke()
            self.assertEqual(wizard.progress.get(), f"Step {len(steps) - 1} of {len(steps)}")
            self.assertIs(self.app.show_setup(platform), wizard)  # a second click brings the same window back

    def test_copying_instagram_keys_fills_the_right_boxes_and_moves_on(self):
        self.copied[0] = "999999999999"  # already on the clipboard before setup: never used
        wizard = self.open("instagram")
        wizard.show(setup_guide.number("instagram", self.app.settings, "config") - 1)
        wizard.poll()
        self.assertEqual(wizard.fields["config_id"].get(), "")
        self.copy(wizard, "1111111111111111")
        self.assertEqual(wizard.fields["config_id"].get(), "1111111111111111")
        wizard.forward.invoke()  # App settings → Basic
        self.copy(wizard, "2222222222222222")
        self.copy(wizard, "0a" * 16)
        self.assertEqual((wizard.fields["app_id"].get(), wizard.fields["app_secret"].get()), ("2222222222222222", "0a" * 16))
        self.assertEqual(wizard.index, len(wizard.steps) - 1)  # every key in: straight to Connect
        self.assertIn("✓ App ID", wizard.status.get())

    def test_googles_download_fills_the_keys_with_no_pasting(self):
        wizard = self.open("youtube")
        file = self.downloads / "client_secret_9-z.apps.googleusercontent.com.json"
        file.write_text(json.dumps({"installed": {"client_id": "9-z.apps.googleusercontent.com", "client_secret": "GOCSPX-abc"}}))
        wizard.poll()  # step 1: Downloads isn't looked at yet
        self.assertEqual(wizard.fields["client_id"].get(), "")
        wizard.show(setup_guide.number("youtube", self.app.settings, "key") - 1)
        wizard.poll()
        self.assertEqual(wizard.fields["client_secret"].get(), "GOCSPX-abc")
        self.assertEqual(wizard.index, len(wizard.steps) - 1)
        self.assertIn("Connect YouTube", wizard.note.get())

    def test_reopens_on_the_step_the_user_left_off(self):
        self.app.settings["setup_steps"] = {"tiktok": 3}
        wizard = self.open("tiktok")
        self.assertEqual(wizard.progress.get(), f"Step 4 of {len(wizard.steps)}")
        wizard.forward.invoke()
        self.app.remember_setup_step.assert_called_with("tiktok", 4)

    def test_tiktok_icon_is_saved_to_downloads_at_the_size_tiktok_asks_for(self):
        wizard = self.open("tiktok")
        wizard.show(setup_guide.number("tiktok", self.app.settings, "details") - 1)
        with patch("multitool.auto_poster.setup_window.subprocess.run") as finder:
            button(wizard.window, "Save the icon to Downloads").invoke()
            button(wizard.window, "Save the icon to Downloads").invoke()  # a second click reuses the file
        icon = self.downloads / "DAW Multitool icon.png"
        self.assertEqual(icon.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        saved = tk.PhotoImage(master=self.root, file=str(icon))
        self.assertEqual((saved.width(), saved.height()), (1024, 1024))
        self.assertLess(icon.stat().st_size, 5_000_000)
        finder.assert_called_with(["open", "-R", str(icon)])
        self.assertEqual(finder.call_count, 2)

    def test_closing_stops_watching(self):
        wizard = self.open("tiktok")
        wizard.close()
        self.assertNotIn("tiktok", self.app.setup_windows)
        self.assertIsNone(wizard.poll_id)


if __name__ == "__main__":
    unittest.main()
