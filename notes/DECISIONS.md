# Decisions log

Choices we've locked in, so neither you nor the agents argue them again.

| Date | Decision | Why |
|---|---|---|
| 2026-10-30 | All Auto Poster logins and app keys live in one Keychain entry (`accounts`) | Self-signed builds have no Apple Team ID, so macOS ties each entry to one build's code hash (`cdhash:` partition) and asks once per entry after every update; one entry = one question. Only an Apple Developer ID removes it; reading keys via /usr/bin/security was rejected (any program could read them) |
| 2026-10-30 | TikTok sign-in asks only for `video.publish`; `video.list` (Display API) stays out until TikTok audits the app | Any scope missing from the TikTok app fails the whole login (“correct the following: scope”), and unaudited posts are private with no post ID for Results anyway |
| 2026-10-30 | Out of credits → the user types "take over" in the other agent. Claude Code hooks keep notes/LIVE_SESSION.md current after every step and hold Claude's automatic continue at the limit reset when project files changed meanwhile | Claude can't write a handoff once it's out of credits, so the log has to write itself. A fully automatic switch would run an unwatched agent over mostly uncommitted work, and two agents must never edit at once. 9Router-style proxies rejected: a third party sees all code and keys, and the free models are much weaker |
| 2026-10-30 | Account setup is a one-step-per-page window that reads the clipboard (only while it's open, only key-shaped text on key steps) and Google's downloaded key JSON in Downloads | User asked for the simplest possible setup; removes pasting and wrong-box mistakes; unrecognised text is ignored, so it fails safe |
| 2026-10-30 | YouTube's Google app stays in Testing (test user, 7-day sign-ins) instead of being published | Google requires home page, privacy policy and terms links for published External apps; Testing needs only 3 fields. The app shows the reconnect date and warns before scheduling past it |
| 2026-10-30 | Pro Tools sessions are in the Project Library only (`project_library.extra_daws` / `extra_project_folders`), not Auto Poster's screen recording | User asked for Pro Tools in the Library; recording Pro Tools is unbuilt/untested, so Auto Poster stays FL Studio / Ableton |
| 2026-10-30 | A post is `published` only after the platform confirms it (YouTube `processed`, Instagram `media_publish`, TikTok `PUBLISH_COMPLETE`); sent-but-unconfirmed uploads are finished automatically by ID, never re-uploaded | User wants posts to be correct and actually go live; an Instagram Reel isn't live until its publish step |
| 2026-10-30 | Before posting or scheduling, platform text rules are checked and a per-track summary must be confirmed | The user sees exactly what goes out, and nothing is sent that a platform would reject |
| 2026-10-29 | Post Scheduler posts from the open app; closed-app posting is an opt-in LaunchAgent the user turns on in the Scheduled tab; >24 h late = missed | User asked for scheduling; a login item is persistence, so it's never installed without the user's click, and late posts never go out by surprise |
| 2026-10-29 | Project Library reuses Auto Poster's project folders/matching and is read-only | One list of folders for both tools; projects are never moved or changed |
| 2026-10-29 | Results Tracker reads stats only from published IDs in receipts; numbers cached in results.json | No new accounts or storage; nothing posted or changed |
| 2026-10-29 | Next tools: Project Library, Post Scheduler, Results Tracker. BPM/key tagger, beat pack builder and split sheet maker are not to be built | User picked these three and said not to build the other ideas at all |
| 2026-10-29 | User-specified collaborator names match literal filename fragments, ignoring case; one saved list serves both tools | User wants bare names such as ar22 recognised without @/prod markers; credit matching never overrides the audio's beat/melody category |
| 2026-10-29 | Auto Poster creates a wide/vertical pair per track, with one receipt; YouTube receives 16:9, TikTok/Instagram share 9:16 | User requested both versions for every upload; source media stays read-only, incomplete pairs cannot publish, old processing IDs remain resumable |
| 2026-10-28 | Auto Poster creates videos locally, then publishes explicitly to selected YouTube/Instagram/TikTok accounts using official APIs | User requested audio + image/video multi-platform posting; originals stay read-only, credentials stay in Keychain, and delivery IDs prevent duplicate retries |
| 2026-10-28 | DAW Multitool is the main app; separate tool adapters share a registry and open their own windows | User wants a bundle of studio functions; organizer is one tool, with its current engine retained |
| 2026-10-28 | Tkinter host + PyInstaller macOS bundle; mutable app data lives in Application Support when bundled | Reuse the Python stack, add functions without changing the home screen, and preserve settings across builds; see [[ARCHITECTURE]] |
| 2026-10-28 | Python + watchdog + mutagen | Simple, runs on Mac, good audio libraries |
| 2026-10-28 | Filename tags first (`loop_`, `beat_`, `stem_`), length rules as fallback | Tags never guess wrong; length covers untagged files |
| 2026-10-28 | Loops go in `Loops/YYYY-MM/` | Kellan's original idea: loops sorted by date |
| 2026-10-28 | Every mover has `--dry-run`, never deletes, never overwrites, logs to `_log.txt` | It's real music, so losing a file is the worst bug |
| 2026-10-28 | Build in phases (see [[PLAN]]); the YouTube type-beat uploader is phase 6 | Organizer first; the uploader builds on `Beats/` |
| 2026-10-28 | Uploader: queue → ffmpeg mp3+art→mp4 → template title/tags → scheduled YouTube API upload. Selling stays on BeatStars/Airbit | Unverified API apps upload as private and the quota is small, so post on a schedule |
| 2026-10-28 | Claude Code + Codex in tandem: one builds, the other runs `cross-review`, and they pass work through [[HANDOFF]] | Second opinion catches bugs; switching agents when one hits its limit |
| 2026-10-28 | Bounce sorter: Finished Beats (has drums) vs Melody Loops (no drums), decided by librosa sound analysis; unsure files go to Needs Review (stopgap) | Kellan wants accuracy above all; fast analysis chosen over the heavy AI separator for now |
| 2026-10-28 | Accuracy is measured on user-labelled copies in `test_exports/real/`, not synthetic audio | Fake audio doesn't sound like real beats, so tuning on it would mislead |
| 2026-10-28 | Options window is Tk (`brew install python-tk@3.15`); settings are saved in config.json | Kellan picked a real Mac window with checkboxes and a preview list |
| 2026-10-29 | Detector v3 adds 808 sub-bass share (25–60 Hz of the whole mix) to the hat/snare trio; model still learned only from hand labels | Bench of 14 hand + 61 name-labelled Downloads files: wrong side 2→1, Needs Review 8→3. Training on name labels too sent vocals/adlibs to Melodies, so it was rejected |
| 2026-10-29 | Project mode matches exports to projects by name only (exact, else longest whole-word prefix; newest tie) and asks the user when nothing matches | FL/Ableton keep no reliable "exported to" path; real Downloads names matched their .flp/.als by prefix, and other producers' loops correctly found nothing |
| 2026-10-29 | Record via `screencapture -R` of the DAW window (CoreGraphics bounds, Screen Recording only) and Space via System Events, guarded by a frontmost check | Window-only keeps Discord/notifications out; play is never sent unless the project window is open and the DAW is in front |
| 2026-10-29 | Sign builds with a self-signed certificate kept in its own keychain (Application Support/DAW Multitool/signing) | Ad-hoc signatures change every build, so macOS drops Screen Recording/Accessibility grants; a fixed certificate gives a stable designated requirement. Login keychain untouched; search list changed only for the codesign call |
| 2026-10-29 | 9:16 project recordings use a stretched, blurred copy as background (user's choice) instead of Fit/Fill | A wide DAW window letterboxed or centre-cropped into 9:16 shows either tiny or partial content |

## Origin
Kellan Spann and Aidan came up with the idea: auto-organize DAW exports (loops by date,
beats in their own folder), plus an auto-poster for type beats on YouTube. Someone
else tried one about a year ago and nothing came of it, so whoever ships it wins.
