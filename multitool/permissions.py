"""Before the home screen: asks macOS once for every permission DAW Multitool uses, so no prompt pops up
mid-task (say, in the middle of recording a project), then shows whatever is still not allowed and how to fix it.
macOS shows its own prompt only the first time; after that, only System Settings can change the answer."""
import ctypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app_paths import DATA_DIR, update_config
from multitool.theme import apply_theme, banner

SETTINGS_PAGE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
FRAMEWORKS = "/System/Library/Frameworks/"


@dataclass
class Permission:
    name: str
    why: str
    allowed: Callable[[], bool]  # asks macOS (its prompt shows if it never asked before) and says if it's allowed
    page: str  # the Privacy & Security page that changes it
    reopen: bool = False  # only takes effect after reopening the app


def folder(name):
    def allowed():
        try:
            os.listdir(Path.home() / name)  # the first look is what makes macOS ask
        except PermissionError:
            return False
        except OSError:
            pass  # no such folder: nothing to allow
        return True
    return allowed


def accessibility():
    """Asks with macOS's own Accessibility prompt, which also puts DAW Multitool in the list to switch on."""
    services = ctypes.CDLL(FRAMEWORKS + "ApplicationServices.framework/ApplicationServices")
    foundation = ctypes.CDLL(FRAMEWORKS + "CoreFoundation.framework/CoreFoundation")
    foundation.CFDictionaryCreate.restype = ctypes.c_void_p
    foundation.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    services.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    services.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    keys = (ctypes.c_void_p * 1)(ctypes.c_void_p.in_dll(services, "kAXTrustedCheckOptionPrompt").value)
    values = (ctypes.c_void_p * 1)(ctypes.c_void_p.in_dll(foundation, "kCFBooleanTrue").value)
    options = foundation.CFDictionaryCreate(
        None, keys, values, 1, ctypes.addressof(ctypes.c_void_p.in_dll(foundation, "kCFTypeDictionaryKeyCallBacks")),
        ctypes.addressof(ctypes.c_void_p.in_dll(foundation, "kCFTypeDictionaryValueCallBacks")))
    try:
        return services.AXIsProcessTrustedWithOptions(options)
    finally:
        foundation.CFRelease(options)


def screen_recording():
    graphics = ctypes.CDLL(FRAMEWORKS + "CoreGraphics.framework/CoreGraphics")
    graphics.CGPreflightScreenCaptureAccess.restype = graphics.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    return graphics.CGPreflightScreenCaptureAccess() or graphics.CGRequestScreenCaptureAccess()


PERMISSIONS = [
    Permission("Downloads folder", "finds your exported beats and the YouTube key file", folder("Downloads"), "FilesAndFolders"),
    Permission("Desktop folder", "finds beats and projects saved on your Desktop", folder("Desktop"), "FilesAndFolders"),
    Permission("Documents folder", "finds your FL Studio and Pro Tools projects", folder("Documents"), "FilesAndFolders"),
    Permission("Accessibility", "presses play in your DAW while it records", accessibility, "Accessibility"),
    Permission("Screen Recording", "records only your DAW window for project videos", screen_recording, "ScreenCapture",
               reopen=True),
]


def wanted(config=None):
    """False once the user ticked "Don't show this at start"."""
    try:
        config = config if config is not None else json.loads((DATA_DIR / "config.json").read_text())
    except (OSError, ValueError):
        return True
    return config.get("app", {}).get("permissions_at_start", True) is not False


def missing(permissions=PERMISSIONS):
    """Asks for each permission in turn (macOS prompts the first time) and returns those not allowed."""
    found = []
    for permission in permissions:
        try:
            if not permission.allowed():
                found.append(permission)
        except Exception:  # a macOS API that changed shouldn't stop the app from starting
            found.append(permission)
    return found


def check_at_start(root, permissions=PERMISSIONS, opener=None):
    """Runs before the home screen. Returns "reopen" if the user chose to reopen the app now."""
    if not wanted():
        return ""
    still = missing(permissions)
    if not still:
        return ""
    return PermissionWindow(root, permissions, still, opener).wait()


class PermissionWindow:
    def __init__(self, root, permissions, still, opener=None):
        apply_theme(root)  # shown before the home screen, which would otherwise theme it
        self.root, self.permissions, self.choice = root, permissions, ""
        self.opener = opener or (lambda url: subprocess.Popen(["/usr/bin/open", url]))
        self.never = tk.BooleanVar(value=False)
        window = self.window = tk.Toplevel(root)
        window.title("DAW Multitool · Permissions")
        window.protocol("WM_DELETE_WINDOW", self.start)
        banner(window, "A few permissions are still off", "Switch each one on, then click Check again.",
               eyebrow="BEFORE YOU START", kind="shield")
        frame = ttk.Frame(window, padding=(26, 14, 26, 22))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Everything else works without them.", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.rows = ttk.Frame(frame)
        self.rows.pack(fill="x")
        buttons = ttk.Frame(frame)
        ttk.Button(buttons, text="Start DAW Multitool", style="Primary.TButton", command=self.start).pack(side="right")
        ttk.Button(buttons, text="Check again", command=lambda: self.show(missing(self.permissions))).pack(side="right", padx=8)
        self.reopen_button = ttk.Button(buttons, text="Reopen the app", command=self.reopen)
        self.show(still)
        ttk.Checkbutton(frame, text="Don't show this at start", variable=self.never).pack(anchor="w", pady=(12, 0))
        buttons.pack(fill="x", pady=(12, 0))

    def show(self, still):
        for child in self.rows.winfo_children():
            child.destroy()
        self.still = still
        self.rows.columnconfigure(0, weight=1)
        for line, permission in enumerate(still):
            ttk.Label(self.rows, text=permission.name, style="Strong.TLabel").grid(row=2 * line, column=0, sticky="w", pady=(10, 0))
            note = permission.why[0].upper() + permission.why[1:] + (". Reopen the app after switching it on." if permission.reopen else ".")
            ttk.Label(self.rows, text=note, wraplength=380, style="Muted.TLabel").grid(row=2 * line + 1, column=0, sticky="w")
            ttk.Button(self.rows, text="Open Settings ↗", style="Quiet.TButton",
                       command=lambda page=permission.page: self.opener(SETTINGS_PAGE + page)).grid(
                           row=2 * line, column=1, rowspan=2, sticky="e")
        if not still:
            ttk.Label(self.rows, text="✓ Everything is allowed.", style="Strong.TLabel").grid(row=0, column=0, sticky="w")
        if any(permission.reopen for permission in still) and getattr(sys, "frozen", False):
            self.reopen_button.pack(side="left")  # only the built app can reopen itself
        else:
            self.reopen_button.pack_forget()

    def remember(self):
        if self.never.get():
            update_config("app", {"permissions_at_start": False})

    def start(self):
        self.remember()
        self.window.destroy()

    def reopen(self):
        self.remember()
        self.choice = "reopen"
        self.window.destroy()

    def wait(self):
        self.root.wait_window(self.window)
        return self.choice


def reopen_app():
    """Screen Recording only counts after a restart: start a fresh copy once this one has quit."""
    if getattr(sys, "frozen", False):
        app = Path(sys.executable).resolve().parents[2]  # …/DAW Multitool.app
        subprocess.Popen(["/bin/sh", "-c", f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done; open "$0"', str(app)])
