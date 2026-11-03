"""Builds the Windows app folder: dist/windows/DAW Multitool/DAW Multitool.exe. Dry run by default (--run builds)."""
import argparse
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def make_icon():
    """PyInstaller wants an .ico; make one from the shared PNG icon (Pillow)."""
    from PIL import Image
    (ROOT / "build").mkdir(exist_ok=True)
    icon = ROOT / "build" / "AppIcon.ico"
    Image.open(ROOT / "assets" / "AppIcon-square.png").save(icon, sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    return icon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="build the app (default: print the command)")
    args = parser.parse_args()
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(ROOT / "dist" / "windows"),
               "--workpath", str(ROOT / "build" / "windows"), str(ROOT / "DAW Multitool Windows.spec")]
    print(("BUILD" if args.run else "DRY RUN") + ": " + " ".join(command))
    if not args.run:
        return 0
    if sys.platform != "win32":
        parser.error("Build the Windows app on Windows.")
    print(f"Icon: {make_icon()}")
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / "build" / "cache"))
    result = subprocess.run(command, cwd=ROOT, env=environment)
    if not result.returncode:
        print(f"Built: {ROOT / 'dist' / 'windows' / 'DAW Multitool' / 'DAW Multitool.exe'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
