"""Finds projects and links each audio export to the project it came from. Read-only: nothing is moved,
renamed or deleted; opening a project only hands it to its DAW."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess

from app_paths import load_section, update_config
from multitool import platform
from multitool.auto_poster.project import SKIP_FOLDERS, daw_for, find_project, project_index
from multitool.auto_poster.render import Cancelled
from multitool.auto_poster.storage import load_settings as poster_settings

VIEWS = ("All projects", "Never exported", "Exports with no project")
SORTS = ("name", "daw", "changed", "exports", "folder")  # the table's columns, in order
NEWEST_FIRST = ("changed", "exports")  # a first click sorts these biggest first, the rest A to Z
SHARED = ("project_folders", "project_search_depth", "daws", "audio_extensions")  # owned by Auto Poster's section


def load_settings():
    """Auto Poster's folders and DAWs, plus the Library's own extras (Pro Tools sessions, which Auto Poster
    doesn't record)."""
    settings = load_section("project_library")
    poster = poster_settings()
    settings.update({key: poster[key] for key in SHARED})
    settings["daws"] = {**poster["daws"], **settings["extra_daws"]}
    settings["project_folders"] = list(dict.fromkeys([*poster["project_folders"], *settings["extra_project_folders"]]))
    return settings


def add_project_folder(settings, folder):
    """Saved in Auto Poster's section so both tools search the same folders."""
    extras = settings.get("extra_project_folders", [])  # the Library's own (Pro Tools) folders stay out of Auto Poster's list
    folders = list(dict.fromkeys([*(f for f in settings["project_folders"] if f not in extras), str(folder)]))
    update_config("auto_poster", {"project_folders": folders})
    settings["project_folders"] = list(dict.fromkeys([*settings["project_folders"], str(folder)]))


def changed(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def export_files(settings, cancelled=lambda: False):
    """Audio in the export folders and the project folders (exports often sit beside the project)."""
    extensions = tuple(e.lower() for e in settings["audio_extensions"])
    roots = dict.fromkeys(Path(r).expanduser() for r in [*settings["export_folders"], *settings["project_folders"]])
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for folder, dirs, files in os.walk(root):
            if cancelled():
                raise Cancelled("Scan stopped.")
            depth = len(Path(folder).relative_to(root).parts)
            dirs[:] = [] if depth >= settings["export_search_depth"] else [
                d for d in dirs if not d.startswith(".") and d.casefold() not in SKIP_FOLDERS]
            found += [Path(folder) / f for f in files if f.lower().endswith(extensions) and not f.startswith(".")]
    return list(dict.fromkeys(found))


@dataclass
class Library:
    projects: dict  # project -> its exports, newest first
    orphans: list   # exports no project matched, newest first
    modified: dict = field(default_factory=dict)  # file -> modified time, read once per scan


def scan(settings, cancelled=lambda: False):
    index = project_index(settings)
    exports = export_files(settings, cancelled)
    modified = {path: changed(path) for path in [*index, *exports]}
    newest = lambda path: modified[path]
    projects = {project: [] for project in sorted(index, key=newest, reverse=True)}
    orphans = []
    for export in exports:
        if cancelled():
            raise Cancelled("Scan stopped.")
        project = find_project(export, index)
        (projects[project] if project else orphans).append(export)
    for linked in projects.values():
        linked.sort(key=newest, reverse=True)
    return Library(projects, sorted(orphans, key=newest, reverse=True), modified)


def visible(library, view, text=""):
    """Rows for a view: [(project, exports)] for project views, [(None, [export])] for orphan exports."""
    text = text.strip().casefold()
    def hit(*paths):
        return not text or any(text in p.name.casefold() for p in paths)
    if view == VIEWS[2]:
        return [(None, [export]) for export in library.orphans if hit(export)]
    return [(project, exports) for project, exports in library.projects.items()
            if (view == VIEWS[0] or not exports) and hit(project, *exports)]


def natural(text):
    """Sorts like Finder: "beat 2" before "beat 10", capitals ignored."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text.casefold()) if part]


def sort_rows(rows, column, descending, modified, daw=lambda path: path.suffix):
    """Rows from visible() in a column's order (ties by name). Exports under a project follow the same order
    for name, date and folder; for DAW and export count they stay newest first."""
    def name(path, project=True):
        return natural(path.stem if project else path.name)

    keys = {"name": lambda path, project: name(path, project),
            "daw": lambda path, project: (natural(daw(path)) if project else [], name(path, project)),
            "changed": lambda path, project: (modified.get(path) or changed(path), name(path, project)),
            "folder": lambda path, project: (natural(str(path.parent)), name(path, project))}
    if column == "exports":
        by_row = lambda row: (len(row[1]) if row[0] else 0, name(row[0] or row[1][0], bool(row[0])))
    else:
        by_row = lambda row: keys[column](row[0] or row[1][0], row[0] is not None)
    ordered = sorted(rows, key=by_row, reverse=descending)
    if column in ("name", "changed", "folder"):
        ordered = [(project, sorted(exports, key=lambda e: keys[column](e, False), reverse=descending) if project else exports)
                   for project, exports in ordered]
    return ordered


def open_in_daw(project, settings):
    """`open -a` loads the project in its DAW; the file itself is never changed here."""
    app = daw_for(project, settings)["app"]
    try:
        platform.open_path(project, app)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"Couldn't open the project. Check its DAW association or configured app: {error}") from error


def show_in_finder(path):
    platform.reveal_path(path)
