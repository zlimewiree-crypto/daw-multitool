"""Build a local macOS app. Default is a dry-run; --run creates a new, versioned build."""
import argparse
from datetime import datetime
import importlib.util
import fcntl
import os
import re
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
# Signing every build with the same local certificate keeps the app's identity stable, so macOS remembers
# Screen Recording / Accessibility permissions across rebuilds (ad-hoc signatures change with every build).
SIGNING = Path.home() / "Library" / "Application Support" / "DAW Multitool" / "signing"
CERTIFICATE = """[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=DAW Multitool Local Signing
[ext]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
"""


def run(*command):
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def signing_keychain():
    """(keychain, password): a self-signed code-signing certificate in its own keychain, created once.
    The login keychain is never changed."""
    keychain, password_file = SIGNING / "signing.keychain-db", SIGNING / "keychain-password"
    if keychain.exists():
        return keychain, password_file.read_text()
    SIGNING.mkdir(parents=True, exist_ok=True, mode=0o700)
    password = secrets.token_hex(16)
    password_file.touch(mode=0o600)
    password_file.write_text(password)
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        (folder / "cert.cnf").write_text(CERTIFICATE)
        # macOS's own openssl: Homebrew's OpenSSL 3 writes .p12 files that `security import` rejects
        run("/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
            "-keyout", str(folder / "key.pem"), "-out", str(folder / "cert.pem"), "-config", str(folder / "cert.cnf"))
        run("/usr/bin/openssl", "pkcs12", "-export", "-inkey", str(folder / "key.pem"), "-in", str(folder / "cert.pem"),
            "-out", str(folder / "id.p12"), "-passout", "pass:" + password)
        run("security", "create-keychain", "-p", password, str(keychain))
        run("security", "set-keychain-settings", str(keychain))  # no auto-lock timer
        run("security", "unlock-keychain", "-p", password, str(keychain))
        run("security", "import", str(folder / "id.p12"), "-k", str(keychain), "-P", password, "-T", "/usr/bin/codesign")
        run("security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password, str(keychain))
    print(f"Created local signing certificate: {keychain}")
    return keychain, password


def sign(app):
    """Re-sign the bundle with the local certificate. codesign only finds the key while its keychain is in the
    search list, so it is added for this one command and the original list is restored."""
    keychain, password = signing_keychain()
    run("security", "unlock-keychain", "-p", password, str(keychain))
    identity = re.search(r"\b[0-9A-F]{40}\b", run("security", "find-identity", "-p", "codesigning", str(keychain)))[0]
    original = [line.strip().strip('"') for line in run("security", "list-keychains", "-d", "user").splitlines() if line.strip()]
    run("security", "list-keychains", "-d", "user", "-s", *original, str(keychain))
    try:
        run("codesign", "--force", "--deep", "--sign", identity, "--keychain", str(keychain), str(app))
    finally:
        run("security", "list-keychains", "-d", "user", "-s", *original)
    run("codesign", "--verify", "--deep", "--strict", str(app))


def build_native():
    """Compiles the native helpers for the bundle when their source changed: macOS's sound classifier
    (native/sound_classifier.swift), the audio bridge and the audio device (multitool/audio/build.py).
    Needs Apple's command line tools (xcode-select --install)."""
    sys.path.insert(0, str(ROOT))
    from multitool.audio import build
    source, target = ROOT / "native" / "sound_classifier.swift", ROOT / "build" / "native" / "sound-classifier"
    if not (target.exists() and target.stat().st_mtime >= source.stat().st_mtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        run("/usr/bin/xcrun", "swiftc", "-O", str(source), "-o", str(target))
    return [target, build.build_bridge(), build.pack_driver()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="build the application")
    mode.add_argument("--dry-run", action="store_true", help="print the build command (the default)")
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = ROOT / "dist" / stamp
    command = [sys.executable, "-m", "PyInstaller", "--distpath", str(destination),
               "--workpath", str(ROOT / "build" / "app"), str(ROOT / "DAW Multitool.spec")]
    print(("BUILD" if args.run else "DRY RUN") + ": " + shlex.join(command), flush=True)
    print(("SIGN" if args.run else "DRY RUN") + f": local certificate in {SIGNING} (created on first build)", flush=True)
    if not args.run:
        return 0
    if sys.platform != "darwin":
        parser.error("Build the macOS application on a Mac.")
    if importlib.util.find_spec("PyInstaller") is None:
        parser.error("Install build tools first: .venv/bin/python -m pip install -r requirements-build.txt")
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / "build" / "cache"))
    (ROOT / "build").mkdir(exist_ok=True)
    try:
        for helper in build_native():
            print(f"Native helper: {helper}", flush=True)
    except (subprocess.CalledProcessError, OSError) as error:
        detail = getattr(error, "stderr", "") or error
        parser.error(f"Couldn't build the native helpers; install Apple's command line tools (xcode-select --install). {detail}")
    with (ROOT / "build" / ".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(command, cwd=ROOT, env=environment)
    if result.returncode:
        return result.returncode
    app = destination / "DAW Multitool.app"
    try:
        sign(app)
    except (subprocess.CalledProcessError, TypeError, OSError) as error:
        detail = getattr(error, "stderr", "") or error
        print(f"WARNING: kept the default signature; macOS may ask for permissions again after this build. {detail}")
    latest = ROOT / "dist" / "DAW Multitool.app"
    if not latest.exists() or latest.is_symlink():
        alias = latest.with_name(f".DAW Multitool-{stamp}.app")
        alias.symlink_to(app.relative_to(latest.parent), target_is_directory=True)
        os.replace(alias, latest)  # update the shortcut; preserve every versioned build
        print(f"Open: {latest}")
    print(f"Built: {app}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
