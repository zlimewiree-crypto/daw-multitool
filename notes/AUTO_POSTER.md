# Auto Poster

Open **DAW Multitool → Auto Poster**. It turns each audio track into an MP4 using
the image or video you choose. Every track gets two versions automatically:
**1920×1080 (16:9) for YouTube** and **1080×1920 (9:16) for TikTok/Instagram**.
Publishing chooses the matching file for each selected account; TikTok and Instagram
share the vertical file.

## What runs by itself (v0.9.0)

- **Automatic background** (the default): the beat's own cover art when the file has one (shown whole over a
  blurred copy), otherwise a purple glow (`auto_background_colours`). Nothing to choose. **Folder…** instead of a
  file makes each beat take the next picture or video in that folder (`background_turns` remembers the order).
- **TikTok / Reels length**: YouTube gets the full beat (or your Start/Length); TikTok and Reels get their own
  part, by default the best 30 s (`short_seconds`; 15/30/45/60 or Same as YouTube): where the drums and 808 hit
  hardest, starting on the beat grid of the BPM in the file name. Each version's start and length are saved in
  the receipt, so platform length limits check the right one.
- **Text and waveform on the video**: the beat's title and "155 BPM · F minor · prod. you" (`overlay_fonts`,
  `overlay_accent`), plus a moving waveform line (`waveform_colour`, `waveform_volume`).
- **Titles**: `{title}` (the name without BPM, key, credits or copy numbers), `{bpm}`, `{key}`, `{credits}`,
  `{name}` (the whole file name). Default `{title} ({credits}) | {bpm} {key}` (`title_template`, remembered).
  BPM and key come from the file name; when it has none, BPM is heard from the audio. A key heard from the audio
  is never put in public text unless `heard_key_in_text` is on (in the `music` settings).
- **Autopilot tab**: choose the folder your DAW exports to and turn it on. Every finished beat that arrives
  after that (never the files already there) is checked for drums (melody loops are skipped; unsure files still
  get videos), both videos are made with the Create/Post choices, it's listed and you get a notification.
  `autopilot.json` in app data remembers each file (a re-export counts as new). **Review ready videos** selects
  them on the Create tab. Nothing is posted until you preview, tick the review box and confirm: TikTok requires
  a person to choose each post's visibility. With the login helper on (Schedule tab), new beats get their videos
  even while the app is closed (project screen recording is never done unattended).
- **Posting times** (Autopilot tab, `post_times`, e.g. 12:00, 18:00): **Schedule…** offers the next free ones,
  one post per slot, skipping times already taken in the queue; or pick a date/time and spacing as before.

## Make a video

1. Add one or more audio files. Under **Background** pick **Still image** or **Downloaded video**, then choose the file. The same visual is used
   for all selected tracks. Images stay on screen; shorter videos loop. The visual's
   original soundtrack is replaced by your audio.
1b. **Project screen recording**: YouTube (16:9) uses the still image you choose; TikTok/Reels (9:16)
   show your FL Studio (.flp) or Ableton (.als) project playing. Each track is matched to a project
   by name (`needa lead 140 fmin.wav` -> `needa lead.flp`; longest matching name, newest file wins;
   DAW Backup folders and autosaves are ignored), searched in `project_folders` up to
   `project_search_depth` folders deep. The **DAW project** column shows the match; select a row and
   press **Choose project…** to change it. On **Create videos**, any track without a project asks you
   to choose one or Cancel (nothing is recorded). Auto Poster then opens the project (`daws` maps
   `.flp`/`.als` to the app and its process name), waits for its window and for its title to hold still
   for `project_settle_seconds` (up to `project_load_seconds`). The window counts when its title names the
   project, or, if the DAW's title doesn't, when its main window's title changed after opening. It records
   only that window with macOS `screencapture`, and after `recording_lead_seconds` presses the DAW's
   `start_keys` (default Home, key code 115 = back to the song start) then Space; Space again stops.
   It records from the song start to the end of your clip, then trims so the playhead lines up with the
   audio (also with "Start at the best part"). If the recording is too short, or the window never moves
   after play (DAW stuck on a dialog), that track fails instead of posting a frozen video.
   Recordings are kept in `Screen recordings/` inside the output folder. Keys are only pressed while the
   DAW is frontmost. Projects are opened, never saved or changed.
   The 9:16 version shows the whole DAW window in the middle over a stretched, blurred copy of itself
   (Fit/Fill don't apply to it). FL Studio must be in SONG mode: its only mode key (L) toggles, and FL
   doesn't save the mode where Auto Poster can read it.
   Needs **Screen Recording** and **Accessibility** for DAW Multitool
   in System Settings → Privacy & Security. Builds are signed with a local certificate
   (`~/Library/Application Support/DAW Multitool/signing/`, made by the first `build_app.py --run`), so
   these permissions survive rebuilds. Save your DAW work first, keep hands off and turn on Do Not
   Disturb while it records.
   Playback keys are sent directly by DAW Multitool to the focused DAW, using the app's own permission.
   System Events/AppleScript is no longer used to press Play. If macOS still reports access as off after
   enabling it, quit and reopen the app; if needed, remove its old Accessibility entry and add the current app.
   The status shows loading, recording time (the full beat), and final checks. A recorder that fails to exit
   is stopped after the planned duration plus `recording_finish_seconds`; its `.log` stays beside the recording.
2. Choose Fit (show the whole visual) or Fill (crop to fill each frame). Both versions
   use that setting. Start/Length trim the same audio in both; length 0 uses the rest.
2b. **TikTok / Reels** picks that version's own part (see "What runs by itself"). **Add my producer tag**
   mixes your tag file in every N seconds (`tag_*` settings) and also saves a tagged MP3 next to the videos.
3. **Create videos** holds media, video options, previews and publishing actions. Open **Post settings** for
   **YouTube**: title (100-character counter), description and visibility.
   **TikTok & Instagram**: one description plus **Hashtags** (with or without #; saved for next time);
   both platforms get "description, blank line, hashtags". The default YouTube title is `{name} ({credits})`; `{name}` uses each audio filename and `{credits}` becomes
   "prod. you x @collaborator" from names like `@hauntshl`, `prod. cari`, `ar22.flp`, `[kimbo]`
   ("Your producer name" goes first; empty brackets disappear when nobody is credited).
   Auto Poster's Credits editor has been removed. Existing saved credit rules still work. To edit the collab
   name list, open Audio Organizer → **Naming, filters & learning options** → **Collab producers**, then **Save credits**.
   These explicit names match anywhere in the filename, ignoring case; no `@` or
   `prod.` marker is needed. They are literal fragments, so choose specific names.
   The Collab credits column shows matches or Solo before rendering. Repeated matches
   and your own name are omitted. Custom titles/captions can include `{credits}` too.
   The organiser has the same saved name list and updates its Credits column; detecting
   a collaborator does not decide whether the audio contains drums.
4. **Check plan** validates without rendering or uploading, and shows each track's start,
   loudness (LUFS) and a warning if it is quiet (< -16 LUFS) or may clip (true peak > -1 dB). **Create videos** renders
   both versions locally. Originals stay untouched and each pair gets a new folder.
5. Select a rendered row and use **Preview 16:9** and **Preview 9:16**. Review both
   before confirming publishing. With no selected rows, batch
   actions use all listed rows.

Audio formats enabled by default: MP3, WAV, AIFF, FLAC, M4A, AAC, OGG and Opus.
Visuals include PNG/JPEG/WebP/BMP/TIFF and MP4/MOV/M4V/WebM/MKV/AVI.
Defaults render H.264/AAC MP4 at 30 fps. FFmpeg is included in the bundled app.

## Connect your accounts

Use the **Accounts** tab. Each **Connect** button opens the platform's sign-in in your
browser, where you pick the account; you're never asked to paste a token.

**First time:** Connect (or **Setup steps**) opens a small setup window (`setup_window.py`)
that shows one step per page (Back / Next, "Step 3 of 6"), floats beside the browser, and
has one **Open this page ↗** button per step plus **Copy** for every value to type. Keys
fill themselves in: while the window is open it recognises a key you copy on the platform's
page (Google `….apps.googleusercontent.com` / `GOCSPX-…`, Meta App ID / 32-hex secret /
Configuration ID by step, TikTok key / secret by shape; anything unrecognised is ignored and
can be pasted), and for YouTube it picks up Google's **Download JSON** file from Downloads.
With every key in it jumps to the last page: **Connect**. Keys are saved in Keychain as `<platform>-app` (kept on
Disconnect), never in config.json, render receipts or the app bundle. Do not paste them in chat.

**YouTube:** Enable YouTube Data API in your Google project, set up the sign-in screen
(External; only app name, support email and contact email: no logo, no links), add yourself
as a test user and **stay in Testing** (Google requires home page, privacy policy and terms
for published External apps). Testing sign-ins last 7 days: Accounts shows the date and
Schedule… warns when a post falls after it; reconnecting is one click. Then create a
**Desktop app** OAuth client (any loopback port works; nothing to register). Connect checks
that every permission box was ticked (Google's granular consent) before saving the login,
and known setup mistakes (API off, not a test user, wrong keys) open a dialog with the fixing page. Connect always shows Google's
account chooser (`prompt=select_account consent`), then the channel. Loopback callback +
PKCE; refresh tokens stay in Keychain. The initial privacy setting is private.
[Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[YouTube resumable uploads](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol).

**Instagram:** Facebook Login in the browser (Graph API publishing route). Enter the Meta
app ID + secret once (plus a Facebook Login for Business configuration ID if the app
uses one; otherwise the scopes in `instagram_scopes` are requested). In the login, tick
the Facebook Page and its Instagram professional account. The app swaps the code for a
long-lived user token, then each shared Page's (non-expiring) Page token; with several
Instagram accounts it asks which one. `http://localhost:8766/callback/instagram` works
in development mode; add it as a redirect URI once the Meta app is live.
No public file hosting is needed: local generated video bytes go to Meta's returned
upload URI, then the container is processed and published.
[Facebook Login manual flow](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow),
[Meta publishing guide](https://developers.facebook.com/docs/instagram-platform/content-publishing/),
[Meta's resumable-upload sample](https://github.com/fbsamples/reels_publishing_apis/tree/main/insta_reels_publishing_api_sample).

**TikTok:** Use a **Sandbox** of your app (works without TikTok's review; add your TikTok
account under Target users). Configure a **Desktop** Login Kit app with Direct Post and `video.publish`.
Register `http://127.0.0.1:8765/callback/tiktok` (port is configurable). Enter your client
key/secret once and Connect; `disable_auto_auth=1` always shows TikTok's login page so
you can switch accounts. The desktop flow uses TikTok's documented hex SHA-256 PKCE
challenge. Selecting TikTok loads the creator's current account name, duration limit,
visibility choices and allowed interactions. Visibility has no default. Comments,
Duet and Stitch start off; disabled account permissions stay disabled. Disclose own-brand,
paid-partnership and AI-generated content when applicable. Accept TikTok's posting terms.
Unaudited clients have private-account/visibility restrictions; public posting depends
on TikTok app approval. Its developer guidelines also restrict private/internal-only
utilities, so access is not guaranteed simply by obtaining credentials.
[Desktop Login Kit](https://developers.tiktok.com/docs/en/login-kit-desktop),
[Direct Post API](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post),
[developer requirements](https://developers.tiktok.com/docs/en/content-sharing-guidelines).

**Disconnect locally** removes saved credentials from Keychain. Revoke the app's grant
in the provider's account settings if you also want to remove its remote permissions.

## Schedule posts

Select rendered videos, tick the review box, choose platforms, then **Schedule…**: pick
the first date/time and (for several) hours between posts. Titles/captions are written
into each receipt then; the queue is `schedule.json` in app data. The **Scheduled** tab
lists, cancels and shows outcomes. Due posts go out while Auto Poster is open (checked
every `schedule_check_minutes`). **Post while the app is closed…** installs
`~/Library/LaunchAgents/com.dawmultitool.scheduler.plist`, which runs
`DAW Multitool --run-schedule` on the same interval with the same data folder and
shows a notification per post; the same button removes it. One runner at a time (lock);
posts more than `schedule_max_late_hours` late are marked missed; a post interrupted
mid-upload is marked "check" (never retried blindly). Publisher's duplicate protection applies.

Each queued post keeps what you approved: its title/captions and the stable ID of each
connected account (YouTube channel ID, Instagram account ID, TikTok open_id). If either
changed before it goes out, nothing is sent and it's marked failed; schedule it again.
A post is claimed under the queue lock just before sending, so one cancelled meanwhile
never goes out. "processing" posts are finished on later checks with the same upload
(Instagram media_publish / TikTok status), for up to `schedule_max_late_hours`, then "check".

## Results

The **Results** tab finds every `post.json` under the output folder (plus scheduled and
open ones) and asks each platform for numbers: YouTube `videos.list` statistics,
Instagram media `like_count/comments_count` + `/insights` views/shares
(`instagram_manage_insights`), TikTok `video/query` (`video.list`: only once TikTok has
audited the app and posts are public; then add the Display API and put `video.list` back
in `tiktok_scopes`). Grouped per beat, best first; sortable; double-click opens the post. Last
numbers are kept in `results.json`. Accounts connected before v0.6.0 must reconnect.

## Publish and check results

Choose platforms, review each video, tick the review confirmation, then press
**Publish selected platforms**. Connecting an account, planning or rendering never
publishes. Before anything is sent (or scheduled) Auto Poster checks each platform's
text rules (YouTube: title ≤ 100 characters, description ≤ 5000 bytes, no `<` `>`;
Instagram: ≤ 2200 characters, 30 hashtags, 20 @mentions; TikTok: 2200 UTF-16 units)
and reads which account is connected right now, then shows a summary to confirm: per
track and platform, that account, the video's size and length, the visibility and the
exact title/caption. Cancel sends nothing. What you confirm (text + account IDs) is
enforced again while posting or when a scheduled post goes out.
An existing remote upload keeps the text sent when it was created.

The batch sends each upload before waiting for processing. Pending IDs are checked in
turn with one shared `poll_attempts` budget and `poll_seconds` interval. Anything still
processing after that is finished by itself every `schedule_check_minutes` while Auto
Poster is open (and by the login helper, if on): the same ID is checked, never re-uploaded.
This matters for Instagram: a Reel is not live until its publish step runs.

How each platform is confirmed:
- YouTube: resumable upload; a dropped chunk resumes where YouTube says it stopped
  (`upload_retries`). `published` only once `videos.list` says `processed`; a rejection
  (duplicate, copyright, bad file) is `failed` with the reason. If YouTube changed the
  visibility you chose, Delivery history notes it. Category = `youtube_category_id` (10, Music).
- Instagram: the upload must answer `success`; container `FINISHED` → `media_publish`
  (error 9007 "not ready" just waits) → the Reel's link is saved. `audio_name` = the
  title, so the sound shows the beat's name (`instagram_audio_name`).
- TikTok: each chunk must answer 206 and the last 201 (a chunk hit by a server error is
  resent); `PUBLISH_COMPLETE` → link (public posts only); `fail_reason` is explained.

Every upload remembers the account it went to; a different connected account is never
used to finish it. Platform errors show the platform's own reason plus what to do.

Videos: H.264 High 4:2:0, 30 fps, AAC 48 kHz stereo 128 kbps, `+faststart`, no MP4 edit
lists (Instagram's Reels spec), bitrate capped at `video_max_bitrate`. Each finished
video is checked for picture, sound and its planned length before it can be posted.
Frame: Fit (bars), Fill (crop) or Blur (whole picture over a blurred copy).

Each pair has two dimension-labelled MP4s, per-video logs, and one `post.json` receipt
listing both files and their snapshots. **Load saved render** reopens that pair after
restarting; source audio is not needed again to publish. Both files must be complete and
unchanged before a new upload (finishing an upload already sent needs no local file).

- `published`: the platform confirmed the post (link in Delivery history).
- `processing`: sent; being finished automatically. Not re-uploaded.
- `failed`: read the error and fix credentials, limits or metadata.
- `uncertain`: the result isn't known yet. Once the whole video is with the platform,
  only the platform's own refusal (YouTube rejected, Instagram ERROR/EXPIRED, TikTok
  FAILED) makes it `failed`; rate limits, dropped connections or an expired sign-in while
  checking leave it `uncertain` and it's checked again automatically. Without a saved ID,
  automatic retry is blocked: check the account before creating another render/post.

## First live test checklist

1. YouTube: follow Accounts → YouTube → Setup steps (stay in Testing, add yourself as a test
   user; reconnect every 7 days). Uploads are limited to 100 per day per Google app.
2. TikTok: until TikTok audits your app, your TikTok account must be **Private** and
   the post visibility **SELF_ONLY** (Only me); public posts are refused
   (`unaudited_client_can_only_post_to_private_accounts`). Developer app needs Content
   Posting API (Direct Post) with `video.publish`, set up on the Sandbox and saved with Apply changes.
   Any scope the app lacks makes TikTok refuse the whole login (“correct the following: scope”).
   App details need an icon (the wizard saves a 1024 px PNG to Downloads) and, for Desktop, a website:
   any real page of the user's works, since TikTok verifies URLs only at review (Sandbox: only for pull-from-URL posting).
3. Instagram: professional (Business/Creator) account linked to a Facebook Page; your
   Facebook account has a role on the Meta app; Connect and tick both Page and account.
4. Post one short private/unlisted test with Publish, wait for `published` + link in
   Delivery history, open the link. Then schedule one a few minutes ahead.

Stop cancels remaining work. A network call already in flight can take up to its
timeout and a video already sent can finish processing. Interrupted outputs are retained;
the source media is never deleted. Incomplete remote transfers may require a new attempt.

## Developer checks

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m multitool.auto_poster.render AUDIO IMAGE_OR_VIDEO --dry-run
.venv/bin/python -m multitool.auto_poster.render AUDIO IMAGE_OR_VIDEO --run
.venv/bin/python scripts/build_app.py --run
```

The render CLI never publishes; default is a dry-run. Settings, formats, file types,
limits, API versions/URLs and timeouts come from `auto_poster` in config.example.json,
overridden by the same section of config.json. `platform_formats` routes platforms to
the configured `formats` resolutions; YouTube must be 16:9 and TikTok/Instagram must
share one 9:16 format. The CLI also defaults to both; `--format` makes a single version
for local use, which cannot start a new social upload. `ffmpeg_path` is an optional override.
Media metadata is cached by path, size and modification time; changed inputs are probed
again, and rendering still waits for exports to finish and checks source snapshots.
Tests generate fake audio through scripts/make_test_files.py in test_exports/ and mock
all provider calls. Generated fixtures are moved to a logged `/private/tmp` archive
after the tests, preserving the fake audio. Live account authorization and real publishing must be checked by
the user with their credentials; no real account was used during implementation.
