"""Fake-media rendering and mocked official API tests. Never uses real accounts."""
import base64
import hashlib
import importlib.util
import io
import json
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
import uuid
import zlib
from dataclasses import replace

import requests

from multitool.auto_poster.accounts import Accounts, APIError
from multitool.auto_poster.publish import Publisher, safe_upload_url
from multitool.auto_poster.render import Cancelled, VideoRequest, check_plan, create_video, ffmpeg_binary, media_info, posting_formats, snapshot
from multitool.auto_poster.storage import atomic_json, load_settings, manifest, social_caption, video_variant

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "test_exports").mkdir(exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="auto_poster_test_", dir=ROOT / "test_exports"))
Path("/private/tmp/daw-auto-poster-test-path.txt").write_text(str(BASE))


def archive_test_media():
    """Keep fake audio recoverable without leaving test outputs in the vault."""
    if not BASE.exists():
        return
    archive = Path(tempfile.mkdtemp(prefix="daw-auto-poster-tests-", dir="/private/tmp"))
    destination = archive / BASE.name
    with (archive / "_log.txt").open("a") as log:
        for status in ("started", "done"):
            if status == "done":
                shutil.move(str(BASE), str(destination))
            log.write(f"{datetime.now().isoformat(timespec='seconds')} | {status} | move | {BASE} | {destination}\n")
            log.flush()
            os.fsync(log.fileno())
    Path("/private/tmp/daw-auto-poster-test-path.txt").write_text(str(destination))


def setUpModule():
    # Registered here, not at import, so it runs when THIS module's tests finish (see test_organizer)
    unittest.addModuleCleanup(archive_test_media)


def png(path):
    def chunk(kind, value):
        return struct.pack("!I", len(value)) + kind + value + struct.pack("!I", zlib.crc32(kind + value) & 0xffffffff)
    raw = b"".join(b"\0" + bytes([25, 80, 140]) * 160 for _ in range(90))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 160, 90, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def response(payload=None, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=payload or {}))


class Vault:
    def __init__(self, profiles=None):
        self.profiles = profiles or {}
    def get(self, platform):
        return self.profiles.get(platform)
    def set(self, platform, value):
        self.profiles[platform] = value


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("fake_audio", ROOT / "scripts/make_test_files.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        cls.audio = BASE / "test 'audio'.wav"
        generator.make_wav(cls.audio, 3)
        cls.visual = BASE / "still image.png"
        png(cls.visual)
        cls.settings = {**load_settings(), "formats": {"Test": [160, 284]}, "fps": 15,
                        "preset": "ultrafast", "crf": 28, "settle_seconds": 0.01}
        cls.video = BASE / "short visual with original sound.mp4"
        subprocess.run([ffmpeg_binary(cls.settings), "-nostdin", "-n", "-loglevel", "error", "-loop", "1",
            "-i", str(cls.visual), "-f", "lavfi", "-i", "sine=frequency=880:duration=0.7", "-t", "0.7",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.video)], check=True)

    def request(self, visual=None):
        return VideoRequest(self.audio, visual or self.visual, BASE / "renders", "Test", title="Test title", caption="Test caption")

    def test_dry_run_creates_nothing_and_preserves_originals(self):
        request = self.request()
        original = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        before = set(BASE.rglob("*"))
        plan = check_plan(request, self.settings)
        self.assertEqual(plan["seconds"], 3)
        self.assertFalse(plan["publishes"])
        self.assertEqual(before, set(BASE.rglob("*")))
        self.assertEqual(original, hashlib.sha256(self.audio.read_bytes()).hexdigest())

    def test_still_image_render_is_playable_and_never_overwrites(self):
        before = self.audio.read_bytes()
        first = manifest(create_video(self.request(), self.settings))
        second = manifest(create_video(self.request(), self.settings))
        video = video_variant(first, "Test")["video"]
        self.assertNotEqual(video, video_variant(second, "Test")["video"])
        info = media_info(Path(video), self.settings)
        self.assertTrue(info["audio"] and info["video"])
        self.assertAlmostEqual(info["duration"], 3, delta=0.2)
        self.assertEqual(self.audio.read_bytes(), before)
        self.assertEqual(first["state"], "ready")

    def test_still_image_read_once_a_second_still_fills_every_frame(self):
        request = VideoRequest(self.audio, self.visual, BASE / "renders", "Test", duration=2.4)
        video = video_variant(manifest(create_video(request, self.settings)), "Test")["video"]
        count = subprocess.run([ffmpeg_binary(self.settings), "-v", "error", "-i", video, "-map", "0:v", "-f", "null",
                                "-", "-stats"], capture_output=True, text=True).stderr
        self.assertEqual(re.findall(r"frame=\s*(\d+)", count)[-1], "36")  # 2.4 s at 15 fps, last 0.4 s included

    def test_rendered_mp4_meets_the_reels_spec_and_wrong_length_is_refused(self):
        from multitool.auto_poster.render import check_output
        video = Path(video_variant(manifest(create_video(self.request(), self.settings)), "Test")["video"])
        data = video.read_bytes()
        moov = data[:data.index(b"mdat")]  # +faststart puts the header (moov) before the media data
        self.assertIn(b"moov", moov)
        self.assertNotIn(b"elst", moov)  # Instagram: no MP4 edit lists
        with self.assertRaisesRegex(ValueError, "instead of 9.0 s"):
            check_output(video, 9, self.settings)

    def test_short_video_loops_and_its_original_sound_is_replaced(self):
        record = manifest(create_video(self.request(self.video), self.settings))
        video = video_variant(record, "Test")["video"]
        self.assertAlmostEqual(media_info(Path(video), self.settings)["duration"], 3, delta=0.2)
        probe = subprocess.run([ffmpeg_binary(self.settings), "-i", video, "-af", "volumedetect",
                                "-f", "null", "-"], capture_output=True, text=True)
        self.assertRegex(probe.stderr, r"max_volume: -(?:inf|[89]\d(?:\.\d+)?) dB")

    def test_cancellation_before_render_creates_no_output(self):
        before = set(BASE.rglob("*"))
        with self.assertRaises(Cancelled):
            create_video(self.request(), self.settings, cancelled=lambda: True)
        self.assertEqual(before, set(BASE.rglob("*")))

    def test_changing_audio_is_rejected(self):
        with patch("multitool.auto_poster.render.snapshot", side_effect=[(10, 1), (10, 1), (11, 2)]):
            with self.assertRaisesRegex(ValueError, "still changing"):
                create_video(self.request(), self.settings)

    def test_trim_clamps_to_audio_end(self):
        request = VideoRequest(self.audio, self.visual, BASE / "renders", "Test", start=2, duration=100)
        self.assertEqual(check_plan(request, self.settings)["seconds"], 1)

    def test_batch_plans_probe_the_shared_visual_once(self):
        from multitool.auto_poster import render
        render._probe.cache_clear()
        with patch.object(render.subprocess, "run", wraps=render.subprocess.run) as probes:
            for index in range(3):
                audio = BASE / f"cached audio {index}.wav"
                shutil.copy2(self.audio, audio)
                check_plan(VideoRequest(audio, self.visual, BASE / "unused", "Test"), self.settings)
            self.assertEqual(probes.call_count, 4)  # three tracks plus one shared visual

    def test_metadata_cache_invalidates_when_modified_time_changes(self):
        from multitool.auto_poster import render
        render._probe.cache_clear()
        path = BASE / "cache invalidation.wav"
        shutil.copy2(self.audio, path)
        with patch.object(render.subprocess, "run", wraps=render.subprocess.run) as probes:
            first = media_info(path, self.settings)
            first["duration"] = -1  # callers must not mutate a cached result
            self.assertGreater(media_info(path, self.settings)["duration"], 0)
            self.assertEqual(probes.call_count, 1)
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            media_info(path, self.settings)
            self.assertEqual(probes.call_count, 2)

    def pair_settings(self):
        return {**self.settings, "formats": {"Landscape 16:9": [160, 90], "Portrait 9:16": [90, 160]}}

    def test_pair_plan_is_read_only_and_both_rendered_versions_have_the_right_dimensions(self):
        settings = self.pair_settings()
        formats = posting_formats(settings)
        before = set(BASE.rglob("*"))
        original = self.audio.read_bytes()
        plan = check_plan(self.request(), settings, formats=formats)
        self.assertEqual(plan["videos"], 2)
        self.assertIn("16:9", plan["format"])
        self.assertIn("9:16", plan["format"])
        self.assertEqual(before, set(BASE.rglob("*")))
        videos = set()
        for visual in (self.visual, self.video):
            record = manifest(create_video(self.request(visual), settings, formats=formats))
            self.assertEqual(record["state"], "ready")
            self.assertEqual(set(record["variants"]), set(formats))
            for name, dimensions in settings["formats"].items():
                version = video_variant(record, name)
                videos.add(version["video"])
                probe = subprocess.run([ffmpeg_binary(settings), "-i", version["video"]], capture_output=True, text=True)
                self.assertIn(f"{dimensions[0]}x{dimensions[1]}", probe.stderr)
                self.assertRegex(probe.stderr, r"Audio: aac")
                self.assertAlmostEqual(media_info(Path(version["video"]), settings)["duration"], 3, delta=0.2)
        self.assertEqual(len(videos), 4)
        self.assertEqual(self.audio.read_bytes(), original)

    def test_the_short_version_has_its_own_trim_and_its_receipt_says_so(self):
        settings = self.pair_settings()
        formats = posting_formats(settings)
        request = replace(self.request(), short_start=1.0, short_duration=1.5)
        plan = check_plan(request, settings, formats=formats)
        self.assertAlmostEqual(plan["seconds_by_format"][formats[1]], 1.5)
        record = manifest(create_video(request, settings, formats=formats))
        wide, tall = (video_variant(record, name) for name in formats)
        self.assertEqual((wide["start"], tall["start"]), (0, 1.0))
        self.assertAlmostEqual(wide["duration"], 3, delta=0.2)
        self.assertAlmostEqual(tall["duration"], 1.5, delta=0.01)  # what publishing checks against platform limits
        self.assertAlmostEqual(media_info(Path(tall["video"]), settings)["duration"], 1.5, delta=0.2)
        self.assertAlmostEqual(media_info(Path(wide["video"]), settings)["duration"], 3, delta=0.2)

    def test_no_background_uses_the_cover_art_or_a_gradient_with_waveform_and_text(self):
        settings = self.pair_settings()
        formats = posting_formats(settings)
        before = set(BASE.rglob("*"))
        request = replace(self.request(), visual=None, waveform=True, overlay=("It's 100% \"done\": yes", "155 BPM · F minor"))
        self.assertEqual(check_plan(request, settings, formats=formats)["visual"], "automatic")
        self.assertEqual(before, set(BASE.rglob("*")))  # planning makes nothing
        from multitool.auto_poster import render
        with patch.object(render, "DATA_DIR", BASE / "app-data"):  # the on-video text files go to app data, then away
            record = manifest(create_video(request, settings, formats=formats))
        self.assertEqual(list((BASE / "app-data" / "video-text").glob("*")), [])
        self.assertEqual((Path(record["visual"]).name, record["fit"], record["state"]), ("background.png", "Fill", "ready"))
        for name in formats:
            self.assertTrue(media_info(Path(video_variant(record, name)["video"]), settings)["video"])
        covered = BASE / "with cover.mp3"
        subprocess.run([ffmpeg_binary(settings), "-nostdin", "-n", "-loglevel", "error", "-i", str(self.audio), "-i", str(self.visual),
                        "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "png", "-id3v2_version", "3",
                        "-disposition:v", "attached_pic", str(covered)], check=True)
        record = manifest(create_video(replace(request, audio=covered, overlay=()), settings, formats=formats))
        self.assertEqual((Path(record["visual"]).name, record["fit"]), ("cover.png", "Blur"))

    def test_cancel_between_versions_keeps_first_video_but_receipt_cannot_publish(self):
        settings = self.pair_settings()
        request = VideoRequest(self.audio, self.visual, BASE / "cancelled-pair", "Landscape 16:9")
        stopped = [False]
        def progress(percent):
            if percent >= 50:
                stopped[0] = True
        with self.assertRaises(Cancelled):
            create_video(request, settings, cancelled=lambda: stopped[0], progress=progress, formats=posting_formats(settings))
        receipt = next(request.output.glob("*/post.json"))
        record = manifest(receipt)
        self.assertEqual(record["state"], "cancelled")
        self.assertEqual(len(record["variants"]), 1)
        self.assertTrue(Path(next(iter(record["variants"].values()))["video"]).is_file())
        with self.assertRaisesRegex(ValueError, "successfully"):
            Publisher(settings, accounts=Mock()).publish(receipt, "youtube", {})

    def test_wrong_platform_aspect_is_rejected(self):
        settings = self.pair_settings()
        settings["formats"]["Landscape 16:9"] = [90, 160]
        with self.assertRaisesRegex(ValueError, "16:9"):
            posting_formats(settings)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**load_settings(), "upload_chunk_bytes": 8, "poll_seconds": 0, "poll_attempts": 2}
        self.folder = BASE / uuid.uuid4().hex
        self.folder.mkdir()
        self.video = self.folder / "video.mp4"
        self.video.write_bytes(b"mock generated video bytes")
        self.receipt = self.folder / "post.json"
        atomic_json(self.receipt, {"version": 2, "state": "ready", "variants": {
            name: {"video": str(self.video), "video_snapshot": list(snapshot(self.video)), "width": width, "height": height}
            for name, (width, height) in self.settings["formats"].items() if name in posting_formats(self.settings)},
            "title": "Test title", "caption": "Test caption", "duration": 10, "delivery": {}})
        self.vault = Vault({
            "youtube": {"access_token": "fake-youtube-token", "channel_id": "UC-test"},
            "instagram": {"access_token": "fake-instagram-token", "user_id": "123"},
            "tiktok": {"access_token": "fake-tiktok-token", "open_id": "open-test"}})
        self.session = Mock()
        self.accounts = Accounts(self.settings, self.vault, self.session)
        self.publisher = Publisher(self.settings, self.accounts)
        self.options = {"youtube_privacy": "private", "made_for_kids": False,
                        "tiktok_privacy": "SELF_ONLY", "tiktok_consent": True}

    def creator(self):
        return response({"data": {"creator_nickname": "Test creator", "creator_username": "tester",
            "privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60, "comment_disabled": True},
            "error": {"code": "ok"}})

    @staticmethod
    def processed(video_id, privacy="private"):
        """YouTube's videos.list reply once it has finished processing an upload."""
        return response({"items": [{"id": video_id, "status": {"uploadStatus": "processed", "privacyStatus": privacy}}]})

    @staticmethod
    def link():
        return response({"permalink": "https://www.instagram.com/reel/test/"})

    @staticmethod
    def chunks(count=3):
        """TikTok answers 206 per chunk and 201 once the whole file arrived."""
        return [response(status=206)] * (count - 1) + [response(status=201)]

    def test_youtube_resumable_chunks_and_duplicate_prevention(self):
        count = (self.video.stat().st_size + 7) // 8
        self.session.request.side_effect = [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
            *[response(status=308, headers={"Range": f"bytes=0-{(index + 1) * 8 - 1}"}) for index in range(count - 1)],
            response({"id": "video-id"}), self.processed("video-id")]
        result = self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(result["state"], "published")
        self.assertEqual(self.session.request.call_args_list[0].kwargs["json"]["status"]["privacyStatus"], "private")
        self.assertEqual(self.session.request.call_args_list[0].kwargs["json"]["snippet"]["categoryId"], "10")  # Music
        self.assertEqual(result["account"], "UC-test")
        calls = self.session.request.call_count
        self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(self.session.request.call_count, calls)

    def test_tiktok_uses_live_options_chunk_remainder_and_status(self):
        self.session.request.side_effect = [self.creator(), response({"data": {
            "publish_id": "tik-id", "upload_url": "https://open-upload.tiktokapis.com/video/upload"}, "error": {"code": "ok"}}),
            *self.chunks(), response({"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7351]},
                                      "error": {"code": "ok"}})]
        result = self.publisher.publish(self.receipt, "tiktok", self.options)
        self.assertEqual(result["state"], "published")
        self.assertEqual(result["url"], "https://www.tiktok.com/@tester/video/7351")
        init = self.session.request.call_args_list[1].kwargs["json"]
        self.assertEqual(init["source_info"]["total_chunk_count"], 3)
        self.assertTrue(init["post_info"]["disable_comment"])
        uploads = [call for call in self.session.request.call_args_list if call.args[0] == "PUT"]
        self.assertEqual(b"".join(call.kwargs["data"] for call in uploads), self.video.read_bytes())

    def test_instagram_direct_file_upload_then_container_publish(self):
        self.session.request.side_effect = [response({"id": "container", "uri": "https://rupload.facebook.com/ig-api-upload/test"}),
            response({"success": True}), response({"status_code": "FINISHED"}), response({"id": "ig-post"}), self.link()]
        result = self.publisher.publish(self.receipt, "instagram", self.options)
        self.assertEqual(result["post_id"], "ig-post")
        self.assertEqual(result["url"], "https://www.instagram.com/reel/test/")
        self.assertEqual(self.session.request.call_args_list[0].kwargs["data"]["audio_name"], "Test title")
        self.assertEqual(self.session.request.call_args_list[1].kwargs["headers"]["offset"], "0")
        self.assertEqual(manifest(self.receipt)["delivery"]["instagram"]["state"], "published")

    def test_youtube_keeps_its_description_while_reels_get_description_and_hashtags(self):
        record = manifest(self.receipt)
        record["social_caption"] = social_caption("new beat out now", "typebeat, #rage typebeat")
        atomic_json(self.receipt, record)
        self.assertEqual(record["social_caption"], "new beat out now\n\n#typebeat #rage")
        self.session.request.side_effect = [response({"id": "container", "uri": "https://rupload.facebook.com/ig-api-upload/test"}),
            response({"success": True}), response({"status_code": "FINISHED"}), response({"id": "ig-post"}), self.link()]
        self.publisher.publish(self.receipt, "instagram", self.options)
        self.assertEqual(self.session.request.call_args_list[0].kwargs["data"]["caption"], "new beat out now\n\n#typebeat #rage")
        count = (self.video.stat().st_size + 7) // 8
        self.session.reset_mock()
        self.session.request.side_effect = [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
            *[response(status=308, headers={"Range": f"bytes=0-{(index + 1) * 8 - 1}"}) for index in range(count - 1)],
            response({"id": "video-id"}), self.processed("video-id")]
        self.publisher.publish(self.receipt, "youtube", self.options)
        snippet = self.session.request.call_args_list[0].kwargs["json"]["snippet"]
        self.assertEqual((snippet["title"], snippet["description"]), ("Test title", "Test caption"))

    def test_platforms_receive_the_correct_version_bytes(self):
        portrait = self.folder / "portrait.mp4"
        portrait.write_bytes(b"V" * self.video.stat().st_size)
        record = manifest(self.receipt)
        record["variants"][self.settings["platform_formats"]["tiktok"]].update(
            video=str(portrait), video_snapshot=list(snapshot(portrait)))
        atomic_json(self.receipt, record)
        youtube_chunks = (self.video.stat().st_size + 7) // 8
        cases = {
            "youtube": [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
                *[response(status=308, headers={"Range": f"bytes=0-{(i + 1) * 8 - 1}"}) for i in range(youtube_chunks - 1)],
                response({"id": "wide-youtube"}), self.processed("wide-youtube")],
            "instagram": [response({"id": "ig-container", "uri": "https://rupload.facebook.com/ig-api-upload/test"}),
                response({"success": True}), response({"status_code": "FINISHED"}), response({"id": "vertical-ig"}), self.link()],
            "tiktok": [self.creator(), response({"data": {"publish_id": "vertical-tik",
                "upload_url": "https://open-upload.tiktokapis.com/video/upload"}, "error": {"code": "ok"}}),
                *self.chunks(), response({"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})]}
        for platform, replies in cases.items():
            with self.subTest(platform=platform):
                sent, replies = [], iter(replies)
                def serve(*args, **kwargs):
                    body = kwargs.get("data")
                    if hasattr(body, "read"):
                        sent.append(body.read())
                    elif isinstance(body, bytes):
                        sent.append(body)
                    return next(replies)
                self.session.request.side_effect = serve
                result = self.publisher.publish(self.receipt, platform, self.options)
                self.assertEqual(result["state"], "published")
                self.assertEqual(b"".join(sent), (self.video if platform == "youtube" else portrait).read_bytes())
                self.assertEqual(result["format"], self.settings["platform_formats"][platform])

    def test_missing_alternate_version_blocks_upload_before_contacting_provider(self):
        record = manifest(self.receipt)
        record["variants"][self.settings["platform_formats"]["tiktok"]]["video"] = str(self.folder / "missing.mp4")
        atomic_json(self.receipt, record)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.session.request.assert_not_called()

    def test_old_single_video_must_be_recreated_before_a_new_upload(self):
        record = manifest(self.receipt)
        record.update(version=1, format="Landscape 16:9", **record.pop("variants")["Landscape 16:9"])
        atomic_json(self.receipt, record)
        with self.assertRaisesRegex(ValueError, "both 16:9 and 9:16"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.session.request.assert_not_called()

    def test_old_single_video_can_still_finish_an_existing_instagram_container(self):
        record = manifest(self.receipt)
        record.update(version=1, format="Portrait 9:16", **record.pop("variants")["Portrait 9:16"])
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "old", "uploaded": True}
        atomic_json(self.receipt, record)
        self.session.request.side_effect = [response({"status_code": "FINISHED"}), response({"id": "finished-old"}), self.link()]
        result = self.publisher.publish(self.receipt, "instagram", self.options)
        self.assertEqual(result["post_id"], "finished-old")
        self.assertEqual(self.session.request.call_count, 3)  # status/publish/link only; no fresh container or transfer

    def test_preview_buttons_select_their_corresponding_saved_version(self):
        from multitool.auto_poster.ui import AutoPosterApp
        portrait = self.folder / "portrait-preview.mp4"
        portrait.write_bytes(b"fake vertical video")
        record = manifest(self.receipt)
        record["variants"]["Portrait 9:16"]["video"] = str(portrait)
        atomic_json(self.receipt, record)
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.jobs, app.settings = {"row": {"receipt": self.receipt}}, self.settings
        app.selected_rows = lambda: ["row"]
        with patch("multitool.auto_poster.ui.subprocess.Popen") as opened:
            app.preview_selected()
            app.preview_selected("tiktok")
        self.assertEqual([call.args[0][-1] for call in opened.call_args_list], [str(self.video), str(portrait)])

    def test_nonblocking_instagram_checks_once_without_sleep(self):
        record = manifest(self.receipt)
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "existing", "uploaded": True}
        atomic_json(self.receipt, record)
        self.session.request.return_value = response({"status_code": "IN_PROGRESS"})
        with patch.object(self.publisher, "pause") as pause:
            result = self.publisher.publish(self.receipt, "instagram", self.options, wait=False)
        self.assertEqual(result["state"], "processing")
        self.session.request.assert_called_once()
        pause.assert_not_called()

    def test_batch_uploads_youtube_before_resuming_instagram_without_reupload(self):
        self.settings["upload_chunk_bytes"] = self.video.stat().st_size
        self.session.request.side_effect = [
            response({"id": "container", "uri": "https://rupload.facebook.com/ig-api-upload/test"}),
            response({"success": True}), response({"status_code": "IN_PROGRESS"}),
            response(headers={"Location": "https://www.googleapis.com/upload/test"}), response({"id": "youtube-id"}),
            self.processed("youtube-id"), response({"status_code": "FINISHED"}), response({"id": "ig-post"}), self.link()]
        with patch.object(self.publisher, "pause"):
            remaining = self.publisher.publish_batch([self.receipt], ["instagram", "youtube"], self.options)
        self.assertEqual(remaining, 0)
        calls = self.session.request.call_args_list
        # Instagram init/upload/check, YouTube init/upload/check, same Instagram ID: check/publish/link
        self.assertEqual(len(calls), 9)
        self.assertIn("googleapis.com", calls[3].args[1])
        self.assertEqual(calls[2].args[1], calls[6].args[1])
        delivery = manifest(self.receipt)["delivery"]
        self.assertEqual(delivery["instagram"]["post_id"], "ig-post")
        self.assertEqual(delivery["youtube"]["remote_id"], "youtube-id")

    def test_processing_tiktok_retry_polls_existing_id_without_duplicate_upload(self):
        record = manifest(self.receipt)
        record["delivery"]["tiktok"] = {"state": "processing", "remote_id": "existing-id", "uploaded": True}
        atomic_json(self.receipt, record)
        self.session.request.return_value = response({"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})
        self.publisher.publish(self.receipt, "tiktok", self.options)
        self.session.request.assert_called_once()
        self.assertTrue(self.session.request.call_args.args[1].endswith("/status/fetch/"))

    def test_uncertain_youtube_result_blocks_automatic_retry(self):
        self.session.request.side_effect = requests.ConnectionError("secret-signed-url-must-not-appear")
        with self.assertRaises(APIError) as caught:
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertNotIn("secret-signed-url", str(caught.exception))
        self.assertEqual(manifest(self.receipt)["delivery"]["youtube"]["state"], "uncertain")
        self.session.reset_mock()
        with self.assertRaisesRegex(APIError, "unknown result"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.session.request.assert_not_called()

    def test_invalid_tiktok_visibility_never_initializes_post(self):
        self.session.request.return_value = self.creator()
        with self.assertRaisesRegex(ValueError, "choose an available"):
            self.publisher.publish(self.receipt, "tiktok", {**self.options, "tiktok_privacy": "PUBLIC_TO_EVERYONE"})
        self.session.request.assert_called_once()

    def test_changed_video_is_not_uploaded(self):
        self.video.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.session.request.assert_not_called()

    def test_untrusted_upload_url_is_rejected(self):
        with self.assertRaises(APIError):
            safe_upload_url("https://evil.example/steal", "youtube")

    def test_stop_before_publish_never_contacts_provider(self):
        with self.assertRaises(Cancelled):
            self.publisher.publish(self.receipt, "youtube", self.options, cancelled=lambda: True)
        self.session.request.assert_not_called()

    def test_missing_account_is_recorded_without_contacting_provider(self):
        self.vault.profiles.pop("youtube")
        with self.assertRaisesRegex(APIError, "Connect"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(manifest(self.receipt)["delivery"]["youtube"]["state"], "failed")
        self.session.request.assert_not_called()

    def test_server_error_does_not_allow_duplicate_youtube_retry(self):
        self.session.request.return_value = response({"error": {"code": 500}}, status=500)
        with self.assertRaises(APIError):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(manifest(self.receipt)["delivery"]["youtube"]["state"], "uncertain")

    def test_known_youtube_id_is_not_reuploaded_after_receipt_failure(self):
        record = manifest(self.receipt)
        record["delivery"]["youtube"] = {"state": "failed", "remote_id": "confirmed-id", "error": "receipt error"}
        atomic_json(self.receipt, record)
        self.session.request.return_value = self.processed("confirmed-id")
        result = self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(result["state"], "published")
        self.assertNotIn("error", result)
        self.session.request.assert_called_once()  # a status check only, no second upload
        self.assertEqual(self.session.request.call_args.args[:2], ("GET", self.settings["youtube_api_url"] + "/videos"))

    def test_cancel_during_instagram_processing_does_not_publish(self):
        record = manifest(self.receipt)
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "existing", "uploaded": True}
        atomic_json(self.receipt, record)
        stop = [False]
        def finished(*args, **kwargs):
            stop[0] = True
            return response({"status_code": "FINISHED"})
        self.session.request.side_effect = finished
        result = self.publisher.publish(self.receipt, "instagram", self.options, cancelled=lambda: stop[0])
        self.assertEqual(result["state"], "processing")
        self.session.request.assert_called_once()
        self.assertEqual(self.session.request.call_args.args[0], "GET")

    def test_failed_instagram_upload_retry_uses_fresh_container(self):
        container = lambda name: response({"id": name, "uri": "https://rupload.facebook.com/ig-api-upload/" + name})
        self.session.request.side_effect = [container("empty"), response({"error": {"code": 400}}, status=400)]
        with self.assertRaises(APIError):
            self.publisher.publish(self.receipt, "instagram", self.options)
        self.session.request.side_effect = [container("fresh"), response({"success": True}),
            response({"status_code": "FINISHED"}), response({"id": "ig-post"}), self.link()]
        result = self.publisher.publish(self.receipt, "instagram", self.options)
        self.assertEqual((result["remote_id"], result["post_id"]), ("fresh", "ig-post"))

    def tiktok_unfinished(self):
        record = manifest(self.receipt)
        record["delivery"]["tiktok"] = {"state": "failed", "remote_id": "old-id", "uploaded": False}
        atomic_json(self.receipt, record)

    def test_tiktok_retry_reuploads_only_after_old_attempt_failed(self):
        self.tiktok_unfinished()
        status = lambda value: response({"data": {"status": value}, "error": {"code": "ok"}})
        self.session.request.side_effect = [status("FAILED"), self.creator(), response({"data": {
            "publish_id": "new-id", "upload_url": "https://open-upload.tiktokapis.com/video/upload"}, "error": {"code": "ok"}}),
            *self.chunks(), status("PUBLISH_COMPLETE")]
        result = self.publisher.publish(self.receipt, "tiktok", self.options)
        self.assertEqual((result["state"], result["remote_id"]), ("published", "new-id"))

    def test_tiktok_retry_waits_while_old_upload_is_open(self):
        self.tiktok_unfinished()
        self.session.request.return_value = response({"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})
        with self.assertRaisesRegex(APIError, "earlier unfinished upload"):
            self.publisher.publish(self.receipt, "tiktok", self.options)
        self.session.request.assert_called_once()  # no second post was started

    def test_youtube_dropped_chunk_resumes_where_youtube_says_it_stopped(self):
        size = self.video.stat().st_size
        sent = []
        replies = iter([response(headers={"Location": "https://www.googleapis.com/upload/test"}),
                        response(status=308, headers={"Range": "bytes=0-7"}),
                        requests.ConnectionError("dropped"),
                        response(status=308, headers={"Range": "bytes=0-15"}),  # YouTube got the second chunk anyway
                        *[response(status=308, headers={"Range": f"bytes=0-{end}"}) for end in range(23, size - 1, 8)],
                        response({"id": "video-id"}), self.processed("video-id")])
        def serve(method, url, **kwargs):
            reply = next(replies)
            if kwargs.get("data"):
                sent.append(kwargs["headers"]["Content-Range"])
            if isinstance(reply, Exception):
                raise reply
            return reply
        self.session.request.side_effect = serve
        with patch.object(self.publisher, "pause"):
            result = self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(result["state"], "published")
        self.assertEqual(sent[:3], [f"bytes 0-7/{size}", f"bytes 8-15/{size}", f"bytes 16-23/{size}"])
        asked = [c for c in self.session.request.call_args_list if c.kwargs.get("headers", {}).get("Content-Range") == f"bytes */{size}"]
        self.assertEqual(len(asked), 1)  # asked once where it stopped; one upload session, so one video

    def test_youtube_gives_up_after_its_retries_without_a_second_upload(self):
        self.session.request.side_effect = [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
                                            *[requests.ConnectionError("down")] * 10]
        with patch.object(self.publisher, "pause"), self.assertRaises(APIError):
            self.publisher.publish(self.receipt, "youtube", self.options)
        posts = [c for c in self.session.request.call_args_list if c.args[0] == "POST"]
        self.assertEqual(len(posts), 1)  # one upload session only
        self.assertEqual(self.session.request.call_count, 2 + self.settings["upload_retries"])  # init, chunk, retries
        self.assertEqual(manifest(self.receipt)["delivery"]["youtube"]["state"], "uncertain")

    def test_youtube_rejection_and_changed_privacy_are_reported(self):
        self.settings["upload_chunk_bytes"] = self.video.stat().st_size
        rejected = response({"items": [{"id": "v", "status": {"uploadStatus": "rejected", "rejectionReason": "duplicate"}}]})
        self.session.request.side_effect = [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
                                            response({"id": "v"}), rejected]
        with self.assertRaisesRegex(APIError, "already has this exact video"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual(manifest(self.receipt)["delivery"]["youtube"]["state"], "failed")
        record = manifest(self.receipt)
        record["delivery"] = {}
        atomic_json(self.receipt, record)
        self.session.request.side_effect = [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
                                            response({"id": "w"}), self.processed("w", privacy="private")]
        result = self.publisher.publish(self.receipt, "youtube", {**self.options, "youtube_privacy": "public"})
        self.assertIn("YouTube set it to private", result["note"])

    def test_youtube_still_processing_is_not_called_published(self):
        self.settings["upload_chunk_bytes"] = self.video.stat().st_size
        busy = response({"items": [{"id": "v", "status": {"uploadStatus": "uploaded"}}]})
        self.session.request.side_effect = [response(headers={"Location": "https://www.googleapis.com/upload/test"}),
                                            response({"id": "v"}), busy, busy]
        result = self.publisher.publish(self.receipt, "youtube", self.options)
        self.assertEqual((result["state"], result["remote_id"]), ("processing", "v"))

    def test_tiktok_resends_a_chunk_after_a_server_error_and_explains_failures(self):
        self.session.request.side_effect = [self.creator(), response({"data": {
            "publish_id": "tik-id", "upload_url": "https://open-upload.tiktokapis.com/video/upload"}, "error": {"code": "ok"}}),
            response(status=206), response({"error": {"code": "internal"}}, status=503), response(status=206),
            response(status=201), response({"data": {"status": "FAILED", "fail_reason": "frame_rate_check_failed"},
                                            "error": {"code": "ok"}})]
        with patch.object(self.publisher, "pause"):
            with self.assertRaisesRegex(APIError, "doesn't accept this frame rate"):
                self.publisher.publish(self.receipt, "tiktok", self.options)
        ranges = [c.kwargs["headers"]["Content-Range"] for c in self.session.request.call_args_list if c.args[0] == "PUT"]
        self.assertEqual(ranges[1], ranges[2])  # the chunk hit by the error was sent again, same bytes

    def test_tiktok_upload_without_201_is_never_called_published(self):
        self.session.request.side_effect = [self.creator(), response({"data": {
            "publish_id": "tik-id", "upload_url": "https://open-upload.tiktokapis.com/video/upload"}, "error": {"code": "ok"}}),
            response(status=206), response(status=206), response(status=200)]
        with self.assertRaisesRegex(APIError, "didn't confirm"):
            self.publisher.publish(self.receipt, "tiktok", self.options)
        self.assertEqual(manifest(self.receipt)["delivery"]["tiktok"]["state"], "uncertain")

    def test_instagram_unconfirmed_upload_and_not_ready_publish(self):
        self.session.request.side_effect = [response({"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/c1"}),
                                            response({"debug_info": {"message": "processing failed"}})]
        with self.assertRaisesRegex(APIError, "didn't confirm it received"):
            self.publisher.publish(self.receipt, "instagram", self.options)
        not_ready = response({"error": {"message": "Media ID is not available", "code": 9007}}, status=400)
        self.session.request.side_effect = [response({"id": "c2", "uri": "https://rupload.facebook.com/ig-api-upload/c2"}),
            response({"success": True}), response({"status_code": "FINISHED"}), not_ready]
        result = self.publisher.publish(self.receipt, "instagram", self.options, wait=False)
        self.assertEqual((result["state"], result["remote_id"]), ("processing", "c2"))  # checked again later, not failed

    def test_instagram_error_status_says_why(self):
        record = manifest(self.receipt)
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "c", "uploaded": True}
        atomic_json(self.receipt, record)
        self.session.request.return_value = response({"status_code": "ERROR", "status": "Error: unsupported format 2207026"})
        with self.assertRaisesRegex(APIError, "unsupported format 2207026"):
            self.publisher.publish(self.receipt, "instagram", self.options)

    def test_text_the_platform_would_reject_is_stopped_before_anything_is_sent(self):
        from multitool.auto_poster.publish import text_problems
        record = manifest(self.receipt)
        record["title"] = "<FREE> beat"
        atomic_json(self.receipt, record)
        with self.assertRaisesRegex(ValueError, "< or >"):
            self.publisher.publish(self.receipt, "youtube", self.options)
        self.session.request.assert_not_called()
        self.assertEqual(manifest(self.receipt)["delivery"], {})
        tags = " ".join(f"#tag{i}" for i in range(31))
        self.assertIn("31", text_problems({"social_caption": tags}, "instagram", self.settings)[0])
        self.assertEqual(text_problems({"social_caption": tags}, "tiktok", self.settings), [])
        self.assertIn("bytes", text_problems({"title": "t", "caption": "é" * 2600}, "youtube", self.settings)[0])
        self.assertEqual(text_problems({"title": "t", "social_caption": "😀" * 1100}, "tiktok", self.settings), [])
        self.assertIn("2202", text_problems({"social_caption": "😀" * 1101}, "tiktok", self.settings)[0])  # UTF-16 units

    def test_unfinished_uploads_are_finished_later_without_uploading_again(self):
        from multitool.auto_poster.publish import pending
        record = manifest(self.receipt)
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "c", "uploaded": True, "account": "123"}
        record["delivery"]["tiktok"] = {"state": "processing", "remote_id": "t", "uploaded": False}  # not fully sent: left alone
        atomic_json(self.receipt, record)
        self.assertEqual(pending([self.receipt]), [(self.receipt, "instagram")])
        self.session.request.side_effect = [response({"status_code": "FINISHED"}), response({"id": "ig-post"}), self.link()]
        [(receipt, platform, state)] = self.publisher.resume(pending([self.receipt]))
        self.assertEqual((platform, state["state"], state["post_id"]), ("instagram", "published", "ig-post"))
        self.assertEqual([c.args[0] for c in self.session.request.call_args_list], ["GET", "POST", "GET"])
        self.assertEqual(pending([self.receipt]), [])

    def test_rate_limited_checks_leave_a_sent_upload_to_be_finished_later(self):
        from multitool.auto_poster.publish import pending
        record = manifest(self.receipt)
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "c", "uploaded": True, "account": "123"}
        atomic_json(self.receipt, record)
        self.session.request.return_value = response({"error": {"code": 4, "message": "Application request limit reached"}},
                                                     status=429)
        with patch("multitool.auto_poster.accounts.time.sleep"):
            [(_, _, state)] = self.publisher.resume(pending([self.receipt]))
        self.assertEqual(self.session.request.call_count, 3)  # tried, then retried twice
        self.assertEqual(manifest(self.receipt)["delivery"]["instagram"]["state"], "uncertain")
        self.assertEqual(pending([self.receipt]), [(self.receipt, "instagram")])  # still waiting to be finished
        self.session.request.reset_mock(return_value=True)
        self.session.request.side_effect = [response({"status_code": "FINISHED"}), response({"id": "ig-post"}), self.link()]
        [(_, _, state)] = self.publisher.resume(pending([self.receipt]))
        self.assertEqual((state["state"], state["post_id"]), ("published", "ig-post"))

    def test_only_a_definitive_refusal_ends_a_sent_upload(self):
        from multitool.auto_poster.publish import pending
        record = manifest(self.receipt)
        record["delivery"]["tiktok"] = {"state": "processing", "remote_id": "t", "uploaded": True}
        atomic_json(self.receipt, record)
        self.session.request.return_value = response({"data": {"status": "FAILED", "fail_reason": "spam_risk_text"},
                                                      "error": {"code": "ok"}})
        [(_, _, state)] = self.publisher.resume(pending([self.receipt]))
        self.assertEqual(state["state"], "failed")
        self.assertIn("flagged the description", state["error"])
        self.assertEqual(pending([self.receipt]), [])

    def test_a_different_account_never_finishes_someone_elses_upload(self):
        record = manifest(self.receipt)
        record["delivery"]["instagram"] = {"state": "processing", "remote_id": "c", "uploaded": True, "account": "999"}
        atomic_json(self.receipt, record)
        [(_, _, state)] = self.publisher.resume([(self.receipt, "instagram")])
        self.assertEqual(state["state"], "skipped")
        self.assertIn("different account", state["error"])
        self.session.request.assert_not_called()
        self.assertEqual(manifest(self.receipt)["delivery"]["instagram"]["state"], "processing")

    def test_platform_errors_show_the_reason_and_what_to_do(self):
        from multitool.auto_poster.accounts import api_request
        quota = {"error": {"code": 403, "message": "You have exceeded your <a href='https://x.example/q?key=1'>quota</a>.",
                           "errors": [{"reason": "quotaExceeded"}]}}
        self.session.request.return_value = response(quota, status=403)
        with self.assertRaises(APIError) as caught:
            api_request(self.session, self.settings, "GET", "https://www.googleapis.com/youtube/v3/videos")
        message = str(caught.exception)
        self.assertIn("quotaExceeded", message)
        self.assertIn("exceeded your quota", message)
        self.assertIn("try again tomorrow", message)
        self.assertNotIn("x.example", message)
        self.session.request.side_effect = [response(status=503), response({"items": []})]
        with patch("multitool.auto_poster.accounts.time.sleep") as slept:
            api_request(self.session, self.settings, "GET", "https://www.googleapis.com/youtube/v3/videos", retries=2)
        slept.assert_called_once()  # a status check is tried again after a server hiccup

    def test_damaged_config_is_set_aside_not_wiped(self):
        from app_paths import update_config
        path = self.folder / "config.json"
        path.write_text('{"bounce_sorter": {"min_seco')
        update_config("auto_poster", {"output_folder": "x"}, path)
        self.assertEqual(json.loads(path.read_text()), {"auto_poster": {"output_folder": "x"}})
        self.assertEqual([p.read_text() for p in self.folder.glob("config.damaged-*.json")], ['{"bounce_sorter": {"min_seco'])
        update_config("bounce_sorter", {"min_seconds": 5}, path)
        self.assertEqual(set(json.loads(path.read_text())), {"auto_poster", "bounce_sorter"})


class AccountTests(unittest.TestCase):
    def test_refresh_rotates_and_persists_tiktok_refresh_token(self):
        vault = Vault({"tiktok": {"access_token": "old", "refresh_token": "refresh-old", "expires_at": 1,
                                  "client_key": "client", "client_secret": "secret"}})
        session = Mock()
        session.request.return_value = response({"access_token": "new", "refresh_token": "rotated", "expires_in": 1000})
        account = Accounts(load_settings(), vault, session)
        self.assertEqual(account.credentials("tiktok")["access_token"], "new")
        self.assertEqual(vault.get("tiktok")["refresh_token"], "rotated")

    def signed_in(self, platform, replies, client=None, vault=None, **options):
        """Runs authorize() with a fake browser that returns straight to the callback; gives back the opened URL's query."""
        state = {}
        class Server:
            def __init__(self, address, handler):
                self.server_port, self.handler = address[1] or 12345, handler
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self):
                callback = self.handler.__new__(self.handler)
                callback.path = f"/callback/{platform}?state=" + state["query"]["state"][0] + "&code=fake-code"
                callback.send_response = Mock()
                callback.send_header = Mock()
                callback.end_headers = Mock()
                callback.wfile = io.BytesIO()
                callback.do_GET()
        def browser(url):
            state["query"] = parse_qs(urlparse(url).query)
            return True
        self.session = Mock()
        self.session.request.side_effect = [response(reply) for reply in replies]
        self.vault = vault if vault is not None else Vault()
        with patch("multitool.auto_poster.accounts.HTTPServer", Server), patch("multitool.auto_poster.accounts.webbrowser.open", browser):
            self.name = Accounts(load_settings(), self.vault, self.session).authorize(platform, client, **options)
        return state["query"]

    def oauth(self, platform):
        identity = ({"items": [{"id": "UC-fake", "snippet": {"title": "Fake channel"}}]} if platform == "youtube" else
                    {"data": {"creator_nickname": "Fake creator"}, "error": {"code": "ok"}})
        query = self.signed_in(platform, [{"access_token": "fake", "refresh_token": "refresh", "expires_in": 1000}, identity],
                               {"client_id": "client", "client_key": "client", "client_secret": "secret"})
        self.assertTrue(self.name.startswith("Fake "))
        verifier = self.session.request.call_args_list[0].kwargs["data"]["code_verifier"]
        expected = (base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                    if platform == "youtube" else hashlib.sha256(verifier.encode()).hexdigest())
        self.assertEqual(query["code_challenge"], [expected])
        self.assertTrue(query["redirect_uri"][0].startswith("http://127.0.0.1:"))
        return query

    def test_setup_steps_show_exactly_the_address_the_sign_in_returns_to(self):
        from multitool.auto_poster import setup_guide
        settings = load_settings()
        shown = lambda platform: [step.copy for step in setup_guide.steps(platform, settings) if "/callback/" in step.copy]
        tiktok = self.oauth("tiktok")["redirect_uri"]
        self.assertEqual(shown("tiktok"), tiktok)
        pages = {"data": [{"name": "Page", "access_token": "p", "instagram_business_account": {"id": "1", "username": "me"}}]}
        instagram = self.signed_in("instagram", [{"access_token": "short"}, {"access_token": "long"}, pages],
                                   vault=Vault({"instagram-app": {"app_id": "app", "app_secret": "secret"}}))["redirect_uri"]
        self.assertEqual(shown("instagram"), instagram)
        self.assertEqual(instagram, ["http://localhost:8766/callback/instagram"])

    def test_a_permission_left_unticked_is_caught_and_nothing_is_saved(self):
        settings = load_settings()
        partial = {"access_token": "fake", "refresh_token": "r", "expires_in": 1000,
                   "scope": "https://www.googleapis.com/auth/youtube.readonly"}  # youtube.upload unticked
        with self.assertRaises(APIError) as caught:
            self.signed_in("youtube", [partial], {"client_id": "c", "client_secret": "s"})
        self.assertEqual(caught.exception.code, "scope_missing")
        self.assertIn("tick every box", str(caught.exception))
        self.assertNotIn("youtube", self.vault.profiles)  # no half-working login kept
        full = {**partial, "scope": " ".join(settings["youtube_scopes"])}
        self.signed_in("youtube", [full, {"items": [{"id": "UC-1", "snippet": {"title": "Fake channel"}}]}],
                       {"client_id": "c", "client_secret": "s"})
        self.assertEqual(self.vault.profiles["youtube"]["channel_id"], "UC-1")
        self.assertAlmostEqual(self.vault.profiles["youtube"]["connected_at"], time.time(), delta=5)

    def test_youtube_oauth_has_pkce_state_and_loopback(self):
        self.assertEqual(self.oauth("youtube")["prompt"], ["select_account consent"])  # Google's account chooser
        self.assertEqual(self.vault.profiles["youtube"]["channel_id"], "UC-fake")  # what schedules are pinned to

    def test_account_id_is_the_stable_id_and_old_youtube_logins_look_it_up_once(self):
        vault = Vault({"youtube": {"access_token": "t", "name": "Fake channel"},
                       "instagram": {"access_token": "t", "user_id": 111}, "tiktok": {"access_token": "t"}})
        session = Mock()
        session.request.return_value = response({"items": [{"id": "UC-fake", "snippet": {"title": "Fake channel"}}]})
        accounts = Accounts(load_settings(), vault, session)
        self.assertEqual([accounts.account_id("youtube"), accounts.account_id("youtube")], ["UC-fake", "UC-fake"])
        self.assertEqual(session.request.call_count, 1)  # saved after the first lookup
        self.assertEqual(accounts.account_id("instagram"), "111")
        with self.assertRaisesRegex(APIError, "Reconnect TikTok"):  # no open_id saved: fail closed
            accounts.account_id("tiktok")

    def test_tiktok_oauth_uses_its_documented_hex_pkce_challenge(self):
        self.assertEqual(self.oauth("tiktok")["disable_auto_auth"], ["1"])  # TikTok's login page, not the last account

    def test_saved_app_keys_mean_connect_needs_nothing_typed(self):
        vault = Vault()
        accounts = Accounts(load_settings(), vault, Mock())
        with self.assertRaisesRegex(ValueError, "app keys"):
            accounts.authorize("tiktok")
        accounts.save_app_keys("tiktok", {"client_key": " key ", "client_secret": "secret"})
        accounts.save_app_keys("tiktok", {"client_key": "", "client_secret": ""})  # blank fields keep what's saved
        vault.profiles.pop("tiktok", None)  # Disconnect removes only the account token
        query = self.signed_in("tiktok", [{"access_token": "fake", "expires_in": 1000},
                                          {"data": {"creator_nickname": "Fake creator"}, "error": {"code": "ok"}}], vault=vault)
        self.assertEqual(query["client_key"], ["key"])

    def test_instagram_signs_in_in_the_browser_and_asks_which_account(self):
        pages = {"data": [{"name": "Beats Page", "access_token": "page-1",
                           "instagram_business_account": {"id": "111", "username": "beats"}},
                          {"name": "No Instagram", "access_token": "page-2"},
                          {"name": "Second Page", "access_token": "page-3",
                           "instagram_business_account": {"id": "333", "username": "second"}}]}
        offered = []
        query = self.signed_in("instagram", [{"access_token": "short"}, {"access_token": "long"}, pages],
                               vault=Vault({"instagram-app": {"app_id": "app", "app_secret": "secret"}}),
                               choose=lambda options: offered.extend(options) or options[1])
        self.assertEqual(query["redirect_uri"], ["http://localhost:8766/callback/instagram"])
        self.assertIn("instagram_content_publish", query["scope"][0])
        self.assertEqual([o["name"] for o in offered], ["beats", "second"])
        self.assertEqual(self.name, "second")
        saved = self.vault.get("instagram")
        self.assertEqual((saved["access_token"], saved["user_id"]), ("page-3", "333"))
        exchange = self.session.request.call_args_list[1].kwargs["params"]
        self.assertEqual(exchange["fb_exchange_token"], "short")  # long-lived token -> non-expiring Page token

    def test_instagram_follows_page_cursors_to_later_accounts(self):
        first = {"data": [{"name": "No Instagram", "access_token": "page-0"}],
                 "paging": {"cursors": {"after": "CURSOR"}, "next": "https://graph.facebook.com/next?access_token=leak"}}
        later = {"data": [{"name": "Beats", "access_token": "page-1", "instagram_business_account": {"id": "111", "username": "beats"}}]}
        self.signed_in("instagram", [{"access_token": "short"}, {"access_token": "long"}, first, later],
                       {"app_id": "app", "app_secret": "secret"})
        self.assertEqual(self.name, "beats")
        call = self.session.request.call_args_list[3]
        self.assertTrue(call.args[1].endswith("/me/accounts"))  # same endpoint; the `next` URL is never requested
        self.assertEqual(call.kwargs["params"]["after"], "CURSOR")

    def test_instagram_with_no_professional_account_explains_what_to_link(self):
        with self.assertRaisesRegex(APIError, "Facebook Page"):
            self.signed_in("instagram", [{"access_token": "short"}, {"access_token": "long"}, {"data": []}],
                           {"app_id": "app", "app_secret": "secret"})


class UIFlowTests(unittest.TestCase):
    def test_publish_requires_the_users_review_confirmation(self):
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.platforms = {"youtube": Mock(get=Mock(return_value=True))}
        app.jobs = {"row": {"receipt": Path("not-opened.json")}}
        app.selected_rows = lambda: ["row"]
        app.reviewed = Mock(get=Mock(return_value=False))
        app.status, app.run_worker = Mock(), Mock()
        app.start_publish()
        app.run_worker.assert_not_called()
        self.assertIn("confirmation", app.status.set.call_args.args[0])

    def composer(self, receipt, title, connected):
        """An Auto Poster window without Tk: the fields publish_inputs reads, a background step that runs at once,
        and `connected` = the logins saved in Keychain right now ({platform: (id, name)})."""
        import queue
        from multitool import credits
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        value = lambda v: Mock(get=Mock(return_value=v))
        app.settings = {**load_settings(), "default_hashtags": "typebeat"}
        app.platforms = {"youtube": value(True), "instagram": value(True), "tiktok": value(False)}
        app.jobs, app.selected_rows = {"row": {"receipt": receipt}}, lambda: ["row"]
        app.reviewed, app.youtube_privacy, app.kids = value(True), value("unlisted"), value(False)
        app.tiktok_privacy, app.tiktok_consent, app.own_brand, app.branded, app.ai = (value(v) for v in ("", False, False, False, False))
        app.interactions = {"comment": value(False)}
        app.title, app.hashtags = value(title), value("typebeat")
        app.caption, app.social_text = value("Buy: link in bio"), value("new one {credits}")
        app.credit_settings = lambda: {**credits.load_settings(), "producer_name": "kel", "known_producers": []}
        # what the window remembered from earlier: deliberately stale
        app.account_labels = {"youtube": value("Old Channel A"), "instagram": value("old_a"), "tiktok": value("")}
        app.accounts = Mock(credentials=lambda platform: {"name": connected[platform][1]},
                            account_id=lambda platform, profile: connected[platform][0])
        import threading
        app.status, app.root, app.events, app.busy, app.cancel = Mock(), Mock(), queue.Queue(), False, threading.Event()
        app.run_worker = lambda work: work()
        return app

    def test_publish_shows_exactly_what_will_go_out_and_blocks_text_that_would_be_rejected(self):
        folder = BASE / uuid.uuid4().hex
        folder.mkdir()
        receipt = folder / "post.json"
        atomic_json(receipt, {"state": "ready", "audio": "/beats/needa lead.wav", "duration": 75, "delivery": {},
                              "variants": {"Landscape 16:9": {"width": 1920, "height": 1080},
                                           "Portrait 9:16": {"width": 1080, "height": 1920}}})
        connected = {"youtube": ("UC-B", "Kel Beats"), "instagram": ("ig-B", "kelbeats")}
        then = Mock()
        with patch("multitool.auto_poster.ui.messagebox") as box:
            app = self.composer(receipt, "<FREE> {name}", connected)
            self.assertIsNone(app.publish_inputs("Post", then))
            box.showwarning.assert_called_once()
            self.assertIn("needa lead.wav: YouTube doesn't allow < or >", box.showwarning.call_args.args[1])
            self.assertTrue(app.events.empty())  # nothing checked or sent
            app = self.composer(receipt, "{name} ({credits})", connected)
            app.publish_inputs("Post", then)
            kind, confirm = app.events.get_nowait()
            box.askokcancel.return_value = False
            confirm()  # Cancel: nothing sent
            then.assert_not_called()
            summary = box.askokcancel.call_args.args[1]
            box.askokcancel.return_value = True
            confirm()
        self.assertEqual(kind, "confirm")
        self.assertIn("YouTube · Kel Beats · 1920×1080, 1:15 · unlisted", summary)  # the login saved now, not the old label
        self.assertNotIn("Old Channel A", summary)
        self.assertIn("“needa lead (prod. kel)”", summary)
        self.assertIn("Instagram · kelbeats · 1080×1920", summary)
        self.assertIn("“new one prod. kel ⏎ #typebeat”", summary)
        targets, rows, options, save_texts, approval = then.call_args.args
        self.assertEqual(approval[receipt.resolve()]["accounts"], {"youtube": "UC-B", "instagram": "ig-B"})
        self.assertEqual(approval[receipt.resolve()]["texts"]["title"], "needa lead (prod. kel)")
        self.assertNotIn("title", manifest(receipt))  # written only when posting starts
        with patch("multitool.auto_poster.ui.Publisher") as made:
            made.return_value.publish_batch.return_value = 0
            app.publish_now(targets, rows, options, save_texts, approval)
        sent = made.return_value.publish_batch.call_args.kwargs["approved"](receipt.resolve())
        self.assertEqual(sent, approval[receipt.resolve()])  # the confirmed accounts are enforced while posting
        self.assertEqual(manifest(receipt)["title"], "needa lead (prod. kel)")

    def test_a_known_setup_mistake_offers_the_page_that_fixes_it(self):
        import queue
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.settings = load_settings()
        app.events, app.status, app.root, app.cancel = queue.Queue(), Mock(), Mock(), Mock(is_set=Mock(return_value=False))
        off = APIError("YouTube Data API v3 has not been used in project 42 before or it is disabled.",
                       code="accessNotConfigured")
        app.accounts = Mock(app_keys=Mock(return_value={"client_id": "c", "client_secret": "s"}),
                            authorize=Mock(side_effect=off))
        app.run_worker = lambda work: work()
        app.connect("youtube")
        self.assertIn("Access blocked", app.status.set.call_args_list[0].args[0])  # browser-side tips while waiting
        kind, platform, message, url = app.events.get_nowait()
        self.assertEqual((kind, url), ("fix", "https://console.cloud.google.com/apis/library/youtube.googleapis.com?project=42"))
        app.events.put((kind, platform, message, url))
        app.history_rows, app.poll_id = {}, None
        with patch("multitool.auto_poster.ui.messagebox.askyesno", return_value=True) as asked, \
                patch("multitool.auto_poster.ui.webbrowser.open") as browser:
            app.poll_events()
        self.assertIn("Click Enable", asked.call_args.args[1])
        browser.assert_called_once_with(url)

    def test_scheduling_past_the_youtube_sign_in_date_asks_first(self):
        from datetime import datetime, timedelta
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.settings, app.root, app.run_worker = load_settings(), Mock(), Mock()
        app.accounts = Mock(vault=Mock(get=Mock(return_value={"connected_at": time.time()})))
        app.ask_schedule = lambda count: [datetime.now().astimezone() + timedelta(days=10)]
        with patch("multitool.auto_poster.ui.messagebox.askokcancel", return_value=False) as asked:
            app.schedule_posts(["youtube"], [("row", Path("/fake/post.json"))], {}, Mock(), {})
        self.assertIn("Connect YouTube again before then", asked.call_args.args[1])
        app.run_worker.assert_not_called()  # cancelled: nothing scheduled
        app.ask_schedule = lambda count: [datetime.now().astimezone() + timedelta(days=2)]
        with patch("multitool.auto_poster.ui.messagebox.askokcancel") as asked:
            app.schedule_posts(["youtube"], [("row", Path("/fake/post.json"))], {}, Mock(), {})
        asked.assert_not_called()
        app.run_worker.assert_called_once()

    def test_paid_partnership_removes_private_tiktok_visibility(self):
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.creator = {"privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]}
        app.branded = Mock(get=Mock(return_value=True))
        app.tiktok_privacy = Mock(get=Mock(return_value="SELF_ONLY"))
        app.tiktok_combo, app.status, app.disclosure = Mock(), Mock(), Mock()
        app.disclosure_changed()
        app.tiktok_privacy.set.assert_called_once_with("")
        app.tiktok_combo.configure.assert_called_once_with(values=["PUBLIC_TO_EVERYONE"])

    def test_stop_releases_the_instagram_account_picker(self):
        import queue, threading
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.events, app.cancel = queue.Queue(), threading.Event()
        threading.Timer(0.3, app.cancel.set).start()  # Stop, with nobody answering the picker
        started = time.monotonic()
        self.assertIsNone(app.choose_account([{"name": "a"}, {"name": "b"}]))
        self.assertLess(time.monotonic() - started, 2)

    def test_connect_without_keys_opens_the_setup_steps_and_keys_typed_there_connect(self):
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.settings = load_settings()
        app.accounts = Mock(app_keys=Mock(return_value={}))
        app.status, app.run_worker, app.show_setup, app.busy = Mock(), Mock(), Mock(), False
        app.connect("tiktok")
        app.show_setup.assert_called_once_with("tiktok")
        app.run_worker.assert_not_called()
        note, wizard = Mock(), Mock()
        app.setup_windows = {"tiktok": wizard}
        fields = {"client_key": Mock(get=Mock(return_value=" key ")), "client_secret": Mock(get=Mock(return_value=""))}
        app.setup_connect("tiktok", fields, note)
        self.assertIn("Client secret", note.set.call_args.args[0])  # says which box is still empty
        app.run_worker.assert_not_called()
        fields["client_secret"].get.return_value = "secret"
        app.setup_connect("tiktok", fields, note)
        wizard.close.assert_called_once()
        app.run_worker.assert_called_once()  # saves the keys and opens the TikTok sign-in


class ChooseProjectTests(unittest.TestCase):
    """Choose project… opens the DAW and records right away; Create videos reuses that recording.
    No window, DAW, screen recording or real file: dialogs, the recorder and the beat's length are stand-ins."""
    def app(self, folder):
        import queue
        from multitool.auto_poster.ui import AutoPosterApp
        app = AutoPosterApp.__new__(AutoPosterApp)
        app.busy, app.settings, app.events, app.root = False, load_settings(), queue.Queue(), Mock()
        app.cancel = Mock(is_set=Mock(return_value=False))
        app.jobs = {"row": {"audio": folder / "beat.wav", "receipt": None}}
        app.tracks, app.status, app.reviewed = Mock(selection=Mock(return_value=())), Mock(), Mock()
        app.visual_kind, app.visual_kind_changed = Mock(get=Mock(return_value="image")), Mock()
        app.output = Mock(get=Mock(return_value=str(folder)))
        app.run_worker = lambda work: work()  # the background step runs at once
        return app

    def test_choosing_the_project_records_it_from_the_start_straight_away(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        app, project = self.app(folder), folder / "beat.flp"
        recording = folder / "Screen recordings" / "take.mov"
        with patch("multitool.auto_poster.ui.filedialog.askopenfilename", return_value=str(project)), \
             patch("multitool.auto_poster.ui.messagebox.askokcancel", return_value=True) as warned, \
             patch("multitool.auto_poster.render.media_info", return_value={"duration": 95.0}), \
             patch("multitool.auto_poster.project.record_project", return_value=(recording, 1.5)) as record:
            app.choose_project()  # one track, nothing selected: it's that track
        app.visual_kind.set.assert_called_once_with("project")
        warned.assert_called_once()  # save your work, don't touch the mouse
        self.assertEqual(record.call_args.args[:3], (project, 95.0, folder.resolve() / "Screen recordings"))
        self.assertEqual(app.jobs["row"]["recording"], {"project": project, "file": recording, "offset": 1.5, "seconds": 95.0})

        recording.parent.mkdir()
        recording.write_bytes(b"x")
        request = Mock(project=project, start=10.0, short_start=None)
        with patch("multitool.auto_poster.ui.check_plan", return_value={"seconds": 30.0}), \
             patch("multitool.auto_poster.project.record_project") as again, \
             patch("multitool.auto_poster.ui.replace", side_effect=lambda r, **changes: changes):
            reused = app.with_project_recording("row", request)
        again.assert_not_called()  # Create videos uses the recording made above
        self.assertEqual(reused, {"vertical_visual": recording, "vertical_start": 11.5})

    def test_cancelling_the_project_dialog_records_nothing(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        app = self.app(folder)
        with patch("multitool.auto_poster.ui.filedialog.askopenfilename", return_value=""), \
             patch("multitool.auto_poster.project.record_project") as record:
            app.choose_project()
        record.assert_not_called()
        app.visual_kind.set.assert_not_called()


class SignInScreenTests(unittest.TestCase):
    def reply(self, error=None, fail=False):
        from multitool.auto_poster.accounts import Accounts
        session = Mock()
        if fail:
            session.post.side_effect = requests.ConnectionError("offline")
        else:
            session.post.return_value = Mock(json=Mock(return_value={"error": error} if error else {"access_token": "clt.x"}))
        settings = {"youtube_token_url": "https://oauth2.googleapis.com/token",
                    "tiktok_token_url": "https://open.tiktokapis.com/v2/oauth/token/"}
        return Accounts(settings, vault=Mock(), session=session), session

    def test_wrong_app_keys_are_caught_before_the_browser_opens(self):
        from multitool.auto_poster.accounts import APIError
        for platform, keys in (("tiktok", {"client_key": "k", "client_secret": "s"}),
                               ("youtube", {"client_id": "i.apps.googleusercontent.com", "client_secret": "s"})):
            accounts, session = self.reply("invalid_client")
            with self.assertRaises(APIError) as caught:
                accounts.check_keys(platform, keys)
            self.assertEqual(caught.exception.code, "invalid_client")
            self.assertEqual(session.post.call_args.kwargs["data"]["client_secret"], "s")
            for fine in (self.reply("invalid_grant"), self.reply(), self.reply(fail=True)):  # real keys, or offline
                fine[0].check_keys(platform, keys)
        accounts, session = self.reply("invalid_client")
        accounts.check_keys("instagram", {"app_id": "1", "app_secret": "s"})  # Meta has no such check: nothing sent
        session.post.assert_not_called()

    def test_the_browser_page_says_what_happened_in_plain_words(self):
        from multitool.auto_poster.accounts import callback_page
        self.assertIn("TikTok sign-in done ✓", callback_page("tiktok").decode())
        self.assertIn("Connect YouTube when you're ready", callback_page("youtube", "access_denied").decode())
        page = callback_page("tiktok", "<script>x</script>").decode()
        self.assertNotIn("<script>", page)
        self.assertIn("DAW Multitool says what to fix", page)


class FakeKeychain:
    """Stands in for macOS Keychain: counts reads per entry, since each entry is one macOS question."""
    def __init__(self, entries=None):
        self.entries, self.reads = dict(entries or {}), []

    def get_password(self, service, name):
        self.reads.append(name)
        return self.entries.get((service, name))

    def set_password(self, service, name, value):
        self.entries[(service, name)] = value

    def delete_password(self, service, name):
        del self.entries[(service, name)]


class OneKeychainEntryTests(unittest.TestCase):
    def store(self, keychain):
        from multitool.auto_poster.accounts import CredentialStore
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return CredentialStore({"keychain_service": "svc"}, backend=keychain, lock_path=Path(folder.name) / "k.lock")

    def test_old_separate_entries_move_into_one_and_only_that_one_is_read_after(self):
        old = {("svc", "youtube"): json.dumps({"access_token": "a"}), ("svc", "youtube-app"): json.dumps({"client_id": "c"}),
               ("svc", "tiktok-app"): json.dumps({"client_key": "k"})}
        keychain = FakeKeychain(old)
        store = self.store(keychain)
        self.assertEqual(store.get("tiktok-app"), {"client_key": "k"})
        self.assertEqual(set(keychain.entries), {("svc", "accounts")})  # the old entries are gone
        keychain.reads.clear()
        self.assertEqual(store.get("youtube"), {"access_token": "a"})
        store.set("instagram", {"access_token": "i"})
        self.assertEqual(set(keychain.reads), {"accounts"})
        store.delete("youtube")
        self.assertEqual(json.loads(keychain.entries[("svc", "accounts")]),
                         {"youtube-app": {"client_id": "c"}, "tiktok-app": {"client_key": "k"}, "instagram": {"access_token": "i"}})

    def test_two_copies_of_the_app_never_drop_each_others_logins(self):
        keychain = FakeKeychain()
        app, helper = self.store(keychain), self.store(keychain)
        app.set("youtube", {"access_token": "y"})
        helper.set("tiktok", {"access_token": "t"})  # e.g. the scheduled-post helper refreshing a token
        self.assertEqual(app.get("youtube"), {"access_token": "y"})
        self.assertEqual(app.get("tiktok"), {"access_token": "t"})
        self.assertIsNone(self.store(FakeKeychain()).get("youtube"))  # nothing saved yet: no entry is created


if __name__ == "__main__":
    unittest.main()
