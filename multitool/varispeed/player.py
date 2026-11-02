"""Live tape-style playback and the recorder.

The beat plays at any speed with the pitch following. A speed change glides there the way a tape motor would:
1/speed changes at a steady rate, as quickly as the limits in the settings allow (core.glide_seconds), so an
octave takes about 0.37 s down and 0.74 s up. Playback reads between samples with linear interpolation, which is
fine for listening; saved copies use the studio-quality resampler in core.py.

The recorder records what you hear to a 32-bit float stereo WAV: the audio thread only queues blocks, and a
background thread writes the file.
"""
from datetime import datetime
from pathlib import Path
import queue
import threading

import numpy as np

from multitool.audio.bridge import output_pairs  # noqa: F401 (the Output pair list)
from . import core


class TapePlayer:
    def __init__(self, samples, rate, options=None, stream=None):
        """`stream(rate, callback, device, channel_map)` opens the sound output; by default the Mac's device
        through PortAudio (sounddevice). Tests pass a stand-in."""
        self.samples = np.ascontiguousarray(samples, dtype=np.float32)
        self.rate = rate
        self.options = options or core.settings()
        self.position = 0.0  # in source samples
        self.clock = 0  # output samples since the output opened: glides follow it
        self.start_speed = self.target = 1.0
        self.glide_from, self.glide_frames = 0, 0
        self.playing = False
        self.recorder = None
        self.dropouts = 0
        self.open_stream = stream or default_stream
        self.stream, self.device, self.channel_map = None, None, None
        self.lock = threading.Lock()

    @property
    def duration(self):
        return len(self.samples) / self.rate

    def _speeds(self, clocks):
        """The speed at these output-clock positions: 1/speed moves in a straight line during a glide."""
        if self.glide_frames <= 0:
            return np.full(len(clocks), self.target)
        t = np.clip((clocks - self.glide_from) / self.glide_frames, 0.0, 1.0)
        return 1 / (1 / self.start_speed + (1 / self.target - 1 / self.start_speed) * t)

    @property
    def speed(self):
        """The speed right now, part-way through a glide included."""
        with self.lock:
            return float(self._speeds(np.array([self.clock]))[0])

    @property
    def running(self):
        return self.stream is not None

    def set_speed(self, speed):
        """Where the speed is heading. With the output running it glides there; otherwise it's there at once."""
        with self.lock:
            now = float(self._speeds(np.array([self.clock]))[0])
            self.start_speed, self.target, self.glide_from = now, speed, self.clock
            self.glide_frames = int(core.glide_seconds(now, speed, self.rate, self.options) * self.rate) \
                if self.running and abs(now - speed) > 1e-9 else 0

    def seek(self, seconds):
        with self.lock:
            self.position = min(max(0.0, seconds * self.rate), max(0, len(self.samples) - 1))

    def render(self, frames):
        """The next `frames` output samples (stereo). Silence while paused; the clock (and any glide) runs on."""
        out = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            speeds = self._speeds(self.clock + np.arange(frames))
            if self.playing:
                positions = self.position + np.concatenate(([0.0], np.cumsum(speeds[:-1])))
                index = positions.astype(np.int64)
                inside = index + 1 < len(self.samples)
                index, frac = index[inside], (positions[inside] - index[inside])[:, None]
                out[:len(index)] = self.samples[index] * (1 - frac) + self.samples[index + 1] * frac
                self.position = float(positions[-1] + speeds[-1])
                if not inside.all():  # reached the end
                    self.playing, self.position = False, 0.0
            self.clock += frames
            if self.clock >= self.glide_from + self.glide_frames and self.glide_frames:
                self.start_speed, self.glide_frames = self.target, 0  # glide finished
            recorder = self.recorder
        if recorder is not None:
            recorder.push(out)
        return out

    def callback(self, outdata, frames, time, status):
        if status and getattr(status, "output_underflow", False):
            self.dropouts += 1
        block = self.render(frames)
        if self.channel_map is None or outdata.shape[1] == 2:
            outdata[:] = block
        else:
            outdata[:] = 0
            outdata[:, self.channel_map[0]] = block[:, 0]
            outdata[:, self.channel_map[1]] = block[:, 1]

    def open(self, device=None, left_channel=0, channels=2):
        """Starts the sound output on `device` (None = the Mac's default), on outputs left_channel+1 and +2."""
        if self.stream is not None:
            return
        self.device = device
        mapping = None
        if channels > 2:
            mapping = [-1] * channels
            mapping[left_channel], mapping[left_channel + 1] = 0, 1
        self.stream = self.open_stream(self.rate, self.callback, device=device, channel_map=mapping)
        self.stream.start()

    def close_output(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            stream.stop()
            stream.close()

    def latency_ms(self):
        latency = getattr(self.stream, "latency", 0) or 0
        return float(latency) * 1000 if isinstance(latency, (int, float)) else 0.0

    def close(self):
        with self.lock:
            self.playing = False
        self.close_output()


def default_stream(rate, callback, device=None, channel_map=None):
    import sounddevice
    extra = sounddevice.CoreAudioSettings(channel_map=channel_map) if channel_map else None
    return sounddevice.OutputStream(samplerate=rate, channels=2, dtype="float32", callback=callback, latency="low",
                                    device=device, extra_settings=extra)


def output_devices():
    """[(name, output channels)] of the Mac's sound outputs, and the current default's name (only read)."""
    import sounddevice
    devices = [(d["name"], d["max_output_channels"]) for d in sounddevice.query_devices() if d["max_output_channels"] > 0]
    try:
        default = sounddevice.query_devices(kind="output")["name"]
    except Exception:
        default = devices[0][0] if devices else ""
    return devices, default


class Recorder:
    """Records what you hear into `folder` as "Varispeed <date time>.wav" (32-bit float stereo). Never replaces a
    file: an existing name gets " (2)"."""
    def __init__(self, folder, rate):
        self.folder, self.rate = Path(folder).expanduser(), rate
        self.blocks = queue.SimpleQueue()
        self.frames = 0
        self.path, self.writer = None, None

    @property
    def seconds(self):
        return self.frames / self.rate

    def start(self, now=None):
        import soundfile
        self.folder.mkdir(parents=True, exist_ok=True)
        for path in take_names(self.folder, now):
            try:
                handle = soundfile.SoundFile(path, "x", samplerate=self.rate, channels=2, subtype="FLOAT")
                break
            except (OSError, soundfile.LibsndfileError) as error:  # "File exists" comes as a plain OSError
                if path.exists():
                    continue
                raise OSError(f"Can't write to the recording folder: {error}") from None
        self.path = path
        self.writer = threading.Thread(target=self._write, args=(handle,), daemon=True)
        self.writer.start()
        return path

    def push(self, block):
        """Called from the audio thread: only queues a copy."""
        self.frames += len(block)
        self.blocks.put(block.copy())

    def _write(self, handle):
        with handle:
            while (block := self.blocks.get()) is not None:
                handle.write(block)

    def stop(self):
        """Finishes the file; returns (path, seconds)."""
        self.blocks.put(None)
        if self.writer:
            self.writer.join()
        return self.path, self.seconds


def take_names(folder, now=None):
    """"Varispeed <date time>.wav", then " (2)", " (3)", … in `folder`."""
    stem = f"Varispeed {(now or datetime.now()):%Y-%m-%d %H.%M.%S}"
    for number in range(1, 1000):
        yield Path(folder) / (f"{stem}.wav" if number == 1 else f"{stem} ({number}).wav")


def next_take(folder, now=None):
    """The first take name not used yet (for recordings the audio bridge makes, which never replace a file)."""
    return next(path for path in take_names(folder, now) if not path.exists())


def latest_take(folder):
    """(path, seconds) of the newest take in the recordings folder, or None (only the latest is shown)."""
    import soundfile
    folder = Path(folder).expanduser()
    try:
        takes = [p for p in folder.iterdir() if p.suffix.lower() == ".wav" and not p.name.startswith(".")]
    except OSError:
        return None
    for path in sorted(takes, key=lambda p: getattr(p.stat(), "st_birthtime", p.stat().st_mtime), reverse=True):
        try:
            return path, soundfile.info(path).duration
        except Exception:
            continue
    return None
