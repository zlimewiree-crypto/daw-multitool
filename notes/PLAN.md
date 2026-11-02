# DAW Multitool — Plan

Tick boxes as phases get finished. Codex reads this file before every phase.

## Foundation — Multi-tool application
- [x] Shared home screen and registry for independent tool windows
- [x] Organizer connected as the first tool, with lazy loading and one window per tool
- [x] Keep bundled resources separate from writable settings and labels
- [x] macOS app build command, packaged Python dependencies and frozen-worker support
- [x] Host lifecycle tests and instructions for adding tools (see [[ARCHITECTURE]])
- [x] Choose the next tool with Kellan — Auto Poster
- [ ] Distribution signing/notarization, when sharing outside the development Mac

## Auto Poster — Audio + visual → social video
- [x] Add a separate Auto Poster tool to the shared app
- [x] Batch audio inputs, still image/video visual, looping, trim, Fit/Fill, aspect presets
- [x] Plan first, render locally, preview; read-only originals and unique output folders
- [x] YouTube/TikTok desktop OAuth; verified Instagram professional-account token setup; Keychain storage
- [x] Official YouTube, Instagram Reels and TikTok upload/publish connectors with per-platform results
- [x] Persistent delivery receipts, processing checks and protection against duplicate unknown-result retries
- [x] Fake-media renders and mocked API/auth tests; include FFmpeg in app bundle
- [x] Always create paired 16:9 YouTube + 9:16 TikTok/Instagram videos; separate previews and automatic upload routing
- [ ] User connects existing credentials in Accounts and validates one live post per selected platform
- [x] Snippet finder: "Start at the best part" (drums + 808 energy, snapped to a beat)
- [x] Collab credits: `{credits}` from @handles / prod. / .flp / [brackets] (shared `multitool/credits.py`; organizer Credits column)
- [x] User-configured producer names recognise bare filename fragments (e.g. ar22), shared across both tools with a credit preview
- [x] Producer tag stamper: tag every N s in the videos + a tagged MP3
- [x] Loudness check in Check plan (LUFS / true peak warnings)
- [x] Automatic background (cover art / glow), waveform, title + BPM/key text; separate best-part TikTok/Reels trim
- [x] Autopilot: watched export folder → skip melody loops → both videos → notification; approve + next free posting times
- [ ] User: turn on Autopilot with the real export folder and approve one scheduled pair

## Varispeed — tape-style speed changes (new tool, v0.9.1)
- [x] Speed 50–200% (±12 st): slider, −12/−1/+1/+12 nudges, 100%, typed values, optional whole semitones
- [x] Live preview that glides between speeds (sounddevice); copies rendered with soxr VHQ, never overwriting
- [x] New BPM/key written into the copy's name in the file's own style
- [x] Mirrors the original's controls and behaviour: readouts, slider, glide curve, Record/takes, output device/pair
- [x] DAW-wide: our own audio device "DAW Multitool" + bridge; Varispeed "Your DAW" mode (v0.9.3)
- [ ] User: install from Settings, set the DAW's output to DAW Multitool, try speeds + Record on a real session

## Project Library — every FL / Ableton project in one window (new tool)
- [x] Search all projects in the configured folders (name, DAW, folder, last changed)
- [x] Link exports to the project they came from; filters: never exported / export with no project
- [x] Open in its DAW or show in Finder; read-only (never moves, renames or deletes projects)

## Post Scheduler — queue rendered pairs to publish later
- [x] Pick a date/time per track; queue saved in app data (schedule.json); edit/cancel before it's due
- [x] Posts due items with the existing publisher and duplicate protection
- [x] Runs while the app is closed only after the user approves a login item (macOS LaunchAgent)

## Results Tracker — how each post performs
- [x] Fetch views/likes/comments for every posted ID in the receipts (YouTube, TikTok, Instagram)
- [x] Table per beat across platforms, sortable; refresh on demand
- [x] Ask for the extra read permissions per platform (reconnect once)
- [ ] User: live check of scheduled post + stats once accounts are connected

The organizer roadmap below is retained independently of Auto Poster.

## Phase 1 — Manual sorter
- [ ] `organize.py` sorts everything currently in `watch_folder` once
- [ ] Tag rules first (`loop_`, `beat_`, `stem_`), then length rules
- [ ] Loops go into `Loops/YYYY-MM/` when `loops_by_month` is true
- [ ] `--dry-run` prints planned moves
- [ ] Never overwrites; logs to `_log.txt`

## Phase 1b — Bounce sorter (replaces the Phase 1 tag/length sorter)
- [x] Window with checkboxes: folder to scan, output folder, folder names, file types, filters
- [x] Finished Beats (has drums) / Melody Loops (no drums) / Needs Review (unsure) via librosa
- [x] Option: add the date the file was made to the name (start or end, chosen format)
- [x] Preview list (moves nothing); untick or re-route any file before sorting
- [x] Keep sorted files for listening and Correct/Incorrect review (C/X); B/M gives actual type; reopen from the move log
- [x] Never overwrites (atomic name claim), logs every move, waits for files to finish writing
- [x] Duplicate finder: identical files and same-name wav/mp3/" (1)" copies are flagged and left unchecked (never deleted)
- [ ] (6 beats + 8 loops so far: 14/14 right blind with detector v3) Kellan copies 15+ beats and 15+ loops into `test_exports/real/`, run `scripts/check_drums.py`
- [x] Reach 0 WRONG on the real test set before sorting the real Downloads folder (v3: 0 wrong on 14 labelled; 1 of 61 name-labelled files)
- [x] Replace "Needs Review" stopgap with a more accurate detector (v4: macOS sound classifier + measurements, 1 wrong vs 24 on 58 real + altered copies)

## Phase 2 — Auto-watcher
- [ ] `watch.py` uses watchdog to sort new files as they appear
- [ ] Waits until file size is stable (`settle_seconds`) before moving

## Phase 3 — Safety
- [ ] `undo` command reverses the last batch from `_log.txt`
- [ ] Skip temp/hidden files

## Phase 4 — Smart features
- [x] Detect BPM + key (the name is the reference; heard from the audio otherwise) and optionally add them to names (`154 c#min`)
- [ ] More categories (vocals, one-shots)

## Phase 5 — Make it nice
- [ ] Menu bar app or simple window
- [ ] Launches at login

## Phase 6 — Type beat uploader
- [ ] Queue new files from `Beats/`
- [ ] ffmpeg: mp3 + cover art → mp4
- [ ] Title/description/tags from a template
- [ ] Upload on a schedule via YouTube Data API (visibility selected by the user; manual uploads are in Auto Poster)

## Open questions (decide with Kellan)
- Categories beyond loops/beats/stems?
- Loops by date, BPM, or both?
- Move or copy originals?
- Just for us, or shareable/sellable later?
