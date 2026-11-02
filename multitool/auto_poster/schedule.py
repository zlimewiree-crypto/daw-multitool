"""Post Scheduler: queued posts live in app data and go out when due, from the open app or from the
optional login helper (a macOS LaunchAgent the user turns on). Posting reuses Publisher, so a post that
already went out is never sent twice."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import uuid

from app_paths import DATA_DIR, FROZEN, RESOURCE_DIR, atomic_json
from .render import Cancelled
from .storage import NAMES, manifest

AGENT = "com.dawmultitool.scheduler"


def queue_file():
    return DATA_DIR / "schedule.json"


@contextmanager
def _lock(name, wait=True):
    """Yields False instead of waiting when `wait` is off and another process holds the lock."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / f".{name}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load():
    try:
        entries = json.loads(queue_file().read_text())
    except (OSError, ValueError):
        return []
    return entries if isinstance(entries, list) else []


def _update(change):
    """Short read-change-write of the queue; never held during an upload."""
    with _lock("schedule"):
        entries = load()
        change(entries)
        atomic_json(queue_file(), entries)
        return entries


def add(receipt, track, platforms, options, due, approved):
    """`approved`: {"texts": the title/captions as approved, "accounts": {platform: account ID}}; checked again before sending."""
    entry = {"id": uuid.uuid4().hex[:12], "receipt": str(receipt), "track": track, "platforms": list(platforms),
             "options": options, "due": due.astimezone().isoformat(timespec="minutes"), "state": "waiting", "detail": "",
             "approved": approved}
    _update(lambda entries: entries.append(entry))
    return entry


def cancel(ids):
    def change(entries):
        for entry in entries:
            if entry["id"] in ids and entry["state"] == "waiting":
                entry.update(state="cancelled", detail="Cancelled; nothing was posted.")
    _update(change)


def _mark(entry_id, **values):
    def change(entries):
        for entry in entries:
            if entry["id"] == entry_id:
                entry.update(values)
    _update(change)


def _ready(entry, now):
    """Waiting and due, or sent but still processing (finished on a later pass with the same upload)."""
    return entry["state"] == "processing" or entry["state"] == "waiting" and datetime.fromisoformat(entry["due"]) <= now


def due_now(now=None):
    now = now or datetime.now().astimezone()
    return [e for e in load() if _ready(e, now)]


def _claim(entry_id, now):
    """Marks the entry posting only if it is still ready under the queue lock, so a post cancelled
    (or claimed by another runner) since the list was read is never sent. Returns it, or None."""
    claimed = []
    def change(entries):
        for entry in entries:
            if entry["id"] == entry_id and _ready(entry, now):
                claimed.append({**entry})
                entry.update(state="posting", detail="")
    _update(change)
    return claimed[0] if claimed else None


def run_due(publisher, settings, cancelled=lambda: False, report=lambda text: None, now=None):
    """Post everything that is due. Returns the entries handled (empty if another runner is already posting)."""
    now = now or datetime.now().astimezone()
    with _lock("schedule-runner", wait=False) as mine:
        if not mine:
            return []
        def interrupted(entries):  # a runner stopped mid-post (Mac shut down, app quit)
            for entry in entries:
                if entry["state"] == "posting":
                    entry.update(state="check", detail="Interrupted while posting. Check the History tab before scheduling it again.")
        _update(interrupted)
        handled = []
        late = timedelta(hours=settings["schedule_max_late_hours"])
        for candidate in due_now(now):
            if cancelled():
                break
            entry = _claim(candidate["id"], now)
            if not entry:
                continue
            processing = entry["state"] == "processing"
            if now - datetime.fromisoformat(entry["posted_at"] if processing else entry["due"]) > late:
                hours = settings["schedule_max_late_hours"]
                result = ({"state": "check", "detail": f"Still processing {hours} h after it was sent. Check the History tab."}
                          if processing else
                          {"state": "missed", "detail": f"Not posted: the Mac was off or asleep for over {hours} h after its time."})
                _mark(entry["id"], **result)
                handled.append({**entry, **result})
                continue
            if not entry.get("approved"):
                result = {"state": "failed", "detail": "Scheduled by an older version; nothing was sent. Schedule it again."}
                _mark(entry["id"], **result)
                handled.append({**entry, **result})
                continue
            results = {}
            try:
                publisher.publish_batch([Path(entry["receipt"])], entry["platforms"], entry["options"], cancelled, report,
                                        completed=lambda receipt, platform, state: results.__setitem__(platform, state),
                                        approved=lambda receipt: entry["approved"])
            except Cancelled:
                pass
            except Exception as error:
                results.setdefault("error", {"state": "failed", "error": str(error)})
            result = outcome(results, entry["platforms"])
            sent = entry.get("posted_at") if processing else datetime.now().astimezone().isoformat(timespec="minutes")
            _mark(entry["id"], **result, posted_at=sent)  # a processing post keeps the time it was first sent
            handled.append({**entry, **result})
        return handled


def outcome(results, platforms):
    states = {p: results.get(p, {}).get("state", "not sent") for p in platforms}
    errors = [f"{NAMES[p]}: {r['error']}" for p, r in results.items() if r.get("error")]
    if all(s == "published" for s in states.values()):
        return {"state": "posted", "detail": "Posted to " + ", ".join(NAMES[p] for p in platforms) + "."}
    if not errors and all(s in ("published", "processing") for s in states.values()):
        return {"state": "processing", "detail": "Sent; still processing on " +
                ", ".join(NAMES[p] for p, s in states.items() if s == "processing") + ". The next check finishes it."}
    return {"state": "failed", "detail": " ".join(errors) or "Not sent: " + ", ".join(f"{NAMES[p]} {s}" for p, s in states.items())}


# ---- optional login helper (LaunchAgent): only installed when the user turns it on ----

def agent_file():
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT}.plist"


def agent_command():
    if not FROZEN:
        return [sys.executable, str(RESOURCE_DIR / "main.py"), "--run-schedule"]
    executable = Path(sys.executable)
    latest = executable.parents[4] / "DAW Multitool.app" / "Contents" / "MacOS" / executable.name  # dist/ link to newest build
    return [str(latest if latest.exists() else executable), "--run-schedule"]


def agent_installed():
    return agent_file().exists()


def _launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def install_agent(settings):
    """Checks the queue every few minutes while you're logged in, even with the app closed."""
    agent_file().parent.mkdir(parents=True, exist_ok=True)
    plist = {"Label": AGENT, "ProgramArguments": agent_command(), "RunAtLoad": True, "ProcessType": "Background",
             "StartInterval": int(settings["schedule_check_minutes"] * 60),
             "EnvironmentVariables": {"DAW_MULTITOOL_DATA_DIR": str(DATA_DIR)},  # same queue as this app
             "StandardOutPath": str(DATA_DIR / "scheduler.log"), "StandardErrorPath": str(DATA_DIR / "scheduler.log")}
    agent_file().write_bytes(plistlib.dumps(plist))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{AGENT}")
    result = _launchctl("bootstrap", domain, str(agent_file()))
    if result.returncode:
        raise OSError("macOS didn't start the scheduler helper: " + result.stderr.strip()[-200:])


def remove_agent():
    _launchctl("bootout", f"gui/{os.getuid()}/{AGENT}")
    agent_file().unlink(missing_ok=True)


def notify(text):
    subprocess.run(["/usr/bin/osascript", "-e", "on run argv", "-e",
                    'display notification (item 1 of argv) with title "DAW Multitool"', "-e", "end run", text],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=15)


def track_name(receipt):
    try:
        return Path(manifest(receipt)["audio"]).name
    except (OSError, ValueError, KeyError):
        return Path(receipt).parent.name


def run_from_helper():
    """`DAW Multitool --run-schedule`: what the login helper runs. Posts what's due, then finishes uploads
    that are still processing (an Instagram Reel isn't live until its publish step)."""
    from . import autopilot
    from .publish import NAMES, Publisher, pending
    from .results import receipts
    from .storage import load_settings
    settings = load_settings()
    made = []
    if settings.get("autopilot_enabled"):  # new beats get their videos made; they still wait for approval
        try:
            made = autopilot.run_once(settings, notify=_quiet_notify)
        except Exception as error:  # never let Autopilot stop scheduled posts from going out
            made = [{"state": "failed", "detail": str(error)}]
    known = lambda: receipts(settings, [e["receipt"] for e in load()])
    if not due_now() and not pending(known()):
        if made:
            print(json.dumps({"autopilot": [{k: e.get(k) for k in ("name", "state", "detail")} for e in made]}))
        return 0
    publisher = Publisher(settings)
    handled = run_due(publisher, settings)
    checked = publisher.resume(pending(known()))
    notes = [f"{entry['track']}: {entry['detail']}" for entry in handled]
    notes += [f"{track_name(r)} is now live on {NAMES[p]}." if state["state"] == "published" else
              f"{track_name(r)} on {NAMES[p]}: {state.get('error', 'failed')}"
              for r, p, state in checked if state["state"] in ("published", "failed")]
    for note in notes:
        try:
            notify(note)
        except (OSError, subprocess.SubprocessError):
            pass
    print(json.dumps({"scheduled": [{k: e[k] for k in ("id", "state", "detail")} for e in handled],
                      "finished": [[str(r), p, state["state"]] for r, p, state in checked],
                      "autopilot": [{k: e.get(k) for k in ("name", "state", "detail")} for e in made]}))
    return 0


def _quiet_notify(text):
    try:
        notify(text)
    except (OSError, subprocess.SubprocessError):
        pass
