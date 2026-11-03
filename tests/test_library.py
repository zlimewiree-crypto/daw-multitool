"""Project Library on fake (empty) project and export files in test_exports/; nothing real is read."""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from multitool.auto_poster.storage import load_settings as poster_settings
from multitool.library.core import VIEWS, Library, add_project_folder, open_in_daw, scan, sort_rows, visible

ROOT = Path(__file__).resolve().parent.parent


class LibraryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "test_exports").mkdir(exist_ok=True)
        self.base = Path(tempfile.mkdtemp(prefix="library_test_", dir=ROOT / "test_exports"))
        # Fakes are moved out, never deleted (same rule as real audio)
        self.addCleanup(lambda: shutil.move(str(self.base), tempfile.mkdtemp(prefix="daw-library-tests-", dir=tempfile.gettempdir())))
        poster = poster_settings()
        self.settings = {"project_folders": [str(self.base / "Projects")], "project_search_depth": 3,
                         "daws": poster["daws"], "audio_extensions": poster["audio_extensions"],
                         "export_folders": [str(self.base / "Downloads")], "export_search_depth": 3}

    def fake(self, relative, age=0):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_links_exports_to_projects_and_finds_the_gaps(self):
        needa = self.fake("Projects/needa lead.flp", age=100)
        dusk = self.fake("Projects/dusk/dusk Project/dusk.als", age=50)
        lonely = self.fake("Projects/never bounced.flp", age=10)
        self.fake("Projects/needa lead (autosaved).flp")  # autosaves aren't projects
        first = self.fake("Downloads/needa lead 140 fmin.wav", age=30)
        newest = self.fake("Downloads/Needa_Lead (1).mp3", age=5)
        beside = self.fake("Projects/dusk/dusk Project/dusk.wav")  # exported next to its project
        stray = self.fake("Downloads/mystery loop.wav")
        self.fake("Projects/dusk/dusk Project/Samples/Recorded/dusk take.wav")  # DAW sample folders are skipped

        library = scan(self.settings)
        self.assertEqual(list(library.projects), [lonely, dusk, needa])  # newest first
        self.assertEqual(library.projects[needa], [newest, first])
        self.assertEqual(library.projects[dusk], [beside])
        self.assertEqual(library.orphans, [stray])

        self.assertEqual(visible(library, VIEWS[1]), [(lonely, [])])
        self.assertEqual(visible(library, VIEWS[2]), [(None, [stray])])
        self.assertEqual([p for p, _ in visible(library, VIEWS[0], "140 FMIN")], [needa])  # search finds exports too

    @patch("multitool.platform.IS_MAC", True)
    def test_pro_tools_sessions_are_listed_without_their_backups_or_recordings(self):
        self.settings["daws"] = {**self.settings["daws"], ".ptx": {"app": "Pro Tools"}, ".ptf": {"app": "Pro Tools"}}
        session = self.fake("Projects/vocal take/vocal take.ptx", age=20)
        old = self.fake("Projects/old song/old song.ptf", age=40)
        self.fake("Projects/vocal take/Session File Backups/vocal take.bak.003.ptx")
        self.fake("Projects/stray vocal take.bak.001.ptx")  # a backup outside its folder is still a backup
        self.fake("Projects/vocal take/Audio Files/Audio 1_01.wav")  # Pro Tools recordings aren't exports
        bounce = self.fake("Projects/vocal take/Bounced Files/vocal take.wav", age=5)
        library = scan(self.settings)
        self.assertEqual(list(library.projects), [session, old])
        self.assertEqual(library.projects[session], [bounce])
        self.assertEqual(library.orphans, [])
        with patch("multitool.library.core.subprocess.run") as run:
            run.return_value.returncode = 0
            open_in_daw(session, self.settings)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-a", "Pro Tools", str(session)])

    @patch("multitool.platform.IS_MAC", True)
    def test_pro_tools_is_added_for_the_library_only(self):
        from multitool.library.core import load_settings
        settings = load_settings()
        self.assertEqual(settings["daws"][".ptx"]["app"], "Pro Tools")
        self.assertIn("~/Documents/Pro Tools", settings["project_folders"])
        self.assertNotIn(".ptx", poster_settings()["daws"])  # Auto Poster's screen recording stays FL Studio / Ableton
        with patch("multitool.library.core.update_config") as save:
            add_project_folder(settings, self.base / "More")
        self.assertNotIn("~/Documents/Pro Tools", save.call_args.args[1]["project_folders"])

    @patch("multitool.platform.IS_MAC", True)
    def test_open_in_daw_hands_the_project_to_its_app(self):
        self.settings["daws"][".als"]["app"] = "Ableton Live"
        project = self.fake("Projects/beat.als")
        with patch("multitool.library.core.subprocess.run") as run:
            run.return_value.returncode = 0
            open_in_daw(project, self.settings)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-a", self.settings["daws"][".als"]["app"], str(project)])
        with self.assertRaisesRegex(ValueError, "isn't a project type"):
            open_in_daw(self.fake("Downloads/beat.wav"), self.settings)

    def test_added_folder_is_shared_with_auto_poster(self):
        with patch("multitool.library.core.update_config") as save:
            add_project_folder(self.settings, self.base / "More")
            add_project_folder(self.settings, self.base / "More")  # no duplicates
        self.assertEqual(save.call_args.args, ("auto_poster", {"project_folders": [str(self.base / "Projects"), str(self.base / "More")]}))

    def test_empty_library_shows_nothing(self):
        self.assertEqual(visible(Library({}, []), VIEWS[0]), [])

    def test_sorting_by_each_column(self):
        beat2 = self.fake("Projects/b/beat 2.flp", age=30)
        beat10 = self.fake("Projects/a/Beat 10.als", age=10)
        alpha = self.fake("Projects/c/alpha.flp", age=20)
        older = self.fake("Downloads/alpha 140.wav", age=40)
        newer = self.fake("Downloads/alpha 150.mp3", age=5)
        library = Library({beat10: [], alpha: [newer, older], beat2: []}, [], {})
        rows = visible(library, VIEWS[0])
        daw = lambda path: {".flp": "FL Studio", ".als": "Ableton Live"}[path.suffix]
        order = lambda column, descending=False: [p for p, _ in sort_rows(rows, column, descending, {}, daw)]
        self.assertEqual(order("name"), [alpha, beat2, beat10])  # like Finder: 2 before 10, capitals ignored
        self.assertEqual(order("name", True), [beat10, beat2, alpha])
        self.assertEqual(order("changed", True), [beat10, alpha, beat2])  # newest first
        self.assertEqual(order("daw"), [beat10, alpha, beat2])  # Ableton Live, then FL Studio by name
        self.assertEqual(order("exports", True)[0], alpha)
        self.assertEqual(order("folder"), [beat10, beat2, alpha])  # a/, b/, c/
        exports = dict(sort_rows(rows, "name", False, {}, daw))[alpha]
        self.assertEqual(exports, [older, newer])  # exports under a project follow the same order
        self.assertEqual(dict(sort_rows(rows, "daw", False, {}, daw))[alpha], [newer, older])  # or stay newest first
        orphans = [(None, [newer]), (None, [older])]
        self.assertEqual(sort_rows(orphans, "name", False, {}, daw), [(None, [older]), (None, [newer])])

    def test_clicking_a_heading_sorts_and_clicking_again_turns_it_round(self):
        import tkinter as tk
        from multitool.library import ui
        first = self.fake("Projects/zebra.flp", age=10)
        second = self.fake("Projects/apple.flp", age=20)
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        window = tk.Toplevel(root)
        window.withdraw()
        settings = {**ui.load_settings(), **self.settings, "sort_column": "changed", "sort_descending": True}
        with patch.object(ui, "load_settings", return_value=settings), patch.object(ui, "update_config") as saved, \
                patch.object(ui.LibraryApp, "start_scan"):
            app = ui.LibraryApp(window)
            app.library = Library({first: [], second: []}, [], {})
            app.show()
            names = lambda: [app.tree.item(row, "text") for row in app.tree.get_children()]
            self.assertEqual(names(), ["zebra", "apple"])
            self.assertTrue(app.tree.heading("changed", "text").endswith("▾"))
            app.sort_by("name")
            self.assertEqual(names(), ["apple", "zebra"])
            self.assertEqual(app.tree.heading("#0", "text"), "Project / export  ▴")
            self.assertEqual(app.tree.heading("changed", "text"), "Last changed")
            app.sort_by("name")
            self.assertEqual(names(), ["zebra", "apple"])
            saved.assert_called_with("project_library", {"sort_column": "name", "sort_descending": True})
            app.close()


if __name__ == "__main__":
    unittest.main()
