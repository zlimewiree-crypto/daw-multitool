"""Regression checks for config transactions, batch scheduling and incremental UI work."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import app_paths
from multitool.auto_poster.accounts import APIError
from multitool.auto_poster.publish import Publisher
from multitool.auto_poster.storage import load_settings
from multitool.auto_poster import ui


class ConfigTransactionTests(unittest.TestCase):
    def test_simultaneous_thread_saves_keep_all_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"existing": true}')
            original = Path.read_text
            start = threading.Barrier(6)
            def slow_read(self, *args, **kwargs):
                value = original(self, *args, **kwargs)
                if self == path:
                    time.sleep(0.02)
                return value
            def save(index):
                start.wait(timeout=5)
                app_paths.update_config(f"tool_{index}", {"saved": True}, path)
            with patch.object(Path, "read_text", slow_read), ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(save, range(6)))
            self.assertEqual(set(json.loads(path.read_text())), {"existing", *(f"tool_{i}" for i in range(6))})

    def test_independent_process_saves_keep_all_sections(self):
        code = '''import sys, time
from pathlib import Path
from app_paths import update_config
path = Path(sys.argv[1])
original = Path.read_text
def slow_read(self, *args, **kwargs):
    value = original(self, *args, **kwargs)
    if self == path:
        time.sleep(0.05)
    return value
Path.read_text = slow_read
update_config(sys.argv[2], {"saved": True}, path)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"existing": true}')
            processes = [subprocess.Popen([sys.executable, "-c", code, str(path), f"process_{i}"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
            for process in processes:
                _, error = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, error)
            self.assertEqual(set(json.loads(path.read_text())), {"existing", *(f"process_{i}" for i in range(4))})

    def test_failed_atomic_write_preserves_previous_file_and_removes_only_temp_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.json"
            path.write_text('{"saved": true}')
            with patch.object(app_paths.json, "dump", side_effect=OSError("test disk failure")):
                with self.assertRaises(OSError):
                    app_paths.atomic_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text()), {"saved": True})
            self.assertEqual(list(Path(directory).iterdir()), [path])


class BatchSchedulingTests(unittest.TestCase):
    def publisher(self):
        publisher = Publisher({**load_settings(), "poll_attempts": 3}, accounts=Mock())
        publisher.pause = Mock()
        return publisher

    def test_all_uploads_are_submitted_before_waiting_for_processing(self):
        publisher = self.publisher()
        receipts = [Path("first.json").resolve(), Path("second.json").resolve()]
        events, attempts = [], Counter()
        def publish(receipt, platform, *args, **kwargs):
            self.assertFalse(kwargs["wait"])
            key = (receipt, platform)
            events.append(key)
            attempts[key] += 1
            return {"state": "processing" if platform == "instagram" and attempts[key] == 1 else "published"}
        publisher.publish = Mock(side_effect=publish)
        publisher.pause.side_effect = lambda cancelled: events.append("wait")
        self.assertEqual(publisher.publish_batch(receipts, ["instagram", "youtube"], {}), 0)
        self.assertEqual(events[:4], [(receipts[0], "instagram"), (receipts[0], "youtube"),
                                     (receipts[1], "instagram"), (receipts[1], "youtube")])
        self.assertEqual(publisher.pause.call_count, 2)

    def test_pending_checks_share_one_budget_and_visit_different_jobs(self):
        publisher = self.publisher()
        receipts = [Path(f"job-{i}.json").resolve() for i in range(5)]
        publisher.publish = Mock(return_value={"state": "processing"})
        self.assertEqual(publisher.publish_batch(receipts, ["instagram"], {}), 5)
        self.assertEqual(publisher.publish.call_count, 8)  # 5 initial submissions + 3 checks, not 5*3 checks
        polled = [call.args[0] for call in publisher.publish.call_args_list[5:]]
        self.assertEqual(polled, receipts[:3])

    def test_cancel_stops_before_next_platform(self):
        publisher = self.publisher()
        publisher.publish = Mock(return_value={"state": "published"})
        stop = threading.Event()
        publisher.publish_batch(["job.json"], ["instagram", "youtube"], {}, cancelled=stop.is_set,
                                completed=lambda *args: stop.set())
        publisher.publish.assert_called_once()
        publisher.pause.assert_not_called()

    def test_one_platform_failure_does_not_block_others(self):
        publisher = self.publisher()
        def publish(receipt, platform, *args, **kwargs):
            if platform == "instagram":
                raise APIError("test unavailable account")
            return {"state": "published"}
        publisher.publish = Mock(side_effect=publish)
        completed = Mock()
        publisher.publish_batch(["job.json"], ["instagram", "youtube"], {}, completed=completed)
        self.assertEqual([call.args[2]["state"] for call in completed.call_args_list], ["failed", "published"])


class HistoryEfficiencyTests(unittest.TestCase):
    def test_three_changed_platforms_read_three_receipts_and_edit_three_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            app = ui.AutoPosterApp.__new__(ui.AutoPosterApp)
            app.jobs, app.history_rows, app.history = {}, {}, Mock()
            app.history.get_children.return_value = []
            app.history.insert.side_effect = (str(i) for i in range(1000))
            for index in range(100):
                path = Path(directory) / f"{index}.json"
                path.write_text(json.dumps({"title": "Test video", "delivery": {
                    platform: {"state": "processing"} for platform in ("youtube", "instagram", "tiktok")}}))
                app.jobs[str(index)] = {"receipt": path}
            app.refresh_history()
            self.assertEqual(app.history.insert.call_count, 300)
            app.history.reset_mock()
            changed = app.jobs["0"]["receipt"]
            with patch.object(ui, "manifest", wraps=ui.manifest) as reads:
                for platform in ("youtube", "instagram", "tiktok"):
                    record = json.loads(changed.read_text())
                    record["delivery"][platform]["state"] = "published"
                    changed.write_text(json.dumps(record))
                    app.refresh_history(changed)
                self.assertEqual(reads.call_count, 3)
            self.assertEqual(app.history.item.call_count, 3)
            app.history.insert.assert_not_called()
            app.history.delete.assert_not_called()
