# 🎛️ DAW Multitool

A macOS home for separate studio tools: Audio Organizer and Auto Poster.

## Open the app
Double-click `Run DAW Multitool.command`, then **Open Audio Organizer**.
For the bundled version, build and open `dist/DAW Multitool.app`; see [[ARCHITECTURE]]
for the build command and how to add another tool.

## Use Auto Poster
Open **Auto Poster**, add audio and an image/video, choose Fit/Fill, and **Create videos** for both YouTube and vertical platforms.
Preview the output, connect credentials in **Accounts**, select platforms, then **Publish selected platforms**.
See [[AUTO_POSTER]] for account setup and delivery status details.
Set **Your producer name**, add names such as `ar22, purptokyo` under **Collab producer names**,
and press **Save credits**. Matching filenames show collaboration credits; the default title includes them.

## Use the Bounce Sorter
Double-click `Run Bounce Sorter.command`, choose your folders, press **Preview**, then **Sort checked files**.
The list stays open after sorting: **Space** listens, **C** marks correct, **X** marks incorrect,
and **B/M** gives the actual type (beat/melody), including files in Needs Review.
Answers save immediately. **Review sorted files** reopens completed files later; labels do not move them again.
Press **Learn from my labels** to improve future predictions.
The **Collab producers** field shares Auto Poster's saved name list; **Save credits** refreshes the Credits column.

## Notes
Graph colours: 🟡 this page · 🟢 using the tools · 🔵 plans and design · 🟣 agent hand-off.
`Notes.base` lists every note with when it last changed.

**Using the tools**
- [[AUTO_POSTER]] — video creation and YouTube/Instagram/TikTok account setup
- [[SETUP]] — installing Codex, Claude Code, VS Code, Python
- [[PROMPTS]] — copy-paste prompts for Codex

**Plans and design**
- [[PLAN]] — phases + checkboxes (what to build next)
- [[DECISIONS]] — choices we've locked in
- [[ARCHITECTURE]] — app structure, adding tools, settings and building the macOS bundle

**Agent hand-off**
- [[AGENTS]] — rules both agents follow, including the hard rules that protect your music
- [[STATE]] — short project map (agents read this first)
- [[HANDOFF]] — what the last agent did + what's next
- [[TANDEM]] — how Claude + Codex pass work back and forth

## Behind the scenes (no need to open)
- `CLAUDE.md` just points Claude at AGENTS.md
- `.agents/skills/` — 7 shared skills: phase-workflow, handoff, cross-review, lean-mode, sync-state, audio-file-safety, make-test-audio
- `.codex/config.toml` — Codex token-saving settings
- `config.example.json` — copy to `config.json` and set your export folder

## Daily loop
1. Open this folder in VS Code → open the Codex panel
2. Check [[PLAN]] for the next unchecked phase
3. Paste a prompt from [[PROMPTS]]
4. Test on fake files, commit, tick the box
