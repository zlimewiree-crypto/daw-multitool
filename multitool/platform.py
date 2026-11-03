"""Small OS boundary shared by the tools. Native Mac audio/capture stay in their own modules."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import time

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
POINTER = "pointinghand" if IS_MAC else "hand2"
MODIFIER = "Command" if IS_MAC else "Control"
FILE_MANAGER = "Finder" if IS_MAC else "Explorer"


def user_data_dir():
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DAW Multitool"
    return Path.home() / "Library" / "Application Support" / "DAW Multitool"


def lock_file(handle, blocking=True):
    """Exclusive process lock; contention raises BlockingIOError when blocking=False.

    Windows locks byte zero, including in an empty file. Always seek to that same byte
    before unlocking. Retry ourselves: msvcrt's blocking mode gives up after ten tries.
    """
    if not IS_WINDOWS:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "File is locked by another process") from error
            time.sleep(0.05)


def unlock_file(handle):
    if IS_WINDOWS:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)


def open_path(path, app=""):
    """Open through a configured DAW, or the OS's file association. Never use a shell."""
    path = str(Path(path).expanduser().resolve())
    if IS_MAC:
        subprocess.run(["/usr/bin/open", *(["-a", app] if app else []), path],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=30)
    elif app and (Path(os.path.expandvars(app)).is_absolute() or app.lower().endswith(".exe")):
        executable = Path(os.path.expandvars(app))
        if not executable.is_file():
            raise FileNotFoundError(f"Configured DAW executable does not exist: {executable}")
        subprocess.Popen([str(executable), path], stdin=subprocess.DEVNULL)
    elif IS_WINDOWS:
        os.startfile(path)
    else:
        raise OSError("Opening files is supported on macOS and Windows.")


def reveal_path(path):
    path = str(Path(path).expanduser().resolve())
    if IS_MAC:
        subprocess.run(["/usr/bin/open", "-R", path], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    elif IS_WINDOWS:
        subprocess.Popen(["explorer.exe", "/select,", path], stdin=subprocess.DEVNULL)


def credential_backend():
    """Select an OS credential vault explicitly; never fall back to plain-text storage."""
    if IS_MAC:
        from keyring.backends.macOS import Keyring
    elif IS_WINDOWS:
        from multitool.windows_credentials import WindowsVault as Keyring
    else:
        raise OSError("Account storage is supported on macOS and Windows.")
    return Keyring()


def require_mac(feature):
    if not IS_MAC:
        raise OSError(f"{feature} is currently available on macOS only. Windows support is pending.")
