"""The one-time account setup as a small wizard: one step per page with its page one click away, floating
above the browser so the values stay readable, and keys filled in by themselves when copied (or, for Google,
when the downloaded JSON lands in Downloads). Steps and recognition rules live in setup_guide.py."""
import json
from pathlib import Path
import shutil
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, ttk
import webbrowser

from app_paths import RESOURCE_DIR
from multitool.scrolling import scroll_area
from multitool.theme import banner
from . import setup_guide
from .accounts import APP_KEYS
from .setup_guide import NAMES

POLL_MS = 700  # how often a newly copied key is looked for while the window is open
ICON_NAME = "DAW Multitool icon.png"
ICON_SOURCE = RESOURCE_DIR / "assets" / "AppIcon-square.png"  # the app's own icon, 1024 px as TikTok requires


class SetupWizard:
    def __init__(self, app, platform):
        self.app, self.platform = app, platform
        self.steps = setup_guide.steps(platform, app.settings)
        self.fields = {key: tk.StringVar() for key, _, _ in setup_guide.FIELDS[platform]}
        self.labels = {key: label for key, label, _ in setup_guide.FIELDS[platform]}
        self.required = APP_KEYS[platform][:2]
        self.note = tk.StringVar()
        self.status = tk.StringVar()
        self.opened, self.index, self.poll_id, self.found = time.time(), 0, None, None
        window = self.window = tk.Toplevel(app.root)
        window.title(f"Auto Poster · Set up {NAMES[platform]}")
        x = max(0, window.winfo_screenwidth() - 600)
        window.geometry(f"560x620+{x}+60")  # beside the browser, not on top of its form
        window.attributes("-topmost", True)  # stays readable while you fill in the platform's page
        window.protocol("WM_DELETE_WINDOW", self.close)
        banner(window, f"Set up {NAMES[platform]}", eyebrow="AUTO POSTER · ACCOUNT SETUP", kind="auto_poster")
        top = ttk.Frame(window, padding=(20, 12, 20, 0))
        top.pack(fill="x")
        self.progress = tk.StringVar()
        ttk.Label(top, textvariable=self.progress, style="Eyebrow.TLabel").pack(side="left")
        bottom = ttk.Frame(window, padding=(20, 8, 20, 16))
        bottom.pack(side="bottom", fill="x")
        self.back = ttk.Button(bottom, text="← Back", command=lambda: self.show(self.index - 1))
        self.back.pack(side="left")
        self.forward = ttk.Button(bottom, text="Next →", style="Primary.TButton", command=self.next)
        self.forward.pack(side="right")
        ttk.Label(window, textvariable=self.note, wraplength=520, padding=(20, 0)).pack(side="bottom", anchor="w")
        holder = ttk.Frame(window)
        holder.pack(fill="both", expand=True)
        self.page = scroll_area(holder, padding=(20, 8))
        for variable in self.fields.values():
            variable.trace_add("write", lambda *_: self.update_status())
        self.seen = self.clipboard()  # only keys copied from now on are picked up
        self.show(app.settings.get("setup_steps", {}).get(platform, 0))  # where the user left off
        self.poll()

    def step_id(self):
        return "final" if self.index == len(self.steps) - 1 else self.steps[self.index].id

    def show(self, index):
        self.index = max(0, min(index, len(self.steps) - 1))
        self.app.remember_setup_step(self.platform, self.index)
        for child in self.page.winfo_children():
            child.destroy()
        step, last = self.steps[self.index], self.index == len(self.steps) - 1
        self.progress.set(f"Step {self.index + 1} of {len(self.steps)}")
        if self.index == 0:
            ttk.Label(self.page, text=setup_guide.INTRO[self.platform], style="Muted.TLabel", wraplength=500,
                      justify="left").pack(anchor="w", pady=(0, 12))
        ttk.Label(self.page, text=step.text, style="Step.TLabel", wraplength=500, justify="left").pack(anchor="w")
        if step.fields:
            table = ttk.Frame(self.page)
            table.pack(fill="x", pady=(10, 0))
            for line, (asked, answer, copy) in enumerate(step.fields):
                ttk.Label(table, text=asked, style="Strong.TLabel", wraplength=190,
                          justify="left").grid(row=line, column=0, sticky="nw", padx=(0, 12), pady=3)
                ttk.Label(table, text=answer, wraplength=230, justify="left").grid(row=line, column=1, sticky="nw", pady=3)
                if copy:
                    ttk.Button(table, text="Copy", command=lambda text=copy: self.copy(text)).grid(row=line, column=2, sticky="nw", padx=6)
        if step.copy:
            line = ttk.Frame(self.page)
            line.pack(fill="x", pady=(10, 0))
            ttk.Entry(line, textvariable=tk.StringVar(value=step.copy), state="readonly").pack(side="left", fill="x", expand=True)
            ttk.Button(line, text="Copy", command=lambda text=step.copy: self.copy(text)).pack(side="left", padx=6)
        if step.icon:
            ttk.Button(self.page, text="Save the icon to Downloads", command=self.save_icon).pack(anchor="w", pady=(10, 0))
        if step.tip:
            ttk.Label(self.page, text=step.tip, style="Muted.TLabel", wraplength=500, justify="left").pack(anchor="w", pady=(10, 0))
        if step.url:
            ttk.Button(self.page, text="Open this page ↗", style="Big.TButton",
                       command=lambda url=step.url: webbrowser.open(url)).pack(anchor="w", pady=(14, 0))
        if self.step_id() in ("config", "key", "final"):
            ttk.Label(self.page, textvariable=self.status).pack(anchor="w", pady=(14, 0))
        if last:
            self.key_boxes()
        self.back.configure(state="disabled" if self.index == 0 else "normal")
        self.forward.configure(text=f"Connect {NAMES[self.platform]}" if last else "Next →")
        self.seen = self.clipboard()
        self.update_status()

    def key_boxes(self):
        boxes = ttk.LabelFrame(self.page, text="Your keys (filled in for you when you copy them)", padding=10)
        boxes.pack(fill="x", pady=(10, 0))
        for key, label, secret in setup_guide.FIELDS[self.platform]:
            row = ttk.Frame(boxes)
            row.pack(fill="x", pady=3)
            optional = "" if key in self.required else " (recommended)"
            ttk.Label(row, text=label + optional, width=24).pack(side="left")
            ttk.Entry(row, textvariable=self.fields[key], show="•" if secret else "").pack(side="left", fill="x", expand=True)
        if self.platform == "youtube":
            ttk.Button(boxes, text="Choose the downloaded JSON…", command=self.choose_json).pack(anchor="w", pady=(6, 0))
        if self.saved():
            ttk.Label(boxes, text="Keys already saved ✓: leave the boxes empty to use them.", style="Muted.TLabel",
                      wraplength=480).pack(anchor="w", pady=(6, 0))

    def saved(self):
        try:
            return self.app.accounts.app_keys(self.platform)
        except Exception:
            return {}

    def update_status(self):
        marks = [("✓ " if self.fields[key].get().strip() else "○ ") + self.labels[key] for key in self.fields]
        self.status.set("   ".join(marks))

    def complete(self):
        return all(self.fields[key].get().strip() for key in self.required)

    def next(self):
        if self.index == len(self.steps) - 1:
            self.app.setup_connect(self.platform, self.fields, self.note)
        else:
            self.show(self.index + 1)

    def copy(self, text):
        self.app.copy_text(text, self.note)
        self.seen = text  # our own Copy buttons never count as a key

    def clipboard(self):
        try:
            return self.app.root.clipboard_get()
        except tk.TclError:  # empty, or not text
            return ""

    def take(self, key, value, source):
        if self.fields[key].get().strip() == value:
            return
        self.fields[key].set(value)
        self.note.set(f"Got your {self.labels[key]} {source} ✓")
        if self.step_id() == "key" and self.complete():
            self.show(len(self.steps) - 1)  # every key is in: straight to Connect
            self.note.set(f"Got your keys ✓ Click Connect {NAMES[self.platform]}.")

    def poll(self):
        """Picks up a key the moment it's copied (and, for YouTube, the JSON once it's downloaded)."""
        if not self.window.winfo_exists():
            return
        text = self.clipboard()
        if text and text != self.seen:
            self.seen = text
            hit = setup_guide.recognise(self.platform, text, self.step_id())
            if hit:
                self.take(*hit, "from what you copied")
        if self.platform == "youtube" and self.step_id() in ("key", "final"):
            found = setup_guide.google_json(newer_than=self.opened - 2 * 86400)
            if found and found[1] != self.found:
                self.found = found[1]
                for key, value in found[0].items():
                    self.take(key, value, f"from {found[1].name}")
        if self.poll_id:  # called early (not by its own timer): never leave a second timer behind
            self.window.after_cancel(self.poll_id)
        self.poll_id = self.window.after(POLL_MS, self.poll)

    def save_icon(self):
        path = setup_guide.DOWNLOADS / ICON_NAME
        if not path.exists():  # a second click reuses the first file instead of overwriting anything
            shutil.copyfile(ICON_SOURCE, path)
        subprocess.run(["open", "-R", str(path)])  # shows it in Finder, ready to drag or pick
        self.note.set(f"Saved “{ICON_NAME}” in Downloads ✓ Upload it as the App icon.")

    def choose_json(self):
        path = filedialog.askopenfilename(parent=self.window, title="Choose the downloaded Google key (JSON)",
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            client = json.loads(Path(path).read_text())["installed"]  # Desktop app keys download as "installed"
            for key in ("client_id", "client_secret"):
                self.fields[key].set(client[key])
            self.note.set("Got your keys from the file ✓")
        except (OSError, ValueError, KeyError, TypeError):
            self.note.set(f"That isn't a Desktop app key. In step {setup_guide.number('youtube', self.app.settings, 'key')} "
                          "choose Application type Desktop app.")

    def close(self):
        if self.poll_id:
            self.window.after_cancel(self.poll_id)
            self.poll_id = None
        self.app.setup_windows.pop(self.platform, None)
        if self.window.winfo_exists():
            self.window.destroy()
