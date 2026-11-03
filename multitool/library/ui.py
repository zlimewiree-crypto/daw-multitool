"""Project Library window: search projects, see their exports, open one in its DAW."""
from datetime import datetime
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app_paths import update_config
from multitool import platform
from multitool.auto_poster.render import Cancelled
from multitool.theme import apply_theme, banner, empty_state, footer
from .core import (NEWEST_FIRST, SORTS, VIEWS, add_project_folder, changed, load_settings, open_in_daw, scan, show_in_finder,
                   sort_rows, visible)

COLUMNS = dict(zip(("#0", "daw", "changed", "exports", "folder"), SORTS))  # table column -> what it sorts by


def when(path, modified):
    stamp = modified.get(path) or changed(path)
    return datetime.fromtimestamp(stamp).strftime("%b %d, %Y  %H:%M").replace(" 0", " ", 1) if stamp else ""


def short(folder):
    """~/Desktop/… instead of /Users/<name>/Desktop/…: the part that matters fits the column."""
    text, home = str(folder), str(Path.home())
    return "~" + text[len(home):] if text == home or text.startswith(home + "/") else text


class LibraryApp:
    def __init__(self, root):
        apply_theme(root)
        self.root = root
        self.settings = load_settings()
        self.library = None
        self.paths = {}  # tree row -> file
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        root.title("Project Library · DAW Multitool")
        root.geometry("1080x720")
        root.minsize(820, 480)
        self.status = tk.StringVar(value="Looking for projects…")
        banner(root, "Project Library", "Every session, and the beats you exported from it.", kind="project_library")
        footer(root, self.status, wraplength=1020)

        bar = ttk.Frame(root, padding=(24, 16, 24, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="Search", style="Field.TLabel").pack(side="left")
        self.search = tk.StringVar()
        self.show_id = None
        self.search.trace_add("write", lambda *args: self.show_soon())
        ttk.Entry(bar, textvariable=self.search, width=32).pack(side="left", padx=(6, 14))
        self.view = tk.StringVar(value=VIEWS[0])
        view = ttk.Combobox(bar, textvariable=self.view, values=VIEWS, state="readonly", width=22)
        view.pack(side="left")
        view.bind("<<ComboboxSelected>>", lambda event: self.show())

        table = ttk.Frame(root, padding=(24, 6))
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("daw", "changed", "exports", "folder"), selectmode="browse")
        self.empty = empty_state(self.tree, "Looking for projects…")
        self.titles = {}
        for key, title, width in (("#0", "Project / export", 300), ("daw", "DAW", 120), ("changed", "Last changed", 170),
                                  ("exports", "Exports", 95), ("folder", "Folder", 320)):
            self.titles[key] = title
            self.tree.heading(key, command=lambda column=COLUMNS[key]: self.sort_by(column))  # click a heading to sort
            self.tree.column(key, width=width, stretch=key in ("#0", "folder"))
        column = self.settings["sort_column"] if self.settings["sort_column"] in SORTS else "changed"
        self.sort = (column, bool(self.settings["sort_descending"]))
        self.show_headings()
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-Button-1>", lambda event: self.open_selected())

        actions = ttk.Frame(root, padding=(24, 10, 24, 14))
        actions.pack(fill="x")
        ttk.Button(actions, text="Open in DAW", style="Primary.TButton", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text=f"Show in {platform.FILE_MANAGER}", command=self.reveal_selected).pack(side="left", padx=8)
        self.rescan_button = ttk.Button(actions, text="Refresh", style="Quiet.TButton", command=self.start_scan)
        self.rescan_button.pack(side="right")
        self.folder_button = ttk.Button(actions, text="Add folder…", style="Quiet.TButton", command=self.add_folder)
        self.folder_button.pack(side="right", padx=8)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll_events)
        self.start_scan()

    def start_scan(self):
        if self.busy:
            return
        self.set_busy(True)
        self.cancel.clear()
        self.status.set("Looking for projects and exports…")
        def run():
            try:
                self.events.put(("library", scan(self.settings, self.cancel.is_set)))
            except Cancelled as error:
                self.events.put(("status", str(error)))
            except Exception as error:
                self.events.put(("status", f"Couldn't scan: {error}"))
            finally:
                self.events.put(("idle",))
        threading.Thread(target=run, daemon=True).start()

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.rescan_button, self.folder_button):
            button.configure(state="disabled" if busy else "normal")

    def poll_events(self):
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "library":
                    self.library = data[0]
                    linked = sum(len(exports) for exports in self.library.projects.values())
                    self.status.set(f"{len(self.library.projects)} projects, {linked} exports linked, "
                                    f"{len(self.library.orphans)} exports with no project found. Double-click a project to open it.")
                    self.show()
                elif kind == "status":
                    self.status.set(data[0])
                elif kind == "idle":
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(100, self.poll_events)

    def daw_name(self, path):
        daw = self.settings["daws"].get(path.suffix.lower(), {})
        name = daw.get("name") or daw.get("app") or daw.get("process") or path.suffix
        return re.sub(r"\s+(\d+|Suite|Standard|Intro|Lite)\b.*$", "", name)  # "Ableton Live 12 Suite" → "Ableton Live"

    def sort_by(self, column):
        """A heading was clicked: sort by it; clicking the same one again turns the order round."""
        current, descending = self.sort
        self.sort = (column, not descending if column == current else column in NEWEST_FIRST)
        self.settings.update(sort_column=self.sort[0], sort_descending=self.sort[1])
        update_config("project_library", {"sort_column": self.sort[0], "sort_descending": self.sort[1]})
        self.show_headings()
        self.show()

    def show_headings(self):
        column, descending = self.sort
        for key, title in self.titles.items():
            arrow = ("  ▾" if descending else "  ▴") if COLUMNS[key] == column else ""
            self.tree.heading(key, text=title + arrow)

    def show_soon(self):
        """Typing redraws once after a short pause, not once per key."""
        if self.show_id:
            self.root.after_cancel(self.show_id)
        self.show_id = self.root.after(150, self.show)

    def show(self):
        self.show_id = None
        self.tree.delete(*self.tree.get_children())
        self.paths = {}
        if not self.library:
            return
        rows = sort_rows(visible(self.library, self.view.get(), self.search.get()), *self.sort, self.library.modified,
                         self.daw_name)
        for project, exports in rows:
            if project is None:
                export = exports[0]
                row = self.tree.insert("", "end", text=export.name, values=("", when(export, self.library.modified), "", short(export.parent)))
                self.paths[row] = export
                continue
            row = self.tree.insert("", "end", text=project.stem, open=bool(self.search.get().strip()),
                                   values=(self.daw_name(project), when(project, self.library.modified), len(exports) or "", short(project.parent)))
            self.paths[row] = project
            for export in exports:
                self.paths[self.tree.insert(row, "end", text="↳ " + export.name,
                                            values=("", when(export, self.library.modified), "", short(export.parent)))] = export
        self.empty.configure(text="No projects match your search." if self.search.get().strip()
                             else "Nothing in this view. Try another view, or Add folder… to look somewhere else.")

    def selected(self):
        rows = self.tree.selection()
        return (rows[0], self.paths.get(rows[0])) if rows else (None, None)

    def open_selected(self):
        row, path = self.selected()
        if path is None:
            self.status.set("Select a project first.")
            return
        project = self.paths.get(self.tree.parent(row)) or path  # an export row opens its project
        if project.suffix.lower() not in self.settings["daws"]:
            show_in_finder(path)
            self.status.set(f"No project found for {path.name}; showing it in {platform.FILE_MANAGER}.")
            return
        try:
            open_in_daw(project, self.settings)
            self.status.set(f"Opening {project.name} in {self.daw_name(project)}.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def reveal_selected(self):
        row, path = self.selected()
        if path is None:
            self.status.set("Select a project or export first.")
            return
        show_in_finder(path)

    def add_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Add a folder with FL Studio, Ableton or Pro Tools projects")
        if folder:
            add_project_folder(self.settings, Path(folder))
            self.start_scan()

    def close(self):
        if self.busy:
            self.cancel.set()
        self.root.after_cancel(self.poll_id)
        if self.show_id:
            self.root.after_cancel(self.show_id)
        self.root.destroy()
