# Build with scripts/build_app.py; tool modules come from the same registry as the home screen.
import sys
from pathlib import Path

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from multitool.tools import TOOLS
from multitool import __version__

data = [(str(root / "config.example.json"), "."), (str(root / "drum_model.json"), "."),
        (str(root / "assets" / "AppIcon-square.png"), "assets"),
        # the audio device, copied into /Library/Audio/Plug-Ins/HAL only when the user clicks Install in Settings
        (str(root / "build" / "native" / "DAWMultitoolAudio.driver.zip"), "native")]
analysis = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    # imageio-ffmpeg's hook already collects its executable; the sound classifier, audio bridge and audio device
    # are built by build_app.py
    binaries=[(str(root / "build" / "native" / "sound-classifier"), "native"),
              (str(root / "build" / "native" / "audio-bridge"), "native")],
    datas=data,
    hiddenimports=[t.module for t in TOOLS] + ["multitool.settings", "keyring.backends.macOS"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    module_collection_mode={"librosa": "py"},  # Numba needs real source paths for cached functions
)
# sounddevice ships PortAudio for every system; a Mac app needs only libportaudio.dylib
analysis.datas = [entry for entry in analysis.datas if not entry[0].lower().endswith(".dll")]
analysis.binaries = [entry for entry in analysis.binaries if not entry[0].lower().endswith(".dll")]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name="DAW Multitool",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="DAW Multitool")
app = BUNDLE(
    collection, name="DAW Multitool.app", bundle_identifier="com.dawmultitool.app",
    icon=str(root / "assets" / "AppIcon.icns"),
    info_plist={"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                "NSHighResolutionCapable": True,
                "NSAppleEventsUsageDescription": "Auto Poster presses play in your DAW while it records a project video.",
                "NSMicrophoneUsageDescription": "DAW Multitool hears your DAW through its own audio device, "
                                                "DAW Multitool, to pass it on to your speakers. It never listens to "
                                                "your microphone.",
                "NSDesktopFolderUsageDescription": "DAW Multitool finds beats and DAW projects saved on your Desktop.",
                "NSDocumentsFolderUsageDescription": "DAW Multitool finds your FL Studio and Pro Tools projects in Documents.",
                "NSDownloadsFolderUsageDescription": "Auto Poster picks up the YouTube key file you download during setup, "
                                                     "and Project Library finds your exported beats."},
)
