"""Varispeed adapter for the shared DAW Multitool home screen."""


def open_tool(window):
    from multitool.varispeed.ui import VarispeedApp
    return VarispeedApp(window)


def self_check():
    """Checks the resampler, the file writer and the playback library are packaged, without playing anything."""
    import numpy as np
    import sounddevice  # noqa: F401 (PortAudio, bundled with the app)
    import soundfile
    from multitool.varispeed import core
    from multitool.varispeed.player import TapePlayer
    from multitool.varispeed.ui import VarispeedApp  # noqa: F401
    tone = np.zeros((4800, 2), dtype=np.float32)
    if len(core.varispeed(tone, 48000, 2.0)) != 2400:
        raise RuntimeError("Varispeed resampling gave an unexpected length")
    player = TapePlayer(tone, 48000, stream=lambda rate, callback: None)
    player.playing = True
    if player.render(256).shape != (256, 2):
        raise RuntimeError("Varispeed playback gave an unexpected block")
    if "WAV" not in soundfile.available_formats():
        raise RuntimeError("The WAV writer is unavailable")
