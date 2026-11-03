# Windows build; run scripts/build_windows.py. The Mac build uses "DAW Multitool.spec" instead.
import sys
from pathlib import Path

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from multitool.tools import TOOLS

analysis = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],  # imageio-ffmpeg's hook collects FFmpeg; sounddevice's hook collects PortAudio
    datas=[(str(root / "config.example.json"), "."), (str(root / "config.windows.json"), "."),
           (str(root / "drum_model.json"), "."), (str(root / "assets" / "AppIcon-square.png"), "assets")],
    hiddenimports=[t.module for t in TOOLS] + ["multitool.settings", "multitool.windows_credentials",
                                              "keyring.backends.Windows"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
    module_collection_mode={"librosa": "py"},  # Numba needs real source paths for cached functions
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [("X utf8", None, "OPTION")],  # the app reads its text files as UTF-8 (notes/WINDOWS.md)
    exclude_binaries=True, name="DAW Multitool", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, icon=str(root / "build" / "AppIcon.ico"),
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="DAW Multitool")
