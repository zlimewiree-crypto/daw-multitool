"""Results Tracker with fake receipts (JSON only) and fake platform replies; no account is contacted."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from multitool.auto_poster import results
from multitool.auto_poster.accounts import APIError
from multitool.auto_poster.storage import load_settings


def reply(payload, status=200):
    return Mock(status_code=status, headers={}, json=Mock(return_value=payload))


class ResultsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)  # holds only fake receipts and the results JSON
        self.base = Path(folder.name)
        patcher = patch("multitool.auto_poster.results.DATA_DIR", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = {**load_settings(), "output_folder": str(self.base / "out")}

    def receipt(self, name, delivery):
        path = self.base / "out" / name / "post.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"audio": f"/beats/{name}.wav", "title": name.title(), "delivery": delivery}))
        return path

    def test_reads_every_platform_and_puts_the_best_beat_first(self):
        self.receipt("quiet", {"youtube": {"state": "published", "remote_id": "yt2"}})
        self.receipt("hit", {"youtube": {"state": "published", "remote_id": "yt1", "url": "https://youtu.be/yt1"},
                             "instagram": {"state": "published", "post_id": "ig1"},
                             "tiktok": {"state": "published", "post_ids": ["tt1"]}})
        self.receipt("failed", {"youtube": {"state": "failed"}})  # never published: not tracked
        rows = results.posts(results.receipts(self.settings))
        self.assertEqual(sorted((r["track"], r["platform"]) for r in rows),
                         [("hit.wav", "instagram"), ("hit.wav", "tiktok"), ("hit.wav", "youtube"), ("quiet.wav", "youtube")])
        accounts = Mock(credentials=Mock(return_value={"access_token": "token"}))
        accounts.session.request.side_effect = [
            reply({"items": [{"id": "yt1", "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "4"}},
                             {"id": "yt2", "statistics": {"viewCount": "10"}}]}),
            reply({"like_count": 30, "comments_count": 2, "permalink": "https://www.instagram.com/reel/x/"}),
            reply({"data": [{"name": "views", "values": [{"value": 500}]}, {"name": "shares", "values": [{"value": 7}]}]}),
            reply({"data": {"videos": [{"id": "tt1", "view_count": 2000, "like_count": 300, "comment_count": 9,
                                        "share_count": 11, "share_url": "https://www.tiktok.com/@me/video/tt1"}]},
                   "error": {"code": "ok"}})]
        results.fetch(rows, accounts, self.settings)
        (best, totals, posts), (second, _, _) = results.by_track(rows)
        self.assertEqual((best, second), ("hit.wav", "quiet.wav"))
        self.assertEqual(totals, {"views": 3500, "likes": 380, "comments": 15, "shares": 18})
        tiktok = accounts.session.request.call_args_list[3]
        self.assertEqual(tiktok.kwargs["json"], {"filters": {"video_ids": ["tt1"]}})
        self.assertEqual(results.last_results()[1], rows)  # last numbers kept for next time

    def test_missing_ids_and_permissions_are_explained_not_fatal(self):
        self.receipt("private", {"tiktok": {"state": "published", "post_ids": []},
                                 "instagram": {"state": "published", "post_id": "ig1"}})
        rows = results.posts(results.receipts(self.settings))
        accounts = Mock(credentials=Mock(return_value={"access_token": "token"}))
        accounts.session.request.side_effect = [reply({"like_count": 3, "comments_count": 0}),
                                                reply({"error": {"code": 10}}, status=403)]
        results.fetch(rows, accounts, self.settings)
        rows = {r["platform"]: r for r in rows}
        self.assertIn("private TikTok", rows["tiktok"]["error"])
        self.assertEqual(rows["instagram"]["likes"], 3)
        self.assertIn("insights", rows["instagram"]["note"])

    def test_disconnected_platform_says_to_reconnect(self):
        self.receipt("beat", {"youtube": {"state": "published", "remote_id": "yt1"}})
        rows = results.posts(results.receipts(self.settings))
        accounts = Mock(credentials=Mock(side_effect=APIError("Connect Youtube in Accounts first.")))
        results.fetch(rows, accounts, self.settings)
        self.assertIn("Reconnect YouTube", rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
