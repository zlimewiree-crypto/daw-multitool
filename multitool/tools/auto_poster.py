"""Auto Poster adapter for the shared DAW Multitool home screen."""


def open_tool(window):
    from multitool.auto_poster.ui import AutoPosterApp
    return AutoPosterApp(window)


def self_check():
    import subprocess
    from multitool.auto_poster.render import ffmpeg_binary
    from multitool.auto_poster.storage import load_settings
    from multitool.auto_poster.accounts import CredentialStore
    from multitool.auto_poster.ui import AutoPosterApp
    result = subprocess.run([ffmpeg_binary(load_settings()), "-version"], capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError("Auto Poster's FFmpeg binary could not start")
