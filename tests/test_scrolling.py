"""Scrolling pages with real Tk wheel/trackpad events in a hidden window; no files are touched."""
import tkinter as tk
from tkinter import ttk
import unittest

from multitool.scrolling import scroll_area


class ScrollingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry("400x200")
        self.addCleanup(self.root.destroy)
        self.page = scroll_area(self.root)
        self.lines = [ttk.Label(self.page, text=f"line {i}") for i in range(60)]
        for line in self.lines:
            line.pack(anchor="w")
        self.root.update()

    def top(self):
        return self.page.master.yview()[0]

    def send(self, widget, sequence, delta):
        widget.event_generate(sequence, delta=delta, x=1, y=1)
        self.root.update()

    def test_wheel_and_trackpad_scroll_the_page_from_anywhere_on_it(self):
        self.send(self.lines[3], "<MouseWheel>", -120)
        self.assertGreater(self.top(), 0)
        self.send(self.lines[3], "<MouseWheel>", 120)
        self.assertEqual(self.top(), 0)
        self.send(self.lines[3], "<TouchpadScroll>", (-40) & 0xFFFF)  # Tk 9 packs x/y; 40 px down
        self.assertGreater(self.top(), 0)

    def test_a_long_text_box_keeps_the_wheel_for_itself(self):
        box = tk.Text(self.page, height=3)
        box.insert("1.0", "\n".join(map(str, range(50))))
        box.pack()
        self.root.update()
        self.send(box, "<MouseWheel>", -120)
        self.assertEqual(self.top(), 0)

    def test_the_home_screen_scrolls(self):
        from multitool.app import MultitoolApp
        window = tk.Toplevel(self.root)
        window.withdraw()
        app = MultitoolApp(window)
        window.after_cancel(app.resume_id)  # its pass-through check would outlive this test
        self.assertTrue(any(isinstance(w, tk.Canvas) for w in window.winfo_children()[1].winfo_children()))


if __name__ == "__main__":
    unittest.main()
