"""Builds the audio device (native/audio_device/) and the audio bridge (native/audio_bridge.m) with Apple's command
line tools (xcode-select --install). The app ships both (scripts/build_app.py); source runs build them here on
first use. Everything goes into build/native/ and is only ever our own build output."""
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent.parent
NATIVE = ROOT / "native"
BUILD = ROOT / "build" / "native"
DRIVER_NAME = "DAWMultitoolAudio.driver"
DRIVER_SOURCES = (NATIVE / "audio_device" / "DAWMultitoolAudio.c", NATIVE / "audio_device" / "Info.plist")
BRIDGE_SOURCE = NATIVE / "audio_bridge.m"
MINIMUM_MACOS = "-mmacosx-version-min=12.0"


def run(*command):
    subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600, check=True)


def frameworks(*names):
    return [part for name in names for part in ("-framework", name)]


def stale(target, sources):
    return not target.exists() or any(source.stat().st_mtime > target.stat().st_mtime for source in sources)


def build_driver(folder=BUILD):
    """DAWMultitoolAudio.driver, rebuilt when its source changed. Signed ad hoc, which macOS's audio server
    accepts for plug-ins in /Library/Audio/Plug-Ins/HAL."""
    bundle = Path(folder) / DRIVER_NAME
    if not stale(bundle / "Contents" / "MacOS" / "DAWMultitoolAudio", DRIVER_SOURCES):
        return bundle
    temporary = bundle.with_name(f".{DRIVER_NAME}-{os.getpid()}")
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        (temporary / "Contents" / "MacOS").mkdir(parents=True)
        shutil.copy2(DRIVER_SOURCES[1], temporary / "Contents" / "Info.plist")
        run("/usr/bin/xcrun", "clang", "-bundle", "-Os", "-std=gnu11", MINIMUM_MACOS, "-arch", platform.machine(),
            str(DRIVER_SOURCES[0]), *frameworks("CoreAudio", "CoreFoundation"),
            "-o", str(temporary / "Contents" / "MacOS" / "DAWMultitoolAudio"))
        run("/usr/bin/codesign", "--force", "--sign", "-", str(temporary))
        shutil.rmtree(bundle, ignore_errors=True)  # the previous build of this same bundle
        temporary.rename(bundle)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return bundle


def pack_driver(folder=BUILD):
    """The device zipped for the app bundle (a bundle inside the app would be re-signed as part of it); Install
    unpacks it with ditto, which keeps the device's own signature."""
    bundle = build_driver(folder)
    archive = Path(folder) / f"{DRIVER_NAME}.zip"
    if not stale(archive, [bundle / "Contents" / "MacOS" / "DAWMultitoolAudio", bundle / "Contents" / "Info.plist"]):
        return archive
    temporary = archive.with_name(f".{archive.name}-{os.getpid()}")
    try:
        run("/usr/bin/ditto", "-c", "-k", "--keepParent", str(bundle), str(temporary))
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def build_bridge(folder=BUILD):
    """The audio-bridge helper, rebuilt when its source changed."""
    target = Path(folder) / "audio-bridge"
    if not stale(target, [BRIDGE_SOURCE]):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".audio-bridge-{os.getpid()}")
    try:
        run("/usr/bin/xcrun", "clang", "-O2", "-fobjc-arc", MINIMUM_MACOS, str(BRIDGE_SOURCE),
            *frameworks("CoreAudio", "CoreFoundation", "AVFoundation", "Foundation"), "-o", str(temporary))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
