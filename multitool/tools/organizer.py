"""Connect the existing organizer to the Multitool window manager."""


def open_tool(window):
    from bounce_sorter_app import App  # audio libraries load only when this tool opens

    return App(window)


def self_check():
    """Check the organizer's packaged dependencies and the bundled sound classifier without reading an audio file."""
    import json
    import subprocess

    import bounce_sorter_app  # noqa: F401 (the window's module must import)
    import drums
    import librosa
    import numpy as np
    import soundfile
    from concurrent.futures import ProcessPoolExecutor
    from multitool import music

    spectrum = np.abs(librosa.stft(np.zeros(4096)))
    librosa.decompose.hpss(spectrum)
    drums.fit([[0, 1], [1, 0], [2, 1], [1, 2]], [0, 0, 1, 1])  # the learning step (SciPy)
    if music.key_from_chroma(music.MINOR_PROFILE)[0] != music.Key("C", True):
        raise RuntimeError("Key detection gave an unexpected result")
    if not soundfile.available_formats():
        raise RuntimeError("Audio decoders are unavailable")
    with ProcessPoolExecutor(max_workers=1) as pool:
        score = pool.submit(drums.drum_probability, drums.DEFAULT_MODEL["means"], drums.DEFAULT_MODEL).result(timeout=30)
    if score != 0.5:
        raise RuntimeError("The audio worker returned an unexpected result")
    classifier = drums.classifier_path()
    from multitool import platform
    if not platform.IS_MAC:
        model = drums.load_model()
        basic = model.get("basic", model)
        drums.drum_probability(basic["means"], model)  # verify the Windows fallback is packaged
        return
    if classifier is None:
        raise RuntimeError("The sound classifier is missing (native/sound_classifier.swift)")
    answer = subprocess.run([str(classifier), "--self-check"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if not json.loads(answer.stdout or "{}").get("ok"):
        raise RuntimeError(f"macOS's sound classifier didn't start: {answer.stdout or answer.stderr}")
