"""Bounce Sorter window: pick options, Preview (moves nothing), fix any rows, then Sort.

Teaching it: select a file, Space = listen, B = beat, M = melody. Then "Learn from my labels".

Run:  .venv/bin/python bounce_sorter_app.py
"""
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import drums
import sorter
from multitool import credits, music
from multitool.theme import apply_theme, banner, empty_state, footer, MUTED

CATEGORY_LABELS = {"beat": "Beats", "loop": "Melodies", "review": "Needs Review"}
NEXT_CATEGORY = {"beat": "loop", "loop": "review", "review": "beat"}
EXT_CHOICES = [".wav", ".mp3", ".aif", ".flac", ".m4a"]
FFPLAY = shutil.which("ffplay")  # can start mid-song; afplay (built into macOS) can't


class App:
    def __init__(self, root):
        self.root = root
        self.settings = sorter.load_settings()
        self.moves = {}  # tree row id -> PlannedMove
        self.use = {}  # tree row id -> include this file when sorting?
        self.labels = drums.load_labels()  # files the user labelled with B / M
        self.credit_settings = credits.load_settings()
        self.events = queue.Queue()  # messages from the worker thread
        self.cancel = threading.Event()
        self.busy = False
        self.player = None  # running ffplay/afplay process
        self.playing_row = None

        apply_theme(root)
        root.title("Audio Organizer · DAW Multitool")
        root.geometry("1040x720")
        root.minsize(760, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)
        banner(root, "Audio Organizer", "Preview your bounces, check the results, then sort.", kind="organizer")
        self.status = tk.StringVar(value="Choose your options, then press Preview. Nothing moves until you press Sort.")
        footer(self.root, self.status, wraplength=980)  # first, so the file list can't squeeze it out
        self.build_options()
        self.build_preview()
        self.poll_id = self.root.after(100, self.poll_events)

    # ---------- layout ----------
    def build_options(self):
        s = self.settings
        frame = ttk.Frame(self.root, padding=(24, 18, 24, 6))
        frame.pack(fill="x")
        frame.columnconfigure(1, weight=1)

        self.source = tk.StringVar(value=s["source_folder"])
        self.output = tk.StringVar(value=s["output_folder"])
        self.folder_row(frame, 0, "Bounces folder", self.source)
        self.folder_row(frame, 1, "Sort into", self.output, hint="empty = same folder")

        self.sorted_name = tk.StringVar(value=s["sorted_folder_name"])
        ttk.Label(frame, text="Folder name", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(frame, textvariable=self.sorted_name).grid(row=2, column=1, sticky="we", padx=8, pady=(4, 0))

        advanced = ttk.Frame(frame)
        advanced.grid(row=4, column=0, columnspan=3, sticky="we", pady=8)
        self.show_options = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Naming, filters & learning options", variable=self.show_options,
                        command=lambda: advanced.grid() if self.show_options.get() else advanced.grid_remove()).grid(
                            row=3, column=0, columnspan=3, sticky="w", pady=(12, 14))
        advanced.columnconfigure(0, weight=1)
        names = ttk.Frame(advanced)
        names.grid(row=0, column=0, sticky="w", pady=(6, 0))
        ttk.Label(names, text="Inside it →  ").pack(side="left")
        self.beats_name = self.name_field(names, "Beats folder:", s["beats_folder_name"])
        self.loops_name = self.name_field(names, "Loops folder:", s["loops_folder_name"])
        self.review_name = self.name_field(names, "Unsure folder:", s["review_folder_name"])
        self.split_month = tk.BooleanVar(value=s["split_by_month"])
        ttk.Checkbutton(names, text="Year / month folders (by date made)", variable=self.split_month).pack(side="left")

        opts = ttk.LabelFrame(advanced, text="Detailed options", padding=12)
        opts.grid(row=1, column=0, sticky="we", pady=8)

        # Naming
        self.add_date = tk.BooleanVar(value=s["add_date"])
        self.date_pos = tk.StringVar(value=s["date_position"])
        self.date_fmt = tk.StringVar(value=s["date_format"])
        row = ttk.Frame(opts)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Add the date the file was made to its name", variable=self.add_date).pack(side="left")
        ttk.Radiobutton(row, text="at start", value="start", variable=self.date_pos).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(row, text="at end", value="end", variable=self.date_pos).pack(side="left")
        ttk.Label(row, text="  format:").pack(side="left")
        ttk.Combobox(row, textvariable=self.date_fmt, width=10,
                     values=["%Y-%m-%d", "%m-%d-%y", "%d.%m.%Y", "%b %d %Y"]).pack(side="left")
        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(6, 0))
        self.add_bpm_key = tk.BooleanVar(value=s["add_bpm_key"])
        ttk.Checkbutton(row, text="Add the BPM and key to names that don't have them (e.g. \"night drive 154 c#min\")",
                        variable=self.add_bpm_key).pack(side="left")

        # Which files
        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="File types:").pack(side="left")
        self.exts = {e: tk.BooleanVar(value=e in s["extensions"]) for e in EXT_CHOICES}
        for e, var in self.exts.items():
            ttk.Checkbutton(row, text=e, variable=var).pack(side="left")
        self.subfolders = tk.BooleanVar(value=s["include_subfolders"])
        ttk.Checkbutton(row, text="Include subfolders", variable=self.subfolders).pack(side="left", padx=(16, 0))

        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(6, 0))
        self.recent_on = tk.BooleanVar(value=s["only_recent_days"] > 0)
        self.recent_days = tk.IntVar(value=s["only_recent_days"] or 30)
        ttk.Checkbutton(row, text="Only files from the last", variable=self.recent_on).pack(side="left")
        ttk.Spinbox(row, from_=1, to=3650, width=5, textvariable=self.recent_days).pack(side="left")
        ttk.Label(row, text="days").pack(side="left")
        self.min_secs = tk.IntVar(value=s["min_seconds"])
        ttk.Label(row, text="    Skip files shorter than").pack(side="left")
        ttk.Spinbox(row, from_=0, to=600, width=4, textvariable=self.min_secs).pack(side="left")
        ttk.Label(row, text="seconds").pack(side="left")

        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(6, 0))
        self.contains = tk.StringVar(value=s["name_contains"])
        self.excludes = tk.StringVar(value=s["name_excludes"])
        ttk.Label(row, text="Name contains (comma-separated, blank = any):").pack(side="left")
        ttk.Entry(row, textvariable=self.contains, width=18).pack(side="left")
        ttk.Label(row, text="  Name does NOT contain:").pack(side="left")
        ttk.Entry(row, textvariable=self.excludes, width=18).pack(side="left")

        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(6, 0))
        self.copy_mode = tk.BooleanVar(value=s["mode"] == "copy")
        ttk.Checkbutton(row, text="Copy instead of move (keeps the originals where they are)",
                        variable=self.copy_mode).pack(side="left")
        self.name_hint = tk.BooleanVar(value=s["loop_name_hint"] > 0)
        ttk.Checkbutton(row, text="Melody words help; conflicting names need review",
                        variable=self.name_hint).pack(side="left", padx=(16, 0))

        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(6, 0))
        self.known_producers = tk.StringVar(value=", ".join(self.credit_settings["known_producers"]))
        ttk.Label(row, text="Collab producers (comma-separated)").pack(side="left")
        ttk.Entry(row, textvariable=self.known_producers).pack(side="left", fill="x", expand=True, padx=8)
        self.save_credits_btn = ttk.Button(row, text="Save credits", command=self.save_credits)
        self.save_credits_btn.pack(side="left")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=4, sticky="we")  # the full width, so no button gets squeezed
        self.preview_btn = ttk.Button(buttons, text="1  Preview files", style="Primary.TButton", command=self.start_preview)
        self.preview_btn.pack(side="left")
        self.review_btn = ttk.Button(buttons, text="Review sorted files", command=self.start_review)
        self.review_btn.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(buttons, text="Stop", style="Quiet.TButton", command=self.cancel.set, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        learning = ttk.Frame(opts)
        learning.pack(fill="x", pady=(10, 0))
        self.learn_btn = ttk.Button(learning, text="Learn from my labels", command=self.start_learn)
        self.learn_btn.pack(side="left", padx=(18, 0))
        self.label_count = tk.StringVar()
        ttk.Label(learning, textvariable=self.label_count, foreground=MUTED).pack(side="left", padx=6)
        self.sort_btn = ttk.Button(buttons, text="2  Sort checked files", style="Primary.TButton", command=self.start_sort, state="disabled")
        self.sort_btn.pack(side="right")
        self.update_label_count()
        advanced.grid_remove()

    def folder_row(self, parent, row, label, var, hint=""):
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="we", padx=8, pady=4)
        ttk.Button(parent, text="Choose…", command=lambda: self.pick_folder(var)).grid(row=row, column=2, sticky="we", pady=4)
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=row, column=3, sticky="w", padx=(10, 0))

    def name_field(self, parent, label, value):
        var = tk.StringVar(value=value)
        ttk.Label(parent, text=label).pack(side="left")
        ttk.Entry(parent, textvariable=var, width=11).pack(side="left", padx=(2, 12))
        return var

    def build_preview(self):
        actions = ttk.Frame(self.root, padding=(24, 8, 24, 2))
        actions.pack(fill="x")
        ttk.Label(actions, text="Mark the selected file:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        for text, command in (("Correct (C)", lambda: self.review_selected(True)),
                              ("Incorrect (X)", lambda: self.review_selected(False)),
                              ("Beat (B)", lambda: self.label_selected("beat")),
                              ("Melody (M)", lambda: self.label_selected("loop"))):
            ttk.Button(actions, text=text, style="Quiet.TButton", command=command).pack(side="left", padx=(0, 2))
        frame = ttk.Frame(self.root, padding=(24, 0, 24, 10))
        frame.pack(fill="both", expand=True)
        hint = ttk.Label(frame, text="Click ✓ to include · double-click “Goes to” to change it · Space = listen · "
                                     "C/X = correct/incorrect · B/M = actual type · ≈ = heard, not in the name",
                         style="Muted.TLabel", justify="left")
        hint.pack(anchor="w", fill="x", pady=(0, 4))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=max(300, event.width)))

        cols = ("use", "file", "bpm", "key", "drums", "you", "result", "dest", "name", "credits", "dup")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        empty_state(self.tree, "No files yet. Press Preview files to see where each bounce would go.")
        for col, text, width, stretch in (("use", "✓", 44, False), ("file", "File", 260, True),
                                          ("bpm", "BPM", 70, False), ("key", "Key", 80, False),
                                          ("drums", "Drum score", 125, False), ("you", "Your label", 115, False),
                                          ("result", "Review", 95, False),
                                          ("dest", "Goes to", 200, False), ("name", "New name", 240, True),
                                          ("credits", "Credits", 160, False), ("dup", "Duplicate of", 200, False)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, stretch=stretch,
                             anchor="center" if col in ("use", "drums", "you", "bpm", "key") else "w")
        for cat, colour in (("beat", "#8dd9a0"), ("loop", "#8ab4f8"), ("review", "#edc47b")):
            self.tree.tag_configure(cat, foreground=colour)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=4)
        scroll.pack(side="right", fill="y", pady=4)
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<space>", lambda e: self.toggle_play() or "break")
        self.tree.bind("<Escape>", lambda e: self.stop_playing())
        for key, label in (("b", "beat"), ("B", "beat"), ("m", "loop"), ("M", "loop")):
            self.tree.bind(key, lambda e, lab=label: self.label_selected(lab) or "break")
        for key, correct in (("c", True), ("C", True), ("x", False), ("X", False)):
            self.tree.bind(key, lambda e, answer=correct: self.review_selected(answer) or "break")

    # ---------- settings ----------
    def pick_folder(self, var):
        start = Path(var.get() or self.source.get()).expanduser()
        chosen = filedialog.askdirectory(initialdir=start if start.exists() else Path.home())
        if chosen:
            var.set(chosen)

    def read_settings(self):
        s, default = dict(self.settings), sorter.DEFAULTS
        s.update(
            source_folder=self.source.get().strip(),
            output_folder=self.output.get().strip(),
            sorted_folder_name=self.sorted_name.get().strip() or default["sorted_folder_name"],
            split_by_month=self.split_month.get(),
            beats_folder_name=self.beats_name.get().strip() or default["beats_folder_name"],
            loops_folder_name=self.loops_name.get().strip() or default["loops_folder_name"],
            review_folder_name=self.review_name.get().strip() or default["review_folder_name"],
            add_date=self.add_date.get(),
            add_bpm_key=self.add_bpm_key.get(),
            date_position=self.date_pos.get(),
            date_format=self.date_fmt.get() or default["date_format"],
            extensions=[e for e, v in self.exts.items() if v.get()],
            include_subfolders=self.subfolders.get(),
            only_recent_days=self.int_or(self.recent_days, 30) if self.recent_on.get() else 0,
            min_seconds=self.int_or(self.min_secs, 0),
            name_contains=self.contains.get(),
            name_excludes=self.excludes.get(),
            mode="copy" if self.copy_mode.get() else "move",
            loop_name_hint=sorter.DEFAULTS["loop_name_hint"] if self.name_hint.get() else 0.0,
        )
        self.settings = s
        sorter.save_settings(s)
        return s

    @staticmethod
    def int_or(var, default):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    # ---------- preview ----------
    def start_preview(self):
        if not self.save_credits():
            return
        s = self.read_settings()
        if not Path(s["source_folder"]).expanduser().is_dir():
            self.status.set("That folder doesn't exist — choose where your bounces are.")
            return
        if not s["extensions"]:
            self.status.set("Tick at least one file type.")
            return
        self.clear_list()

        def work():
            moves, skipped = sorter.plan(s, progress=lambda i, n, name: self.events.put(("progress", i, n, name)),
                                         cancelled=self.cancel.is_set)
            self.events.put(("planned", moves, skipped))
        self.run_worker(work)

    def show_plan(self, moves, skipped):
        for m in moves:
            row = self.tree.insert("", "end")
            self.moves[row] = m
            # only byte-identical copies start unchecked; a same-name guess stays selected
            self.use[row] = m.destination is None and m.duplicate_kind != "identical"
            self.refresh_row(row)
        stopped = " (stopped early)" if self.cancel.is_set() else ""
        dups = sum(m.duplicate_of is not None for m in moves)
        same = sum(m.duplicate_kind == "identical" for m in moves)
        dup_note = (f" {same} exact copies left unchecked, {dups - same} possible copies to listen to (nothing is deleted)."
                    if dups else "")
        self.status.set(f"{len(moves)} files{stopped}: {self.count_summary()}. "
                        f"{len(skipped)} skipped as too short.{dup_note} Nothing has moved yet.")
        self.update_sort_button()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    def start_review(self):
        if self.busy:
            return
        try:
            settings = self.read_settings()
            moves = sorter.load_sorted(settings)
        except (OSError, ValueError) as error:
            self.status.set(f"Couldn't load sorted files: {error}")
            return
        self.clear_list()
        self.show_plan(moves, [])
        self.status.set(f"{len(moves)} sorted files ready to review. Space = listen; C/X = correct/incorrect; "
                        "B/M = actual type. Labels save immediately; files stay in their sorted folders.")

    def clear_list(self):
        self.stop_playing()
        self.tree.delete(*self.tree.get_children())
        self.moves.clear()
        self.use.clear()

    def count_summary(self):
        cats = [m.category for m in self.moves.values()]
        return f"{cats.count('beat')} beats, {cats.count('loop')} melodies, {cats.count('review')} unsure"

    def save_credits(self):
        try:
            names = credits.producer_names(self.known_producers.get())
            changes = {"known_producers": names} if names != self.credit_settings["known_producers"] else {}
            self.credit_settings = credits.save_settings(changes)
            self.known_producers.set(", ".join(self.credit_settings["known_producers"]))
            for row in self.moves:
                self.refresh_row(row)
            self.status.set("Saved collaborators. Credits updated in the list.")
            return True
        except (OSError, ValueError) as error:
            self.status.set(f"Couldn't save credits: {error}")
            return False

    def refresh_row(self, row):
        m = self.moves[row]
        drums_pct = "?" if m.probability is None else f"{m.probability * 100:.0f}%"
        label = self.labels.get(str(m.path), {}).get("label")
        result = "Unreviewed" if not label else "Labelled" if m.category == "review" else (
            "Correct" if label == m.category else "Incorrect")
        folder = m.destination.parent if m.destination else m.folder(self.settings)
        info = m.music or {}
        self.tree.item(row, tags=(m.category,), values=(
            "✓" if self.use[row] else "", m.path.name, music.label(info.get("bpm"), info.get("bpm_source")),
            music.label(info.get("key"), info.get("key_source")), drums_pct, CATEGORY_LABELS.get(label, ""), result,
            folder.relative_to(sorter.sorted_root(self.settings)), m.path.name if m.destination else m.new_name,
            ", ".join(credits.collaborators(m.path.name, self.credit_settings)),
            ("Same file as " if m.duplicate_kind == "identical" else "Maybe: ") + m.duplicate_of.name
            if m.duplicate_of else ""))

    def column_at(self, x):
        """The column's name ("use", "dest", ...) under the pointer, whatever its position."""
        column = self.tree.identify_column(x)
        try:
            return self.tree.column(column, "id") if column else ""
        except tk.TclError:
            return ""

    def on_click(self, event):
        row, col = self.tree.identify_row(event.y), self.column_at(event.x)
        if row and col == "use" and not self.busy and not self.moves[row].destination:
            self.use[row] = not self.use[row]
            self.refresh_row(row)
            self.update_sort_button()

    def on_double_click(self, event):
        row, col = self.tree.identify_row(event.y), self.column_at(event.x)
        if row and col == "dest" and not self.busy and not self.moves[row].destination:
            m = self.moves[row]
            m.category = NEXT_CATEGORY[m.category]
            self.refresh_row(row)

    # ---------- listening + labelling ----------
    def toggle_play(self):
        if self.busy:
            return
        row = self.tree.focus()
        if not row or row not in self.moves:
            return
        if self.playing_row == row and self.player and self.player.poll() is None:
            self.stop_playing()
        else:
            self.play(row)

    def play(self, row):
        self.stop_playing()
        path = self.moves[row].path
        from multitool import platform
        if not platform.IS_MAC and not FFPLAY:
            try:
                from multitool.varispeed.player import FilePreview
                self.player = FilePreview(path)
            except Exception as error:
                self.status.set(f"Couldn't play {path.name}: {error}")
                return
            self.playing_row = row
            self.status.set(f"▶ {path.name}   (Space = stop · B = beat · M = melody)")
            return
        if FFPLAY:
            # Start a quarter of the way in, so long intros don't hide the drums
            start = int((sorter.audio_seconds(path) or 0) * 0.25)
            cmd = [FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", str(start), str(path)]
        else:
            cmd = ["afplay", str(path)]
        try:
            self.player = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        except OSError as error:
            self.status.set(f"Couldn't play {path.name}: {error}")
            return
        self.playing_row = row
        self.status.set(f"▶ {path.name}   (Space = stop · B = beat · M = melody)")

    def stop_playing(self):
        if self.player:
            self.player.terminate()
        self.player, self.playing_row = None, None

    def label_selected(self, label):
        """Save the user's B/M answer as training data, apply it to this row, then move to the next file."""
        row = self.tree.focus()
        if not row or row not in self.moves or self.busy:
            return
        m = self.moves[row]
        try:
            feats = m.features if m.features is not None else drums.DrumDetector().features(m.path)
            entry = {"label": label, "features": feats, "version": drums.FEATURE_VERSION,
                     "predicted": m.category, "correct": label == m.category if m.category != "review" else None}
            updated = dict(self.labels)
            updated.pop(str(m.src), None)
            updated[str(m.path)] = entry
            drums.save_labels(updated)
        except (OSError, ValueError) as error:
            self.status.set(f"Couldn't save this label: {error}")
            return
        self.labels = updated
        if m.destination is None:
            m.category = label  # before sorting, your answer decides the destination
        self.refresh_row(row)
        self.update_label_count()

        nxt = self.tree.next(row)
        if nxt:
            self.tree.selection_set(nxt)
            self.tree.focus(nxt)
            self.tree.see(nxt)
            self.play(nxt)
        else:
            self.stop_playing()

    def review_selected(self, correct):
        row = self.tree.focus()
        if not row or row not in self.moves or self.busy:
            return
        category = self.moves[row].category
        if category == "review":
            self.status.set("This file was unsure. Press B for beat or M for melody to give its actual type.")
            return
        self.label_selected(category if correct else {"beat": "loop", "loop": "beat"}[category])

    def update_label_count(self):
        kinds = [e["label"] for e in self.labels.values()]
        self.label_count.set(f"You've labelled {kinds.count('beat')} beats · {kinds.count('loop')} melodies")

    def start_learn(self):
        s = self.read_settings()
        self.stop_playing()

        def work():
            detector = drums.DrumDetector(settings=s)
            rows, _ = drums.labelled_rows(detector, cancelled=self.cancel.is_set)
            if self.cancel.is_set():
                self.events.put(("learned", None, "Learning stopped; existing model kept."))
                return
            n_beats = sum(r[1] for r in rows)
            minimum = s["minimum_training_per_class"]
            if n_beats < minimum or len(rows) - n_beats < minimum:
                self.events.put(("learned", None, f"Label at least {minimum} beats and {minimum} melodies first."))
                return
            # trained together with the altered reference copies shipped in drum_model.json, so a few labels
            # can't undo what makes the detector robust to 808 lines and crackle in melodies
            model, held_out = drums.learn([(f, lab) for _, lab, f in rows], base=drums.reference_rows())
            right = wrong = unsure = 0
            for (path, lab, _), p in zip(rows, held_out):
                category, _ = drums.classify_score(p, path.name, s)
                guess = 1 if category == "beat" else 0 if category == "loop" else None
                right += guess == lab
                wrong += guess is not None and guess != lab
                unsure += guess is None
            msg = (f"Learned from {n_beats} beats + {len(rows) - n_beats} melodies. Blind test: "
                   f"{right} right, {wrong} wrong, {unsure} unsure.")
            if wrong or self.cancel.is_set():
                self.events.put(("learned", None, msg + " Existing model kept; correct/add labels before trying again."))
            else:
                drums.save_model(model)
                self.events.put(("learned", s, msg))
        self.run_worker(work)

    def reclassify(self, s):
        """Re-sort the preview with the new model (instant: measurements are cached). Labels still win."""
        detector = drums.DrumDetector(s["beat_threshold"], s["loop_threshold"], s["loop_name_hint"], settings=s)
        for row, m in self.moves.items():
            if m.destination is not None:
                continue  # learning changes predictions, never the recorded sorted location
            m.category, m.probability = detector.classify(m.src)
            if str(m.src) in self.labels:
                m.category = self.labels[str(m.src)]["label"]
            self.refresh_row(row)

    # ---------- sorting ----------
    def start_sort(self):
        s = self.read_settings()
        self.stop_playing()
        chosen = [self.moves[r] for r in self.tree.get_children()
                  if self.use[r] and self.moves[r].destination is None]
        # Names/folders may have changed since the preview; re-apply them
        for m in chosen:
            m.new_name = sorter.new_name(m.src, s, m.music)
        if not chosen:
            self.status.set("No files are checked.")
            return

        def work():
            n = sorter.execute(chosen, s, report=lambda msg: self.events.put(("log", msg)), cancelled=self.cancel.is_set)
            self.events.put(("sorted", n, len(chosen)))
        self.run_worker(work)

    # ---------- worker messages ----------
    def run_worker(self, work):
        """Runs `work` on a background thread with the buttons locked; any error comes back as a status message."""
        self.set_busy(True)
        self.cancel.clear()

        def run():
            try:
                work()
            except Exception as error:
                self.events.put(("error", str(error)))
        threading.Thread(target=run, daemon=True).start()

    def poll_events(self):
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "progress":
                    i, n, name = data
                    self.status.set(f"Listening {i}/{n}: {name}  (first scan is slow; results are remembered)")
                elif kind == "planned":
                    self.set_busy(False)
                    self.show_plan(*data)
                elif kind == "learned":
                    s, msg = data
                    self.set_busy(False)
                    if s and self.moves:
                        self.reclassify(s)
                        msg += f" Preview updated: {self.count_summary()}."
                    self.status.set(msg)
                elif kind == "log":
                    self.status.set(data[0])
                elif kind == "sorted":
                    n, total = data
                    self.set_busy(False)
                    if not self.finish_sort():
                        continue
                    out = sorter.sorted_root(self.settings)
                    self.status.set(f"Done: {n} of {total} files sorted into {out}. "
                                    "Review them here: Space = listen, C/X = correct/incorrect, B/M = actual type.")
                elif kind == "error":
                    self.set_busy(False)
                    self.finish_sort()
                    self.status.set(f"Problem: {data[0]}")
        except queue.Empty:
            pass
        self.poll_id = self.root.after(100, self.poll_events)

    def finish_sort(self):
        updated = dict(self.labels)
        for row, move in self.moves.items():
            if move.destination is not None:
                self.use[row] = False
                if str(move.src) in updated and move.src != move.destination:
                    updated[str(move.destination)] = updated.pop(str(move.src))
                self.refresh_row(row)
        if updated != self.labels:
            try:
                drums.save_labels(updated)
            except OSError as error:
                self.status.set(f"Files sorted, but their saved labels couldn't be updated: {error}")
                return False
            self.labels = updated
            for row in self.moves:
                self.refresh_row(row)
        self.update_sort_button()
        return True

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.save_credits_btn, self.preview_btn, self.review_btn, self.learn_btn):
            button.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.update_sort_button()

    def update_sort_button(self):
        """Sort works only when nothing else is running and at least one unsorted file is ticked."""
        ready = not self.busy and any(self.use[row] and move.destination is None for row, move in self.moves.items())
        self.sort_btn.configure(state="normal" if ready else "disabled")

    def close(self):
        self.cancel.set()
        if self.busy:
            self.status.set("Stopping… wait for the current work to finish, then close.")
            return
        if getattr(self, "poll_id", None):
            self.root.after_cancel(self.poll_id)
        self.stop_playing()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
