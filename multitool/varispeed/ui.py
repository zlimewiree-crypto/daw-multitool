"""Varispeed window, laid out like the original Varispeed menu (github.com/undulaemusic/Varispeed): speed readouts you
can double-click to type into, the 50%–100%–200% slider (double-click resets), semitone nudges and 100% (⌘0);
Record / Stop with the latest take and its folder; Output to device with the device and output pair.
It plays either beat files (and saves varispeed copies with the new BPM and key in their names) or, like the
original, your whole DAW: the DAW plays into DAW Multitool's own audio device, whose clock runs at this speed
(set up in Settings; multitool/audio/). Space plays/stops."""
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app_paths import update_config
from multitool import music
from multitool.audio import bridge as audio_bridge, device as audio_device
from multitool.scrolling import scroll_area
from multitool.theme import ACCENT, BACKGROUND, BORDER, DANGER, MUTED, PANEL, apply_theme, banner, empty_state, footer, icon
from . import core
from .player import Recorder, TapePlayer, latest_take, next_take, output_devices, output_pairs

DEFAULT_RECORDINGS = "~/Music/Varispeed Recordings"


def device_installed():
    return audio_device.installed_path().is_dir()


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class EditableReadout:
    """Shows a value; double-click to type a new one. Return commits, Escape or clicking away cancels.
    Accepts a leading + or − (the typographic minus too), the unit and a comma decimal point."""
    def __init__(self, parent, font, unit, width, value, on_commit, style="TLabel"):
        self.value, self.on_commit, self.unit_text = value, on_commit, unit
        self.frame = ttk.Frame(parent)
        self.text = tk.StringVar()
        self.label = ttk.Label(self.frame, textvariable=self.text, font=font, style=style)
        self.label.pack(side="left")
        self.label.bind("<Double-Button-1>", self.edit)
        self.typed = tk.StringVar()
        self.entry = ttk.Entry(self.frame, textvariable=self.typed, width=width, font=font, justify="right")
        self.unit = ttk.Label(self.frame, text=unit, font=font, style="Muted.TLabel")
        self.entry.bind("<Return>", self.commit)
        self.entry.bind("<Escape>", lambda event: self.finish())
        self.entry.bind("<FocusOut>", lambda event: self.finish())
        self.editing = False

    def edit(self, event=None):
        self.typed.set(self.value())
        self.label.pack_forget()
        self.entry.pack(side="left")
        self.unit.pack(side="left", padx=(3, 0))
        self.editing = True
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def commit(self, event=None):
        number = core.edited_number(self.typed.get(), self.unit_text)
        self.finish()
        if number is not None:
            self.on_commit(number)
        return "break"

    def finish(self):
        if self.editing:
            self.editing = False
            self.entry.pack_forget()
            self.unit.pack_forget()
            self.label.pack(side="left")


class VarispeedApp:
    def __init__(self, root, stream=None, devices=None, bridge=None):
        """`stream` opens the sound output, `devices` lists the Mac's outputs and `bridge` runs the audio bridge
        for Your DAW mode (tests pass stand-ins)."""
        apply_theme(root)
        self.root, self.stream, self.list_devices = root, stream, devices or output_devices
        self.bridge = bridge or audio_bridge.shared()
        self.daw_take = None  # the take the audio bridge is recording, in Your DAW mode
        self.listener = lambda event: self.events.put(("bridge", event))
        self.settings = core.settings()
        self.files = {}  # table row -> {"path", "info"}
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False  # saving copies
        self.player, self.loaded, self.play_when_loaded = None, None, False
        self.recorder, self.saving_take, self.problem = None, False, ""
        self.target = core.clamp(self.settings["speed"], self.settings)
        self.devices, self.default_device = [], ""
        self.source = tk.StringVar(value="daw" if self.settings["source"] == "daw" else "files")
        family = root._root().studio_fonts["Title"][0]
        self.big, self.small = (family, 34, "bold"), (family, 16, "normal")
        root.title("Varispeed · DAW Multitool")
        root.geometry("900x900")
        root.minsize(760, 640)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Add a beat and press Play. Move the speed while it plays; Record keeps what you hear.")
        banner(root, "Varispeed", "Tape-style speed: slow a beat down or speed it up, with the pitch following.", kind="varispeed")
        footer(root, self.status, wraplength=860)
        page = scroll_area(root, padding=(26, 14, 26, 10))
        self.build_header(page)
        self.build_speed(page)
        ttk.Separator(page).pack(fill="x", pady=12)
        slot = ttk.Frame(page)  # the beat files or your DAW
        slot.pack(fill="x")
        self.beat_section, self.daw_section = ttk.Frame(slot), ttk.Frame(slot)
        self.build_beat(self.beat_section)
        self.build_daw(self.daw_section)
        ttk.Separator(page).pack(fill="x", pady=12)
        self.build_record(page)
        self.output_section = ttk.Frame(page)  # beat files only: Your DAW plays where Settings says
        ttk.Separator(self.output_section).pack(fill="x", pady=12)
        self.build_output(self.output_section)
        self.bridge.listen(self.listener)
        root.bind("<space>", lambda event: self.typing(event) or self.toggle_play() or "break")
        root.bind("<Command-Key-0>", lambda event: self.set_target(1.0))
        self.refresh_devices()
        self.show_source()
        if self.daw_mode:
            self.status.set("Your DAW plays at this speed. Press play in the DAW; Record keeps what you hear.")
        self.set_target(self.target)
        self.show_take()
        self.poll_id = root.after(100, self.poll)

    # ---------- layout ----------
    def build_header(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="Plays", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        for value, text in (("files", "Beat files"), ("daw", "Your DAW")):
            ttk.Radiobutton(row, text=text, value=value, variable=self.source,
                            command=self.source_changed).pack(side="left", padx=(0, 14))
        self.dot = tk.Canvas(row, width=10, height=10, highlightthickness=0, background=self.root.cget("background"))
        self.dot.pack(side="right", padx=(6, 0))
        self.output_state = tk.StringVar(value="Audio output off")
        ttk.Label(row, textvariable=self.output_state, style="Muted.TLabel").pack(side="right")

    def build_speed(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.percent = EditableReadout(row, self.big, "%", 7, lambda: f"{self.target * 100:.1f}",
                                       lambda value: self.set_target(core.clamp(value / 100, self.settings)))
        self.percent.frame.pack(side="left")
        self.semis = EditableReadout(row, self.small, "st", 7, lambda: f"{core.semitones_from_speed(self.target):.2f}",
                                     lambda value: self.set_target(core.set_semitones(value, self.settings)), style="Muted.TLabel")
        self.semis.frame.pack(side="left", padx=(12, 0), pady=(10, 0))
        self.heading_to = tk.StringVar()
        ttk.Label(row, textvariable=self.heading_to, style="Muted.TLabel").pack(side="right")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="50%", style="Muted.TLabel").pack(side="left", anchor="n")
        ttk.Label(row, text="200%", style="Muted.TLabel").pack(side="right", anchor="n")
        middle = ttk.Frame(row)
        middle.pack(side="left", fill="x", expand=True, padx=8)
        self.slider = tk.DoubleVar()
        scale = ttk.Scale(middle, from_=-1, to=1, variable=self.slider, command=self.slid)
        scale.pack(fill="x")
        scale.bind("<Double-Button-1>", lambda event: self.set_target(1.0) or "break")  # double-click resets to 100%
        mark = tk.Canvas(middle, height=8, highlightthickness=0, background=self.root.cget("background"))
        mark.pack(fill="x")
        mark.bind("<Configure>", lambda event: (mark.delete("all"), mark.create_line(
            event.width / 2, 0, event.width / 2, 8, fill=MUTED, width=1.5)))  # the 100% mark: the slider's middle
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(8, 0))
        for step in (-12, -1, 1, 12):
            ttk.Button(row, text=core.semitone_text(step), width=-6,
                       command=lambda n=step: self.set_target(core.nudge(self.target, n, self.settings))).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="100%", width=-6, command=lambda: self.set_target(1.0)).pack(side="right")

    def build_beat(self, parent):
        ttk.Label(parent, text="Beat", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(parent, text="What you hear: the selected beat at the speed above. Space plays · ⌘0 = 100%",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        self.add_button = ttk.Button(row, text="Add audio files…", command=self.add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(row, text="Remove", style="Quiet.TButton", command=self.remove_selected)
        self.remove_button.pack(side="left", padx=8)
        self.tree = ttk.Treeview(parent, columns=("file", "music", "now", "saves"), show="headings", height=3, selectmode="extended")
        empty_state(self.tree, "Add a beat to hear it at a new speed.")
        for key, title, width, stretch in (("file", "Beat", 240, True), ("music", "BPM · Key", 100, False),
                                           ("now", "At this speed", 140, False), ("saves", "Saves as", 300, True)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=stretch)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.load_selected())
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 0))
        self.play_button = ttk.Button(row, text="▶  Play", style="Primary.TButton", command=self.toggle_play)
        self.play_button.pack(side="left")
        self.time = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(row, textvariable=self.time, style="Muted.TLabel", width=12).pack(side="left", padx=(12, 6))
        self.position = tk.DoubleVar()
        ttk.Scale(row, from_=0, to=1, variable=self.position, command=self.seek).pack(side="left", fill="x", expand=True)
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="Save copies in", style="Muted.TLabel").pack(side="left")
        self.folder = tk.StringVar(value=self.settings["output_folder"])
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="Choose…", command=self.choose_folder).pack(side="left")
        self.save_button = ttk.Button(row, text="Save varispeed copies", command=self.save)
        self.save_button.pack(side="left", padx=(8, 0))

    def build_daw(self, parent):
        ttk.Label(parent, text="Your DAW", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(parent, text='Your whole DAW at the speed above, like tape. Set your DAW\'s output to "DAW Multitool" and '
                               "press play in the DAW.", style="Muted.TLabel", wraplength=760).pack(anchor="w", pady=(2, 8))
        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 8))
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        self.daw_dot = tk.Canvas(card, width=10, height=10, highlightthickness=0, background=PANEL)
        self.daw_dot.grid(row=0, column=0, padx=(2, 10))
        self.daw_state = tk.StringVar()
        ttk.Label(card, textvariable=self.daw_state, style="CardHeading.TLabel").grid(row=0, column=1, sticky="w")
        self.daw_detail = tk.StringVar()
        ttk.Label(card, textvariable=self.daw_detail, style="CardMuted.TLabel", wraplength=560,
                  justify="left").grid(row=1, column=1, sticky="w", pady=(2, 0))
        self.daw_button = ttk.Button(card, text="Open Settings", style="Card.Quiet.TButton", command=self.daw_action)
        self.daw_button.grid(row=0, column=2, rowspan=2, padx=(10, 0))
        self.daw_meter = tk.Canvas(parent, height=6, highlightthickness=0, background=BACKGROUND)
        self.daw_meter.pack(fill="x", pady=(10, 0))

    def build_record(self, parent):
        style = ttk.Style(self.root)
        style.configure("Record.TButton", foreground=DANGER)
        style.map("Record.TButton", foreground=[("disabled", MUTED)])
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.record_button = ttk.Button(row, text="●  Record", style="Record.TButton", command=self.toggle_recording)
        self.record_button.pack(side="left")
        self.record_state = tk.StringVar(value="Records what you hear")
        ttk.Label(row, textvariable=self.record_state, style="Muted.TLabel").pack(side="left", padx=12)
        self.take_box = ttk.Frame(parent)
        self.take_box.pack(fill="x")
        self.record_problem = tk.StringVar()
        ttk.Label(parent, textvariable=self.record_problem, foreground=DANGER, wraplength=760).pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Save to", style="Muted.TLabel").pack(side="left")
        self.recordings_name = tk.StringVar()
        ttk.Button(row, textvariable=self.recordings_name, style="Quiet.TButton", command=self.reveal_recordings).pack(side="left")
        self.change_button = ttk.Button(row, text="Change…", command=self.choose_recordings)
        self.change_button.pack(side="right")
        self.default_button = ttk.Button(row, text="Default", style="Quiet.TButton", command=self.default_recordings)

    def build_output(self, parent):
        self.output_on = tk.BooleanVar(value=self.settings["output_enabled"])
        ttk.Checkbutton(parent, text="Output to device", variable=self.output_on, command=self.output_changed).pack(anchor="w")
        self.device = tk.StringVar()
        self.device_box = ttk.Combobox(parent, textvariable=self.device, state="readonly", width=44)
        self.device_box.pack(anchor="w", pady=(8, 0))
        self.device_box.bind("<<ComboboxSelected>>", lambda event: self.output_changed())
        self.pair = tk.StringVar()
        self.pair_box = ttk.Combobox(parent, textvariable=self.pair, state="readonly", width=44)
        self.pair_box.pack(anchor="w", pady=(6, 0))
        self.pair_box.bind("<<ComboboxSelected>>", lambda event: self.output_changed())
        self.output_info = tk.StringVar()
        self.output_info_label = ttk.Label(parent, textvariable=self.output_info, style="Muted.TLabel", wraplength=760)
        self.output_info_label.pack(anchor="w", pady=(8, 0))

    # ---------- speed ----------
    @staticmethod
    def typing(event):
        """True while a text box has the keyboard, so Space types instead of playing."""
        return isinstance(event.widget, (ttk.Entry, tk.Entry))

    def set_target(self, speed):
        self.target = core.clamp(speed, self.settings)
        self.slider.set(core.slider_position(self.target, self.settings))
        if self.player:
            self.player.set_speed(self.target)
        if self.daw_mode:
            self.bridge.set_speed(self.target)
        self.refresh_rows()
        self.show_speed()

    def slid(self, value):
        semitones = core.slider_semitones(float(value), self.settings)
        if semitones == 0:
            self.slider.set(0.0)  # snaps to 100% near the middle
        self.set_target(core.set_semitones(semitones, self.settings))

    def current_speed(self):
        if self.daw_mode:  # the device's clock, part-way through a glide included
            return self.bridge.status.get("speed", self.target) if self.daw_running() else self.target
        return self.player.speed if self.player and self.player.running else self.target

    def show_speed(self):
        current = self.current_speed()
        self.percent.text.set(core.percent_text(current))
        self.semis.text.set(core.semitone_text(core.semitones_from_speed(current)))
        self.heading_to.set(f"→ {core.percent_text(self.target)}" if abs(current - self.target) > 0.0005 else "")
        speed = f" {current * 100:.0f}%" if abs(current - 1) > 0.0005 else ""
        recording = self.recorder or self.daw_take
        self.root.title(f"{'● ' if recording else ''}Varispeed{speed} · DAW Multitool")  # like its menu bar label

    # ---------- the beat ----------
    def add_files(self):
        from multitool.auto_poster.storage import load_settings
        extensions = " ".join("*" + ext for ext in load_settings()["audio_extensions"])
        paths = filedialog.askopenfilenames(parent=self.root, title="Choose beats", filetypes=[("Audio files", extensions)])
        known = {str(entry["path"]) for entry in self.files.values()}
        for path in paths:
            if path in known:
                continue
            path = Path(path)
            row = self.tree.insert("", "end")
            self.files[row] = {"path": path, "info": music.describe(path, listen=False)}  # heard values arrive on loading
        self.refresh_rows()
        if paths and not self.tree.selection():
            self.tree.selection_set(self.tree.get_children()[0])

    def remove_selected(self):
        if self.busy:
            return
        for row in self.tree.selection():
            entry = self.files.pop(row, None)
            if entry and self.loaded == entry["path"]:
                self.unload()
            self.tree.delete(row)

    def refresh_rows(self):
        for row, entry in self.files.items():
            info, now = entry["info"], core.changed(entry["info"], self.target)
            here = " · ".join(part for part in (str(now["bpm"]) if now["bpm"] else "",
                                                (now["key"].short if now["key_exact"] else "between keys") if now["key"] else "") if part)
            named = " · ".join(part for part in (music.label(info["bpm"], info["bpm_source"]),
                                                 music.label(info["key"], info["key_source"])) if part)
            self.tree.item(row, values=(entry["path"].name, named, here, core.new_name(entry["path"], self.target, info)))

    def selected(self):
        rows = self.tree.selection() or self.tree.get_children()
        return (rows[0], self.files[rows[0]]) if rows else (None, None)

    def load_selected(self):
        row, entry = self.selected()
        if entry is None or entry["path"] == self.loaded:
            return
        self.unload()
        self.loaded = entry["path"]
        self.status.set(f"Loading {entry['path'].name}…")
        self.play_button.configure(state="disabled")
        def work(path=entry["path"]):
            try:
                samples, rate = core.decode(path)
                self.events.put(("loaded", path, samples, rate, core.describe(path)))
            except Exception as error:
                self.events.put(("failed", path, str(error)))
        threading.Thread(target=work, daemon=True).start()

    def unload(self):
        if self.recorder:
            self.stop_recording()  # a new beat may have another sample rate
        if self.player:
            self.player.close()
        self.player, self.loaded = None, None
        self.play_button.configure(text="▶  Play", state="normal")

    def toggle_play(self):
        if self.daw_mode:
            self.status.set("Press play in your DAW: Varispeed changes its speed.")
            return
        if self.player and self.player.playing:
            self.player.playing = False
            if not self.recorder:
                self.player.close_output()
            self.play_button.configure(text="▶  Play")
            return
        if not self.files:
            self.status.set("Add a beat first.")
            return
        if self.player is None:
            self.play_when_loaded = True
            self.load_selected()
            return
        if not self.start_output():
            return
        self.player.set_speed(self.target)
        self.player.playing = True
        self.play_button.configure(text="■  Stop")
        self.status.set(f"Playing {self.loaded.name}. Move the speed while it plays.")

    def seek(self, value):
        if self.player:
            self.player.seek(float(value) * self.player.duration)

    # ---------- output ----------
    def refresh_devices(self):
        try:
            self.devices, self.default_device = self.list_devices()
        except Exception as error:
            self.devices, self.default_device = [], ""
            self.problem = f"Couldn't list the sound outputs: {error}"
        names = [name for name, _ in self.devices]
        chosen = self.settings["output_device"] or self.default_device  # first time: the Mac's current output (only read)
        self.device_box.configure(values=names)
        self.device.set(chosen if chosen in names else ("Not connected" if chosen else "Choose…"))
        self.refresh_pairs()

    def selected_device(self):
        return next(((name, channels) for name, channels in self.devices if name == self.device.get()), None)

    def refresh_pairs(self):
        device = self.selected_device()
        pairs = output_pairs(device[1] if device else 2)
        self.pair_box.configure(values=pairs)
        left = min(self.settings["output_left_channel"], 2 * (len(pairs) - 1))
        self.pair.set(pairs[left // 2])

    def output_changed(self):
        """Output on/off, device or outputs changed: remembered, and a running output restarts on the new one."""
        pairs = list(self.pair_box.cget("values"))
        left = 2 * pairs.index(self.pair.get()) if self.pair.get() in pairs else 0
        self.remember(output_enabled=self.output_on.get(), output_left_channel=left,
                      output_device=self.device.get() if self.selected_device() else self.settings["output_device"])
        self.refresh_pairs()
        self.problem = ""
        if not (self.player and self.player.running):
            return
        playing = self.player.playing
        if not self.output_on.get():
            if self.recorder:
                self.stop_recording()
            self.player.playing = False
            self.player.close_output()
            self.play_button.configure(text="▶  Play")
            return
        self.player.close_output()
        if self.start_output():
            self.player.playing = playing

    def start_output(self):
        """Opens the chosen output (the original's "audio passthrough"). False, with the reason shown, if it can't."""
        if self.player.running:
            return True
        self.problem = ""
        if not self.output_on.get():
            self.problem = "Turn on Output to device to hear it."
        elif self.selected_device() is None:
            self.problem = "Choose an output device." if not self.settings["output_device"] else "Output device not connected."
        else:
            name, channels = self.selected_device()
            left = min(self.settings["output_left_channel"], max(0, channels - 2))
            try:
                self.player.open(device=name, left_channel=left, channels=channels)
            except Exception as error:
                self.problem = f"Couldn't start the sound output: {error}"
        if self.problem:
            self.status.set(self.problem)
        return not self.problem

    # ---------- recording ----------
    def recordings_folder(self):
        return Path(self.settings["recordings_folder"]).expanduser()

    def toggle_recording(self):
        if self.recorder or self.daw_take:
            self.stop_recording()
            return
        if self.saving_take:
            return
        if self.daw_mode:
            self.record_daw()
            return
        if self.player is None:
            self.record_problem.set("Add a beat (and let it load) first.")
            return
        if not self.start_output():
            return
        recorder = Recorder(self.recordings_folder(), self.player.rate)
        try:
            recorder.start()
        except OSError:
            self.record_problem.set("Can't write to the recording folder. Choose another one.")
            return
        self.record_problem.set("")
        self.recorder = recorder
        self.player.recorder = recorder
        self.record_button.configure(text="■  Stop")
        self.show_speed()

    def record_daw(self):
        """Records what you hear from your DAW: the audio bridge writes the take (32-bit float, never replacing)."""
        if not self.daw_running():
            self.record_problem.set("Turn on pass-through first (Settings) and play your DAW into DAW Multitool.")
            return
        folder = self.recordings_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = next_take(folder)
        except (OSError, StopIteration):
            self.record_problem.set("Can't write to the recording folder. Choose another one.")
            return
        if not self.bridge.record(path):
            self.record_problem.set("The audio bridge isn't running. Turn on pass-through in Settings.")
            return
        self.record_problem.set("")
        self.daw_take = path
        self.record_button.configure(text="■  Stop")
        self.show_speed()

    def stop_recording(self, wait=False):
        if self.daw_take:
            self.daw_take = None
            self.bridge.stop_record()  # the bridge says "recorded" once the file is finished
            self.record_button.configure(text="●  Record", state="disabled")
            self.saving_take = True
            self.show_speed()
            return
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        if self.player:
            self.player.recorder = None
            if not self.player.playing:
                self.player.close_output()
        self.record_button.configure(text="●  Record", state="disabled")
        self.saving_take = True
        self.show_speed()
        def finish():
            path, seconds = recorder.stop()
            self.events.put(("take", path, seconds))
        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=True).start()

    def show_take(self):
        for child in self.take_box.winfo_children():
            child.destroy()
        folder = self.recordings_folder()
        self.recordings_name.set(f"📁  {folder.name}")
        if self.settings["recordings_folder"] != DEFAULT_RECORDINGS:
            self.default_button.pack(side="right", padx=8)
        else:
            self.default_button.pack_forget()
        take = latest_take(folder)
        if not take:
            return
        path, seconds = take
        ttk.Label(self.take_box, text="Drag and drop into your DAW: click the take to show it in Finder, then drag it in.",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(self.take_box, style="Card.TFrame", padding=(10, 6))
        row.pack(fill="x", pady=(4, 0))
        parts = [icon(row, "wave", size=24),
                 ttk.Label(row, text=path.stem.replace("Varispeed ", ""), style="Card.TLabel"),
                 ttk.Label(row, text=clock(seconds), style="CardMuted.TLabel")]
        parts[0].pack(side="left", padx=(0, 8))
        parts[1].pack(side="left")
        parts[2].pack(side="right")
        for part in (row, *parts):
            part.bind("<Button-1>", lambda event, p=path: self.reveal(p))

    @staticmethod
    def reveal(path):
        subprocess.run(["/usr/bin/open", "-R", str(path)], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)

    def reveal_recordings(self):
        folder = self.recordings_folder()
        folder.mkdir(parents=True, exist_ok=True)
        take = latest_take(folder)
        if take:
            self.reveal(take[0])
        else:
            subprocess.run(["/usr/bin/open", str(folder)], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)

    def choose_recordings(self):
        if self.recorder or self.saving_take:
            return
        folder = filedialog.askdirectory(parent=self.root, title="Choose where Varispeed saves recordings",
                                         initialdir=str(self.recordings_folder()))
        if folder:
            self.remember(recordings_folder=folder)
            self.record_problem.set("")
            self.show_take()

    def default_recordings(self):
        self.remember(recordings_folder=DEFAULT_RECORDINGS)
        self.show_take()

    # ---------- saving copies ----------
    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Save varispeed copies in")
        if folder:
            self.folder.set(folder)

    def save(self):
        if self.busy:
            return
        rows = list(self.tree.selection() or self.tree.get_children())
        folder = self.folder.get().strip()
        if not rows or not folder:
            self.status.set("Add a beat first." if not rows else "Choose a folder for the copies.")
            return
        jobs = [(self.files[row]["path"], self.files[row]["info"]) for row in rows]
        speed, options = self.target, dict(self.settings)
        self.remember(output_folder=folder)
        self.set_busy(True)
        self.cancel.clear()
        def work():
            saved = 0
            for path, info in jobs:
                if self.cancel.is_set():
                    break
                self.events.put(("status", f"Saving {path.name} at {core.readout(speed)}…"))
                try:
                    saved += bool(core.save(path, speed, folder, options, info, self.cancel.is_set))
                except Exception as error:
                    self.events.put(("status", f"Couldn't save {path.name}: {error}"))
            self.events.put(("done", saved, folder))
        threading.Thread(target=work, daemon=True).start()

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.save_button, self.add_button, self.remove_button):
            button.configure(state="disabled" if busy else "normal")

    # ---------- your DAW ----------
    @property
    def daw_mode(self):
        return self.source.get() == "daw"

    def daw_running(self):
        return self.bridge.running and bool(self.bridge.status.get("input")) and bool(self.bridge.status.get("output"))

    def source_changed(self):
        """Beat files ⇄ your DAW. The DAW goes back to 100% when you leave Your DAW."""
        if self.recorder or self.daw_take:
            self.stop_recording()
        self.remember(source=self.source.get())
        if self.daw_mode:
            if self.player:
                self.player.playing = False
                self.player.close_output()
                self.play_button.configure(text="▶  Play")
            self.start_daw()
            self.bridge.set_speed(self.target)
            self.status.set("Your DAW plays at this speed. Press play in the DAW; Record keeps what you hear.")
        else:
            self.bridge.set_speed(1.0)
            self.status.set("Your DAW is back to 100%. Add a beat and press Play.")
        self.show_source()

    def show_source(self):
        if self.daw_mode:
            self.beat_section.pack_forget()
            self.output_section.pack_forget()
            self.daw_section.pack(fill="x")
        else:
            self.daw_section.pack_forget()
            self.beat_section.pack(fill="x")
            self.output_section.pack(fill="x")
        if self.daw_mode and not self.bridge.running:
            self.start_daw()

    def start_daw(self):
        """Turns pass-through on (with Settings' output) when the device is installed."""
        if self.bridge.running or not device_installed():
            return
        options = audio_bridge.settings()
        try:
            self.bridge.start(options["output_device"], options["output_left_channel"])
        except OSError as error:
            self.problem = str(error)
            return
        if not options["pass_through"]:
            audio_bridge.remember(pass_through=True)

    def daw_action(self):
        if self.daw_button.cget("text") == "Turn on":
            self.start_daw()
        else:
            self.root.event_generate("<<OpenSettings>>")

    def show_daw(self):
        status = self.bridge.status if self.bridge.running else {}
        button = "Open Settings"
        if not device_installed():
            state, detail, colour = ("Set up the DAW Multitool audio device",
                                     'Install it in Settings, then choose "DAW Multitool" as your DAW\'s output.', "#4a4560")
        elif not self.bridge.running:
            state, detail, colour, button = ("Pass-through is off", "Turn it on to hear your DAW through DAW Multitool.",
                                             "#4a4560", "Turn on")
        elif not status.get("device"):
            state, detail, colour = ("Waiting for macOS's audio", "The DAW Multitool device hasn't appeared yet.", "#f5b942")
        elif status.get("microphone") in ("denied", "restricted"):
            state, detail, colour = ("macOS is blocking the device",
                                     "Allow DAW Multitool under Privacy & Security ▸ Microphone (it only hears the "
                                     "DAW Multitool device).", DANGER)
        elif not status.get("output"):
            state, detail, colour = ("No sound output", "Choose where you listen in Settings.", "#f5b942")
        else:
            state = f"Your DAW  →  DAW Multitool  →  {status.get('output_name') or 'your output'}"
            detail = (f"{status.get('in_rate', 0) / 1000:g} kHz · latency {status.get('latency_ms', 0):.0f} ms · "
                      f"dropouts {status.get('dropouts', 0)}")
            colour = "#3ddc84"
        self.daw_state.set(state)
        self.daw_detail.set(detail)
        self.daw_button.configure(text=button)
        self.daw_dot.delete("all")
        self.daw_dot.create_oval(1, 1, 9, 9, fill=colour, outline="")
        from multitool.settings import level_fraction
        width = self.daw_meter.winfo_width()
        self.daw_meter.delete("all")
        self.daw_meter.create_rectangle(0, 0, width, 6, fill=BORDER, outline="")
        fill = level_fraction(status.get("level", 0))
        if fill:
            self.daw_meter.create_rectangle(0, 0, width * fill, 6, fill=ACCENT, outline="")

    def heard(self, event):
        """What the audio bridge said (in Your DAW mode it records the takes)."""
        kind = event.get("type")
        if kind == "recorded":  # after Stop, or when the bridge had to stop (it says why in an "error" too)
            self.daw_take, self.saving_take = None, False
            self.record_button.configure(text="●  Record", state="normal")
            if event.get("seconds", 0) < 0:
                self.record_problem.set("Couldn't finish writing the take (is the disk full?).")
            else:
                self.status.set(f"Saved the take {Path(event['path']).name} ({clock(event.get('seconds', 0))}).")
            self.show_take()
        elif kind == "error" and ("record" in event.get("message", "").lower()):
            if self.daw_take:
                self.daw_take = None
                self.record_button.configure(text="●  Record")
            self.record_problem.set(event["message"])
        elif kind == "stopped" and (self.daw_take or self.saving_take):
            self.daw_take, self.saving_take = None, False
            self.record_button.configure(text="●  Record", state="normal")
            self.record_problem.set("The audio bridge stopped, so the recording did too.")
            self.show_take()

    def remember(self, **values):
        values.setdefault("speed", round(self.target, 6))
        changes = {key: value for key, value in values.items() if self.settings.get(key) != value}
        if changes:
            self.settings.update(changes)
            update_config("varispeed", changes)

    # ---------- the loop (10 times a second, like the original) ----------
    def poll(self):
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "loaded":
                    path, samples, rate, heard = data
                    if path != self.loaded:
                        continue  # another beat was chosen meanwhile
                    self.player = TapePlayer(samples, rate, self.settings, self.stream)
                    self.player.set_speed(self.target)
                    for entry in self.files.values():
                        if entry["path"] == path:
                            entry["info"] = heard
                    self.refresh_rows()
                    self.play_button.configure(state="normal")
                    self.status.set(f"{path.name} is ready: press Play (Space).")
                    if self.play_when_loaded:
                        self.play_when_loaded = False
                        self.toggle_play()
                elif kind == "failed":
                    self.unload()
                    self.status.set(f"Couldn't open {Path(data[0]).name}: {data[1]}")
                elif kind == "take":
                    self.saving_take = False
                    self.record_button.configure(state="normal")
                    self.status.set(f"Saved the take {Path(data[0]).name} ({clock(data[1])}).")
                    self.show_take()
                elif kind == "status":
                    self.status.set(data[0])
                elif kind == "bridge":
                    self.heard(data[0])
                elif kind == "done":
                    self.set_busy(False)
                    self.status.set(f"Saved {data[0]} varispeed cop{'y' if data[0] == 1 else 'ies'} in {data[1]}. "
                                    "The originals are unchanged.")
        except queue.Empty:
            pass
        self.show_speed()
        running = self.daw_running() if self.daw_mode else bool(self.player and self.player.running)
        self.output_state.set("Audio output on" if running else "Audio output off")
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 9, 9, fill="#3ddc84" if running else "#4a4560", outline="")
        if self.recorder:
            self.record_state.set(clock(self.recorder.seconds))
        elif self.daw_take:
            self.record_state.set(clock(self.bridge.status.get("recorded", 0)))
        else:
            self.record_state.set("Saving…" if self.saving_take else "Records what you hear")
        if self.daw_mode:
            self.show_daw()
        if self.problem:
            self.output_info.set(self.problem)
            self.output_info_label.configure(foreground=DANGER)
        elif running and not self.daw_mode:
            self.output_info.set(f"Latency {self.player.latency_ms():.0f} ms · dropouts {self.player.dropouts}")
            self.output_info_label.configure(foreground=MUTED)
        else:
            self.output_info.set("")
        if self.player:
            played = self.player.position / self.player.rate
            self.time.set(f"{clock(played)} / {clock(self.player.duration)}")
            self.position.set(played / self.player.duration if self.player.duration else 0)
            if not self.player.playing and self.play_button.cget("text").startswith("■"):
                self.play_button.configure(text="▶  Play")
                if not self.recorder:
                    self.player.close_output()
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Stopping… wait for the copy being saved to finish, then close.")
            return
        self.stop_recording(wait=True)  # finishes the take, like quitting the original
        if self.player:
            self.player.close()
        if self.daw_mode:
            self.bridge.set_speed(1.0)  # your DAW back to normal speed (pass-through stays as Settings has it)
        self.bridge.unlisten(self.listener)
        self.remember()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()
