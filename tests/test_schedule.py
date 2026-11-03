"""Post Scheduler with a fake publisher and a temporary queue; nothing is posted and no login helper is installed."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from multitool.auto_poster import schedule
from multitool.auto_poster.accounts import Accounts
from multitool.auto_poster.publish import Publisher
from multitool.auto_poster.render import posting_formats, snapshot
from multitool.auto_poster.storage import load_settings, manifest

APPROVED = {"texts": {"title": "Beat", "caption": "Out now", "social_caption": "Out now #beat"},
            "accounts": {"youtube": "channel-A", "tiktok": "open-A", "instagram": "ig-A"}}


class Vault:
    def __init__(self, profiles):
        self.profiles = profiles
    def get(self, platform):
        return self.profiles.get(platform)
    def set(self, platform, value):
        self.profiles[platform] = value


class FakePublisher:
    def __init__(self, state="published", error=None, during=lambda: None):
        self.calls, self.state, self.error, self.during = [], state, error, during

    def publish_batch(self, receipts, platforms, options, cancelled, report, completed, approved):
        self.calls.append((list(receipts), platforms, options))
        self.during()
        for receipt in receipts:
            for platform in platforms:
                completed(receipt, platform, {"state": self.state, **({"error": self.error} if self.error else {})})
        return 0


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)  # holds only the queue JSON and lock files
        patcher = patch("multitool.auto_poster.schedule.DATA_DIR", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = load_settings()
        self.now = datetime.now().astimezone()

    def add(self, minutes, platforms=("youtube", "tiktok")):
        return schedule.add(Path("/fake/post.json"), "beat.wav", platforms, {"youtube_privacy": "private"},
                            self.now + timedelta(minutes=minutes), APPROVED)

    def states(self):
        return {e["id"]: e["state"] for e in schedule.load()}

    def test_posts_only_what_is_due_and_never_twice(self):
        due, later = self.add(-5), self.add(60)
        publisher = FakePublisher()
        handled = schedule.run_due(publisher, self.settings, now=self.now)
        self.assertEqual([e["id"] for e in handled], [due["id"]])
        self.assertEqual(publisher.calls, [([Path("/fake/post.json")], ["youtube", "tiktok"], {"youtube_privacy": "private"})])
        self.assertEqual(self.states(), {due["id"]: "posted", later["id"]: "waiting"})
        schedule.run_due(publisher, self.settings, now=self.now)
        self.assertEqual(len(publisher.calls), 1)  # a posted entry is never sent again

    def test_still_processing_is_finished_on_the_next_pass_and_errors_are_kept(self):
        sent = self.add(-1)
        schedule.run_due(FakePublisher("processing"), self.settings, now=self.now)
        self.assertEqual(self.states()[sent["id"]], "processing")
        self.assertIn("still processing", schedule.load()[0]["detail"])
        finish = FakePublisher()  # the real Publisher resumes the saved upload ID; it never uploads again
        schedule.run_due(finish, self.settings, now=self.now + timedelta(minutes=5))
        self.assertEqual((len(finish.calls), self.states()[sent["id"]]), (1, "posted"))
        failed = self.add(-1, ("instagram",))
        schedule.run_due(FakePublisher("failed", "token expired"), self.settings, now=self.now)
        entry = next(e for e in schedule.load() if e["id"] == failed["id"])
        self.assertEqual((entry["state"], entry["detail"]), ("failed", "Instagram: token expired"))

    def test_far_too_late_is_skipped_not_posted(self):
        old = self.add(-60 * (self.settings["schedule_max_late_hours"] + 1))
        publisher = FakePublisher()
        handled = schedule.run_due(publisher, self.settings, now=self.now)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(self.states()[old["id"]], "missed")
        self.assertIn("asleep", handled[0]["detail"])

    def test_cancelled_while_another_post_is_sending_is_never_sent(self):
        first, second = self.add(-10), self.add(-5)
        publisher = FakePublisher(during=lambda: schedule.cancel({second["id"]}))
        schedule.run_due(publisher, self.settings, now=self.now)
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(self.states(), {first["id"]: "posted", second["id"]: "cancelled"})

    def test_older_entries_without_approval_are_not_sent(self):
        old = {**self.add(-1), "id": "old", "approved": None}  # added outside the lock: _update can't nest
        schedule._update(lambda entries: entries.append(old))
        publisher = FakePublisher()
        schedule.run_due(publisher, self.settings, now=self.now)
        self.assertEqual(self.states()["old"], "failed")
        self.assertEqual(len(publisher.calls), 1)  # only the approved entry

    def test_cancel_and_interrupted_posts(self):
        keep, drop = self.add(30), self.add(40)
        schedule.cancel({drop["id"]})
        schedule._mark(keep["id"], state="posting")  # as if the Mac shut down mid-upload
        schedule.run_due(FakePublisher(), self.settings, now=self.now)
        self.assertEqual(self.states(), {keep["id"]: "check", drop["id"]: "cancelled"})

    def test_only_one_runner_posts_at_a_time(self):
        self.add(-1)
        publisher = FakePublisher()
        with schedule._lock("schedule-runner"):
            self.assertEqual(schedule.run_due(publisher, self.settings, now=self.now), [])
        self.assertEqual(publisher.calls, [])

    def test_helper_finishes_unfinished_uploads_even_with_nothing_scheduled(self):
        receipt = Path("/fake/beat-1/post.json")
        publisher = Mock()
        publisher.resume.return_value = [(receipt, "instagram", {"state": "published"})]
        with patch("multitool.auto_poster.publish.Publisher", return_value=publisher) as made, \
                patch("multitool.auto_poster.publish.pending", return_value=[(receipt, "instagram")]), \
                patch("multitool.auto_poster.results.receipts", return_value=[receipt]), \
                patch.object(schedule, "notify") as notify, patch("builtins.print"):
            self.assertEqual(schedule.run_from_helper(), 0)
        publisher.resume.assert_called_once_with([(receipt, "instagram")])
        self.assertEqual(notify.call_args.args[0], "beat-1 is now live on Instagram.")
        with patch("multitool.auto_poster.publish.Publisher") as made, \
                patch("multitool.auto_poster.publish.pending", return_value=[]), \
                patch("multitool.auto_poster.results.receipts", return_value=[]):
            self.assertEqual(schedule.run_from_helper(), 0)
        made.assert_not_called()  # nothing due, nothing unfinished: no account is touched

    @patch("multitool.platform.IS_MAC", True)
    @patch("os.getuid", return_value=501, create=True)
    def test_helper_runs_this_app_on_this_queue(self, getuid):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        with patch("multitool.auto_poster.schedule.Path.home", return_value=Path(home.name)), \
                patch("multitool.auto_poster.schedule.subprocess.run", return_value=Mock(returncode=0, stderr="")) as run:
            schedule.install_agent(self.settings)
            plist = plistlib.loads(schedule.agent_file().read_bytes())
            self.assertTrue(schedule.agent_installed())
            schedule.remove_agent()
            self.assertFalse(schedule.agent_installed())
        self.assertEqual(plist["ProgramArguments"][-1], "--run-schedule")
        self.assertEqual(plist["EnvironmentVariables"]["DAW_MULTITOOL_DATA_DIR"], str(schedule.DATA_DIR))
        self.assertEqual(plist["StartInterval"], self.settings["schedule_check_minutes"] * 60)
        self.assertEqual(run.call_args_list[1].args[0][:2], ["/bin/launchctl", "bootstrap"])


class SwitchingVault:
    """Keychain stand-in whose saved YouTube login changes from account A to B after the first read."""
    def __init__(self):
        self.reads = 0
    def get(self, platform):
        self.reads += 1
        account = "A" if self.reads == 1 else "B"
        return {"access_token": f"token-{account}", "channel_id": f"channel-{account}"}
    def set(self, platform, value):
        pass


class ApprovalTests(unittest.TestCase):
    """The real Publisher with a fake render and accounts: a scheduled post that no longer matches sends nothing,
    and the account that passed the check is the very one that uploads."""
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)  # fake receipt JSON and fake (non-audio) video bytes only
        self.settings = {**load_settings(), "upload_chunk_bytes": 1 << 20, "poll_seconds": 0, "poll_attempts": 1}
        video = Path(folder.name) / "video.mp4"
        video.write_bytes(b"fake rendered video")
        variants = {name: {"video": str(video), "video_snapshot": list(snapshot(video)), "width": w, "height": h}
                    for name, (w, h) in self.settings["formats"].items() if name in posting_formats(self.settings)}
        self.receipt = Path(folder.name) / "post.json"
        self.receipt.write_text(json.dumps({"state": "ready", "delivery": {}, "duration": 10, "variants": variants,
                                            **APPROVED["texts"]}))
        self.session = Mock()
        self.accounts = Accounts(self.settings, Vault({"youtube": {"access_token": "token-A", "channel_id": "channel-A"}}),
                                 self.session)

    def send(self):
        results = {}
        Publisher(self.settings, self.accounts).publish_batch(
            [self.receipt], ["youtube"], {"youtube_privacy": "private"}, approved=lambda receipt: APPROVED,
            completed=lambda receipt, platform, state: results.update({platform: state}))
        return results["youtube"]

    def test_switched_account_sends_nothing(self):
        self.accounts.vault.profiles["youtube"] = {"access_token": "token-B", "channel_id": "channel-B"}
        self.assertIn("different account", self.send()["error"])
        self.session.request.assert_not_called()
        self.assertEqual(manifest(self.receipt)["delivery"], {})  # receipt untouched

    def test_text_changed_after_scheduling_sends_nothing(self):
        self.receipt.write_text(json.dumps({**manifest(self.receipt), "title": "Some later title"}))
        self.assertIn("changed after you confirmed it", self.send()["error"])
        self.session.request.assert_not_called()
        self.assertEqual(manifest(self.receipt)["delivery"], {})

    def test_the_checked_login_is_the_one_that_uploads_even_if_keychain_changes(self):
        self.accounts.vault = SwitchingVault()  # Codex's race: A when checked, B on any later read
        self.session.request.side_effect = [
            Mock(status_code=200, headers={"Location": "https://www.googleapis.com/upload/test"}, json=Mock(return_value={})),
            Mock(status_code=200, headers={}, json=Mock(return_value={"id": "video-A"})),
            Mock(status_code=200, headers={}, json=Mock(return_value={"items": [
                {"id": "video-A", "status": {"uploadStatus": "processed", "privacyStatus": "private"}}]}))]
        self.assertEqual(self.send()["state"], "published")
        tokens = {call.kwargs["headers"]["Authorization"] for call in self.session.request.call_args_list}
        self.assertEqual(tokens, {"Bearer token-A"})
        self.assertEqual(self.accounts.vault.reads, 1)


if __name__ == "__main__":
    unittest.main()
