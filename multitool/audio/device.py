"""DAW Multitool's own audio device, called "DAW Multitool": a Core Audio plug-in your DAW can play into, so DAW
Multitool sits between the DAW and your speakers or interface. Varispeed's "Your DAW" mode changes the DAW's speed
through it, and later tools can use it too.

Installing copies DAWMultitoolAudio.driver into /Library/Audio/Plug-Ins/HAL (the `plugin_folder` setting) and
restarts macOS's audio for a few seconds; macOS asks for your password. Removing deletes only that bundle.
Nothing is installed until you click Install in Settings. From Terminal it's a dry run unless you add --run:
    python -m multitool.audio.device install|remove|status [--run]
"""
import argparse
from dataclasses import dataclass
from pathlib import Path
import plistlib
import shlex
import subprocess
import zipfile

from app_paths import FROZEN, RESOURCE_DIR, load_section
from multitool import platform
from . import build

DEVICE_UID = "com.dawmultitool.audio.device"
DRIVER_NAME = build.DRIVER_NAME
BUNDLED = RESOURCE_DIR / "native" / f"{DRIVER_NAME}.zip"  # zipped in the app (scripts/build_app.py)
# coreaudiod restarts by itself; on recent macOS killall is what works, older versions also take kickstart
RESTART_AUDIO = "(killall coreaudiod 2>/dev/null || launchctl kickstart -kp system/com.apple.audio.coreaudiod)"


def settings():
    return load_section("audio")


def bundled_driver(build_it=True):
    """The device the app ships with (zipped), or (source runs) one built from native/. None when neither is possible."""
    if not platform.IS_MAC:
        return None
    if BUNDLED.is_file():
        return BUNDLED
    if FROZEN or not build_it:
        return None
    try:
        return build.build_driver()
    except (OSError, subprocess.SubprocessError):
        return None


def installed_path(options=None):
    return Path((options or settings())["plugin_folder"]) / DRIVER_NAME


def version(bundle):
    """The build number (CFBundleVersion) of a device bundle or its zip, or None."""
    try:
        if Path(bundle).suffix == ".zip":
            with zipfile.ZipFile(bundle) as archive:
                info = plistlib.loads(archive.read(f"{DRIVER_NAME}/Contents/Info.plist"))
        else:
            with open(Path(bundle) / "Contents" / "Info.plist", "rb") as handle:
                info = plistlib.load(handle)
        return int(info.get("CFBundleVersion", 0))
    except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile, plistlib.InvalidFileException):
        return None


@dataclass
class Status:
    installed: bool
    loaded: bool  # macOS's audio has it: apps can choose the device
    installed_version: int | None
    bundled_version: int | None

    @property
    def update(self):
        return self.installed and bool(self.bundled_version) and (self.installed_version or 0) < self.bundled_version

    def summary(self):
        if not self.installed:
            return "Not installed"
        if self.update:
            return "Installed · a newer version is ready"
        if not self.loaded:
            return "Installed · macOS's audio hasn't loaded it yet"
        return "Installed and ready"


def status(device_uids=(), options=None, bundle=None):
    """What's installed: `device_uids` are the Mac's audio devices right now (bridge.list_devices)."""
    path = installed_path(options)
    bundle = bundle if bundle is not None else bundled_driver(build_it=False)
    return Status(path.is_dir(), DEVICE_UID in device_uids, version(path), version(bundle) if bundle else None)


def install_commands(source, options=None):
    """The shell commands that install or update the device from its bundle or zip, run as administrator."""
    path = installed_path(options)
    target, folder, source = shlex.quote(str(path)), shlex.quote(str(path.parent)), shlex.quote(str(source))
    copy = f"/usr/bin/ditto -x -k {source} {folder}" if source.rstrip("'").endswith(".zip") else f"/usr/bin/ditto {source} {target}"
    return [f"mkdir -p {folder}", f"rm -rf {target}", copy, f"chown -R root:wheel {target}", f"chmod -R go-w {target}",
            RESTART_AUDIO]


def remove_commands(options=None):
    return [f"rm -rf {shlex.quote(str(installed_path(options)))}", RESTART_AUDIO]


def _escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def as_administrator(commands, prompt, runner=subprocess.run):
    """Runs shell commands as administrator behind macOS's own password prompt. Returns (ok, message)."""
    script = f'do shell script "{_escape(" && ".join(commands))}" with administrator privileges with prompt "{_escape(prompt)}"'
    try:
        result = runner(["/usr/bin/osascript", "-e", script], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if result.returncode == 0:
        return True, ""
    message = (result.stderr or "").strip()
    return False, "Cancelled." if "-128" in message else (message or "macOS didn't allow it.")


def install(run=False, runner=subprocess.run, options=None):
    """Installs or updates the device. A dry run unless `run`: (True, the commands it would run)."""
    platform.require_mac("The DAW Multitool audio device")
    source = bundled_driver()
    if source is None:
        return False, ("This copy of DAW Multitool doesn't include its audio device. From source, install Apple's "
                       "command line tools first (xcode-select --install).")
    commands = install_commands(source, options)
    if not run:
        return True, "\n".join(commands)
    return as_administrator(commands, 'DAW Multitool wants to add its audio device, "DAW Multitool".', runner)


def remove(run=False, runner=subprocess.run, options=None):
    """Removes the device. A dry run unless `run`."""
    platform.require_mac("The DAW Multitool audio device")
    commands = remove_commands(options)
    if not run:
        return True, "\n".join(commands)
    return as_administrator(commands, 'DAW Multitool wants to remove its audio device, "DAW Multitool".', runner)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install or remove DAW Multitool's audio device (dry run unless --run).")
    parser.add_argument("action", choices=("install", "remove", "status"))
    parser.add_argument("--run", action="store_true", help="really do it (asks for your Mac password)")
    args = parser.parse_args(argv)
    if args.action == "status":
        from .bridge import list_devices
        print(status([d["uid"] for d in list_devices()["devices"]]).summary())
        return 0
    ok, message = (install if args.action == "install" else remove)(run=args.run)
    print(message if args.run else "DRY RUN: would run as administrator:\n" + message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
