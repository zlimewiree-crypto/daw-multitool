# Claude + Codex tandem workflow

Both agents read the **same rules** (`AGENTS.md`) and the **same skills** (`.agents/skills/`).
Each one passes work to the other through [[HANDOFF]] and the project map in [[STATE]].

## The loop (one phase)
1. **Codex builds:** `$phase-workflow do phase 1`
   → it builds, tests on fake audio, and writes a handoff.
2. **You commit:** `git add . && git commit -m "phase 1"` (or ask the agent to do it).
3. **Claude reviews:** `/cross-review`
   → it reports "ship it" or a short list of fixes.
4. **Codex fixes:** "fix the review findings in HANDOFF"
5. Tick the box in [[PLAN]] and move to the next phase.

Swap roles anytime: Claude can build (`/phase-workflow do phase 2`) and Codex can
review (`$cross-review`). The skills are the same either way.

## How to call skills
| | Codex | Claude Code |
|---|---|---|
| Call a skill | `$handoff` | `/handoff` |
| See all skills | `/skills` | `/skills` (or type `/`) |
| Start fresh, cheaply | new chat, then "read the handoff" | `/clear`, then "read the handoff" |

## Who's better at what (rough guide)
- **Codex:** fast building, running things, small fixes.
- **Claude:** planning a phase, reviewing, tricky bugs, explaining code.
- When one runs out of credits, switch (see below).

## When one runs out of credits
1. Claude stops and a Mac notification tells you. Nothing is lost: what it was doing is already in
   [[LIVE_SESSION]], which Claude updates by itself after every step.
2. Click the Codex icon in VS Code and type **take over**. Codex reads that note and the handoff,
   then finishes the job.
3. Claude carries on by itself when its limit resets, but only if no project files changed in between.
   If Codex worked meanwhile, Claude waits. Type **take over** in Claude when you want it back.

Codex ran out first? Type **take over** in Claude; it works the same way round.
Start both chats from this project folder (in VS Code: File → Open Workspace from File →
`DAW multitool.code-workspace`). A Claude chat started anywhere else doesn't read these rules or save its progress.

## Saving tokens
- Start a **new chat for each phase**. Long chats re-send the whole history every message.
- Say "read the handoff" instead of re-explaining the project.
- `lean-mode` is always on. Say "lean" if replies get wordy.
- Don't install skills you won't use. Each one adds a little to every session.
