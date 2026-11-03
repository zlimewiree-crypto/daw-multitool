"""Host and settings checks; no audio files or GUI windows are used."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import app_paths
from multitool.app import MultitoolApp, main
from multitool.tools import TOOLS, Tool


class MultitoolTests(unittest.TestCase):
    def host(self):
        app = MultitoolApp.__new__(MultitoolApp)
        app.root, app.status, app.windows = Mock(), Mock(), {}
        return app

    def test_registry_ids_are_unique(self):
        self.assertEqual(len(TOOLS), len({t.id for t in TOOLS}))

    def test_dry_run_is_lazy_and_writes_nothing(self):
        with patch("multitool.app.initialize_data") as initialize, patch("multitool.app.tk.Tk") as window:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--dry-run"]), 0)
            self.assertIn("organizer", output.getvalue())
            initialize.assert_not_called()
            window.assert_not_called()
        probe = subprocess.run([sys.executable, "-X", "utf8", "-c",  # UTF-8 mode, as Windows runs use (notes/WINDOWS.md)
            "import sys; from multitool.app import main; main(['--dry-run']); "
            "assert 'drums' not in sys.modules and 'librosa' not in sys.modules"], capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_opening_same_tool_reuses_its_window(self):
        host, window, adapter = self.host(), Mock(), Mock()
        with patch("multitool.app.tk.Toplevel", return_value=window), patch(
            "multitool.app.importlib.import_module", return_value=adapter) as loader:
            host.open_tool(TOOLS[0])
            host.open_tool(TOOLS[0])
        loader.assert_called_once_with(TOOLS[0].module)
        adapter.open_tool.assert_called_once_with(window)
        window.lift.assert_called_once()

    def test_additional_tool_runs_independently(self):
        host = self.host()
        second = Tool("extra", "Another Tool", "Test extension", "example.tool")
        with patch("multitool.app.tk.Toplevel", side_effect=[Mock(), Mock()]), patch(
            "multitool.app.importlib.import_module", side_effect=[Mock(), Mock()]) as loader:
            host.open_tool(TOOLS[0])
            host.open_tool(second)
        self.assertEqual(set(host.windows), {"organizer", "extra"})
        self.assertEqual(loader.call_count, 2)

    def test_failed_tool_does_not_remove_another_tool(self):
        host = self.host()
        host.windows["other"] = (Mock(), Mock())
        with patch("multitool.app.tk.Toplevel"), patch("multitool.app.logging.exception"), patch(
            "multitool.app.messagebox.showerror") as error, patch(
            "multitool.app.importlib.import_module", side_effect=ImportError("missing dependency")):
            host.open_tool(TOOLS[0])
        self.assertEqual(set(host.windows), {"other"})
        error.assert_called_once()

    def test_quit_cancels_busy_tool_and_waits_for_it(self):
        host = self.host()
        controller = Mock(busy=True, cancel=threading.Event())
        host.windows["organizer"] = (Mock(), controller)
        host.close()
        self.assertTrue(controller.cancel.is_set())
        host.root.destroy.assert_not_called()
        controller.close.assert_not_called()
        controller.busy = False
        host.close()
        controller.close.assert_called_once()
        host.root.destroy.assert_called_once()

    def test_initialization_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_paths, "DATA_DIR", Path(directory)):
            app_paths.initialize_data()
            config = Path(directory) / "config.json"
            self.assertIn("bounce_sorter", json.loads(config.read_text()))
            config.write_text('{"personal": true}')
            app_paths.initialize_data()
            self.assertEqual(json.loads(config.read_text()), {"personal": True})
            self.assertFalse((Path(directory) / "labels.json").exists())

    def test_outdated_shipped_model_is_upgraded_but_a_trained_one_is_kept(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_paths, "DATA_DIR", Path(directory)):
            folder = Path(directory)
            model = folder / "drum_model.json"
            shipped = json.loads((app_paths.RESOURCE_DIR / "drum_model.json").read_text())
            stamped_old = {"source": "older app", "weights": {}, "means": {}, "scales": {}, "bias": 0, "origin": "shipped"}
            model.write_text(json.dumps(stamped_old))
            app_paths.initialize_data()  # the app shipped this copy: upgrade it, keep a backup
            self.assertEqual(json.loads(model.read_text()), {**shipped, "origin": "shipped"})
            backups = list(folder.glob("drum_model.previous-*.json"))
            self.assertEqual([json.loads(b.read_text()) for b in backups], [stamped_old])
            trained = {"source": "learned from 9 beats + 9 melodies", "weights": {"snap_noise": 1.0},
                       "means": {"snap_noise": 0.1}, "scales": {"snap_noise": 0.1}, "bias": 0.3}
            model.write_text(json.dumps(trained))
            app_paths.initialize_data()  # no stamp, unknown fingerprint: the user trained it, never replace
            self.assertEqual(json.loads(model.read_text()), trained)
            for odd in ("[]", '{"feature_version": "3"}', "not json"):
                model.write_text(odd)
                app_paths.initialize_data()  # damaged or odd files never stop the app from starting
                self.assertEqual(model.read_text(), odd)

    def test_first_copy_of_the_model_is_stamped_as_shipped(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_paths, "DATA_DIR", Path(directory)):
            app_paths.initialize_data()
            self.assertEqual(json.loads((Path(directory) / "drum_model.json").read_text())["origin"], "shipped")

    def test_data_override_reaches_organizer_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, DAW_MULTITOOL_DATA_DIR=directory)
            code = ("from pathlib import Path; import app_paths, sorter, drums; "
                    "assert sorter.CONFIG_FILE.parent == app_paths.DATA_DIR; "
                    "assert drums.LABELS_FILE.parent == app_paths.DATA_DIR; "
                    "assert drums.CACHE_FILE.parent == app_paths.DATA_DIR; "
                    "assert not list(app_paths.DATA_DIR.iterdir())")
            probe = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True)
            self.assertEqual(probe.returncode, 0, probe.stderr)


if __name__ == "__main__":
    unittest.main()
