"""Claude Code hook (wired up in .claude/settings.json). After every request, edit and reply it rewrites
notes/LIVE_SESSION.md, so Codex can take over even when Claude stops mid-task at a usage limit and can't
write a handoff. It also holds Claude's own automatic continue (at the limit reset) when project files
changed meanwhile, and tells Claude what changed once the user brings it back."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

# Standard library only: this hook has to keep working while an agent is halfway through editing the app.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from multitool.platform import lock_file  # standard library only; also works when the hook runs on Windows
NOTE = "notes/LIVE_SESSION.md"
STATE = ".claude/live_session.json"
WATCHED = ("multitool", "tests", "scripts", "notes", ".agents")  # where another agent's edits show up
TOP_LEVEL = {".py", ".md", ".spec", ".txt", ".sh"}  # root files; config.json and logs belong to the running app
AUTO_CONTINUE = "Continue the task you were working on when the limit was reached"  # Claude Code 2.1.314's resume prompt
SECRET = re.compile(r"GOCSPX-[\w-]+|\bsk-[\w-]{16,}|\bya29\.[\w.-]+|\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{32,}\b")
RESET = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*[ap]m)", re.I)
KEEP_PROMPTS, KEEP_COMMANDS, KEEP_FILES = 4, 6, 60
HEADLINE = {"working": "working on the last request", "finished": "finished the last request",
            "stopped": "stopped before finishing",
            "waiting": "waiting for you: its usage limit reset, but project files changed meanwhile, so it didn't continue"}


def stamp(when):
    return datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M")


def tidy(text, limit, lines=False):
    """Hides key-shaped strings and shortens; keeps line breaks only when asked."""
    text = SECRET.sub("[hidden]", text.strip() if lines else " ".join(text.split()))
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def changed_since(root, since):
    """Project files saved after Claude's last activity, newest first: another agent's (or the user's) work."""
    paths = [path for path in root.iterdir() if path.suffix in TOP_LEVEL]
    for folder in WATCHED:
        paths += (root / folder).rglob("*")
    found = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.name.startswith(".") or "__pycache__" in path.parts or relative == NOTE or not path.is_file():
            continue  # .DS_Store and editor swap files aren't anyone's work
        try:
            saved = path.stat().st_mtime
        except OSError:
            continue
        if saved > since + 1:
            found.append((saved, relative))
    return [relative for _, relative in sorted(found, reverse=True)]


def latest_words(transcript, error):
    """Claude's newest text in its transcript, i.e. what it was doing when it stopped.
    Best effort: the transcript layout is Claude Code's own and may change."""
    try:
        with open(transcript, "rb") as stream:
            stream.seek(max(0, os.path.getsize(transcript) - 300_000))
            lines = stream.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # the first line may be cut in half
        content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
        if (not isinstance(content, list) or entry.get("type") != "assistant" or entry.get("isSidechain")
                or entry.get("isApiErrorMessage")):
            continue
        text = "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
        if text.strip() and not text.startswith("API Error") and text.strip() != error:
            return text
    return ""


def notify(title, text):
    """A Mac notification banner (sent by osascript, so macOS files it under Script Editor)."""
    try:
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
                        "-e", "end run", title, text], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass  # no banner is fine: the note is what the next agent needs


def on_prompt(state, event, now, root):
    text, seen = event.get("prompt") or "", state.get("seen")
    changed = changed_since(root, seen) if seen else []
    if AUTO_CONTINUE in text:
        if changed:  # Codex (or the user) worked since Claude stopped: don't run over that unattended
            state["status"] = ["waiting", "", now]
            return json.dumps({"decision": "block", "suppressOriginalPrompt": True, "reason": (
                f"Claude's usage limit has reset, but Claude didn't continue by itself: {len(changed)} project "
                f"file(s) changed after it stopped at {stamp(seen)} (newest: {changed[0]}), so Codex has "
                "probably taken over. When you want Claude back, type: take over")})
        state["status"] = ["working", "continued by itself after its usage limit reset", now]
    else:
        state["prompts"] = (state.get("prompts", []) + [[now, tidy(text, 1500)]])[-KEEP_PROMPTS:]
        state["status"] = ["working", "", now]
    state["seen"] = now
    if not changed:
        return ""
    names = ", ".join(changed[:8]) + (f" and {len(changed) - 8} more" if len(changed) > 8 else "")
    return (f"Since Claude's last activity here ({stamp(seen)}), these project files changed outside this session, "
            f"probably because Codex took over: {names}. Before continuing, read the top entry of notes/HANDOFF.md "
            "and check those files; don't redo or undo that work without saying why.")


def on_tool(state, event, now, root):
    data = event.get("tool_input") or {}
    path = data.get("file_path") or data.get("notebook_path")
    if path and Path(path).resolve().is_relative_to(root):
        relative, files = Path(path).resolve().relative_to(root).as_posix(), state.setdefault("files", {})
        if relative == "notes/HANDOFF.md":
            files.clear()  # the handoff now describes those changes
        elif relative != NOTE:
            files.pop(relative, None)
            files[relative] = now  # newest last
            for oldest in list(files)[:-KEEP_FILES]:
                del files[oldest]
    if event.get("tool_name") == "Bash":
        what = data.get("description") or data.get("command") or ""
        state["commands"] = (state.get("commands", []) + [[now, tidy(what, 120)]])[-KEEP_COMMANDS:]
    state["seen"] = now


def on_stop(state, event, now, root):
    if event.get("last_assistant_message"):
        state["said"] = tidy(event["last_assistant_message"], 1500, lines=True)
    state["status"] = ["finished", "", now]
    state["seen"] = now


def on_failure(state, event, now, root):
    error = event.get("error") or "unknown"
    message = tidy(event.get("last_assistant_message") or event.get("error_details") or "", 200)
    words = latest_words(event.get("transcript_path"), message)
    if words:
        state["said"] = tidy(words, 1500, lines=True)
    limit = {"rate_limit": "hit its usage limit", "billing_error": "ran out of credits"}.get(error)
    reason = limit or f"API error: {error}"
    state["status"] = ["stopped", f"{reason} ({message})" if message else reason, now]
    state["seen"] = now
    if limit:
        reset = RESET.search(message)
        notify(f"Claude {limit}" + (f" (resets {reset.group(1)})" if reset else ""),
               "To keep going now, open Codex in VS Code and type: take over")


HANDLERS = {"UserPromptSubmit": on_prompt, "PostToolUse": on_tool, "Stop": on_stop, "StopFailure": on_failure}


def render(state):
    status, detail, at = state.get("status") or ["working", "", state.get("seen", time.time())]
    lines = ["# Live session log (Claude Code)",
             "Rewritten by `scripts/live_session.py` after every request, edit and reply, so another agent can take "
             "over even when Claude stops mid-task. Don't edit it. How to take over: AGENTS.md, "
             "\"When an agent runs out of credits\".", "",
             f"**Claude:** {HEADLINE.get(status, status)} ({stamp(at)})" + (f": {detail}" if detail else ""),
             f"**Last activity:** {stamp(state.get('seen', at))}", "", "## Last requests (newest last)"]
    lines += [f"- {stamp(when)} · {text}" for when, text in state.get("prompts", [])] or ["- none yet"]
    lines += ["", "## What Claude said last"]
    lines += ["> " + line for line in (state.get("said") or "nothing yet").splitlines()]
    files = list(state.get("files", {}).items())
    lines += ["", "## Files Claude changed since its last handoff (newest last)"]
    if len(files) > 25:
        lines.append(f"- … and {len(files) - 25} earlier ones")
    lines += [f"- {path} · {stamp(when)}" for path, when in files[-25:]] or ["- none"]
    lines += ["", "## What Claude ran last"]
    lines += [f"- {stamp(when)} · {what}" for when, what in state.get("commands", [])] or ["- nothing yet"]
    return "\n".join(lines) + "\n"


def replace(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)  # readers (Codex, Obsidian) never see half a file


def run(event, root=ROOT, now=None):
    """Handles one hook event and returns what the hook prints (context for Claude, or a block decision)."""
    handler = HANDLERS.get(event.get("hook_event_name"))
    if handler is None:
        return ""
    root = Path(root).resolve()
    state_path = root / STATE
    state_path.parent.mkdir(exist_ok=True)
    with open(state_path.with_suffix(".lock"), "w") as lock:
        lock_file(lock)  # Claude can run several tools at once
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        output = handler(state, event, time.time() if now is None else now, root)
        replace(state_path, json.dumps(state, indent=1))
        replace(root / NOTE, render(state))
    return output or ""


if __name__ == "__main__":
    print(run(json.load(sys.stdin)), end="")
