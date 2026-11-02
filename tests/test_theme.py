"""The shared theme's keyboard focus ring: it shows on the control the Tab key reaches, never after a mouse click."""
import tkinter as tk
from tkinter import ttk
import unittest

from multitool.theme import apply_theme


class FocusRingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        apply_theme(self.root)
        self.first, self.second = ttk.Button(self.root, text="One"), ttk.Button(self.root, text="Two")
        self.first.pack()
        self.second.pack()
        self.root.update()

    def test_tab_shows_the_ring_and_leaving_hides_it(self):
        self.root.tk.call("tk::TabToWindow", str(self.first))  # what the Tab key runs
        self.root.update()
        self.assertTrue(self.first.instate(["user1"]))
        self.root.tk.call("tk::TabToWindow", str(self.second))
        self.root.update()
        self.assertTrue(self.second.instate(["user1"]))
        self.first.event_generate("<FocusOut>")
        self.assertFalse(self.first.instate(["user1"]))

    def test_focus_without_the_keyboard_shows_no_ring(self):
        self.first.focus_force()
        self.root.update()
        self.assertFalse(self.first.instate(["user1"]))


if __name__ == "__main__":
    unittest.main()
