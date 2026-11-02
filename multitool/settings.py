"""Settings, opened from the knob on the home screen (or ⌘, and DAW Multitool ▸ Settings… in the menu bar).

Audio device: install, update or remove "DAW Multitool", DAW Multitool's own audio device (multitool/audio/device.py).
Pass-through: hear what your DAW plays into it on your speakers, headphones or interface (multitool/audio/bridge.py).
"""
import math
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from app_paths import DATA_DIR
from multitool import __version__
from multitool.audio import bridge as audio_bridge, device
from multitool.scrolling import scroll_area
from multitool.theme import ACCENT, BACKGROUND, BORDER, DANGER, MUTED, PANEL, apply_theme, banner, footer, icon

MICROPHONE_PAGE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
GREEN, AMBER, GREY = "#3ddc84", "#f5b942", "#4a4560"
DEFAULT_OUTPUT = "The Mac's sound output"


def open_tool(window):
    return SettingsWindow(window)


def level_fraction(level):
    """How full a level meter is for a peak level (0–1): −60 dB empty, 0 dB full."""
    return 0.0 if level <= 0 else min(1.0, max(0.0, (20 * math.log10(level) + 60) / 60))


def flow_text(status):
    """One line on what the bridge is doing, from its status."""
    if not status:
        return "Starting…", GREY
    if not status.get("device"):
        return "Waiting for the DAW Multitool device: install it above.", AMBER
    if not status.get("output"):
        return "No sound output to play on.", AMBER
    text = (f"On · {status['in_rate'] / 1000:g} kHz in → {status.get('output_name') or 'output'} at "
            f"{status['out_rate'] / 1000:g} kHz · latency {status.get('latency_ms', 0):.0f} ms · "
            f"dropouts {status.get('dropouts', 0)}")
    if status.get("output_missing"):
        text += " · your chosen device isn't connected, so it plays on the Mac's output"
    return text, GREEN


class SettingsWindow:
    def __init__(self, root, bridge=None, devices=None, runner=None):
        """`devices` lists the Mac's sound devices and `runner` stands in for macOS's password prompt (tests)."""
        apply_theme(root)
        self.root = root
        self.bridge = bridge or audio_bridge.shared()
        self.list_devices = devices or audio_bridge.list_devices
        self.runner = runner or subprocess.run
        self.options = audio_bridge.settings()
        self.events = queue.Queue()
        self.cancel = threading.Event()  # the home screen's close checks these, like a tool's
        self.busy = False  # installing or removing
        self.found = {"default_output": "", "devices": []}
        self.choices = []  # (label, uid, output channels) in the Play to list
        self.recheck_id = None
        self.listener = lambda event: self.events.put(("bridge", event))
        root.title("Settings · DAW Multitool")
        root.geometry("780x800")
        root.minsize(640, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="")
        banner(root, "Settings", "DAW Multitool's own audio device, and where its sound goes.", kind="settings")
        footer(root, self.status, wraplength=720)
        page = scroll_area(root, padding=(26, 18, 26, 10))
        self.build_device(page)
        ttk.Separator(page).pack(fill="x", pady=16)
        self.build_pass_through(page)
        ttk.Separator(page).pack(fill="x", pady=16)
        self.build_help(page)
        ttk.Separator(page).pack(fill="x", pady=16)
        self.build_about(page)
        self.bridge.listen(self.listener)
        self.refresh_devices(wait=False)
        self.poll_id = root.after(250, self.poll)

    # ---------- layout ----------
    def build_device(self, parent):
        ttk.Label(parent, text="Audio device", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(parent, style="Muted.TLabel", wraplength=700, justify="left",
                  text='Adds an audio device called "DAW Multitool" to your Mac. Choose it as your DAW\'s output and DAW '
                       "Multitool sits between your DAW and your speakers, so Varispeed can change your whole DAW's speed."
                  ).pack(anchor="w", pady=(2, 10))
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10))
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        icon(card, "wave", size=38).grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ttk.Label(card, text="DAW Multitool", style="CardHeading.TLabel").grid(row=0, column=1, sticky="sw")
        line = ttk.Frame(card, style="Panel.TFrame")
        line.grid(row=1, column=1, sticky="nw", pady=(2, 0))
        self.device_dot = tk.Canvas(line, width=10, height=10, highlightthickness=0, background=PANEL)
        self.device_dot.pack(side="left", padx=(0, 6))
        self.device_state = tk.StringVar(value="Checking…")
        ttk.Label(line, textvariable=self.device_state, style="CardMuted.TLabel").pack(side="left")
        self.install_button = ttk.Button(card, text="Install…", style="Card.Primary.TButton", command=self.install)
        self.remove_button = ttk.Button(card, text="Remove…", style="Card.Quiet.TButton", command=self.remove)
        ttk.Label(parent, style="Muted.TLabel", wraplength=700, justify="left",
                  text="Installing, updating or removing it asks for your Mac password and restarts macOS's audio for a "
                       "few seconds, so quit your DAW first. Nothing changes until you click.").pack(anchor="w", pady=(8, 0))

    def build_pass_through(self, parent):
        ttk.Label(parent, text="Pass-through", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(parent, style="Muted.TLabel", wraplength=700, justify="left",
                  text="Plays what your DAW sends to DAW Multitool on your speakers, headphones or interface. Keep DAW "
                       "Multitool open while you use it: the sound reaches you through it.").pack(anchor="w", pady=(2, 10))
        self.pass_on = tk.BooleanVar(value=self.bridge.running)
        ttk.Checkbutton(parent, text="Pass audio through", variable=self.pass_on,
                        command=self.toggle_pass_through).pack(anchor="w")
        grid = ttk.Frame(parent)
        grid.pack(fill="x", pady=(10, 0))
        ttk.Label(grid, text="Play to", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.output = tk.StringVar()
        self.output_box = ttk.Combobox(grid, textvariable=self.output, state="readonly", width=44,
                                       postcommand=lambda: self.refresh_devices(wait=True))
        self.output_box.grid(row=0, column=1, sticky="w")
        self.output_box.bind("<<ComboboxSelected>>", lambda event: self.output_changed())
        ttk.Label(grid, text="Outputs", style="Muted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 14), pady=(6, 0))
        self.pair = tk.StringVar()
        self.pair_box = ttk.Combobox(grid, textvariable=self.pair, state="readonly", width=44)
        self.pair_box.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.pair_box.bind("<<ComboboxSelected>>", lambda event: self.output_changed())
        line = ttk.Frame(parent)
        line.pack(fill="x", pady=(14, 0))
        self.flow_dot = tk.Canvas(line, width=10, height=10, highlightthickness=0, background=BACKGROUND)
        self.flow_dot.pack(side="left", padx=(0, 8))
        self.flow = tk.StringVar(value="Off")
        ttk.Label(line, textvariable=self.flow, style="Muted.TLabel", wraplength=660, justify="left").pack(side="left")
        self.meter = tk.Canvas(parent, height=6, highlightthickness=0, background=BACKGROUND)
        self.meter.pack(fill="x", pady=(10, 0))
        self.problem = tk.StringVar()
        ttk.Label(parent, textvariable=self.problem, foreground=DANGER, wraplength=700, justify="left").pack(anchor="w", pady=(6, 0))
        self.privacy_button = ttk.Button(parent, text="Open Microphone settings", style="Quiet.TButton",
                                         command=lambda: self.open_url(MICROPHONE_PAGE))

    def build_help(self, parent):
        ttk.Label(parent, text="How to use it", style="Heading.TLabel").pack(anchor="w", pady=(0, 8))
        steps = ("Install the audio device above.",
                 'In your DAW\'s audio settings, choose "DAW Multitool" as the output device.',
                 "Turn on Pass audio through and choose where you listen.",
                 'In Varispeed, choose "Your DAW" to change your whole DAW\'s speed. Record keeps what you hear.')
        for number, text in enumerate(steps, 1):
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=str(number), style="Badge.TLabel").pack(side="left", padx=(0, 10))
            ttk.Label(row, text=text, wraplength=640, justify="left").pack(side="left")
        ttk.Label(parent, style="Muted.TLabel", wraplength=700, justify="left",
                  text="macOS asks once to let DAW Multitool use the microphone: that's how it hears the DAW Multitool "
                       "device. It never listens to your microphone.").pack(anchor="w", pady=(10, 0))

    def build_about(self, parent):
        ttk.Label(parent, text="About", style="Heading.TLabel").pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text=f"DAW Multitool v{__version__}", style="Muted.TLabel").pack(side="left")
        ttk.Button(row, text="Show my settings in Finder", style="Quiet.TButton", command=self.reveal_data).pack(side="right")

    # ---------- the audio device ----------
    def refresh_devices(self, wait=False):
        """Asks macOS for its sound devices (only reads). `wait`: now, for the list that's about to open."""
        if wait:
            self.show_devices(self.list_devices())
            return
        threading.Thread(target=lambda: self.events.put(("devices", self.list_devices())), daemon=True).start()

    def show_devices(self, found):
        self.found = found
        listening = audio_bridge.outputs(found)
        default = next((d for d in listening if d["uid"] == found["default_output"]), None)
        choices = [(f"{DEFAULT_OUTPUT} ({default['name']})", "", default["outputs"]) if default else (DEFAULT_OUTPUT, "", 2)]
        choices += [(d["name"], d["uid"], d["outputs"]) for d in listening]
        chosen = self.options["output_device"]
        if chosen and not any(uid == chosen for _, uid, _ in choices):
            choices.append(("Your chosen device (not connected)", chosen, 2))
        self.choices = choices
        self.output_box.configure(values=[label for label, _, _ in choices])
        self.output.set(next(label for label, uid, _ in choices if uid == chosen))
        self.refresh_pairs()
        self.show_device_state()

    def refresh_pairs(self):
        channels = next((count for label, _, count in self.choices if label == self.output.get()), 2)
        pairs = audio_bridge.output_pairs(channels)
        self.pair_box.configure(values=pairs)
        if self.pair.get() not in pairs:  # first time, or a device with fewer outputs
            self.pair.set(pairs[min(self.options["output_left_channel"] // 2, len(pairs) - 1)])

    def chosen_output(self):
        uid = next((uid for label, uid, _ in self.choices if label == self.output.get()), "")
        pairs = list(self.pair_box.cget("values"))
        return uid, 2 * pairs.index(self.pair.get()) if self.pair.get() in pairs else 0

    def device_status(self):
        return device.status([d.get("uid") for d in self.found["devices"]], self.options)

    def show_device_state(self):
        state = self.device_status()
        self.device_state.set(state.summary())
        colour = GREEN if state.installed and state.loaded and not state.update else (AMBER if state.installed else GREY)
        self.device_dot.delete("all")
        self.device_dot.create_oval(1, 1, 9, 9, fill=colour, outline="")
        self.install_button.grid_forget()
        self.remove_button.grid_forget()
        if not state.installed or state.update or not state.loaded:
            self.install_button.configure(text="Install…" if not state.installed else
                                          ("Update…" if state.update else "Reinstall…"))
            self.install_button.grid(row=0, column=2, rowspan=2, padx=(12, 0))
        if state.installed:
            self.remove_button.grid(row=0, column=3, rowspan=2, padx=(6, 0))
        for button in (self.install_button, self.remove_button):
            button.configure(state="disabled" if self.busy else "normal")

    def install(self):
        if self.busy:
            return
        if messagebox.askokcancel("Install the audio device",
                                  'DAW Multitool will add its audio device, "DAW Multitool", to your Mac.\n\n'
                                  "macOS asks for your password, then its audio restarts for a few seconds: quit your "
                                  "DAW first.", parent=self.root):
            self.as_administrator(device.install, "Installing the audio device… enter your Mac password if asked.")

    def remove(self):
        if self.busy:
            return
        if messagebox.askokcancel("Remove the audio device",
                                  'DAW Multitool will remove its audio device, "DAW Multitool". Set your DAW to another '
                                  "output afterwards.\n\nmacOS asks for your password, then its audio restarts for a few "
                                  "seconds.", parent=self.root, icon="warning"):
            if self.bridge.running:
                self.pass_on.set(False)
                self.toggle_pass_through()
            self.as_administrator(device.remove, "Removing the audio device… enter your Mac password if asked.")

    def as_administrator(self, action, text):
        self.busy = True
        self.show_device_state()
        self.status.set(text)

        def work():
            ok, message = action(run=True, runner=self.runner, options=self.options)
            self.events.put(("changed", action is device.install, ok, message))
        threading.Thread(target=work, daemon=True).start()

    def changed(self, installed, ok, message):
        self.busy = False
        if not ok:
            self.status.set(f"Nothing changed: {message}")
        elif installed:
            self.status.set('Installed. macOS\'s audio is restarting: in a few seconds choose "DAW Multitool" as your '
                            "DAW's output, then turn on Pass audio through.")
        else:
            self.status.set("Removed the audio device. Choose another output in your DAW.")
        self.show_device_state()
        if ok:  # the device appears (or goes) once macOS's audio has restarted
            self.recheck_id = self.root.after(int(self.options["device_check_seconds"] * 1000), self.refresh_devices)

    # ---------- pass-through ----------
    def toggle_pass_through(self):
        on = self.pass_on.get()
        audio_bridge.remember(pass_through=on)
        self.options["pass_through"] = on
        if not on:
            self.bridge.stop()
            self.status.set("Pass-through is off: your DAW's sound on DAW Multitool goes nowhere until you turn it on again.")
            return
        try:
            self.bridge.start(*self.chosen_output())
        except OSError as error:
            self.pass_on.set(False)
            self.status.set(str(error))
            return
        self.status.set('Passing your DAW\'s sound through. In your DAW, choose "DAW Multitool" as the output.')

    def output_changed(self):
        self.refresh_pairs()
        uid, left = self.chosen_output()
        if (uid, left) != (self.options["output_device"], self.options["output_left_channel"]):
            audio_bridge.remember(output_device=uid, output_left_channel=left)
            self.options.update(output_device=uid, output_left_channel=left)
        if self.bridge.running:
            self.bridge.set_output(uid, left)

    def show_flow(self):
        status = self.bridge.status if self.bridge.running else None
        text, colour = flow_text(status) if status is not None else ("Off", GREY)
        self.flow.set(text)
        self.flow_dot.delete("all")
        self.flow_dot.create_oval(1, 1, 9, 9, fill=colour, outline="")
        width = self.meter.winfo_width()
        self.meter.delete("all")
        self.meter.create_rectangle(0, 0, width, 6, fill=BORDER, outline="")
        fill = level_fraction(status.get("level", 0)) if status else 0
        if fill:
            self.meter.create_rectangle(0, 0, width * fill, 6, fill=ACCENT, outline="")
        blocked = bool(status) and status.get("microphone") in ("denied", "restricted")
        if blocked:
            self.problem.set("macOS is stopping DAW Multitool from hearing its audio device. Allow DAW Multitool under "
                             "Privacy & Security ▸ Microphone, then turn Pass audio through off and on.")
            self.privacy_button.pack(anchor="w")
        elif self.problem.get().startswith("macOS is stopping"):
            self.problem.set("")
        if not blocked:
            self.privacy_button.pack_forget()

    def heard(self, event):
        kind = event.get("type")
        if kind == "error":
            self.problem.set(event.get("message", ""))
        elif kind == "stopped":
            self.pass_on.set(False)
            self.status.set("The audio bridge stopped. Turn Pass audio through on to start it again.")

    # ---------- the loop ----------
    def poll(self):
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "devices":
                    self.show_devices(data[0])
                elif kind == "changed":
                    self.changed(*data)
                elif kind == "bridge":
                    self.heard(data[0])
        except queue.Empty:
            pass
        self.pass_on.set(self.bridge.running)
        self.show_flow()
        self.poll_id = self.root.after(250, self.poll)

    @staticmethod
    def open_url(url):
        subprocess.run(["/usr/bin/open", url], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)

    @staticmethod
    def reveal_data():
        config = DATA_DIR / "config.json"
        target = ["-R", str(config)] if config.exists() else [str(DATA_DIR)]
        subprocess.run(["/usr/bin/open", *target], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)

    def close(self):
        self.bridge.unlisten(self.listener)
        for timer in (self.poll_id, self.recheck_id):
            if timer:
                self.root.after_cancel(timer)
        self.root.destroy()
