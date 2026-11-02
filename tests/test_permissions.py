"""The permission check before the home screen, with stand-in permissions: macOS is never asked,
no window is shown and the real settings file isn't written."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from multitool import permissions
from multitool.permissions import Permission, PermissionWindow


def buttons(widget):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            yield child
        yield from buttons(child)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.state = {"Screen Recording": False, "Accessibility": False, "Downloads folder": True}
        self.asked = []
        self.list = [Permission(name, "why", self.answer(name), name.replace(" ", ""), reopen=name == "Screen Recording")
                     for name in self.state]

    def answer(self, name):
        def allowed():
            self.asked.append(name)
            return self.state[name]
        return allowed

    def test_every_permission_is_asked_once_and_only_missing_ones_listed(self):
        broken = Permission("Odd", "why", Mock(side_effect=OSError("changed API")), "Odd")
        still = permissions.missing(self.list + [broken])
        self.assertEqual(self.asked, ["Screen Recording", "Accessibility", "Downloads folder"])
        self.assertEqual([p.name for p in still], ["Screen Recording", "Accessibility", "Odd"])  # an error never stops the app

    def test_window_opens_the_right_settings_page_and_check_again_updates_it(self):
        opened = []
        window = PermissionWindow(self.root, self.list, permissions.missing(self.list), opener=opened.append)
        window.window.withdraw()
        self.addCleanup(lambda: window.window.winfo_exists() and window.window.destroy())
        next(b for b in buttons(window.rows) if b.cget("text") == "Open Settings ↗").invoke()
        self.assertEqual(opened, [permissions.SETTINGS_PAGE + "ScreenRecording"])
        self.state.update({"Screen Recording": True, "Accessibility": True})
        next(b for b in buttons(window.window) if b.cget("text") == "Check again").invoke()
        self.assertEqual(window.still, [])
        with patch.object(permissions, "update_config") as saved:
            next(b for b in buttons(window.window) if b.cget("text") == "Start DAW Multitool").invoke()
        saved.assert_not_called()  # "Don't show this at start" wasn't ticked
        self.assertFalse(window.window.winfo_exists())

    def test_dont_show_again_is_remembered_and_then_nothing_is_asked(self):
        window = PermissionWindow(self.root, self.list, permissions.missing(self.list))
        window.window.withdraw()
        window.never.set(True)
        with patch.object(permissions, "update_config") as saved:
            window.start()
        saved.assert_called_once_with("app", {"permissions_at_start": False})
        self.asked.clear()
        with patch.object(permissions, "wanted", return_value=False):
            self.assertEqual(permissions.check_at_start(self.root, self.list), "")
        self.assertEqual(self.asked, [])
        self.assertFalse(permissions.wanted({"app": {"permissions_at_start": False}}))
        self.assertTrue(permissions.wanted({}))

    def test_nothing_shows_when_everything_is_allowed(self):
        self.state.update({"Screen Recording": True, "Accessibility": True})
        with patch.object(permissions, "wanted", return_value=True), \
             patch.object(permissions, "PermissionWindow") as window:
            self.assertEqual(permissions.check_at_start(self.root, self.list), "")
        window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
