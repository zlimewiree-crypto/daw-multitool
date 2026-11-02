# Setup (one time, on your Mac)

> **Shortcut:** open Terminal, type `bash ` (with a space), drag `setup.sh` from this
> vault into the window, press Enter. It does steps 1–3 for you and skips anything
> already installed. The steps below are what it does, in case you want to go manual.

## 1. Tools
Open **Terminal** and run these one at a time:

```bash
# Homebrew (skip if `brew --version` already works)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install python ffmpeg git
brew install --cask codex      # Codex CLI (or: npm install -g @openai/codex)
codex                          # choose "Sign in with ChatGPT"
```

## 2. VS Code
- Extensions tab → search **Codex** (publisher: OpenAI) → Install → sign in with ChatGPT.
- Also install **Python** (Microsoft).
- File → Open Folder → this vault folder.

## 3. Project
In VS Code's terminal (Terminal → New Terminal), inside this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # then edit watch_folder to your real export folder
python3 scripts/make_test_files.py   # fake test audio
git init && git add . && git commit -m "starter kit"
```

## 4. Skills
- Your custom skills are already in `.agents/skills/` — type `$` in Codex to see them.
- Browse OpenAI's official extras: type `$skill-installer` in Codex (lists curated skills, installs the ones you pick). Restart Codex if a new one doesn't show.

## 5. Obsidian
Obsidian hides dot-folders (`.agents`, `.venv`, `.git`) — that's fine, they're for Codex/Python, not reading.

## ChatGPT vs Codex
- **ChatGPT**: brainstorming, "how should this work", explaining errors.
- **Codex**: actually editing files and running code in this folder.
