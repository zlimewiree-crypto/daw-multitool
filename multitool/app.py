"""Home screen and lifecycle for independently registered studio tools."""
import argparse
import importlib
import json
import logging
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from app_paths import DATA_DIR, FROZEN, initialize_data
from multitool import __version__
from multitool.audio import bridge as audio_bridge
from multitool.scrolling import scroll_area
from multitool.tools import TOOLS, Tool
from multitool.theme import HEADER, PANEL, RAISED, apply_theme, banner, hover_card, icon, on_hover

SETTINGS = Tool("settings", "Settings", "DAW Multitool's own audio device and where its sound goes.", "multitool.settings")


class MultitoolApp:
    def __init__(self, root):
        self.root = root
        self.windows = {}
        apply_theme(root)
        root.title("DAW Multitool")
        root.geometry("920x600")
        root.minsize(740, 480)
        root.protocol("WM_DELETE_WINDOW", self.close)
        sidebar = ttk.Frame(root, padding=(14, 20), style="Sidebar.TFrame")
        sidebar.pack(side="left", fill="y")
        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.pack(fill="x", padx=6, pady=(0, 26))
        icon(brand, "logo", size=30).pack(side="left", padx=(0, 10))
        ttk.Label(brand, text="DAW Multitool", style="Brand.TLabel").pack(side="left")
        ttk.Label(sidebar, text="TOOLS", style="SidebarEyebrow.TLabel").pack(anchor="w", padx=8, pady=(0, 6))
        for tool in TOOLS:
            self.nav_item(sidebar, tool)
        ttk.Label(sidebar, text=f"v{__version__}", style="SidebarMuted.TLabel").pack(side="bottom", anchor="w", padx=8)
        self.status = tk.StringVar(value="")
        main = ttk.Frame(root)
        ttk.Frame(root, width=1, style="Rule.TFrame").pack(side="left", fill="y")  # a hairline beside the sidebar
        main.pack(side="left", fill="both", expand=True)
        ttk.Label(main, textvariable=self.status, wraplength=500, padding=(28, 4, 28, 12), style="Muted.TLabel").pack(side="bottom", anchor="w")
        bar = banner(main, "Home", "Sort your bounces, post your beats, change their speed and find any project.")
        self.settings_knob(bar)
        content = scroll_area(main, padding=(24, 20, 24, 8))  # more tools than fit the window scroll with the wheel or trackpad
        for tool in TOOLS:
            card = ttk.Frame(content, style="Card.TFrame", padding=(4, 2), cursor="pointinghand")
            card.pack(fill="x", pady=(0, 10))
            card.columnconfigure(1, weight=1)
            tile = icon(card, tool.id, size=40)
            tile.grid(row=0, column=0, rowspan=2, padx=(4, 0), pady=6)
            name = ttk.Label(card, text=tool.name, style="CardHeading.TLabel")
            name.grid(row=0, column=1, sticky="sw", padx=(14, 0), pady=(6, 0))
            about = ttk.Label(card, text=tool.description, wraplength=380, justify="left", style="CardMuted.TLabel")
            about.grid(row=1, column=1, sticky="nw", padx=(14, 0), pady=(2, 6))
            ttk.Button(card, text="Open  →", style="Card.Quiet.TButton",
                       command=lambda t=tool: self.open_tool(t)).grid(row=0, column=2, rowspan=2, padx=(12, 2))
            card.bind("<Configure>", lambda event, text=about: text.configure(wraplength=max(220, event.width - 230)), add="+")
            for part in (card, tile, name, about):  # the whole card opens the tool
                part.bind("<Button-1>", lambda event, t=tool: self.open_tool(t))
            hover_card(card)
        try:  # DAW Multitool ▸ Settings… (⌘,) in the menu bar
            root.tk.createcommand("tk::mac::ShowPreferences", self.open_settings)
        except tk.TclError:
            pass
        root.bind_all("<<OpenSettings>>", lambda event: self.open_settings())  # tool windows ask for it this way
        self.resume_id = root.after(400, self.resume_audio)

    def settings_knob(self, bar):
        """The knob at the top right of the home screen: opens Settings."""
        knob = ttk.Frame(bar, style="Header.TFrame", cursor="pointinghand", padding=(16, 0, 0, 0))
        knob.pack(side="right", before=bar.pack_slaves()[0])  # its room first, so the long subtitle wraps instead
        dial = icon(knob, "settings", size=34, background=HEADER)
        dial.pack(side="left", padx=(0, 8))
        label = ttk.Label(knob, text="Settings", style="HeaderLink.TLabel")
        label.pack(side="left")
        for part in (knob, dial, label):
            part.bind("<Button-1>", lambda event: self.open_settings())
        on_hover(knob, lambda: label.state(["active"]), lambda: label.state(["!active"]))
        self.knob = knob

    def open_settings(self):
        self.open_tool(SETTINGS)

    def resume_audio(self):
        """Pass-through comes back on if it was on when the app last closed."""
        try:
            if audio_bridge.resume():
                self.status.set("Pass-through is on: your DAW's sound on DAW Multitool plays through.")
        except Exception:
            logging.exception("Could not resume pass-through")

    def nav_item(self, sidebar, tool):
        """A sidebar link: the tool's icon and name on a rounded highlight under the pointer."""
        row = ttk.Frame(sidebar, style="NavRow.TFrame", padding=(8, 6), cursor="pointinghand")
        row.pack(fill="x", pady=1)
        tile = icon(row, tool.id, size=26)
        tile.pack(side="left", padx=(0, 10))
        label = ttk.Label(row, text=tool.name, style="SidebarNav.TLabel")
        label.pack(side="left")
        for part in (row, tile, label):
            part.bind("<Button-1>", lambda event: self.open_tool(tool))

        def light(on):
            for part in (row, label):
                part.state(["active" if on else "!active"])
            tile.configure(background=RAISED if on else PANEL)
        on_hover(row, lambda: light(True), lambda: light(False))

    def open_tool(self, tool):
        if tool.id in self.windows:
            window, _ = self.windows[tool.id]
            window.deiconify()
            window.lift()
            window.focus_set()
            return
        window = tk.Toplevel(self.root)
        window.withdraw()
        try:
            controller = importlib.import_module(tool.module).open_tool(window)
        except Exception as error:
            logging.exception("Could not open %s", tool.id)
            window.destroy()
            self.status.set(f"Couldn't open {tool.name}. Other tools are still available.")
            messagebox.showerror(tool.name, f"Couldn't open this tool:\n{error}", parent=self.root)
            return
        self.windows[tool.id] = (window, controller)
        window.protocol("WM_DELETE_WINDOW", lambda: self.close_tool(tool.id))
        window.bind("<Destroy>", lambda event: self.forget_tool(tool.id, window, event), add="+")
        window.deiconify()
        self.status.set(f"{tool.name} is open.")

    def forget_tool(self, tool_id, window, event):
        if event.widget is window:
            self.windows.pop(tool_id, None)

    def close_tool(self, tool_id):
        window, controller = self.windows[tool_id]
        if getattr(controller, "busy", False):
            controller.cancel.set()
            controller.status.set("Stopping… wait for this task to finish, then close the window.")
            window.lift()
            return False
        controller.close()
        return True

    def close(self):
        for tool_id in list(self.windows):
            if not self.close_tool(tool_id):
                return
        audio_bridge.stop_shared()  # finishes any recording and puts the DAW back to 100%
        if getattr(self, "resume_id", None):
            self.root.after_cancel(self.resume_id)
        self.root.destroy()


def self_check():
    """Exercise packaged lazy imports without opening a window or reading audio."""
    for tool in TOOLS:
        adapter = importlib.import_module(tool.module)
        if not callable(adapter.open_tool):
            raise ValueError(f"Tool {tool.id} has no open_tool function")
        check = getattr(adapter, "self_check", None)
        if check:
            check()
    check_audio()
    print(json.dumps({"status": "ok", "version": __version__, "bundled": FROZEN,
                      "tools": [t.id for t in TOOLS], "data_directory": str(DATA_DIR)}))


def check_audio():
    """The audio device and bridge are bundled, and the bridge's resampler works (no audio device is opened)."""
    from multitool.audio import device
    helper = audio_bridge.helper_path(build_it=False)
    if FROZEN and (helper is None or device.bundled_driver(build_it=False) is None):
        raise RuntimeError("The audio device or the audio bridge is missing from the app")
    if helper:
        result = subprocess.run([str(helper), "--self-check"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=60)
        if result.stdout.strip() != "ok":
            raise RuntimeError(f"The audio bridge's self-check failed: {result.stdout.strip() or result.returncode}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="DAW Multitool — studio tools in one application")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list tools; open no windows and change no files")
    mode.add_argument("--list-tools", action="store_true", help="list registered tools")
    mode.add_argument("--self-check", action="store_true", help="check bundled dependencies without opening a window")
    mode.add_argument("--run-schedule", action="store_true", help="post scheduled Auto Poster videos that are due, then exit")
    args = parser.parse_args(argv)
    if args.dry_run or args.list_tools:
        for tool in TOOLS:
            print(f"{tool.id}: {tool.name} — {tool.description}")
        return 0
    if args.self_check:
        self_check()
        return 0
    if args.run_schedule:
        from multitool.auto_poster.schedule import run_from_helper
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=DATA_DIR / "app.log", level=logging.INFO)
        return run_from_helper()
    root = tk.Tk()
    try:
        initialize_data()
        logging.basicConfig(filename=DATA_DIR / "app.log", level=logging.INFO)
        if FROZEN:  # from source, macOS would file the prompts under Terminal or VS Code instead
            from multitool import permissions
            root.withdraw()
            apply_theme(root)
            if permissions.check_at_start(root) == "reopen":
                permissions.reopen_app()
                root.destroy()
                return 0
            root.deiconify()
        MultitoolApp(root)
    except Exception as error:
        messagebox.showerror("DAW Multitool", f"Couldn't start the application:\n{error}", parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0
