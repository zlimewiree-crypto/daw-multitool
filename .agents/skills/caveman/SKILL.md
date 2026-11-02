---
name: caveman
description: Ultra-short reply style that cuts output tokens. Use when the user says "caveman", "be brief", "less tokens", "short answers", and ALWAYS for agent-to-agent text (handoff entries, status lines between tool calls). Off for normal explanations to the producers unless they ask.
---

# Caveman (terse mode)

Rules adapted from the caveman skill (github.com/JuliusBrussee/caveman, Apache-2.0). Only the
writing rules: no installer, hooks, proxy or telemetry.

## When
- **Always:** `notes/HANDOFF.md` entries, notes written for the other agent, one-line
  progress updates between tool calls.
- **On request:** replies to the user after "caveman" / "be brief" / "less tokens", until they
  say "normal mode". The default for the user is still plain language (AGENTS.md).

## Rules
1. Result first, then the reason, then the next step. Nothing else.
2. No greetings, hedging, recaps or closing lines.
3. Short, plain words. No made-up abbreviations.
4. Drop "the/a" if it reads fine. Keep every negation (not, never, only, except) and every number exact.
5. One idea per sentence. Short sentences.
6. Code, commands, paths and errors are copied exactly.
7. Progress: one line per phase, not per command.

Example: "Bug in sorter.new_name: date format with '/' makes a subfolder. Fixed: '/' → '-'. Tests 80/80."

## Never terse
Write full, clear sentences for: anything that could lose or move audio files, irreversible
actions, step-by-step instructions the user will follow, a confused user, questions to the
user, and anything saved for people to read later (docs, Home.md, commit messages).
