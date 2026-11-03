"""Auto Poster window: media -> rendered preview -> explicitly selected platforms."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from app_paths import FROZEN
from multitool import credits, music
from multitool import platform as host_platform
from multitool.scrolling import scroll_area
from multitool.theme import DANGER, MUTED, apply_theme, banner, empty_state, footer, section_title
from . import autopilot, results, schedule, setup_guide
from .setup_window import SetupWizard
from .accounts import APP_KEYS, APIError, Accounts, PLATFORMS
from .publish import APPROVED_TEXTS, NAMES, Publisher, pending, post_text, text_problems
from .render import Cancelled, check_plan, create_video, posting_formats
from .storage import atomic_json, load_settings, manifest, receipt_lock, save_preferences, social_caption, video_variant


SHORT_CHOICES = {"Same as YouTube": 0, "Best 15 s": 15, "Best 30 s": 30, "Best 45 s": 45, "Best 60 s": 60}
BACKGROUND_HINTS = {
    "auto": "Nothing to choose: the beat's own cover art when it has one, otherwise a purple glow, with a moving waveform.",
    "image": "Stays on screen for the whole beat. Choose a folder instead and each beat takes the next picture in it.",
    "video": "Loops to the length of your beat; the video's own sound is replaced by your beat. A folder of videos takes turns.",
    "project": "YouTube uses this still image (or an automatic one if you leave it empty). TikTok / Reels open each track's "
               "FL Studio or Ableton project (matched by name), press play and record only the DAW window, shown over a "
               "blurred copy of itself.",
}


def patterns(extensions):
    """File-dialog filter for these extensions: "*.wav *.mp3"."""
    return " ".join("*" + extension for extension in extensions)


def music_text(path):
    """'155 · fmin' from the file name, for the track list."""
    info = music.parse_name(Path(path).name)
    return " · ".join(part for part in (str(info.bpm) if info.bpm else "", info.key.short if info.key else "") if part)


class AutoPosterApp:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.accounts = Accounts(self.settings)
        self.jobs = {}
        self.history_rows = {}
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.creator = None
        apply_theme(root)
        root.title("Auto Poster · DAW Multitool")
        root.geometry("1080x860")
        root.minsize(940, 700)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Add audio and a visual. Creating a video does not upload anything.")
        banner(root, "Auto Poster", "Turn a beat into videos, check them, then post.", kind="auto_poster")
        footer(root, self.status, wraplength=1020)
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(16, 0))
        self.composer = self.scroll_page("Create")
        self.post_page = self.scroll_page("Post")
        self.autopilot_page = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.autopilot_page, text="Autopilot")
        self.account_page = self.scroll_page("Accounts")
        self.delivery_page = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.delivery_page, text="History")
        self.schedule_page = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.schedule_page, text="Schedule")
        self.results_page = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.results_page, text="Results")
        self.build_composer()
        self.build_autopilot()
        self.build_accounts()
        self.build_history()
        self.build_schedule()
        self.build_results()
        self.refresh_accounts()
        self.poll_id = root.after(100, self.poll_events)
        self.schedule_id = root.after(3000, self.check_schedule)
        self.autopilot_id = root.after(4000, self.autopilot_tick)

    def scroll_page(self, title):
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text=title)
        return scroll_area(page, padding=(4, 8, 12, 8))

    def section(self, parent, title, hint=""):
        """A compact section whose guidance wraps with the window."""
        card = ttk.LabelFrame(parent, labelwidget=section_title(parent, title), padding=(0, 4, 0, 0), style="Section.TLabelframe")
        card.pack(fill="x", pady=(0, 26))
        if hint:
            label = ttk.Label(card, text=hint, foreground=MUTED, wraplength=800, justify="left")
            label.pack(fill="x", pady=(0, 8))
            label.bind("<Configure>", lambda event: label.configure(wraplength=max(200, event.width)))
        return card

    def counter(self, parent, read, limit):
        """A live 'n / limit' label that turns red past the platform limit."""
        label = ttk.Label(parent, foreground=MUTED, width=12, anchor="e")
        def update(*_):
            n = len(read())
            label.configure(text=f"{n} / {limit}", foreground=DANGER if n > limit else MUTED)
        return label, update

    def build_composer(self):
        frame = self.composer

        # ---- 1  Audio & picture
        media = self.section(frame, "1  Add your media", "Two versions are created: 16:9 for YouTube and 9:16 for TikTok / Reels.")
        self.visual = tk.StringVar(value=self.settings.get("visual", ""))
        self.output = tk.StringVar(value=self.settings["output_folder"])
        self.visual_kind = tk.StringVar(value=self.settings["visual_kind"] if self.settings["visual_kind"] in BACKGROUND_HINTS else "auto")
        if not host_platform.IS_MAC and self.visual_kind.get() == "project":
            self.visual_kind.set("auto")
        self.visual_hint = tk.StringVar()
        row = ttk.Frame(media)
        row.pack(fill="x", pady=(0, 3))
        ttk.Label(row, text="Background", width=16, style="Field.TLabel").pack(side="left")
        for value, text in (("auto", "Automatic"), ("image", "Still image"), ("video", "Downloaded video"),
                            ("project", "Project screen recording")):
            ttk.Radiobutton(row, text=text, value=value, variable=self.visual_kind,
                            state="disabled" if value == "project" and not host_platform.IS_MAC else "normal",
                            command=self.visual_kind_changed).pack(side="left", padx=(6, 12))
        hint = ttk.Label(media, textvariable=self.visual_hint, foreground=MUTED, wraplength=800, justify="left")
        hint.pack(fill="x", pady=(4, 8))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=max(200, event.width)))
        for label, variable, chooser in (("Background file", self.visual, self.choose_visual),
                                          ("Save videos in", self.output, self.choose_output)):
            row = ttk.Frame(media)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=16, style="Field.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(row, text="Choose…", command=chooser).pack(side="left")
            if variable is self.visual:
                ttk.Button(row, text="Folder…", command=self.choose_visual_folder).pack(side="left", padx=(6, 0))
        row = ttk.Frame(media)
        row.pack(fill="x", pady=5)
        self.add_button = ttk.Button(row, text="Add audio files…", command=self.choose_audio)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(row, text="Remove", style="Quiet.TButton", command=self.remove_selected)
        self.project_button = ttk.Button(row, text="Choose project…", command=self.choose_project)
        self.project_button.pack(side="left", padx=8)
        self.remove_button.pack(side="left")
        self.tracks = ttk.Treeview(media, columns=("track", "music", "status", "credits", "project"), show="headings", height=4,
                                   selectmode="extended")
        empty_state(self.tracks, "Add audio files or choose a DAW project to start, or let Autopilot add new beats.")
        self.tracks.heading("track", text="Audio track")
        self.tracks.heading("music", text="BPM · Key")
        self.tracks.heading("status", text="Video status")
        self.tracks.heading("credits", text="Collab credits")
        self.tracks.heading("project", text="DAW project")
        self.tracks.column("track", width=300)  # the file name gets the spare width
        self.tracks.column("music", width=110, stretch=False)
        self.tracks.column("status", width=190, stretch=False)
        self.tracks.column("credits", width=150, stretch=False)
        self.tracks.column("project", width=190, stretch=False)
        self.tracks.pack(fill="x", pady=5)

        # ---- 2  Video options
        options = self.section(frame, "2  Shape the video")
        row = ttk.Frame(options)
        row.pack(fill="x")
        self.fit = tk.StringVar(value="Fit")
        self.start, self.length = tk.StringVar(value="0"), tk.StringVar(value="0")
        ttk.Label(row, text="Frame").pack(side="left", padx=(0, 4))
        # Blur: the whole picture over a blurred, stretched copy of itself instead of black bars
        ttk.Combobox(row, textvariable=self.fit, values=["Fit", "Fill", "Blur"], state="readonly", width=7).pack(side="left", padx=(0, 14))
        for label, var in (("YouTube start (s)", self.start), ("Length (s, 0 = full)", self.length)):
            ttk.Label(row, text=label).pack(side="left", padx=4)
            ttk.Entry(row, textvariable=var, width=7).pack(side="left", padx=4)
        row = ttk.Frame(options)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="TikTok / Reels").pack(side="left", padx=(0, 4))
        short = {seconds: label for label, seconds in SHORT_CHOICES.items()}
        self.short_choice = tk.StringVar(value=short.get(self.settings["short_seconds"], "Best 30 s"))
        # the best N seconds: where the drums and 808 hit hardest, starting on the beat grid of the name's BPM
        ttk.Combobox(row, textvariable=self.short_choice, values=list(SHORT_CHOICES), state="readonly", width=15).pack(side="left", padx=(0, 14))
        self.overlay = tk.BooleanVar(value=self.settings["overlay"])
        ttk.Checkbutton(row, text="Title, BPM & key on the video", variable=self.overlay).pack(side="left", padx=(0, 12))
        self.waveform = tk.BooleanVar(value=self.settings["waveform"])
        ttk.Checkbutton(row, text="Moving waveform", variable=self.waveform).pack(side="left")
        row = ttk.Frame(options)
        row.pack(fill="x", pady=(8, 0))
        self.use_tag = tk.BooleanVar(value=False)
        self.tag_path = tk.StringVar(value=self.settings["tag_path"])
        self.tag_every = tk.StringVar(value=str(self.settings["tag_every_seconds"]))
        ttk.Checkbutton(row, text="Add my producer tag", variable=self.use_tag).pack(side="left")
        ttk.Button(row, text="Choose tag…", command=self.choose_tag).pack(side="left", padx=8)
        ttk.Label(row, text="every").pack(side="left")
        ttk.Entry(row, textvariable=self.tag_every, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="s").pack(side="left")
        ttk.Label(row, textvariable=self.tag_path, foreground=MUTED).pack(side="left", padx=8)

        # Saved credit rules still fill {credits}.
        self.credit_values = credits.load_settings()
        self.producer = tk.StringVar(value=self.credit_values["producer_name"])
        self.known_producers = tk.StringVar(value=", ".join(self.credit_values["known_producers"]))

        self.platforms = {platform: tk.BooleanVar(value=False) for platform in PLATFORMS}
        self.account_labels = {platform: tk.StringVar(value="Not connected") for platform in PLATFORMS}

        # ---- 4  YouTube
        youtube = self.section(self.post_page, "YouTube", "16:9 video · Private by default")
        row = ttk.Frame(youtube)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Post to YouTube", variable=self.platforms["youtube"]).pack(side="left")
        ttk.Label(row, textvariable=self.account_labels["youtube"], foreground=MUTED).pack(side="left", padx=8)
        row = ttk.Frame(youtube)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Title", width=12, style="Field.TLabel").pack(side="left")
        self.title = tk.StringVar(value=self.settings["title_template"])
        ttk.Entry(row, textvariable=self.title).pack(side="left", fill="x", expand=True, padx=6)
        title_count, update_title = self.counter(row, self.title.get, self.settings["youtube_title_limit"])
        title_count.pack(side="left")
        self.title.trace_add("write", update_title)
        ttk.Label(youtube, text="{title} = the beat's name · {bpm} {key} = from the file name · {credits} = prod. credits · "
                               "{name} = the whole file name", foreground=MUTED).pack(anchor="w", padx=(96, 0))
        row = ttk.Frame(youtube)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Description", width=12, style="Field.TLabel").pack(side="left", anchor="n")
        self.caption = tk.Text(row, width=1, height=4, wrap="word", font="TkDefaultFont")
        self.caption.pack(side="left", fill="x", expand=True, padx=6)
        desc_count, update_desc = self.counter(row, lambda: self.caption.get("1.0", "end-1c"), self.settings["youtube_caption_limit"])
        desc_count.pack(side="left", anchor="n")
        self.caption.bind("<KeyRelease>", update_desc)
        row = ttk.Frame(youtube)
        row.pack(fill="x", pady=(8, 0))
        self.youtube_privacy = tk.StringVar(value="private")
        self.kids = tk.BooleanVar(value=False)
        ttk.Label(row, text="Visibility", width=12, style="Field.TLabel").pack(side="left")
        ttk.Combobox(row, textvariable=self.youtube_privacy, values=["private", "unlisted", "public"],
                     state="readonly", width=10).pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Made for kids", variable=self.kids).pack(side="left", padx=12)

        # ---- 5  TikTok & Instagram Reels
        social = self.section(self.post_page, "TikTok & Instagram", "9:16 video · Shared caption and hashtags")
        row = ttk.Frame(social)
        row.pack(fill="x")
        for platform, label in (("tiktok", "Post to TikTok"), ("instagram", "Post to Instagram Reels")):
            ttk.Checkbutton(row, text=label, variable=self.platforms[platform], command=self.target_changed).pack(side="left")
            ttk.Label(row, textvariable=self.account_labels[platform], foreground=MUTED).pack(side="left", padx=(6, 20))
        row = ttk.Frame(social)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Description", width=12, style="Field.TLabel").pack(side="left", anchor="n")
        self.social_text = tk.Text(row, width=1, height=3, wrap="word", font="TkDefaultFont")
        self.social_text.pack(side="left", fill="x", expand=True, padx=6)
        social_count, update_social = self.counter(row, self.social_preview, self.settings["caption_limit"])
        social_count.pack(side="left", anchor="n")
        self.social_text.bind("<KeyRelease>", update_social)
        row = ttk.Frame(social)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Hashtags", width=12, style="Field.TLabel").pack(side="left")
        self.hashtags = tk.StringVar(value=self.settings["default_hashtags"])
        ttk.Entry(row, textvariable=self.hashtags).pack(side="left", fill="x", expand=True, padx=6)
        self.hashtags.trace_add("write", update_social)
        ttk.Label(social, text="Type tags with or without # (e.g. typebeat rage #producer). Saved for next time. "
                               "{name} and {credits} work in the description too.", foreground=MUTED,
                  wraplength=740, justify="left").pack(anchor="w", padx=(96, 0))
        row = ttk.Frame(social)
        row.pack(fill="x", pady=(10, 0))
        self.tiktok_privacy = tk.StringVar()
        ttk.Label(row, text="TikTok visibility", style="Field.TLabel").pack(side="left")
        self.tiktok_combo = ttk.Combobox(row, textvariable=self.tiktok_privacy, values=[], state="readonly", width=26)
        self.tiktok_combo.pack(side="left", padx=6)
        self.creator_label = tk.StringVar(value="TikTok: connect an account to load its options.")
        ttk.Label(row, textvariable=self.creator_label, foreground=MUTED).pack(side="left", padx=8)
        row = ttk.Frame(social)
        row.pack(fill="x", pady=4)
        self.interactions = {}
        self.interaction_buttons = {}
        for key, title in (("comment", "Allow comments"), ("duet", "Allow Duet"), ("stitch", "Allow Stitch")):
            self.interactions[key] = tk.BooleanVar(value=False)
            button = ttk.Checkbutton(row, text=title, variable=self.interactions[key], state="disabled")
            button.pack(side="left", padx=(0, 12))
            self.interaction_buttons[key] = button
        self.own_brand, self.branded, self.ai = (tk.BooleanVar(value=False) for _ in range(3))
        for title, variable in (("Promotes my brand", self.own_brand), ("Paid partnership", self.branded), ("AI-generated", self.ai)):
            ttk.Checkbutton(row, text=title, variable=variable, command=self.disclosure_changed).pack(side="left", padx=6)
        self.disclosure = tk.StringVar(value="Instagram Reels publishes to your profile; YouTube visibility applies only to YouTube.")
        ttk.Label(social, textvariable=self.disclosure, wraplength=940, foreground=MUTED).pack(anchor="w")
        self.tiktok_consent = tk.BooleanVar(value=False)
        ttk.Checkbutton(social, text="For TikTok, I agree to its Music Usage Confirmation and, for paid partnerships, its Branded Content Policy.",
                        variable=self.tiktok_consent).pack(anchor="w")

        # ---- Publish
        publish = self.section(frame, "3  Review & share", "Pick platforms and captions in the Post tab before publishing.")
        actions = ttk.Frame(publish)
        actions.pack(fill="x")
        self.plan_button = ttk.Button(actions, text="Check plan", command=self.start_plan)
        self.create_button = ttk.Button(actions, text="Create videos", style="Primary.TButton", command=self.start_render)
        self.preview_button = ttk.Button(actions, text="Preview 16:9", command=self.preview_selected)
        self.vertical_preview_button = ttk.Button(actions, text="Preview 9:16", command=lambda: self.preview_selected("tiktok"))
        self.stop_button = ttk.Button(actions, text="Stop", command=self.cancel.set, state="disabled")
        for button in (self.create_button, self.plan_button, self.preview_button, self.vertical_preview_button, self.stop_button):
            button.pack(side="left", padx=(0, 8))
        self.reviewed = tk.BooleanVar(value=False)
        ttk.Checkbutton(publish, text="I previewed both videos and checked the selected accounts.",
                        variable=self.reviewed).pack(anchor="w", pady=(8, 4))
        delivery = ttk.Frame(publish)
        delivery.pack(fill="x", pady=(4, 10))
        ttk.Button(delivery, text="Captions & platforms →", style="Quiet.TButton", command=lambda: self.tabs.select(1)).pack(side="left")
        self.publish_button = ttk.Button(delivery, text="Publish", style="Primary.TButton", command=self.start_publish)
        self.publish_button.pack(side="right")
        self.schedule_button = ttk.Button(delivery, text="Schedule…", command=self.start_schedule)
        self.schedule_button.pack(side="right", padx=8)
        self.progress = ttk.Progressbar(publish, maximum=100)
        self.progress.pack(fill="x")
        update_title()
        update_desc()
        update_social()
        self.visual_kind_changed()

    def social_preview(self):
        """The exact TikTok / Instagram caption (before {name}/{credits} are filled per track)."""
        return social_caption(self.social_text.get("1.0", "end-1c"), self.hashtags.get())

    def build_autopilot(self):
        page = self.autopilot_page
        ttk.Label(page, text="Autopilot", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(page, text="Export a beat into the watched folder and Auto Poster does the rest: it skips melody loops, "
                  "makes both videos with your Create and Post settings and lets you know. Nothing is posted until you "
                  "approve it: review the new videos, then Schedule… offers your next free posting times. Autopilot "
                  "works while this window is open; to keep it working when it's closed, turn on Post while the app is "
                  "closed… on the Schedule tab.",
                  style="Muted.TLabel", wraplength=920, justify="left").pack(anchor="w", pady=(4, 14))
        self.autopilot_folder = tk.StringVar(value=self.settings["autopilot_folder"])
        self.post_times = tk.StringVar(value=", ".join(self.settings["post_times"]))
        for label, variable, extra in (("Watch folder", self.autopilot_folder, None),
                                       ("Posting times", self.post_times, "daily, 24 h, e.g. 12:00, 18:00")):
            row = ttk.Frame(page)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=16, style="Field.TLabel").pack(side="left")
            if extra:
                ttk.Entry(row, textvariable=variable, width=24).pack(side="left", padx=6)
                ttk.Label(row, text=extra, style="Muted.TLabel").pack(side="left", padx=6)
            else:
                ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True, padx=6)
                ttk.Button(row, text="Choose…", command=self.choose_autopilot_folder).pack(side="left")
        self.autopilot_on = tk.BooleanVar(value=self.settings["autopilot_enabled"])
        self.skip_loops = tk.BooleanVar(value=self.settings["autopilot_options"].get("skip_loops", True))
        ttk.Checkbutton(page, text="Autopilot on: make videos for new beats in this folder",
                        variable=self.autopilot_on, command=self.autopilot_toggled).pack(anchor="w", pady=(12, 2))
        ttk.Checkbutton(page, text="Skip melody loops (the drum detector decides; files it's unsure about still get videos)",
                        variable=self.skip_loops, command=self.save_autopilot_settings).pack(anchor="w", pady=2)
        self.autopilot_list = ttk.Treeview(page, columns=("when", "file", "what"), show="headings", height=7)
        empty_state(self.autopilot_list, "New beats appear here as Autopilot handles them.")
        for key, title, width in (("when", "When", 150), ("file", "File", 300), ("what", "What happened", 420)):
            self.autopilot_list.heading(key, text=title)
            self.autopilot_list.column(key, width=width, stretch=key == "what")
        self.autopilot_list.pack(fill="both", expand=True, pady=(12, 0))
        row = ttk.Frame(page)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="Review ready videos", style="Primary.TButton", command=self.review_ready).pack(side="left")
        ttk.Button(row, text="Check now", command=lambda: self.autopilot_tick(by_hand=True)).pack(side="left", padx=8)
        self.restore_autopilot_rows()
        self.refresh_autopilot()

    def choose_autopilot_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="The folder your DAW exports finished beats to")
        if folder:
            self.autopilot_folder.set(folder)
            if self.autopilot_on.get():
                autopilot.turn_on(folder)  # a new folder starts fresh: its existing files are the backlog
            self.save_autopilot_settings()

    def save_autopilot_settings(self):
        """Folder, on/off, posting times and the skip choice, saved for the next session and the helper."""
        times = [part.strip() for part in self.post_times.get().replace(";", ",").split(",") if part.strip()]
        values = {"autopilot_enabled": self.autopilot_on.get(), "autopilot_folder": self.autopilot_folder.get().strip(),
                  "post_times": times or ["18:00"],
                  "autopilot_options": {**self.settings.get("autopilot_options", {}), "skip_loops": self.skip_loops.get()}}
        changes = {key: value for key, value in values.items() if self.settings.get(key) != value}
        if changes:
            self.settings.update(changes)
            save_preferences(changes)

    def autopilot_toggled(self):
        folder = self.autopilot_folder.get().strip()
        if self.autopilot_on.get():
            if not folder or not Path(folder).expanduser().is_dir():
                self.autopilot_on.set(False)
                self.status.set("Choose the folder your DAW exports beats to first (Watch folder).")
                return
            try:
                self.save_choices(self.options())  # Autopilot and the helper make videos with these
            except (ValueError, OSError) as error:
                self.autopilot_on.set(False)
                self.status.set(f"Autopilot needs the Create settings first: {error}")
                return
            autopilot.turn_on(folder)
            self.status.set(f"Autopilot on: beats exported to {Path(folder).name} from now on get their videos made.")
        else:
            self.status.set("Autopilot off. Videos it already made stay in the list.")
        self.save_autopilot_settings()
        self.refresh_autopilot()

    def refresh_autopilot(self):
        self.autopilot_list.delete(*self.autopilot_list.get_children())
        for when, name, what in autopilot.activity(autopilot.load_ledger()):
            self.autopilot_list.insert("", "end", values=(when, name, what))

    def restore_autopilot_rows(self):
        """Videos Autopilot made earlier (or the helper made while the app was closed) that haven't gone out yet."""
        sent = {str(Path(entry["receipt"]).resolve()) for entry in schedule.load()}
        for entry in autopilot.load_ledger()["files"].values():
            receipt = entry.get("receipt")
            if entry.get("state") != "ready" or not receipt or str(Path(receipt).resolve()) in sent:
                continue
            try:
                record = manifest(receipt)
            except (OSError, ValueError):
                continue
            if record.get("state") != "ready" or record.get("delivery"):
                continue
            audio = Path(record["audio"])
            row = self.tracks.insert("", "end", values=(audio.name, music_text(audio), "16:9 + 9:16 ready to preview"))
            self.jobs[row] = {"audio": audio, "receipt": Path(receipt), "autopilot": True}
        if self.jobs:
            self.refresh_credits(self.credit_values)

    def review_ready(self):
        rows = [row for row, job in self.jobs.items() if job.get("autopilot") and job.get("receipt")]
        if not rows:
            self.status.set("No Autopilot videos are waiting. New ones appear on the Create tab as beats are exported.")
            return
        self.tabs.select(0)
        self.tracks.selection_set(rows)
        self.status.set(f"{len(rows)} new video pair(s) selected. Preview them, tick the review box, choose platforms on "
                        "the Post tab, then Schedule….")

    def autopilot_tick(self, by_hand=False):
        """Every autopilot_check_seconds: new finished beats in the folder go to the worker, one batch at a time."""
        self.autopilot_id = self.root.after(self.settings["autopilot_check_seconds"] * 1000, self.autopilot_tick)
        if not self.autopilot_on.get():
            if by_hand:
                self.status.set("Turn Autopilot on first.")
            return
        if self.busy:
            if by_hand:
                self.status.set("Auto Poster is busy. Autopilot checks again when this finishes.")
            return
        folder, ledger = self.autopilot_folder.get().strip(), autopilot.load_ledger()
        if folder and not autopilot.watching(folder, ledger):  # typed a different folder: start fresh there
            autopilot.turn_on(folder)
            self.save_autopilot_settings()
            self.status.set(f"Autopilot now watches {Path(folder).name}: beats exported there from now on get videos.")
            return
        files = autopilot.new_files(folder, self.settings, ledger)
        if not files:
            if by_hand:
                self.status.set("Autopilot: no new beats in the folder.")
            return
        try:
            options = {**self.options(), "skip_loops": self.skip_loops.get()}
            autopilot.check_options(options)  # a missing setting never uses up a new beat
            credit_settings = self.credit_settings()
        except (ValueError, OSError) as error:
            self.status.set(f"Autopilot is waiting: {error}")
            return
        if options["visual_kind"] == "project":
            options["visual_kind"] = "auto"  # recording a DAW needs someone at the Mac
        rows = []
        for path in files:
            row = self.tracks.insert("", "end", values=(path.name, music_text(path), "Autopilot: checking…"))
            self.jobs[row] = {"audio": path, "receipt": None, "autopilot": True}
            rows.append((row, path))
        self.refresh_credits(credit_settings)
        def work():
            drums, ready = None, 0
            for row, path in rows:
                if self.cancel.is_set():
                    break
                key = autopilot.claim(path)
                if not key:
                    self.events.put(("drop", row))  # the background helper is already on it
                    continue
                try:
                    if options["skip_loops"]:
                        drums = drums or autopilot.detector()
                    entry = autopilot.process(path, options, self.settings, credit_settings, cancelled=self.cancel.is_set,
                                              progress=lambda pct, r=row: self.events.put(("progress", r, pct)), drums=drums,
                                              report=lambda text, r=row: self.events.put(("row", r, text)))
                except Cancelled:
                    entry = {"state": "failed", "detail": "stopped before it finished"}
                except Exception as error:
                    entry = {"state": "failed", "detail": str(error)}
                autopilot.remember(path, entry, key)
                if entry["state"] == "ready":
                    ready += 1
                    self.events.put(("rendered", row, Path(entry["receipt"])))
                elif entry["state"] == "skipped":
                    self.events.put(("drop", row))
                else:
                    self.events.put(("row", row, f"Autopilot: {entry['detail']}"))
            if ready:
                try:
                    schedule.notify(f"{ready} new beat video pair{'s' if ready > 1 else ''} ready to review in Auto Poster.")
                except (OSError, subprocess.SubprocessError):
                    pass
            self.events.put(("autopilot", ready))
        self.status.set(f"Autopilot: {len(files)} new beat(s) found.")
        self.run_worker(work)

    def build_accounts(self):
        page = self.account_page
        ttk.Label(page, text="Connect accounts", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(page, text="Connect once and pick the account in your browser. The first time, Connect walks you "
                  "through a short setup. Logins stay in your computer's secure credential store.", style="Muted.TLabel",
                  wraplength=920).pack(anchor="w", pady=(4, 22))
        self.connect_buttons, self.key_labels, self.setup_windows = [], {}, {}
        for platform, hint in (("youtube", "Pick your Google account, then the channel."),
                               ("instagram", "Log in with Facebook and tick the Facebook Page and its Instagram "
                                             "professional account."),
                               ("tiktok", "Log in to the TikTok account to post to.")):
            frame = ttk.Frame(page, style="Card.TFrame", padding=(22, 18))  # one rounded card per platform
            frame.pack(fill="x", pady=(0, 12))
            section_title(frame, NAMES[platform], card=True).pack(anchor="w")
            ttk.Label(frame, text=hint, wraplength=880, style="CardMuted.TLabel").pack(anchor="w", pady=(4, 0))
            self.connect_row(frame, platform)
            self.key_labels[platform] = tk.StringVar()
            note = ttk.Label(frame, textvariable=self.key_labels[platform], style="CardMuted.TLabel", wraplength=880)
            def show_note(*_, label=note, var=self.key_labels[platform]):  # an empty line takes no space
                label.pack(anchor="w", pady=(8, 0)) if var.get() else label.pack_forget()
            self.key_labels[platform].trace_add("write", show_note)
        ttk.Button(page, text="Platform docs and TikTok terms ↗", style="Quiet.TButton", command=self.show_guides).pack(anchor="w")

    def connect_row(self, parent, platform):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(14, 0))
        button = ttk.Button(row, text="Connect " + NAMES[platform], style="Card.Primary.TButton",
                            command=lambda: self.connect(platform))
        button.pack(side="left")
        self.connect_buttons.append(button)
        ttk.Label(row, textvariable=self.account_labels[platform], style="CardMuted.TLabel").pack(side="left", padx=14)
        ttk.Button(row, text="Disconnect", style="Card.Quiet.TButton", command=lambda: self.disconnect(platform)).pack(side="right")
        ttk.Button(row, text="Setup steps", style="Card.Quiet.TButton", command=lambda: self.show_setup(platform)).pack(side="right", padx=4)

    def show_setup(self, platform):
        """The one-time developer-app setup as a small step-by-step window (setup_window.py)."""
        wizard = self.setup_windows.get(platform)
        if wizard is not None and wizard.window.winfo_exists():
            wizard.window.deiconify()
            wizard.window.lift()
            return wizard
        self.setup_windows[platform] = SetupWizard(self, platform)
        return self.setup_windows[platform]

    def remember_setup_step(self, platform, index):
        """So the setup window reopens where the user left off, even after the app restarts."""
        steps = {**self.settings.get("setup_steps", {}), platform: index}
        if steps != self.settings.get("setup_steps"):
            self.settings["setup_steps"] = steps
            save_preferences({"setup_steps": steps})

    def come_forward(self):
        """After the browser sign-in, puts DAW Multitool back in front instead of leaving the user in the browser."""
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))
        if FROZEN and host_platform.IS_MAC:  # the built Mac app: also make it the active app
            subprocess.Popen(["osascript", "-e", 'tell application id "com.dawmultitool.app" to activate'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def copy_text(self, text, note):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        note.set(f"Copied: {text}")

    def setup_connect(self, platform, fields, note):
        typed = {key: variable.get().strip() for key, variable in fields.items()}
        try:
            saved = self.accounts.app_keys(platform)
        except Exception:
            saved = {}
        missing = [label for key, label, _ in setup_guide.FIELDS[platform]
                   if key in APP_KEYS[platform][:2] and not (typed.get(key) or saved.get(key))]
        if missing:
            note.set(f"Copy your {' and '.join(missing)} first (Back to the step that shows them).")
            return
        problem = setup_guide.key_problem(platform, typed)
        if problem:
            note.set(problem)
            return
        if self.busy:
            note.set("Auto Poster is busy. Wait for it to finish, or press Stop, then try again.")
            return
        wizard = self.setup_windows.get(platform)
        if wizard is not None:
            wizard.close()
        self.connect(platform, typed)

    def build_history(self):
        ttk.Label(self.delivery_page, text="Delivery receipts", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.delivery_page, text="Saved posts, processing status and links. Unfinished uploads are checked automatically. "
                  "A post is marked published when the platform confirms it.",
                  wraplength=920).pack(anchor="w", pady=8)
        self.history = ttk.Treeview(self.delivery_page, columns=("video", "platform", "format", "state", "detail"), show="headings")
        empty_state(self.history, "Videos you post show up here, with a link to each one.")
        for key, title, width in (("video", "Video", 200), ("platform", "Platform", 100), ("format", "Version", 150),
                                  ("state", "Status", 100), ("detail", "Link / details", 330)):
            self.history.heading(key, text=title)
            self.history.column(key, width=width)
        self.history.pack(fill="both", expand=True)
        row = ttk.Frame(self.delivery_page)
        row.pack(fill="x", pady=10)
        ttk.Button(row, text="Load saved render…", command=self.load_receipt).pack(side="left")
        ttk.Button(row, text="Refresh receipts", command=self.refresh_history).pack(side="left", padx=8)

    def show_guides(self):
        window = tk.Toplevel(self.root)
        window.title("Auto Poster · Platform docs and TikTok terms")
        window.geometry("760x480")
        for title, url in (("YouTube desktop OAuth setup", "https://developers.google.com/identity/protocols/oauth2/native-app"),
                           ("Meta: Facebook Login setup", "https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow"),
                           ("Instagram publishing setup", "https://developers.facebook.com/docs/instagram-platform/content-publishing/"),
                           ("TikTok Desktop Login Kit", "https://developers.tiktok.com/docs/en/login-kit-desktop"),
                           ("TikTok Direct Post requirements", "https://developers.tiktok.com/docs/en/content-sharing-guidelines"),
                           ("TikTok Music Usage Confirmation", "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"),
                           ("TikTok Branded Content Policy", "https://www.tiktok.com/legal/page/global/bc-policy/en")):
            ttk.Button(window, text=title, command=lambda u=url: webbrowser.open(u)).pack(fill="x", padx=24, pady=10)

    def visual_kind_of(self, path):
        suffix = Path(path).suffix.lower()
        return "image" if suffix in self.settings["image_extensions"] else "video" if suffix in self.settings["video_extensions"] else None

    def file_kind(self):
        """Project mode still uses an image file: it is the YouTube version's background."""
        return "video" if self.visual_kind.get() == "video" else "image"

    def visual_kind_changed(self):
        kind = self.visual_kind.get()
        self.visual_hint.set(BACKGROUND_HINTS[kind])
        current = self.visual.get().strip()
        if current and kind != "auto" and not Path(current).expanduser().is_dir() and self.visual_kind_of(current) != self.file_kind():
            self.visual.set("")  # the chosen file is the other kind; pick a new one
            self.reviewed.set(False)
        self.refresh_projects()

    def refresh_projects(self):
        """Project mode: match each track to its DAW project by name; a project picked by hand is kept."""
        project_mode = self.visual_kind.get() == "project"
        missing = [job for job in self.jobs.values() if not job.get("project")]
        if project_mode and missing:
            from .project import find_project, project_index
            index = project_index(self.settings)
            for job in missing:
                job["project"] = find_project(job["audio"], index)
        for row, job in self.jobs.items():
            project = job.get("project")
            self.tracks.set(row, "project", project.name if project else "Not found: Choose project…" if project_mode else "")

    def choose_project(self):
        """Pick a track's FL Studio / Ableton project, then open it and record it playing from the song start.
        The recording stays with the track, so Create videos uses it instead of recording again."""
        if self.busy:
            return
        if not self.jobs:
            self.choose_audio()  # the video needs the beat's audio too
        rows = list(self.tracks.selection()) or (list(self.jobs) if len(self.jobs) == 1 else [])
        if not rows:
            if self.jobs:
                messagebox.showinfo("Auto Poster · Choose project", "Click the track in the list first, then "
                                    "Choose project…", parent=self.root)
            return
        for row in rows:
            if not self.ask_project(row):
                return
        if self.visual_kind.get() != "project":
            self.visual_kind.set("project")  # a chosen project means the TikTok / Reels video shows it playing
            self.visual_kind_changed()
        if self.record_warning():
            self.record_projects(rows)

    def record_projects(self, rows):
        """Opens each project and records it playing the whole beat from the song start."""
        if not self.output.get().strip():
            self.status.set("Choose a folder for the videos first (Save videos in).")
            return
        folder = Path(self.output.get()).expanduser().resolve() / "Screen recordings"
        jobs = [(row, self.jobs[row]["project"], self.jobs[row]["audio"]) for row in rows]
        def work():
            from .project import record_project
            from .render import media_info
            recorded, problem, notes = 0, "", []
            for row, project, audio in jobs:
                if self.cancel.is_set():
                    break
                self.events.put(("row", row, "Reading the beat's length…"))
                try:
                    seconds = media_info(audio, self.settings)["duration"]
                    recording, offset = record_project(project, seconds, folder, self.settings, self.cancel.is_set,
                                                       self.reporter(row), warn=notes.append)
                except Cancelled:
                    self.events.put(("row", row, "Recording stopped; project unchanged"))
                    break
                except Exception as error:  # e.g. Screen Recording / Accessibility not allowed yet
                    problem = str(error)
                    self.events.put(("row", row, f"Recording failed: {error}"))
                    continue
                self.jobs[row]["recording"] = {"project": project, "file": recording, "offset": offset, "seconds": seconds}
                self.events.put(("row", row, "Project recorded ✓"))
                recorded += 1
            if recorded:
                self.events.put(("recorded", f"Recorded {recorded} project(s) from the start. Click Create videos to make "
                                             "the videos: they use these recordings.", "\n".join(dict.fromkeys(notes))))
            else:
                self.events.put(("recorded", "Nothing was recorded.", "\n".join([problem, *dict.fromkeys(notes)]).strip()))
        self.run_worker(work)

    def reporter(self, row):
        """Shows recording progress on the track's row and in the status bar (safe from a worker thread)."""
        def report(text):
            self.events.put(("row", row, text))
            self.events.put(("status", text))
        return report

    def has_recording(self, row):
        saved, job = self.jobs[row].get("recording"), self.jobs[row]
        return bool(saved and saved["project"] == job.get("project") and Path(saved["file"]).is_file())

    def ask_project(self, row):
        """File dialog for one track's project; False if the user cancels."""
        path = filedialog.askopenfilename(parent=self.root, title=f"Choose the project for {self.jobs[row]['audio'].name}",
                                          filetypes=[("FL Studio / Ableton projects", patterns(self.settings["daws"]))])
        if not path:
            return False
        self.jobs[row]["project"] = Path(path)
        self.jobs[row].pop("recording", None)  # an earlier recording was of another project
        self.tracks.set(row, "project", Path(path).name)
        self.reviewed.set(False)
        return True

    def confirm_projects(self):
        """Every selected track needs a project (ask for missing ones), then a heads-up before the DAW takes over."""
        self.refresh_projects()
        for row in self.selected_rows():
            if not self.jobs[row].get("project"):
                name = self.jobs[row]["audio"].name
                if not (messagebox.askokcancel("Auto Poster · Project not found",
                        f"Couldn't find the FL Studio or Ableton project for “{name}”.\n\nChoose it now, or Cancel to stop.",
                        parent=self.root) and self.ask_project(row)):
                    self.status.set(f"Cancelled: no project chosen for {name}. Nothing was recorded or rendered.")
                    return False
        if all(self.has_recording(row) for row in self.selected_rows()):
            return True  # already recorded with Choose project…: nothing will take over the DAW
        return self.record_warning()

    def record_warning(self):
        """The heads-up before Auto Poster takes over the DAW."""
        return messagebox.askokcancel("Auto Poster · Record projects",
            "Auto Poster will open each project in FL Studio or Ableton, press play and record only that window "
            "for the TikTok / Reels version.\n\n• Save your work in your DAW first.\n"
            "• FL Studio: leave it in SONG mode (it keeps the last mode you used).\n"
            "• Don't touch the mouse or keyboard while it records.\n"
            "• Turn on Do Not Disturb so notifications stay out of the video.\n\n"
            "Auto Poster never saves or changes your project files.", parent=self.root)

    def choose_visual(self):
        if self.busy:
            return
        image = self.file_kind() == "image"
        extensions = self.settings["image_extensions"] if image else self.settings["video_extensions"]
        path = filedialog.askopenfilename(parent=self.root, title="Choose a still image" if image else "Choose a downloaded video",
                                          filetypes=[("Images" if image else "Videos", patterns(extensions))])
        if path:
            self.visual.set(path)
            if self.visual_kind_of(path) != self.file_kind():
                self.visual_kind.set(self.visual_kind_of(path) or self.visual_kind.get())
            self.visual_kind_changed()
            self.reviewed.set(False)

    def choose_visual_folder(self):
        """A folder of backgrounds: each beat takes the next picture (or video) in it, round and round."""
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self.root, title="Choose a folder of backgrounds (each beat takes the next one)")
        if folder:
            self.visual.set(folder)
            if self.visual_kind.get() not in ("image", "video"):
                self.visual_kind.set("image")
            self.visual_kind_changed()
            self.reviewed.set(False)

    def choose_tag(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose your producer tag (audio)",
                                          filetypes=[("Audio", patterns(self.settings["audio_extensions"]))])
        if path:
            self.tag_path.set(path)
            self.use_tag.set(True)

    def tag_request(self):
        """The tag file for this render, or None. Saves the choice for next time."""
        if not self.use_tag.get():
            return None
        if not self.tag_path.get():
            raise ValueError("Choose your producer tag file, or untick “Add my producer tag”.")
        every = float(self.tag_every.get())
        if every <= 0:
            raise ValueError("The tag interval must be more than 0 seconds.")
        changes = {key: value for key, value in (("tag_path", self.tag_path.get()), ("tag_every_seconds", every))
                   if self.settings.get(key) != value}
        if changes:  # Autopilot asks for the options often; only real changes are written
            self.settings.update(changes)
            save_preferences(changes)
        return Path(self.tag_path.get()).expanduser().resolve()

    def choose_output(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, title="Save rendered videos in")
        if path:
            self.output.set(path)

    def choose_audio(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title="Choose audio files",
            filetypes=[("Audio files", patterns(self.settings["audio_extensions"])), ("All files", "*")])
        existing = {str(job["audio"]) for job in self.jobs.values()}
        for path in paths:
            if path in existing:
                continue
            row = self.tracks.insert("", "end", values=(Path(path).name, music_text(path), "Waiting"))
            self.jobs[row] = {"audio": Path(path), "receipt": None}
        self.refresh_credits(self.credit_settings())
        self.refresh_projects()
        self.reviewed.set(False)

    def remove_selected(self):
        if not self.busy:
            for row in self.tracks.selection():
                self.jobs.pop(row, None)
                self.tracks.delete(row)
            self.refresh_history()

    def selected_rows(self):
        return list(self.tracks.selection() or self.tracks.get_children())

    def options(self):
        """The Create and Post choices as plain values (main thread). Building each video from them happens on
        the worker thread, because finding the best part and a missing BPM reads the audio."""
        try:
            start, length = float(self.start.get() or 0), float(self.length.get() or 0)
        except ValueError:
            raise ValueError("YouTube start and length must be numbers of seconds.") from None
        if not self.output.get().strip():
            raise ValueError("Choose a folder for the rendered videos.")
        tag = self.tag_request()
        return {"visual_kind": self.visual_kind.get(), "visual": self.visual.get().strip(), "fit": self.fit.get(),
                "start": start, "length": length, "short_seconds": SHORT_CHOICES.get(self.short_choice.get(), 30),
                "overlay": self.overlay.get(), "waveform": self.waveform.get(), "use_tag": tag is not None,
                "tag": str(tag) if tag else "", "output": self.output.get().strip(), "title": self.title.get(),
                "caption": self.caption.get("1.0", "end-1c"), "social_description": self.social_text.get("1.0", "end-1c"),
                "hashtags": self.hashtags.get(), "skip_loops": self.skip_loops.get()}

    def save_choices(self, options):
        """Remembers the choices for next time, and for Autopilot and the background helper."""
        values = {"visual_kind": options["visual_kind"], "visual": options["visual"], "short_seconds": options["short_seconds"],
                  "overlay": options["overlay"], "waveform": options["waveform"], "title_template": options["title"],
                  "output_folder": options["output"], "autopilot_options": options}
        changes = {key: value for key, value in values.items() if self.settings.get(key) != value}
        if changes:
            self.settings.update(changes)
            save_preferences(changes)

    def render_jobs(self, take_turns=True):
        """(options, credit settings, [(row, audio, background)]) for the selected rows, after the checks that need
        no audio. With a folder of backgrounds, each track takes the next one (take_turns=False only looks:
        Check plan shouldn't use up turns)."""
        if not self.jobs:
            raise ValueError("Add at least one audio file.")
        options = self.options()
        kind, chosen = options["visual_kind"], Path(options["visual"]).expanduser() if options["visual"] else None
        if kind in ("image", "video") and not chosen:
            raise ValueError("Choose a background image or video (or a folder of them), or pick Automatic.")
        if chosen and kind != "auto" and not chosen.is_dir() and self.visual_kind_of(chosen) != self.file_kind():
            wanted = "a still image" if self.file_kind() == "image" else "a downloaded video"
            raise ValueError(f"Background is set to {wanted}, but that file isn't one. Choose the file again.")
        jobs = []
        for row in self.selected_rows():
            visual = None
            if chosen and chosen.is_dir() and kind != "auto":
                visual = autopilot.next_visual(chosen, "video" if kind == "video" else "image", self.settings,
                                               remember_choice=take_turns)
            jobs.append((row, self.jobs[row]["audio"], visual))
        self.save_choices(options)
        return options, self.credit_settings(), jobs

    def request_for(self, row, audio, options, credit_settings, visual):
        """Worker thread: one track's full request, with its best part for TikTok/Reels."""
        request = autopilot.build_request(audio, options, self.settings, credit_settings, visual=visual)
        if options["visual_kind"] == "project":
            request = replace(request, project=self.jobs[row].get("project"))
        return autopilot.with_short_version(request, options, self.settings)

    def credit_settings(self):
        values = {"producer_name": self.producer.get().strip(), "known_producers": credits.producer_names(self.known_producers.get())}
        changes = {key: value for key, value in values.items() if value != self.credit_values[key]}
        self.credit_values = credits.save_settings(changes)
        self.producer.set(self.credit_values["producer_name"])
        self.known_producers.set(", ".join(self.credit_values["known_producers"]))
        return self.credit_values

    def refresh_credits(self, settings):
        for row, job in self.jobs.items():
            names = credits.collaborators(job["audio"].name, settings)
            self.tracks.set(row, "credits", ", ".join(names) if names else "Solo")

    def with_project_recording(self, row, request):
        """Worker-thread step in project mode: record the DAW playing from the song start to the end of this clip,
        then line the recording up with the clip so the playhead matches the audio."""
        if not request.project:
            return request
        from .project import record_project
        formats = posting_formats(self.settings)
        plan = check_plan(request, self.settings, formats=formats)
        end = request.start + plan["seconds"]
        if request.short_start is not None:  # the TikTok/Reels part may run past the YouTube part
            end = max(end, request.short_start + plan.get("seconds_by_format", {}).get(formats[-1], 0))
        saved = self.jobs[row].get("recording")
        if self.has_recording(row) and saved["seconds"] >= end - 0.05:  # made by Choose project…
            return replace(request, vertical_visual=saved["file"], vertical_start=saved["offset"] + request.start)
        self.events.put(("row", row, "Recording the project…"))
        recording, offset = record_project(request.project, end, request.output / "Screen recordings",
                                           self.settings, self.cancel.is_set, self.reporter(row),
                                           warn=lambda text: self.events.put(("warning", text)))
        self.events.put(("row", row, "Creating videos…"))
        return replace(request, vertical_visual=recording, vertical_start=offset + request.start)

    def start_plan(self):
        try:
            self.refresh_projects()
            options, credit_settings, jobs = self.render_jobs(take_turns=False)
        except (ValueError, OSError) as error:
            self.status.set(str(error))
            return
        def work():
            plans = []
            for row, audio, visual in jobs:
                request = self.request_for(row, audio, options, credit_settings, visual)
                plan = check_plan(request, self.settings, formats=posting_formats(self.settings), measure_loudness=True)
                if options["visual_kind"] == "project" and not request.project:
                    plan["warnings"].append("no DAW project found: you'll be asked to choose it (or cancel) before rendering")
                plans.append(plan)
            self.events.put(("plan", plans))
        self.run_worker(work)

    def start_render(self):
        if self.busy or (self.visual_kind.get() == "project" and not self.confirm_projects()):
            return
        try:
            options, credit_settings, jobs = self.render_jobs()
        except (ValueError, OSError) as error:
            self.status.set(str(error))
            return
        self.reviewed.set(False)
        def work():
            ready = 0
            for row, audio, visual in jobs:
                if self.cancel.is_set():
                    break
                try:
                    request = self.with_project_recording(row, self.request_for(row, audio, options, credit_settings, visual))
                    receipt = create_video(request, self.settings, self.cancel.is_set,
                        progress=lambda pct, r=row: self.events.put(("progress", r, pct)), formats=posting_formats(self.settings))
                    self.events.put(("rendered", row, receipt))
                    ready += 1
                except Cancelled:
                    self.events.put(("row", row, "Stopped; original kept"))
                    break
                except Exception as error:
                    self.events.put(("row", row, str(error)))
            self.events.put(("done", f"{ready} track(s) ready, two videos each. Preview both versions, then publish when ready."))
        self.run_worker(work)

    def preview_selected(self, platform="youtube"):
        for row in self.selected_rows():
            receipt = self.jobs[row]["receipt"]
            if receipt:
                try:
                    video = Path(video_variant(manifest(receipt), self.settings["platform_formats"][platform])["video"])
                    if video.exists():
                        host_platform.open_path(video)
                        return
                except (OSError, ValueError, KeyError) as error:
                    self.status.set(str(error))
                    return
        self.status.set("Create a video first, then select its row to open the preview.")

    def publish_inputs(self, action, then):
        """Checks the publish settings and each platform's text rules, reads which account is connected right now
        (in the background: a sign-in may need refreshing), then shows exactly what will go out. Once the user
        confirms, calls then(platforms, rows, options, save_texts, approval): approval[receipt] is the confirmed
        text + account IDs, enforced again when posting. Returns None after saying what's missing."""
        targets = [p for p, value in self.platforms.items() if value.get()]
        rows = [(row, self.jobs[row]["receipt"]) for row in self.selected_rows() if self.jobs[row]["receipt"]]
        if not targets or not rows:
            self.status.set("Create/select a rendered video and choose at least one platform.")
            return None
        if not self.reviewed.get():
            self.status.set("Preview the videos and tick the review confirmation before publishing.")
            return None
        if "tiktok" in targets and (not self.tiktok_privacy.get() or not self.tiktok_consent.get()):
            self.status.set("Load TikTok account options, choose visibility, and accept its posting terms.")
            return None
        options = {"youtube_privacy": self.youtube_privacy.get(), "made_for_kids": self.kids.get(),
                   "tiktok_privacy": self.tiktok_privacy.get(), "tiktok_consent": self.tiktok_consent.get(),
                   "own_brand": self.own_brand.get(), "branded_content": self.branded.get(), "is_aigc": self.ai.get(),
                   **{"allow_" + key: value.get() for key, value in self.interactions.items()}}
        title_template = self.title.get()
        caption = self.caption.get("1.0", "end-1c")
        social_description = self.social_text.get("1.0", "end-1c")
        hashtags = self.hashtags.get()
        if hashtags != self.settings["default_hashtags"]:
            self.settings["default_hashtags"] = hashtags
            save_preferences({"default_hashtags": hashtags})
        cs = self.credit_settings()
        prepared = {}  # receipt -> (record, the filled-in texts the user confirms below)
        for _, receipt in rows:
            receipt = Path(receipt).resolve()
            if receipt in prepared:
                continue
            try:
                record = manifest(receipt)
                audio = Path(record["audio"])
            except (OSError, ValueError, KeyError):
                self.status.set(f"Couldn't read the saved render {receipt.parent.name}. Create its videos again.")
                return None
            # the title, BPM and key the render used (saved in its receipt), else what the name says
            details = record.get("music") or {key: value for key, value in autopilot.describe(audio, cs, listen=False).items()
                                              if key in ("title", "bpm", "key")}
            fill = lambda text, audio=audio, details=details: credits.fill(text, audio.name, audio.stem, cs, details)
            prepared[receipt] = (record, {"title": fill(title_template), "caption": fill(caption),
                                          "social_description": social_description, "hashtags": hashtags,
                                          "social_caption": fill(social_caption(social_description, hashtags))})
        problems = [f"{Path(record['audio']).name}: {problem}" for record, texts in prepared.values()
                    for platform in targets if not self.already_sent(record, platform)
                    for problem in text_problems(texts, platform, self.settings)]
        if problems:
            messagebox.showwarning("Auto Poster · Fix before posting", "\n".join(problems[:12]), parent=self.root)
            self.status.set("Fix the title or captions first. Nothing was sent.")
            return None
        def accounts():
            identities = {}  # platform -> (stable account ID, name), from the login that is saved right now
            for platform in targets:
                profile = self.accounts.credentials(platform)
                identities[platform] = (self.accounts.account_id(platform, profile), profile.get("name") or "connected")
            self.events.put(("confirm", lambda: self.confirm_post(action, targets, rows, options, prepared, identities, then)))
        self.status.set("Checking the connected accounts…")
        self.run_worker(accounts)

    def confirm_post(self, action, targets, rows, options, prepared, identities, then):
        if self.busy:  # the account check is still finishing
            self.root.after(50, lambda: self.confirm_post(action, targets, rows, options, prepared, identities, then))
            return
        if not messagebox.askokcancel(f"Auto Poster · {action}",
                                      self.post_summary(targets, prepared, options, action, identities), parent=self.root):
            self.status.set("Nothing was sent.")
            return
        ids = {platform: identity for platform, (identity, _) in identities.items()}
        approval = {receipt: {"texts": {key: texts.get(key, "") for key in APPROVED_TEXTS}, "accounts": ids}
                    for receipt, (_, texts) in prepared.items()}
        def save_texts(receipt):
            """Writes this track's confirmed title and captions into its receipt; returns the track name."""
            receipt = Path(receipt).resolve()
            with receipt_lock(receipt):
                record = manifest(receipt)
                record.update(prepared[receipt][1])
                atomic_json(receipt, record)
            return Path(record["audio"]).name
        then(targets, rows, options, save_texts, approval)

    @staticmethod
    def already_sent(record, platform):
        delivery = record.get("delivery", {}).get(platform, {})
        return delivery.get("state") == "published" or bool(delivery.get("remote_id"))

    def post_summary(self, targets, prepared, options, action, identities):
        """Exactly what will go out, per track and platform, before anything is sent."""
        visibility = {"youtube": options["youtube_privacy"], "tiktok": options["tiktok_privacy"] or "?",
                      "instagram": "Reels on your profile"}
        lines = [f"{action} {len(prepared)} track(s) to {', '.join(NAMES[p] for p in targets)}:", ""]
        for record, texts in list(prepared.values())[:5]:
            lines.append(Path(record["audio"]).name)
            for platform in targets:
                if self.already_sent(record, platform):
                    lines.append(f"   {NAMES[platform]}: already sent, won't post again")
                    continue
                version = record.get("variants", {}).get(self.settings["platform_formats"][platform], {})
                seconds = round(record.get("duration") or 0)
                lines.append(f"   {NAMES[platform]} · {identities[platform][1]} · "
                             f"{version.get('width', '?')}×{version.get('height', '?')}, {seconds // 60}:{seconds % 60:02d} · "
                             f"{visibility[platform]}")
                title, caption = post_text(texts, platform)
                shown = title if platform == "youtube" else caption
                shown = " ⏎ ".join(line for line in shown.splitlines() if line.strip()) or "(no caption)"
                lines.append(f"      “{shown[:90]}{'…' if len(shown) > 90 else ''}”")
        if len(prepared) > 5:
            lines.append(f"…and {len(prepared) - 5} more track(s) with the same text.")
        lines += ["", "Nothing has been sent yet." + (" Choose the time next." if action == "Schedule" else "")]
        return "\n".join(lines)

    def start_publish(self):
        self.publish_inputs("Post", self.publish_now)

    def publish_now(self, targets, rows, options, save_texts, approval):
        def work():
            publisher = Publisher(self.settings, self.accounts)
            receipt_rows = {}
            for row, receipt in rows:
                if self.cancel.is_set():
                    break
                receipt = Path(receipt).resolve()
                receipt_rows.setdefault(receipt, []).append(row)
                if len(receipt_rows[receipt]) == 1:
                    save_texts(receipt)
            def completed(receipt, platform, result):
                message = result.get("error") or result["state"]
                for row in receipt_rows[receipt]:
                    self.events.put(("row", row, f"{NAMES[platform]}: {message}"))
                self.events.put(("history", receipt))
            remaining = publisher.publish_batch(receipt_rows, targets, options, self.cancel.is_set,
                report=lambda text: self.events.put(("status", text)), completed=completed, approved=approval.get)
            message = "Stopped; posts already sent can finish processing." if self.cancel.is_set() else "Posting pass finished."
            later = (f" {remaining} still processing: Auto Poster finishes them by itself every "
                     f"{self.settings['schedule_check_minutes']} min while it's open." if remaining else "")
            self.events.put(("done", f"{message}{later} See the History tab for links."))
        self.run_worker(work)

    def start_schedule(self):
        self.publish_inputs("Schedule", self.schedule_posts)

    def schedule_posts(self, targets, rows, options, save_texts, approval):
        receipts = list(dict.fromkeys(Path(receipt).resolve() for _, receipt in rows))
        times = self.ask_schedule(len(receipts))
        if not times:
            return
        last = max(times)
        try:
            deadline = setup_guide.reconnect_by(self.accounts.vault.get("youtube")) if "youtube" in targets else None
        except Exception:
            deadline = None  # Keychain unavailable: posting will say so
        if deadline and last > deadline and not messagebox.askokcancel(
                "Auto Poster · YouTube sign-in", f"Your last post is on {last:%a %d %b}, but Google may sign YouTube out on "
                f"{deadline:%a %d %b} while your Google app is in Testing (7 days per sign-in).\n\nSchedule anyway? "
                "Click Connect YouTube again before then and the posts go out as planned.", parent=self.root):
            return
        def work():
            for receipt, due in zip(receipts, times):
                # the accounts and text confirmed in the summary, not whoever is connected later
                schedule.add(receipt, save_texts(receipt), targets, options, due, approval[receipt])
            self.events.put(("scheduled",))
            self.events.put(("done", f"Scheduled {len(receipts)} post(s), first at {min(times):%a %d %b %H:%M}. "
                             + ("They post while DAW Multitool is open, or anytime with the helper on (Schedule tab)."
                                if not schedule.agent_installed() else "The scheduler helper posts them even with the app closed.")))
        self.run_worker(work)

    def ask_schedule(self, count):
        """When each post goes out: by default the next free daily posting times (Autopilot tab), or from a chosen
        date and time with hours between posts. A list of times, or None if cancelled."""
        self.save_autopilot_settings()
        slots = autopilot.next_free_times(count, self.settings, autopilot.scheduled_times(schedule.load()))
        window = tk.Toplevel(self.root)
        window.title("Auto Poster · Schedule posts")
        window.transient(self.root)
        start = slots[0] if slots else (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        day, clock = tk.StringVar(value=f"{start:%Y-%m-%d}"), tk.StringVar(value=f"{start:%H:%M}")
        spacing = tk.StringVar(value=str(self.settings["schedule_spacing_hours"]))
        use_slots = tk.BooleanVar(value=len(slots) == count)
        body = ttk.Frame(window, padding=16)
        body.pack(fill="both")
        ttk.Label(body, text=f"Schedule {count} post(s) to the selected platforms.").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        if len(slots) == count:
            shown = ", ".join(f"{slot:%a %d %b %H:%M}" for slot in slots[:3]) + (" …" if count > 3 else "")
            ttk.Radiobutton(body, text=f"At my next free posting times: {shown}", value=True, variable=use_slots).grid(
                row=1, column=0, columnspan=2, sticky="w")
            ttk.Radiobutton(body, text="From this date and time:", value=False, variable=use_slots).grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        for line, (label, variable) in enumerate((("Date (YYYY-MM-DD)", day), ("Time (24 h, HH:MM)", clock)), start=3):
            ttk.Label(body, text=label).grid(row=line, column=0, sticky="w", pady=3)
            ttk.Entry(body, textvariable=variable, width=14).grid(row=line, column=1, sticky="w", padx=8)
        if count > 1:
            ttk.Label(body, text="Hours between posts").grid(row=5, column=0, sticky="w", pady=3)
            ttk.Spinbox(body, from_=0, to=720, textvariable=spacing, width=6).grid(row=5, column=1, sticky="w", padx=8)
        problem = tk.StringVar()
        ttk.Label(body, textvariable=problem, foreground=DANGER, wraplength=360).grid(row=6, column=0, columnspan=2, sticky="w")
        chosen = []
        def done():
            if use_slots.get():
                chosen[:] = [slots]
                window.destroy()
                return
            try:
                first = datetime.strptime(f"{day.get().strip()} {clock.get().strip()}", "%Y-%m-%d %H:%M").astimezone()
                hours = float(spacing.get() or 0)
            except ValueError:
                problem.set("Use a date like 2026-11-02 and a time like 18:30.")
                return
            if first <= datetime.now().astimezone() or hours < 0:
                problem.set("Pick a time in the future (and 0 or more hours between posts).")
                return
            chosen[:] = [[first + timedelta(hours=hours * number) for number in range(count)]]
            window.destroy()
        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text="Schedule", style="Primary.TButton", command=done).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="left", padx=6)
        window.grab_set()
        self.root.wait_window(window)
        return chosen[0] if chosen else None

    def build_schedule(self):
        page = self.schedule_page
        ttk.Label(page, text="Scheduled posts", style="Heading.TLabel").pack(anchor="w")
        helper_hint = ("On Mac, the optional helper can post while the app is closed. " if host_platform.IS_MAC else
                       "On Windows, keep Auto Poster open for scheduled posts. ")
        ttk.Label(page, text="Select videos on the Create tab and click Schedule. " + helper_hint +
                  f"Posts more than {self.settings['schedule_max_late_hours']} h late are skipped, never sent by surprise.",
                  wraplength=920).pack(anchor="w", pady=8)
        self.schedule_list = ttk.Treeview(page, columns=("due", "track", "platforms", "state", "detail"), show="headings")
        empty_state(self.schedule_list, "Nothing scheduled. Posts you schedule show up here.")
        for key, title, width in (("due", "When", 140), ("track", "Track", 220), ("platforms", "Platforms", 160),
                                  ("state", "Status", 90), ("detail", "Details", 330)):
            self.schedule_list.heading(key, text=title)
            self.schedule_list.column(key, width=width)
        self.schedule_list.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="Cancel selected", command=self.cancel_scheduled).pack(side="left")
        ttk.Button(row, text="Refresh", command=self.refresh_schedule).pack(side="left", padx=8)
        self.agent_label = tk.StringVar()
        self.agent_button = ttk.Button(row, textvariable=self.agent_label, command=self.toggle_agent)
        if not host_platform.IS_MAC:
            self.agent_button.configure(state="disabled")
        self.agent_button.pack(side="right")
        self.refresh_schedule()

    def refresh_schedule(self):
        self.schedule_list.delete(*self.schedule_list.get_children())
        for entry in sorted(schedule.load(), key=lambda e: e["due"], reverse=True):
            due = datetime.fromisoformat(entry["due"])
            self.schedule_list.insert("", "end", iid=entry["id"], values=(
                f"{due:%a %d %b %H:%M}", entry["track"], ", ".join(NAMES[p] for p in entry["platforms"]),
                entry["state"], entry["detail"]))
        self.agent_label.set("Turn off posting while closed" if schedule.agent_installed() else "Post while the app is closed…")
        if not host_platform.IS_MAC:
            self.agent_label.set("Background helper: Mac only")

    def cancel_scheduled(self):
        ids = set(self.schedule_list.selection())
        if not ids:
            self.status.set("Select scheduled posts to cancel.")
            return
        schedule.cancel(ids)
        self.refresh_schedule()
        self.status.set("Cancelled the selected posts that hadn't gone out yet.")

    def toggle_agent(self):
        try:
            if schedule.agent_installed():
                schedule.remove_agent()
                self.status.set("Scheduler helper turned off. Scheduled posts now go out only while DAW Multitool is open.")
            elif messagebox.askokcancel("Post while the app is closed", "This adds a small macOS login helper "
                    f"(~/Library/LaunchAgents/{schedule.AGENT}.plist) that checks every "
                    f"{self.settings['schedule_check_minutes']} minutes for due posts and sends them with your connected "
                    "accounts. It only posts what you scheduled. With Autopilot on, it also makes videos for new beats "
                    "(it never posts those until you approve them). Turn it off here anytime.", parent=self.root):
                schedule.install_agent(self.settings)
                self.status.set("Scheduler helper on: due posts go out even when DAW Multitool is closed.")
        except (OSError, subprocess.SubprocessError) as error:
            self.status.set(f"Couldn't change the scheduler helper: {error}")
        self.refresh_schedule()

    def build_results(self):
        page = self.results_page
        ttk.Label(page, text="Results", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(page, text="Views, likes, comments and shares for every published post saved in your output folder, "
                  "best beat first. Click a column to sort; double-click a row to open the post. "
                  "Only reads numbers; nothing is posted or changed.", wraplength=920).pack(anchor="w", pady=8)
        columns = ("platform", *results.STATS, "detail")
        self.results_list = ttk.Treeview(page, columns=columns)
        empty_state(self.results_list, "Views, likes and comments show up here after you post.")
        self.results_list.heading("#0", text="Beat", command=lambda: self.sort_results("#0"))
        self.results_list.column("#0", width=230)
        for key, width in (("platform", 100), ("views", 90), ("likes", 80), ("comments", 115), ("shares", 90), ("detail", 330)):
            self.results_list.heading(key, text=key.title() if key != "detail" else "Details",
                                      command=lambda k=key: self.sort_results(k))
            self.results_list.column(key, width=width, anchor="e" if key in results.STATS else "w")
        self.results_list.pack(fill="both", expand=True)
        self.results_list.bind("<Double-Button-1>", lambda event: self.open_result())
        row = ttk.Frame(page)
        row.pack(fill="x", pady=8)
        self.results_button = ttk.Button(row, text="Refresh stats", command=self.refresh_results)
        self.results_button.pack(side="left")
        self.results_note = tk.StringVar()
        ttk.Label(row, textvariable=self.results_note, foreground=MUTED).pack(side="left", padx=12)
        self.result_links, self.results_sort = {}, ("views", True)
        self.show_results(*results.last_results())

    def refresh_results(self):
        extra = self.known_receipts()
        def work():
            rows = results.posts(results.receipts(self.settings, extra))
            if not rows:
                self.events.put(("done", "No published posts found in the output folder yet."))
                return
            self.events.put(("status", f"Asking the platforms about {len(rows)} post(s)…"))
            results.fetch(rows, self.accounts, self.settings)
            self.events.put(("results", *results.last_results()))
            self.events.put(("done", "Stats refreshed."))
        self.run_worker(work)

    def show_results(self, fetched, rows):
        self.results_rows, self.results_fetched = rows, fetched
        self.results_list.delete(*self.results_list.get_children())
        self.result_links = {}
        key, descending = self.results_sort
        def value(item):
            return item.casefold() if isinstance(item, str) else item or 0
        grouped = results.by_track(rows)
        if key == "#0":
            grouped.sort(key=lambda g: g[0].casefold(), reverse=descending)
        elif key in results.STATS:
            grouped.sort(key=lambda g: g[1][key], reverse=descending)
        for track, totals, posts in grouped:
            parent = self.results_list.insert("", "end", text=track, open=True, values=(
                "All", *(f"{totals[k]:,}" for k in results.STATS), posts[0].get("title", "")))
            if key in results.STATS or key == "platform":
                posts = sorted(posts, key=lambda r: value(r.get(key)), reverse=descending)
            for post in posts:
                child = self.results_list.insert(parent, "end", text="", values=(
                    NAMES[post["platform"]], *("" if post.get(k) is None else f"{post[k]:,}" for k in results.STATS),
                    post.get("error") or post.get("note") or post.get("url", "")))
                self.result_links[child] = post.get("url", "")
        self.results_note.set(f"Last checked {datetime.fromisoformat(fetched):%a %d %b %H:%M}." if fetched
                              else "Click Refresh stats after your first post.")

    def sort_results(self, key):
        current, descending = self.results_sort
        self.results_sort = (key, not descending if key == current else key != "#0")
        self.show_results(self.results_fetched, self.results_rows)

    def open_result(self):
        rows = self.results_list.selection()
        url = self.result_links.get(rows[0]) if rows else None
        if url and url.startswith("https://"):
            webbrowser.open(url)
        else:
            self.status.set("Double-click a platform row that has a link.")

    def known_receipts(self):
        """Renders this window knows of besides the output folder: scheduled ones and the tracks in the list."""
        return [e["receipt"] for e in schedule.load()] + [job["receipt"] for job in self.jobs.values() if job["receipt"]]

    def check_schedule(self):
        """While this window is open: posts anything scheduled that's due, and finishes uploads that are still
        processing (an Instagram Reel isn't live until its publish step). The helper does the same when it's closed."""
        self.schedule_id = self.root.after(self.settings["schedule_check_minutes"] * 60_000, self.check_schedule)
        if self.busy:
            return
        extra = self.known_receipts()
        unfinished = pending(results.receipts(self.settings, extra))
        if not schedule.due_now() and not unfinished:
            return
        def work():
            report = lambda text: self.events.put(("status", text))
            publisher = Publisher(self.settings, self.accounts)
            handled = schedule.run_due(publisher, self.settings, self.cancel.is_set, report)
            for entry in handled:
                self.events.put(("history", Path(entry["receipt"]).resolve()))
            self.events.put(("scheduled",))
            checked = publisher.resume(pending(results.receipts(self.settings, extra)), self.cancel.is_set, report)
            for receipt, _, _ in checked:
                self.events.put(("history", receipt))
            notes = [f"{e['track']}: {e['detail']}" for e in handled]
            notes += [f"{schedule.track_name(r)} is now live on {NAMES[p]}." for r, p, state in checked if state["state"] == "published"]
            notes += [f"{schedule.track_name(r)} on {NAMES[p]}: {state.get('error', 'failed')}"
                      for r, p, state in checked if state["state"] == "failed"]
            if notes:
                self.events.put(("done", " ".join(notes)))
        self.run_worker(work)

    def connect(self, platform, typed=None):
        """Saves keys typed in the setup steps, then opens the browser sign-in. With no keys yet, opens the setup steps."""
        typed = typed or {}
        try:
            saved = self.accounts.app_keys(platform)
        except Exception:
            saved = {}  # authorize() reports the Keychain problem
        if not all(typed.get(key, "").strip() or saved.get(key) for key in APP_KEYS[platform][:2]):
            self.show_setup(platform)  # first time: the step-by-step setup
            self.status.set(f"First time connecting {NAMES[platform]}: follow the setup steps in the new window.")
            return
        def work():
            self.accounts.save_app_keys(platform, typed)
            try:
                self.accounts.check_keys(platform, self.accounts.app_keys(platform))  # a wrong key: say so before the browser
                name = self.accounts.authorize(platform, cancelled=self.cancel.is_set, choose=self.choose_account)
            except APIError as error:
                fix = setup_guide.fix_for(platform, error, self.settings)  # a known setup mistake: what to do + its page
                self.events.put(("fix", platform, *(fix or (str(error), ""))))
                return
            self.events.put(("connected", platform, name))
            if platform == "tiktok":
                self.events.put(("creator", self.accounts.creator_info()))
        self.status.set(setup_guide.waiting(platform, self.settings))
        self.run_worker(work)

    def choose_account(self, options):
        """Called on the worker thread: asks on the main thread and waits for the answer (None = cancel)."""
        answer = queue.Queue()
        self.events.put(("choose", options, answer))
        while not self.cancel.is_set():
            try:
                return answer.get(timeout=0.2)
            except queue.Empty:
                pass
        return None  # Stop: the dialog closes itself

    def ask_account(self, options):
        window = tk.Toplevel(self.root)
        window.title("Auto Poster · Choose Instagram account")
        window.transient(self.root)
        ttk.Label(window, text="You shared more than one Instagram account. Which one should Auto Poster post to?",
                  wraplength=420).pack(padx=16, pady=(14, 8))
        names = tk.Listbox(window, height=min(len(options), 8), exportselection=False)
        for option in options:
            names.insert("end", f"@{option['name']}" + (f"  ({option['page']})" if option.get("page") else ""))
        names.selection_set(0)
        names.pack(fill="x", padx=16)
        chosen = []
        def pick():
            chosen[:] = [options[i] for i in names.curselection()][:1]
            window.destroy()
        buttons = ttk.Frame(window)
        buttons.pack(pady=12)
        ttk.Button(buttons, text="Connect", command=pick).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="left", padx=6)
        names.bind("<Double-Button-1>", lambda event: pick())
        def watch_stop():
            if not window.winfo_exists():
                return
            if self.cancel.is_set():
                window.destroy()
            else:
                window.after(200, watch_stop)
        watch_stop()  # no grab, so the main window's Stop button still works
        self.root.wait_window(window)
        return chosen[0] if chosen else None

    def disconnect(self, platform):
        if self.busy:
            return
        try:
            self.accounts.vault.delete(platform)
            self.refresh_accounts()
            self.status.set(f"{NAMES[platform]} credentials removed locally. Provider account permissions can be revoked in its account settings.")
        except Exception:
            self.status.set("Couldn't access Keychain to disconnect. Check macOS Keychain permissions.")

    def refresh_accounts(self):
        for platform in PLATFORMS:
            try:
                profile = self.accounts.vault.get(platform)
                self.account_labels[platform].set(profile.get("name", "Connected") if profile else "Not connected")
                note = ("App keys saved ✓ (Setup steps to replace them)" if self.accounts.app_keys(platform)
                        else "")  # the page's intro already says Connect walks through setup
                deadline = setup_guide.reconnect_by(profile) if platform == "youtube" else None
                if deadline:
                    note += (f" · Google may sign you out on {deadline:%a %d %b} while your Google app is in Testing: "
                             "just click Connect YouTube again." if deadline > datetime.now().astimezone() else
                             " · Google may have signed you out (apps in Testing last 7 days): click Connect YouTube.")
                self.key_labels[platform].set(note)
            except Exception:
                self.account_labels[platform].set("Keychain unavailable")

    def target_changed(self):
        if self.platforms["tiktok"].get() and not self.busy:
            def work():
                self.events.put(("creator", self.accounts.creator_info()))
            self.run_worker(work)

    def apply_creator(self, info):
        self.creator = info
        self.tiktok_combo.configure(values=info.get("privacy_level_options", []))
        self.tiktok_privacy.set("")  # TikTok requires an explicit selection, never a default
        self.creator_label.set(f"TikTok: {info.get('creator_nickname', '')} · maximum {info.get('max_video_post_duration_sec', '?')} seconds")
        for key, button in self.interaction_buttons.items():
            self.interactions[key].set(False)
            button.configure(state="disabled" if info.get(key + "_disabled", True) else "normal")
        self.disclosure_changed()

    def disclosure_changed(self):
        choices = self.creator.get("privacy_level_options", []) if self.creator else []
        if self.branded.get():
            choices = [value for value in choices if value != "SELF_ONLY"]
            if self.tiktok_privacy.get() == "SELF_ONLY":
                self.tiktok_privacy.set("")
                self.status.set("Choose a non-private TikTok visibility for paid partnership content.")
            self.disclosure.set("TikTok disclosure: Paid partnership.")
        elif self.own_brand.get():
            self.disclosure.set("TikTok disclosure: Promotional content.")
        else:
            self.disclosure.set("Instagram Reels publishes to your profile; YouTube visibility applies only to YouTube.")
        self.tiktok_combo.configure(values=choices)

    def load_receipt(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="Open a saved render's post.json", filetypes=[("Render receipt", "*.json")])
        if not path:
            return
        try:
            record = manifest(path)
            versions = list(record["variants"].values()) if "variants" in record else [record]
            if record.get("state") != "ready" or not versions or not all(Path(version["video"]).is_file() for version in versions):
                raise ValueError("This is not a completed render receipt.")
            row = self.tracks.insert("", "end", values=(Path(record["audio"]).name, music_text(record["audio"]), "Ready; saved render"))
            self.jobs[row] = {"audio": Path(record["audio"]), "receipt": Path(path)}
            self.refresh_credits(self.credit_settings())
            self.title.set(record["title"])
            self.caption.delete("1.0", "end")
            self.caption.insert("1.0", record["caption"])
            if "social_description" in record:
                self.social_text.delete("1.0", "end")
                self.social_text.insert("1.0", record["social_description"])
                self.hashtags.set(record.get("hashtags", ""))
            self.reviewed.set(False)
            self.refresh_history()
            self.status.set("Saved video loaded. Review its preview and pick platforms in the Post tab.")
        except (OSError, ValueError, KeyError) as error:
            self.status.set(str(error))

    def refresh_history(self, receipt=None):
        if receipt is None:
            self.history.delete(*self.history.get_children())
            self.history_rows = {}
            receipts = dict.fromkeys(Path(job["receipt"]).resolve() for job in self.jobs.values() if job["receipt"])
        else:
            receipts = [Path(receipt).resolve()]
        for path in receipts:
            try:
                record = manifest(path)
                for platform, value in record.get("delivery", {}).items():
                    key = (path, platform)
                    link = " · ".join(part for part in (value.get("note"), value.get("url")) if part)
                    values = (record["title"], NAMES.get(platform, platform), value.get("format", ""), value.get("state", ""),
                              value.get("error") or link or value.get("post_id") or value.get("remote_id", ""))
                    previous = self.history_rows.get(key)
                    if previous and previous[1] == values:
                        continue
                    row = previous[0] if previous else self.history.insert("", "end", values=values)
                    if previous:
                        self.history.item(row, values=values)
                    self.history_rows[key] = (row, values)
            except (OSError, ValueError):
                pass

    def run_worker(self, work):
        if self.busy:
            return
        self.set_busy(True)
        self.cancel.clear()
        def run():
            try:
                work()
            except Exception as error:
                self.events.put(("error", str(error)))
            finally:
                self.events.put(("idle",))
        threading.Thread(target=run, daemon=True).start()

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.add_button, self.remove_button, self.project_button, self.plan_button, self.create_button,
                       self.publish_button, self.schedule_button, self.results_button, *self.connect_buttons):
            button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def poll_events(self):
        changed_receipts = set()
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "progress":
                    row, percent = data
                    self.progress["value"] = percent
                    self.tracks.set(row, "status", f"Creating video {percent}%")
                elif kind == "rendered":
                    row, receipt = data
                    self.jobs[row]["receipt"] = receipt
                    self.tracks.set(row, "status", "16:9 + 9:16 ready to preview")
                elif kind == "row":
                    self.tracks.set(data[0], "status", data[1])
                elif kind in ("done", "error", "status"):
                    self.status.set(data[0])
                elif kind == "recorded":
                    message, problem = data
                    self.status.set(message)
                    self.come_forward()  # the DAW was in front while it recorded
                    if problem:
                        messagebox.showwarning("Auto Poster · Recording", problem, parent=self.root)
                elif kind == "warning":
                    messagebox.showwarning("Auto Poster · Recording", data[0], parent=self.root)
                elif kind == "idle":
                    self.set_busy(False)
                elif kind == "plan":
                    lines = [f"{Path(p['audio']).name}: from {p['start']:.1f}s for {p['seconds']:.1f}s, {p['format']}, {p['fit']}"
                             + (f"; TikTok/Reels: {list(p['seconds_by_format'].values())[-1]:.0f}s from {p['short_start']:.1f}s"
                                if p.get("short_start") is not None else "")
                             + (f", records {Path(p['project']).name}" if p.get("project") else "")
                             + (f", {p['lufs']:.1f} LUFS" if p.get("lufs") is not None else "")
                             + "".join(f"\n   ⚠ {w}" for w in p["warnings"]) for p in data[0]]
                    messagebox.showinfo("Auto Poster · Plan", "\n".join(lines) + "\n\nOriginals stay untouched. Nothing has rendered or uploaded.", parent=self.root)
                elif kind == "choose":
                    options, answer = data
                    if not self.cancel.is_set():
                        answer.put(self.ask_account(options))
                elif kind == "fix":
                    platform, message, url = data
                    self.status.set(message)
                    self.come_forward()
                    if url:
                        if messagebox.askyesno(f"Auto Poster · {NAMES[platform]}", message + "\n\nOpen that page now?",
                                               parent=self.root):
                            webbrowser.open(url)
                    else:
                        messagebox.showinfo(f"Auto Poster · {NAMES[platform]}", message, parent=self.root)
                elif kind == "connected":
                    self.refresh_accounts()
                    self.status.set(f"Connected {NAMES[data[0]]}: {data[1]}.")
                    self.remember_setup_step(data[0], 0)  # set up: Setup steps starts from the top next time
                    self.come_forward()
                    messagebox.showinfo(f"Auto Poster · {NAMES[data[0]]}", f"{NAMES[data[0]]} is connected: {data[1]} ✓",
                                        parent=self.root)
                elif kind == "scheduled":
                    self.refresh_schedule()
                elif kind == "results":
                    self.show_results(*data)
                elif kind == "confirm":
                    self.root.after(0, data[0])  # after this pass, so the account check's worker is idle
                elif kind == "creator":
                    self.apply_creator(data[0])
                elif kind == "drop":
                    self.jobs.pop(data[0], None)
                    if self.tracks.exists(data[0]):
                        self.tracks.delete(data[0])
                elif kind == "autopilot":
                    self.refresh_autopilot()
                    if data[0]:
                        self.status.set(f"Autopilot made {data[0]} new video pair(s). Click Review ready videos on the "
                                        "Autopilot tab when you're ready.")
                elif kind == "history":
                    changed_receipts.add(data[0])
        except queue.Empty:
            pass
        for receipt in changed_receipts:
            self.refresh_history(receipt)
        self.poll_id = self.root.after(100, self.poll_events)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Stopping… wait for the current transfer, then close. Posts already sent may finish processing.")
            return
        self.root.after_cancel(self.poll_id)
        self.root.after_cancel(self.schedule_id)
        self.root.after_cancel(self.autopilot_id)
        self.root.destroy()
