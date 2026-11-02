"""Official upload APIs with persistent IDs; repeated clicks do not duplicate posts.
A saved upload (YouTube video, Instagram container, TikTok publish ID) is only checked afterwards, never sent
again, and a post only counts as published once the platform itself confirms it."""
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from .accounts import PLATFORMS, APIError, Accounts, api_request, response_json
from .render import Cancelled, posting_formats, snapshot
from .storage import NAMES, manifest, receipt_lock, save_delivery, video_variant

APPROVED_TEXTS = ("title", "caption", "social_caption")  # what a scheduled post was approved with
RESUMABLE = ("processing", "uncertain")  # with a fully sent upload saved: safe to check again
TIKTOK_FAILURES = {  # TikTok fail_reason -> plain words
    "file_format_check_failed": "TikTok can't read this video format",
    "duration_check_failed": "the video is too short or too long for this TikTok account",
    "frame_rate_check_failed": "TikTok doesn't accept this frame rate",
    "picture_size_check_failed": "TikTok doesn't accept this picture size",
    "internal": "a TikTok server problem",
    "auth_removed": "the TikTok account removed this app's access; reconnect it",
    "spam_risk_too_many_posts": "TikTok's daily limit for app posts is reached; try tomorrow",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting",
    "spam_risk_text": "TikTok flagged the description as spam; change it",
    "spam_risk": "TikTok flagged the post as risky",
}


def safe_upload_url(url, platform):
    host = urlparse(url).hostname or ""
    domains = {"youtube": "googleapis.com", "instagram": "facebook.com", "tiktok": "tiktokapis.com"}
    domain = domains[platform]
    if urlparse(url).scheme != "https" or urlparse(url).username or not (host == domain or host.endswith("." + domain)):
        raise APIError("The platform returned an unexpected upload destination. Nothing was sent.")
    return url


def post_text(job, platform):
    """(title, caption) as the platform shows them: YouTube keeps its own description; TikTok and Reels
    get the social caption (description + hashtags)."""
    caption = job.get("caption", "")
    if platform in ("tiktok", "instagram") and "social_caption" in job:
        caption = job["social_caption"]
    return job.get("title", ""), caption


def text_problems(job, platform, settings):
    """Plain-language reasons the platform would reject this text, so nothing is sent (or scheduled) that can't post."""
    title, caption = post_text(job, platform)
    problems = []
    if platform == "youtube":
        if not title.strip():
            problems.append("YouTube needs a title.")
        if len(title) > settings["youtube_title_limit"]:
            problems.append(f"The YouTube title is {len(title)} characters; the limit is {settings['youtube_title_limit']}.")
        if len(caption.encode()) > settings["youtube_caption_limit"]:
            problems.append(f"The YouTube description is over {settings['youtube_caption_limit']} bytes; shorten it.")
        if "<" in title + caption or ">" in title + caption:
            problems.append("YouTube doesn't allow < or > in the title or description.")
        return problems
    length = len(caption.encode("utf-16-le")) // 2 if platform == "tiktok" else len(caption)  # TikTok counts UTF-16
    if length > settings["caption_limit"]:
        problems.append(f"The {NAMES[platform]} caption is {length} characters; the limit is {settings['caption_limit']}.")
    if platform == "instagram":
        for mark, key, label in (("#", "instagram_hashtag_limit", "hashtags"), ("@", "instagram_mention_limit", "@mentions")):
            count = len(re.findall(rf"(?<![\w{mark}]){mark}\w", caption))
            if count > settings[key]:
                problems.append(f"Instagram allows {settings[key]} {label} per caption; this one has {count}.")
    return problems


def pending(receipts):
    """(receipt, platform) for every upload that was fully sent but not yet confirmed. An Instagram Reel in
    this state isn't live until its publish step runs, so these are finished later without sending anything again."""
    found = []
    for receipt in receipts:
        try:
            record = manifest(receipt)
        except (OSError, ValueError):
            continue
        for platform, delivery in record.get("delivery", {}).items():
            if (delivery.get("state") in RESUMABLE and delivery.get("remote_id")
                    and (platform == "youtube" or delivery.get("uploaded"))):
                found.append((Path(receipt), platform))
    return found


class Publisher:
    def __init__(self, settings, accounts=None):
        self.settings = settings
        self.accounts = accounts or Accounts(settings)

    def request(self, method, url, **kwargs):
        return api_request(self.accounts.session, self.settings, method, url, **kwargs)

    def publish(self, receipt, platform, options, cancelled=lambda: False, report=lambda text: None, *, wait=True,
                approved=None):
        """`approved` ({"texts", "accounts"}, from the scheduler): send nothing unless the text and the
        connected account are still the ones approved when it was scheduled."""
        with receipt_lock(receipt):
            return self._publish_locked(receipt, platform, options, cancelled, report, wait=wait, approved=approved)

    def _publish_locked(self, receipt, platform, options, cancelled, report, *, wait, approved=None):
        record = manifest(receipt)
        if record.get("state") != "ready":
            raise ValueError("Render the video successfully before publishing.")
        previous = record.get("delivery", {}).get(platform, {})
        if previous.get("state") == "published":
            return previous
        if previous.get("state") in ("uncertain", "uploading") and not previous.get("remote_id"):
            raise APIError("A previous attempt has an unknown result. Check the platform account before creating another post. "
                           "To post anyway, create the video again and post the new copy.")
        if cancelled():
            raise Cancelled("Posting stopped before the next platform.")
        if platform not in PLATFORMS:
            raise ValueError("Choose an available publishing platform.")
        # A saved upload is only checked from here on. YouTube saves its video ID once the whole file arrived.
        polling = bool(previous.get("remote_id")) and (platform == "youtube" or bool(previous.get("uploaded")))
        format_name = (previous.get("format") if polling else None) or self.settings["platform_formats"][platform]
        job = {**record, "format": format_name}
        if not polling:  # a check needs no local file, so moving the videos later can't block finishing a post
            formats = posting_formats(self.settings)
            if not all(name in record.get("variants", {}) for name in formats):
                raise ValueError("Create the videos again to get both 16:9 and 9:16 versions before uploading.")
            for name in formats:
                version = video_variant(record, name)
                video = Path(version["video"])
                if not video.is_file() or list(snapshot(video)) != version["video_snapshot"]:
                    raise ValueError("A rendered video changed or is missing. Render both versions again before publishing.")
                width, height = self.settings["formats"][name]
                if version["width"] * height != version["height"] * width:
                    raise ValueError("A saved video has the wrong aspect ratio. Create both versions again.")
            job.update(video_variant(record, format_name))
            problems = text_problems(record, platform, self.settings)
            if problems:
                raise ValueError(" ".join(problems))
        job["title"], job["caption"] = post_text(record, platform)
        now = datetime.now(timezone.utc).isoformat()
        try:
            profile = self.accounts.credentials(platform)  # read once: this exact login is checked and then used
            account = self.accounts.account_id(platform, profile, required=approved is not None)
        except APIError as error:
            if not polling:
                save_delivery(receipt, platform, {**previous, "state": "failed", "error": str(error), "updated": now})
            raise
        self.check_account(record, platform, approved, polling, previous, account)
        state = {**previous, "state": "processing" if polling else "validating", "updated": now, "format": format_name,
                 "title": previous.get("title", job["title"]) if previous.get("remote_id") else job["title"],
                 "caption": previous.get("caption", job["caption"]) if previous.get("remote_id") else job["caption"]}
        if account and not polling:
            state["account"] = account  # later checks of this upload must use the same account
        state.pop("error", None)
        if not polling:
            save_delivery(receipt, platform, state)
        def checkpoint(**values):
            state.update(values)
            save_delivery(receipt, platform, state)
        try:
            if cancelled():
                raise Cancelled("Stopped before starting the next transfer.")
            if not polling:
                checkpoint(state="uploading")
            method = getattr(self, "post_" + platform)
            result = method(job, profile, options, state, checkpoint, cancelled, report, wait=wait)
            checkpoint(**result)
            return state
        except Exception as error:
            # Once the whole video is with the platform, only its own definitive refusal ends the post; a rate
            # limit, dropped connection or expired sign-in while checking leaves it to be checked again.
            sent = bool(state.get("remote_id")) and (platform == "youtube" or bool(state.get("uploaded")))
            rejected = isinstance(error, APIError) and error.rejected
            uncertain = isinstance(error, APIError) and error.uncertain or (sent and not rejected)
            checkpoint(state="cancelled" if isinstance(error, Cancelled) else "uncertain" if uncertain else "failed",
                       error=str(error))
            raise

    def check_account(self, record, platform, approved, polling, previous, account):
        """Raises before anything is saved or sent when the text or the account isn't the approved one."""
        if approved is not None:
            if not polling and any(record.get(key, "") != value for key, value in approved["texts"].items()):
                raise ValueError("The title or caption changed after you confirmed it. Nothing was sent; "
                                 "publish or schedule it again to confirm the new text.")
            if account != approved["accounts"].get(platform):
                raise ValueError(f"{NAMES[platform]} is connected to a different account than the one you confirmed. "
                                 "Nothing was sent; reconnect that account, or publish or schedule it again.")
        if polling and previous.get("account") and account and account != previous["account"]:
            raise ValueError(f"{NAMES[platform]} is connected to a different account than the one this video was "
                             "uploaded to. Reconnect that account to finish this post.")

    def publish_batch(self, receipts, platforms, options, cancelled=lambda: False,
                      report=lambda text: None, completed=lambda receipt, platform, state: None, approved=lambda receipt: None):
        """Send every selected upload first, then check pending IDs in bounded round-robin order.
        `approved(receipt)`: what the user confirmed for that track (text + account IDs), or None."""
        pending = deque()

        def attempt(receipt, platform):
            try:
                state = self.publish(receipt, platform, options, cancelled, report, wait=False, approved=approved(receipt))
            except Exception as error:
                completed(receipt, platform, {"state": "failed", "error": str(error)})
                return False
            completed(receipt, platform, state)
            return state.get("state") == "processing"

        for receipt in dict.fromkeys(Path(path).resolve() for path in receipts):
            for platform in dict.fromkeys(platforms):
                if cancelled():
                    return len(pending)
                if attempt(receipt, platform):
                    pending.append((receipt, platform))
        # One global polling budget/interval avoids multiplying request rate by the batch size.
        for _ in range(self.settings["poll_attempts"]):
            if not pending or cancelled():
                break
            self.pause(cancelled)
            if cancelled():
                break
            receipt, platform = pending.popleft()
            if attempt(receipt, platform):
                pending.append((receipt, platform))
        return len(pending)

    def resume(self, items, cancelled=lambda: False, report=lambda text: None):
        """Checks uploads from `pending()` once each, finishing them (Instagram's publish step) without sending
        the video again. Returns [(receipt, platform, state)]: "published", "processing", "failed" (the platform
        refused it; saved in History) or "skipped" (couldn't check this time, e.g. no connection)."""
        checked = []
        for receipt, platform in items:
            if cancelled():
                break
            try:
                state = self.publish(receipt, platform, {}, cancelled, report, wait=False)
            except Exception as error:
                try:
                    saved = manifest(receipt)["delivery"][platform]
                except (OSError, ValueError, KeyError):
                    saved = {}
                state = saved if saved.get("state") == "failed" else {"state": "skipped", "error": str(error)}
            checked.append((receipt, platform, state))
        return checked

    def post_youtube(self, job, profile, options, state, checkpoint, cancelled, report, *, wait=True):
        auth = {"Authorization": f"Bearer {profile['access_token']}"}
        if not state.get("remote_id"):
            video = Path(job["video"])
            privacy = options.get("youtube_privacy")
            if privacy not in ("private", "unlisted", "public"):
                raise ValueError("Choose YouTube visibility.")
            snippet = {"title": job["title"], "description": job["caption"]}
            if self.settings.get("youtube_category_id"):
                snippet["categoryId"] = str(self.settings["youtube_category_id"])  # 10 = Music
            size = video.stat().st_size
            response = self.request("POST", self.settings["youtube_upload_url"], params={"uploadType": "resumable", "part": "snippet,status"},
                headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                json={"snippet": snippet,
                      "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(options.get("made_for_kids"))}})
            location = safe_upload_url(response.headers.get("Location", ""), "youtube")
            payload = self.youtube_upload(location, video, size, auth, cancelled, report)
            if not payload.get("id"):
                raise APIError("YouTube accepted the transfer but returned no video ID.", True)
            checkpoint(state="processing", remote_id=payload["id"], uploaded=True, privacy=privacy,
                       url="https://www.youtube.com/watch?v=" + payload["id"])
        return self.youtube_status(state, auth, cancelled, report, wait)

    def youtube_upload(self, location, video, size, auth, cancelled, report):
        """Sends the file in chunks. After a dropped connection or server error it asks YouTube how much arrived
        and carries on from there (YouTube's resumable protocol), so no byte is sent as a second video."""
        retries = 0
        with video.open("rb") as stream:
            offset = 0
            while True:
                if cancelled():
                    raise Cancelled("Stopped. An incomplete upload may remain at YouTube.")
                stream.seek(offset)
                chunk = stream.read(self.settings["upload_chunk_bytes"])
                end = offset + len(chunk) - 1
                response, failure = None, None
                try:
                    response = self.request("PUT", location, headers={**auth, "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"}, data=chunk)
                except APIError as error:
                    failure = error
                while response is None:  # ask YouTube how much arrived, retrying while the connection is down
                    if not failure.uncertain or retries >= self.settings["upload_retries"]:
                        raise failure
                    retries += 1
                    self.pause(cancelled, 2 ** retries)
                    if cancelled():
                        raise Cancelled("Stopped. An incomplete upload may remain at YouTube.")
                    try:
                        response = self.request("PUT", location, headers={**auth, "Content-Length": "0",
                                                                          "Content-Range": f"bytes */{size}"})
                        end = None  # carry on from whatever YouTube says it has
                    except APIError as error:
                        failure = error
                if response.status_code in (200, 201):
                    report("YouTube upload 100%")
                    return response_json(response)
                if response.status_code != 308:
                    raise APIError("YouTube returned an unexpected upload result.", True)
                received = re.fullmatch(r"bytes=0-(\d+)", response.headers.get("Range", ""))
                arrived = int(received[1]) + 1 if received else 0
                if end is not None and arrived != end + 1:
                    raise APIError("YouTube did not confirm the full chunk. Check your channel before another attempt.", True)
                offset = arrived
                report(f"YouTube upload {int(offset / size * 100)}%")

    def youtube_status(self, state, auth, cancelled, report, wait):
        """Published once YouTube has processed the video; a rejection (duplicate, copyright, bad file) fails it."""
        video_id = state["remote_id"]
        attempts = self.settings["poll_attempts"] if wait else 1
        for index in range(attempts):
            if cancelled():
                break
            found = response_json(self.request("GET", self.settings["youtube_api_url"] + "/videos", headers=auth,
                                               params={"part": "status", "id": video_id}, retries=2)).get("items", [])
            status = found[0].get("status", {}) if found else {}
            upload = status.get("uploadStatus")
            if upload == "processed":
                result = {"state": "published", "remote_id": video_id}
                chosen, actual = state.get("privacy"), status.get("privacyStatus")
                if chosen and actual and actual != chosen:
                    result["note"] = f"YouTube set it to {actual} (you chose {chosen}); change it in YouTube Studio."
                return result
            if upload in ("rejected", "failed", "deleted"):
                reason = status.get("rejectionReason") or status.get("failureReason") or upload
                raise APIError(f"YouTube {upload} this video ({reason})."
                               + (" Your channel already has this exact video." if reason == "duplicate"
                                  else " See YouTube Studio for details."), code=reason, rejected=True)
            report("YouTube is processing the video…")
            if wait and index < attempts - 1:
                self.pause(cancelled)
        return {"state": "processing", "remote_id": video_id}

    def post_instagram(self, job, profile, options, state, checkpoint, cancelled, report, *, wait=True):
        base = self.settings["instagram_api_url"]
        auth = {"Authorization": f"Bearer {profile['access_token']}"}
        # A container whose upload never finished can't be used; an unpublished container is
        # harmless, so start a fresh one (nothing goes live until media_publish).
        if not (state.get("remote_id") and state.get("uploaded")):
            video = Path(job["video"])
            if not self.settings["instagram_min_seconds"] <= job["duration"] <= self.settings["instagram_max_seconds"]:
                raise ValueError("Instagram Reels must be 3 seconds to 15 minutes long.")
            if video.stat().st_size > self.settings["instagram_max_bytes"]:
                raise ValueError("The video is over Instagram's 300 MB limit for Reels. Try a shorter clip.")
            data = {"media_type": "REELS", "upload_type": "resumable", "caption": job["caption"]}
            if self.settings.get("instagram_audio_name") and job.get("title"):
                data["audio_name"] = job["title"][:100]  # the Reel's sound shows the beat's name, not "Original audio"
            result = response_json(self.request("POST", f"{base}/{profile['user_id']}/media", headers=auth, data=data))
            checkpoint(remote_id=result["id"], uploaded=False)
            uri = safe_upload_url(result.get("uri", result.get("upload_url", "")), "instagram")
            if cancelled():
                raise Cancelled("Stopped before sending the video to Instagram.")
            with video.open("rb") as stream:
                sent = response_json(self.request("POST", uri,
                    headers={"Authorization": f"OAuth {profile['access_token']}", "offset": "0",
                             "file_size": str(video.stat().st_size), "Content-Type": "application/octet-stream"}, data=stream))
            if not sent.get("success"):
                raise APIError("Instagram didn't confirm it received the video. Nothing was published; try again.", True)
            checkpoint(uploaded=True)
        container = state["remote_id"]
        attempts = self.settings["poll_attempts"] if wait else 1
        for index in range(attempts):
            if cancelled():
                return {"state": "processing", "remote_id": container}
            status = response_json(self.request("GET", f"{base}/{container}", headers=auth,
                                                params={"fields": "status_code,status"}, retries=2))
            code = status.get("status_code")
            if code == "PUBLISHED":
                return {"state": "published", "remote_id": container}
            if code == "FINISHED":
                if cancelled():
                    return {"state": "processing", "remote_id": container}
                try:
                    result = response_json(self.request("POST", f"{base}/{profile['user_id']}/media_publish", headers=auth,
                                                        data={"creation_id": container}))
                except APIError as error:
                    if error.code != 9007:  # 9007: not ready to publish yet, so check again
                        raise
                else:
                    return {"state": "published", "remote_id": container, "post_id": result["id"],
                            **self.instagram_link(base, auth, result["id"])}
            if code in ("ERROR", "EXPIRED"):
                detail = re.sub(r"https?://\S+", "", str(status.get("status", "")))[:160]
                raise APIError(f"Instagram rejected this video ({code.lower()}{': ' + detail if detail else ''}). "
                               "Check the media requirements, then create the videos again to try a new post.", rejected=True)
            report("Instagram is processing the video…")
            if wait and index < attempts - 1:
                self.pause(cancelled)
        return {"state": "processing", "remote_id": container}

    def instagram_link(self, base, auth, media_id):
        """The live Reel's link for History; the post is already published either way."""
        try:
            found = response_json(self.request("GET", f"{base}/{media_id}", headers=auth, params={"fields": "permalink"}))
        except APIError:
            return {}
        return {"url": found["permalink"]} if found.get("permalink") else {}

    def post_tiktok(self, job, profile, options, state, checkpoint, cancelled, report, *, wait=True):
        base = self.settings["tiktok_api_url"]
        auth = {"Authorization": f"Bearer {profile['access_token']}"}
        initial_status = None
        if state.get("remote_id") and not state.get("uploaded"):
            # TikTok posts automatically once the upload lands, so ask before sending it again.
            initial_status = self.tiktok_status(base, auth, state["remote_id"])
            status = initial_status.get("status")
            if status == "PROCESSING_UPLOAD":
                raise APIError("TikTok is still waiting on an earlier unfinished upload. Try again later.")
            if status != "FAILED":
                checkpoint(uploaded=True)  # the earlier transfer arrived; just follow it
            else:
                initial_status = None
        if not (state.get("remote_id") and state.get("uploaded")):
            creator = self.accounts.creator_info(profile)
            privacy = options.get("tiktok_privacy")
            if privacy not in creator.get("privacy_level_options", []):
                raise ValueError("Refresh TikTok account options and choose an available visibility.")
            if job["duration"] > creator.get("max_video_post_duration_sec", 0):
                raise ValueError("This video is longer than this TikTok account currently allows.")
            if not options.get("tiktok_consent"):
                raise ValueError("Review and accept the TikTok posting terms before publishing.")
            if options.get("branded_content") and privacy == "SELF_ONLY":
                raise ValueError("TikTok paid partnership content cannot use Only me visibility.")
            post = {"title": job["caption"], "privacy_level": privacy,
                    "disable_comment": creator.get("comment_disabled", False) or not options.get("allow_comment", False),
                    "disable_duet": creator.get("duet_disabled", False) or not options.get("allow_duet", False),
                    "disable_stitch": creator.get("stitch_disabled", False) or not options.get("allow_stitch", False),
                    "brand_content_toggle": bool(options.get("branded_content")),
                    "brand_organic_toggle": bool(options.get("own_brand")), "is_aigc": bool(options.get("is_aigc"))}
            video = Path(job["video"])
            size = video.stat().st_size
            chunk_size = min(size, self.settings["upload_chunk_bytes"])  # 5-64 MB chunks; a smaller file goes whole
            count = max(1, size // chunk_size)  # TikTok: rounded down; the last chunk takes the remainder
            if cancelled():
                raise Cancelled("Stopped before creating a TikTok post.")
            result = response_json(self.request("POST", base + "/post/publish/video/init/", headers=auth,
                json={"post_info": post, "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                                        "chunk_size": chunk_size, "total_chunk_count": count}}))["data"]
            checkpoint(remote_id=result["publish_id"], uploaded=False, username=creator.get("creator_username", ""))
            self.tiktok_upload(safe_upload_url(result["upload_url"], "tiktok"), video, size, chunk_size, count, cancelled, report)
            checkpoint(uploaded=True)
        publish_id = state["remote_id"]
        attempts = self.settings["poll_attempts"] if wait else 1
        for index in range(attempts):
            if cancelled():
                break
            result = initial_status if index == 0 and initial_status is not None else self.tiktok_status(base, auth, publish_id)
            if result.get("status") == "PUBLISH_COMPLETE":
                # TikTok's field name is spelled this way; IDs only come back for public posts that passed moderation
                ids = [str(i) for i in result.get("publicaly_available_post_id", result.get("publicly_available_post_id")) or []]
                done = {"state": "published", "remote_id": publish_id, "post_ids": ids}
                if ids and state.get("username"):
                    done["url"] = f"https://www.tiktok.com/@{state['username']}/video/{ids[0]}"
                return done
            if result.get("status") == "FAILED":
                reason = str(result.get("fail_reason", "unknown"))
                raise APIError(f"TikTok couldn't post this video: {TIKTOK_FAILURES.get(reason, reason)}. "
                               "Create the videos again to try a new post.", code=reason, rejected=True)
            report("TikTok is processing the video…")
            if wait and index < attempts - 1:
                self.pause(cancelled)
        return {"state": "processing", "remote_id": publish_id}

    def tiktok_upload(self, upload_url, video, size, chunk_size, count, cancelled, report):
        """PUTs each chunk: TikTok answers 206 per chunk and 201 once the whole file arrived. A chunk hit by a
        dropped connection or server error is sent again (TikTok's documented retry); one publish ID is one post."""
        retries = 0
        with video.open("rb") as stream:
            offset = 0
            for index in range(count):
                last = index == count - 1
                chunk = stream.read(size - offset if last else chunk_size)
                end = offset + len(chunk) - 1
                while True:
                    if cancelled():
                        raise Cancelled("Stopped. TikTok may keep the incomplete transfer temporarily.")
                    try:
                        response = self.request("PUT", upload_url, headers={"Content-Type": "video/mp4",
                            "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"}, data=chunk)
                        break
                    except APIError as error:
                        if not error.uncertain or retries >= self.settings["upload_retries"]:
                            raise
                        retries += 1
                        self.pause(cancelled, 2 ** retries)
                if response.status_code != (201 if last else 206):
                    raise APIError("TikTok didn't confirm the upload. Check your TikTok profile before trying again.", True)
                offset = end + 1
                report(f"TikTok upload {int(offset / size * 100)}%")

    def tiktok_status(self, base, auth, publish_id):
        return response_json(self.request("POST", base + "/post/publish/status/fetch/", headers=auth,
                                          json={"publish_id": publish_id}, retries=2))["data"]

    def pause(self, cancelled, seconds=None):
        deadline = time.monotonic() + (self.settings["poll_seconds"] if seconds is None else seconds)
        while time.monotonic() < deadline and not cancelled():
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
