"""Runs the audio bridge (native/audio_bridge.m) for the whole app: it hears what your DAW plays into the DAW
Multitool device and passes it on to your speakers or interface, at Varispeed's speed, and can record what you
hear. There is one bridge per app (shared()); it stops when the app quits, and the DAW goes back to 100%.

The helper reads commands on stdin and writes JSON lines: "status" about 4 times a second, "recorded" when a
take is finished, "error"; here a "stopped" event also says the helper ended by itself."""
import json
import os
import subprocess
import threading

from app_paths import FROZEN, RESOURCE_DIR, load_section, update_config
from . import build, device

BUNDLED = RESOURCE_DIR / "native" / "audio-bridge"


def helper_path(build_it=True):
    """The bridge the app ships with, or (source runs) one built from native/. None when neither is possible."""
    if BUNDLED.is_file() and os.access(BUNDLED, os.X_OK):
        return BUNDLED
    if FROZEN:
        return None
    built = build.BUILD / "audio-bridge"
    if build_it:
        try:
            return build.build_bridge()
        except (OSError, subprocess.SubprocessError):
            pass
    return built if built.is_file() else None


def list_devices(helper=None, runner=subprocess.run):
    """The Mac's sound devices: {"default_output": uid, "devices": [{"uid", "name", "inputs", "outputs", "rate"}]}.
    Only reads; empty when it can't tell."""
    empty = {"default_output": "", "devices": []}
    helper = helper or helper_path()
    if helper is None:
        return empty
    try:
        result = runner([str(helper), "--list"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        found = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return empty
    return found if isinstance(found, dict) and isinstance(found.get("devices"), list) else empty


def output_pairs(channels):
    """'Outputs 1–2', 'Outputs 3–4', … for a device with this many outputs."""
    return [f"Outputs {left + 1}–{left + 2}" for left in range(0, max(channels - 1, 1), 2)]


def outputs(found):
    """Devices you can listen on: anything with outputs except the DAW Multitool device itself."""
    return [d for d in found["devices"] if d.get("outputs", 0) > 0 and d.get("uid") != device.DEVICE_UID]


class Bridge:
    def __init__(self, helper=None, popen=subprocess.Popen):
        """`popen` starts the helper (tests pass a stand-in, so no audio device is ever opened)."""
        self.helper, self.popen = helper, popen
        self.process = None
        self.status = {}  # the helper's latest status line
        self.listeners = []  # called on the bridge's reader thread with every event: hand them to your own queue
        self.speed, self.output = 1.0, ("", 0)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, output_uid="", left=0):
        """Starts passing audio through to `output_uid` (empty: the Mac's default output), outputs left+1/left+2."""
        if self.running:
            self.set_output(output_uid, left)
            return
        helper = self.helper or helper_path()
        if helper is None:
            raise OSError("This copy of DAW Multitool doesn't include its audio bridge.")
        self.status = {}
        self.process = self.popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
        self.set_output(output_uid, left)
        self.set_speed(self.speed)

    def _send(self, line):
        process = self.process
        if process is None or process.poll() is not None:
            return False
        try:
            process.stdin.write(line + "\n")
            process.stdin.flush()
            return True
        except (OSError, ValueError):  # it ended meanwhile
            return False

    def set_output(self, uid, left=0):
        self.output = (uid, left)
        self._send(f"output {int(left)} {uid.splitlines()[0] if uid else ''}".rstrip())

    def set_speed(self, speed):
        """The DAW's speed: the device's clock glides there like a tape motor."""
        self.speed = float(speed)
        self._send(f"speed {self.speed:.6f}")

    def record(self, path):
        """Records what you hear into a new WAV at `path` (the helper never replaces a file). False if not running."""
        if "\n" in str(path):
            raise ValueError("A recording's name can't have a line break in it.")
        return self._send(f"record {path}")

    def stop_record(self):
        self._send("stop-record")

    def listen(self, callback):
        self.listeners.append(callback)

    def unlisten(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def _tell(self, event):
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # a window that closed mid-event
                pass

    def _read(self, process):
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "status":
                self.status = event
            self._tell(event)
        if process is self.process:  # it ended without being asked
            self.status = {}
            self._tell({"type": "stopped"})

    def stop(self, timeout=5):
        """Stops passing audio through: finishes any recording and puts the DAW back to 100%."""
        process, self.process = self.process, None
        self.status = {}
        if process is None:
            return
        try:
            process.stdin.write("quit\n")
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


_shared = None


def shared():
    """The app's one bridge."""
    global _shared
    if _shared is None:
        _shared = Bridge()
    return _shared


def stop_shared():
    """When the app quits."""
    if _shared is not None:
        _shared.stop()


def settings():
    return load_section("audio")


def remember(**values):
    update_config("audio", values)


def resume(bridge=None, options=None):
    """At app start: pass-through comes back on if it was on when the app closed and the device is installed."""
    options = options or settings()
    if not options["pass_through"] or not device.installed_path(options).is_dir():
        return False
    try:
        (bridge or shared()).start(options["output_device"], options["output_left_channel"])
    except OSError:
        return False
    return True
