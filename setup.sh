#!/bin/bash
# One-command setup for Beat Organizer.
# Run from Terminal:  bash setup.sh   (or drag this file into Terminal after typing "bash ")
# Safe to run again — it skips anything already installed.

cd "$(dirname "$0")" || exit 1
step() { echo; echo "==> $1"; }

# Homebrew (Apple Silicon puts it in /opt/homebrew)
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew isn't installed yet. Paste this into Terminal (it asks for your Mac password):"
  echo
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo
  echo "When it finishes, run:  bash setup.sh  again."
  exit 1
fi

step "Installing Python, ffmpeg and Git (skips anything already installed)"
brew install python ffmpeg git

step "Codex"
if command -v codex >/dev/null 2>&1; then
  echo "Codex is already installed ($(codex --version 2>/dev/null))"
else
  brew install --cask codex
fi

step "Claude Code"
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
  echo "Claude Code is already installed"
else
  curl -fsSL https://claude.ai/install.sh | bash
fi

step "VS Code extensions (Codex + Claude Code + Python)"
if command -v code >/dev/null 2>&1; then
  code --install-extension openai.chatgpt || echo "Couldn't add Codex extension — search 'Codex' by OpenAI in VS Code's Extensions tab."
  code --install-extension anthropic.claude-code || echo "Couldn't add Claude Code extension — search 'Claude Code' by Anthropic in VS Code's Extensions tab."
  code --install-extension ms-python.python || true
else
  echo "Skipped: in VS Code press Cmd+Shift+P → 'Shell Command: Install code command in PATH', then run this script again."
fi

step "Python environment + libraries"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt && echo "watchdog + mutagen installed"

step "Config"
if [ -f config.json ]; then
  echo "config.json already exists — leaving it alone"
else
  cp config.example.json config.json
  echo "Made config.json — open it and set \"watch_folder\" to where FL/Ableton export your audio"
fi

step "Fake test audio"
./.venv/bin/python scripts/make_test_files.py

step "Git (version history, so you can undo anything Codex does)"
if [ ! -d .git ]; then
  git init -q
  git config user.name >/dev/null || git config user.name "DAW Multitool"
  git config user.email >/dev/null || git config user.email "noreply@localhost"
  git add . && git commit -qm "starter kit" && echo "First save point created"
else
  echo "Git already set up"
fi

echo
echo "✅ Done. Last step: sign in to Codex —"
echo "   run:  codex login   (choose 'Sign in with ChatGPT')"
echo "Then sign in to Claude Code: run  claude  once and follow the browser prompt."
echo "Open 'DAW multitool.code-workspace' and tell Claude or Codex:  read the handoff and continue"
