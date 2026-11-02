"""Scrolling pages for tool windows: a scrollbar, plus the mouse wheel and the Mac trackpad (a Tk canvas has neither)."""
import tkinter as tk
from tkinter import ttk

SELF_SCROLLING = (tk.Text, tk.Listbox, ttk.Treeview)  # boxes that scroll their own contents


def scroll_area(parent, padding=0):
    """Packs a scrolling area into `parent` and returns the frame to put the page's widgets in.
    The wheel/trackpad scroll it whenever the pointer is over it, except over a list or text box
    that has its own scrolling to do."""
    canvas = tk.Canvas(parent, highlightthickness=0, yscrollincrement=1)  # 1 unit = 1 pixel: smooth trackpad scrolling
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)

    def moved(first, last):
        scrollbar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            scrollbar.pack_forget()  # nothing to scroll: no scrollbar, like macOS
        elif not scrollbar.winfo_ismapped():
            scrollbar.pack(side="right", fill="y", before=canvas)

    canvas.configure(yscrollcommand=moved)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    content = ttk.Frame(canvas, padding=padding)
    item = canvas.create_window((0, 0), window=content, anchor="nw")
    content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))

    def scrolls(event, pixels):
        widget = event.widget
        if isinstance(widget, str):  # a Tk-internal widget tkinter doesn't know by name
            return
        if isinstance(widget, SELF_SCROLLING) and widget.yview() != (0.0, 1.0):
            return  # let that box scroll itself
        while widget is not None and widget is not canvas:
            widget = widget.master
        if widget is canvas and canvas.yview() != (0.0, 1.0):  # only when the page is taller than the window
            canvas.yview_scroll(pixels, "units")

    def wheel(event):
        # Tk 9 reports 120 per wheel notch; it scrolls its own text boxes by delta / 4 pixels, so match that
        scrolls(event, int(-event.delta / 4) if abs(event.delta) >= 4 else -event.delta * 30)

    def trackpad(event):
        low = event.delta & 0xFFFF  # Tk packs the trackpad's sideways and up/down movement into one number
        scrolls(event, -(low if low < 0x8000 else low - 0x10000))

    # On the window, not app-wide: every widget inside passes its wheel events up to its window,
    # and these bindings go away when the window closes.
    window = parent.winfo_toplevel()
    window.bind("<MouseWheel>", wheel, add="+")
    try:
        window.bind("<TouchpadScroll>", trackpad, add="+")  # Tk 9's trackpad event; older Tk sends MouseWheel
    except tk.TclError:
        pass
    return content
