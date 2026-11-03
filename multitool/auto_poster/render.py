"""Read-only source media -> a new MP4. No originals are renamed or deleted."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from app_paths import DATA_DIR, RESOURCE_DIR
from .storage import NAMES, atomic_json


class Cancelled(Exception):
    pass


def ffmpeg_binary(settings):
    configured = settings.get("ffmpeg_path", "")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise ValueError("The configured FFmpeg file does not exist.")
        return str(path)
    for bundled in (RESOURCE_DIR / "imageio_ffmpeg" / "binaries").glob("ffmpeg*"):
        if bundled.is_file():
            return str(bundled)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def snapshot(path):
    info = Path(path).stat()
    return info.st_size, info.st_mtime_ns


def media_info(path, settings):
    path = Path(path).resolve()
    return dict(_probe(str(path), *snapshot(path), ffmpeg_binary(settings), settings["probe_timeout"]))


@lru_cache(maxsize=256)
def _probe(path, size, modified_ns, binary, timeout):
    """Bounded metadata cache; touching or replacing a source forces a fresh probe."""
    result = subprocess.run([binary, "-hide_banner", "-i", path],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    if snapshot(path) != (size, modified_ns):
        raise ValueError("Source media changed while checking it. Let the export finish and try again.")
    text = result.stderr
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    seconds = (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) if duration else None
    return {"duration": seconds, "audio": "Audio:" in text, "video": "Video:" in text}


@dataclass(frozen=True)
class VideoRequest:
    audio: Path
    visual: Path | None  # None: made automatically (the beat's own cover art, else a gradient) when rendering
    output: Path
    format: str
    fit: str = "Fit"
    start: float = 0
    duration: float = 0
    title: str = ""
    caption: str = ""
    tag: Path | None = None  # producer tag mixed into the audio every few seconds (source stays untouched)
    project: Path | None = None  # DAW project screen-recorded for the vertical version
    vertical_visual: Path | None = None  # replaces `visual` in the TikTok/Reels version only
    vertical_start: float = 0  # seconds into vertical_visual where this audio's start lines up
    visual_start: float = 0
    social_description: str = ""  # TikTok / Reels draft, saved with the render so it survives a restart
    hashtags: str = ""
    social_caption: str = ""
    short_start: float | None = None  # TikTok/Reels version's own start (the best part); None = same as YouTube
    short_duration: float = 0  # ...and its length (0 = to the end)
    overlay: tuple = ()  # text drawn on both versions: the title, then e.g. "155 BPM · F minor"
    waveform: bool = False  # a moving waveform of the beat over the picture
    music: dict | None = None  # {"title", "bpm", "key"} as written in the texts; kept in the receipt for posting


def for_format(request, format_name, settings):
    """The request one version renders: YouTube keeps the main visual and trim; the vertical one may have its own
    trim (the best part) and use the DAW recording, kept in step with the audio."""
    request = replace(request, format=format_name)
    if format_name == settings["platform_formats"]["youtube"]:
        return request
    if request.short_start is not None:
        shift = request.short_start - request.start  # the recording lines up with the song, so it moves too
        request = replace(request, start=request.short_start, duration=request.short_duration,
                          vertical_start=request.vertical_start + shift)
    if request.vertical_visual:
        request = replace(request, visual=request.vertical_visual, visual_start=request.vertical_start, fit="Blur")
    return request


def tag_times(duration, settings):
    """Seconds (from the start of the output) where the producer tag plays."""
    first, every = settings["tag_first_seconds"], settings["tag_every_seconds"]
    times, t = [], first
    while t < duration - 1:
        times.append(t)
        if every <= 0:
            break
        t += every
    return times


def tag_filter(request, settings, duration, beat="1:a", tag="2:a"):
    """FFmpeg filter that lays the tag over the beat and outputs [a]; None when there is no tag."""
    times = tag_times(duration, settings) if request.tag else []
    if not times:
        return None
    stereo = f"aformat=sample_rates={settings['sample_rate']}:channel_layouts=stereo"
    copies = "".join(f"[t{i}]" for i in range(len(times)))
    parts = [f"[{tag}]{stereo},volume={settings['tag_volume_db']}dB,asplit={len(times)}{copies}"]
    parts += [f"[t{i}]adelay=delays={int(t * 1000)}:all=1[d{i}]" for i, t in enumerate(times)]
    delayed = "".join(f"[d{i}]" for i in range(len(times)))
    # normalize=0 keeps the beat at its own level; the limiter stops the tag from clipping it
    parts.append(f"[{beat}]{stereo}[m];[m]{delayed}amix=inputs={len(times) + 1}:duration=first:normalize=0,"
                 f"alimiter=limit=0.97[a]")
    return ";".join(parts)


def posting_formats(settings):
    """One wide and one vertical version, with configurable resolution and platform routing."""
    mapping = settings["platform_formats"]
    for platform in ("youtube", "tiktok", "instagram"):
        name = mapping[platform]
        width, height = settings["formats"][name]
        aspect_width, aspect_height = (16, 9) if platform == "youtube" else (9, 16)
        if not all(isinstance(n, int) and n > 0 and n % 2 == 0 for n in (width, height)) or width * aspect_height != height * aspect_width:
            raise ValueError(f"Configure a {'16:9' if platform == 'youtube' else '9:16'} format with even dimensions for {NAMES[platform]}.")
    formats = tuple(dict.fromkeys(mapping[platform] for platform in ("youtube", "tiktok", "instagram")))
    if len(formats) != 2:
        raise ValueError("TikTok and Instagram must share the same vertical format.")
    return formats


def validate(request, settings):
    audio = request.audio.resolve()
    visual = request.visual.resolve() if request.visual else None  # None: made automatically when rendering
    for path in (audio, visual):
        if path and (not path.is_file() or path.name.startswith(".") or path.stat().st_size == 0):
            raise ValueError(f"Choose a completed, non-empty media file: {path.name}")
    if audio.suffix.lower() not in settings["audio_extensions"]:
        raise ValueError("This audio format is not enabled in Auto Poster settings.")
    if visual and visual.suffix.lower() not in settings["image_extensions"] + settings["video_extensions"]:
        raise ValueError("Choose a supported image or video for the visual.")
    if request.format not in settings["formats"] or request.fit not in ("Fit", "Fill", "Blur"):
        raise ValueError("Choose an available format and Fit, Fill or Blur.")
    if not all(math.isfinite(value) and value >= 0 for value in (request.start, request.duration)):
        raise ValueError("Start and length must be zero or positive.")
    info = media_info(audio, settings)
    if not info["audio"] or not info["duration"]:
        raise ValueError(f"Couldn't read an audio track and duration from {audio.name}.")
    available = info["duration"] - request.start
    if available <= 0:
        raise ValueError(f"The start time is past the end of {audio.name}.")
    if visual and not media_info(visual, settings)["video"]:
        raise ValueError(f"Couldn't read a picture or video stream from {visual.name}.")
    if request.tag:
        tag = request.tag.resolve()
        if not tag.is_file() or tag.suffix.lower() not in settings["audio_extensions"] or not media_info(tag, settings)["audio"]:
            raise ValueError(f"Choose a readable audio file for your producer tag: {tag.name}")
    return min(request.duration or available, available)


def _quoted(path):
    """A file path as a filter option value: FFmpeg reads it literally inside single quotes."""
    text = Path(path).as_posix().replace(":", "\\:")  # drive letters are FFmpeg option separators unless escaped
    if "'" in text:
        raise ValueError("A file path for the video text contains an apostrophe.")
    return "'" + text + "'"


def overlay_files(lines, folder=None):
    """Writes each line of on-video text to its own small file (drawtext reads text files, so titles with
    quotes, colons or % need no escaping). Returns the file paths."""
    folder = Path(folder or DATA_DIR / "video-text")
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for line in lines:
        path = folder / f"{uuid.uuid4().hex}.txt"
        path.write_text(line, encoding="utf-8")
        paths.append(path)
    return paths


def overlay_font(settings, bold=True):
    """The first installed font from the settings (every Mac has the Arial family in Supplemental)."""
    for candidate in settings["overlay_fonts" if bold else "overlay_fonts_regular"]:
        candidate = os.path.expandvars(candidate)
        if Path(candidate).is_file():
            return candidate
    raise ValueError("None of the fonts for on-video text were found. Check overlay_fonts in the settings.")


def text_filters(lines, files, settings, width, height):
    """drawtext steps: a big white title and a smaller purple line under it, centred, sized to fit the width.
    Tall videos put them in the top third (TikTok's own buttons cover the bottom), wide ones lower down."""
    filters = []
    tall = height > width
    top = height * (0.15 if tall else 0.62)
    for index, (line, path) in enumerate(zip(lines, files)):
        base = (width * 0.10 if tall else height * 0.085) if index == 0 else (width * 0.045 if tall else height * 0.042)
        size = int(min(base, width * 0.88 / max(1, 0.62 * len(line))))  # long titles shrink instead of running off
        y = int(top if index == 0 else top + width * (0.13 if tall else 0.065) + (index - 1) * size * 1.4)
        colour = "white" if index == 0 else settings["overlay_accent"]
        filters.append(f"drawtext=fontfile={_quoted(overlay_font(settings, index == 0))}:textfile={_quoted(path)}:"
                       f"expansion=none:fontsize={size}:fontcolor={colour}:x=(w-text_w)/2:y={y}:"
                       f"shadowcolor=black@0.55:shadowx={max(2, size // 30)}:shadowy={max(2, size // 30)}")
    return filters


def command(request, settings, target, duration, text=()):
    width, height = settings["formats"][request.format]
    image = request.visual.suffix.lower() in settings["image_extensions"]
    visual_args = ["-f", "image2", "-loop", "1", "-framerate", "1", "-pattern_type", "none"] if image else ["-stream_loop", "-1", "-ss", str(request.visual_start)]
    # a still is read and scaled once a second, then repeated up to fps after scaling: same video, ~2.5x faster
    end = f"setsar=1,fps={settings['fps']}" if image else "setsar=1"
    extras = text_filters(request.overlay, text, settings, width, height) if request.overlay else []
    if request.waveform:  # the picture ends as [p]; the waveform and text go on top of it
        end += "[p]"
    else:
        end += ("," + ",".join(extras) if extras else "") + "[v]"
    if request.fit == "Blur":  # whole visual in the middle, over a stretched, blurred copy filling the frame
        # blur a small copy: same look, far faster on big recordings; >= 64 px or FFmpeg 9's gblur turns the colour green
        small = f"{max(64, width // 16 * 2)}:{max(64, height // 16 * 2)}"
        picture = (f"[0:v]split[bg][fg];[bg]scale={small},gblur=sigma=6,scale={width}:{height}[b];"
                   f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[f];"
                   f"[b][f]overlay=(W-w)/2:(H-h)/2,{end}")
    elif request.fit == "Fit":
        picture = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,{end}")
    else:
        picture = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},{end}"
    beat = "1:a"
    if request.waveform:  # a thin line following the beat, across the lower part of the frame
        wave_height = int(height * (0.13 if height > width else 0.2))
        y = int(height * (0.70 if height > width else 0.76))
        beat = "beat"
        picture += (f";[1:a]asplit=2[beat][wave];[wave]aformat=channel_layouts=mono,dynaudnorm=f=150:g=15,volume={settings['waveform_volume']},"
                    f"showwaves=s={width}x{wave_height}:mode=p2p:n={max(1, round(6480 / width))}:draw=full:rate={settings['fps']}:"
                    f"colors={settings['waveform_colour']}:scale=lin[w];[p][w]overlay=0:{y}"
                    + ("," + ",".join(extras) if extras else "") + "[v]")
    audio = tag_filter(request, settings, duration, beat=beat)
    if audio:  # tag as a third input; picture and mixed audio come out of one filter graph
        streams = ["-i", str(request.tag), "-filter_complex", f"{picture};{audio}", "-map", "[v]", "-map", "[a]"]
    elif request.waveform:
        streams = ["-filter_complex", f"{picture};[beat]anull[a]", "-map", "[v]", "-map", "[a]"]
    else:
        streams = ["-filter_complex", picture, "-map", "[v]", "-map", "1:a:0"]
    return [ffmpeg_binary(settings), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            *visual_args, "-i", str(request.visual), "-ss", str(request.start), "-i", str(request.audio),
            *streams, "-t", str(duration), "-r", str(settings["fps"]), "-c:v", "libx264",
            "-preset", settings["preset"], "-crf", str(settings["crf"]), "-pix_fmt", "yuv420p",
            # capped well under Instagram's 25 Mbps; a still image or DAW window never gets near it
            "-maxrate", settings["video_max_bitrate"], "-bufsize", settings["video_max_bitrate"],
            "-c:a", "aac", "-b:a", settings["audio_bitrate"], "-ar", str(settings["sample_rate"]),
            # Instagram's Reels spec: moov atom first and no MP4 edit lists
            "-ac", "2", "-movflags", "+faststart", "-use_editlist", "0", "-shortest", "-progress", "pipe:1", str(target)]


def check_plan(request, settings, *, formats=None, measure_loudness=False):
    formats = tuple(dict.fromkeys(formats or (request.format,)))
    seconds = {name: validate(for_format(request, name, settings), settings) for name in formats}
    duration = seconds[formats[0]]
    plan = {"audio": str(request.audio), "visual": str(request.visual) if request.visual else "automatic",
            "start": request.start, "seconds": duration, "seconds_by_format": seconds,
            "format": " + ".join(formats), "videos": len(formats), "fit": request.fit, "output_folder": str(request.output),
            "originals": "read-only", "publishes": False, "warnings": []}
    if request.project:
        plan["project"] = str(request.project)
    if request.short_start is not None:
        plan["short_start"] = request.short_start
    if measure_loudness:
        plan.update(loudness(request.audio, request.start, duration, settings))
        plan["warnings"] = loudness_warnings(plan, settings)
    return plan


def loudness(path, start, duration, settings):
    """Integrated loudness (LUFS) and true peak (dBTP) of the part that goes in the video."""
    info = Path(path).stat()
    return _loudness(str(path), info.st_size, info.st_mtime_ns, float(start), float(duration),
                     ffmpeg_binary(settings), settings["probe_timeout"] * 4)


@lru_cache(maxsize=256)  # keyed by size + modified time, so an edited bounce is measured again
def _loudness(path, size, modified_ns, start, duration, binary, timeout):
    result = subprocess.run([binary, "-hide_banner", "-nostats", "-nostdin", "-ss", str(start), "-t", str(duration),
                             "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    summary = result.stderr.split("Summary:")[-1]
    number = lambda pattern: (float(m[1]) if (m := re.search(pattern, summary)) and m[1] != "-inf" else None)
    return {"lufs": number(r"I:\s*(-?[\d.]+|-inf)\s*LUFS"), "true_peak": number(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS")}


def loudness_warnings(plan, settings):
    """Plain-language warnings; they never block a render."""
    warnings = []
    if plan["lufs"] is not None and plan["lufs"] < settings["loudness_min_lufs"]:
        warnings.append(f"quiet ({plan['lufs']:.1f} LUFS; most type beats sit around -8 to -12)")
    if plan["true_peak"] is not None and plan["true_peak"] > settings["true_peak_max_db"]:
        warnings.append(f"may clip after upload (peaks at {plan['true_peak']:.1f} dB)")
    return warnings


def create_video(request, settings, cancelled=lambda: False, progress=lambda value: None, *, formats=None):
    formats = tuple(dict.fromkeys(formats or (request.format,)))
    # the tag is a source too: if it changes mid-render, the videos would carry different tags
    before = {path: snapshot(path) for path in (request.audio, request.visual, request.tag, request.vertical_visual) if path}
    deadline = time.monotonic() + settings["settle_seconds"]
    while time.monotonic() < deadline:
        if cancelled():
            raise Cancelled("Stopped before reading source media.")
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    if any(snapshot(path) != value for path, value in before.items()):
        raise ValueError("Source media is still changing. Let the export finish, then try again.")
    durations = check_plan(request, settings, formats=formats)["seconds_by_format"]
    duration = durations[formats[0]]
    if cancelled():
        raise Cancelled("Stopped before rendering.")
    request.output.mkdir(parents=True, exist_ok=True)
    folder = request.output / f"{request.audio.stem[:70]}-{uuid.uuid4().hex[:12]}"
    folder.mkdir()  # a new directory for every attempt; never reuse or overwrite an output
    if request.visual is None:
        request = replace(request, **automatic_visual(request, settings, folder))
    text = []
    receipt = folder / "post.json"
    record = {"version": 2, "created": datetime.now(timezone.utc).isoformat(),
              "audio": str(request.audio.resolve()), "visual": str(request.visual.resolve()),
              "title": request.title or request.audio.stem, "caption": request.caption,
              "social_description": request.social_description, "hashtags": request.hashtags,
              "social_caption": request.social_caption,
              "duration": duration, "start": request.start, "fit": request.fit,
              "state": "rendering", "variants": {}, "delivery": {}}
    if request.project:
        record.update(project=str(request.project), vertical_visual=str(request.vertical_visual))
    if request.music:
        record["music"] = dict(request.music)
    atomic_json(receipt, record)
    try:
        text = overlay_files(request.overlay) if request.overlay else []
        for index, format_name in enumerate(formats):
            if cancelled():
                raise Cancelled("Stopped before creating the next version. Completed files were kept.")
            if any(snapshot(path) != value for path, value in before.items()):
                raise ValueError("A source changed between versions. Let the export finish, then render again.")
            width, height = settings["formats"][format_name]
            final = folder / f"video-{index + 1}-{width}x{height}.mp4"
            version = for_format(request, format_name, settings)
            _encode_video(version, settings, final, durations[format_name], before, cancelled,
                          lambda pct, i=index: progress(min(99, int((i + pct / 100) * 100 / len(formats)))), text)
            record["variants"][format_name] = {"video": str(final), "video_snapshot": list(snapshot(final)),
                                               "width": width, "height": height, "start": version.start,
                                               "duration": durations[format_name]}
            atomic_json(receipt, record)
        if request.tag and tag_times(duration, settings):
            if cancelled():
                raise Cancelled("Stopped before saving the tagged MP3. Completed videos were kept.")
            record["tagged_mp3"] = str(_encode_tagged_mp3(request, settings, folder, duration, before, cancelled))
        record["state"] = "ready"  # neither version can be uploaded until the complete set succeeds
        atomic_json(receipt, record)
        progress(100)
        return receipt
    except Exception as error:
        record.update(state="cancelled" if isinstance(error, Cancelled) else "failed", error=str(error))
        atomic_json(receipt, record)
        raise
    finally:
        for path in text:
            path.unlink(missing_ok=True)  # our own one-line text files for the on-video titles, never media


def _encode_tagged_mp3(request, settings, folder, duration, before, cancelled):
    """Audio-only copy of the (trimmed) beat with the tag laid over it, for free downloads/previews.
    Stop works mid-encode; a source that changed meanwhile means no MP3 is published."""
    final = folder / f"{request.audio.stem[:70]} (tagged).mp3"
    partial = folder / "tagged.partial.mp3"
    process = subprocess.Popen([ffmpeg_binary(settings), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                                "-ss", str(request.start), "-i", str(request.audio), "-i", str(request.tag),
                                "-filter_complex", tag_filter(request, settings, duration, beat="0:a", tag="1:a"),
                                "-map", "[a]", "-t", str(duration), "-c:a", "libmp3lame", "-b:a", settings["tag_mp3_bitrate"],
                                str(partial)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + max(120, duration * 2)
    try:
        while process.poll() is None:
            if cancelled():
                raise Cancelled("Stopped while saving the tagged MP3. Completed videos were kept.")
            if time.monotonic() > deadline:
                raise ValueError("Saving the tagged MP3 took too long.")
            time.sleep(0.1)
    finally:
        stop(process)
        errors = process.stderr.read().decode(errors="replace")[-300:]
        process.stderr.close()
    if process.returncode:
        raise ValueError("Couldn't save the tagged MP3: " + errors)
    if any(snapshot(path) != value for path, value in before.items()):
        raise ValueError("A source changed while saving the tagged MP3. Render again.")
    os.link(partial, final)  # no-overwrite: fails if the name exists (the folder is brand new anyway)
    partial.unlink()  # only our own temporary MP3
    return final


def stop(process):
    """Asks a running FFmpeg to stop, and forces it if it hasn't after 5 seconds."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def automatic_visual(request, settings, folder):
    """The picture for a beat with no background chosen: its own cover art when the file has one (shown whole
    over a blurred copy), otherwise a soft purple glow on black. Both are saved in the render's new folder."""
    ffmpeg = ffmpeg_binary(settings)
    cover = folder / "cover.png"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(request.audio), "-an",
                    "-map", "0:v:0?", "-frames:v", "1", str(cover)], stdin=subprocess.DEVNULL, capture_output=True,
                   timeout=settings["probe_timeout"])
    if cover.is_file() and cover.stat().st_size:
        return {"visual": cover, "fit": "Blur"}
    background = folder / "background.png"
    base, glow = (tuple(int(colour[i:i + 2], 16) for i in (2, 4, 6)) for colour in settings["auto_background_colours"])
    # brightest a little above the middle, fading to the base colour towards the corners
    fade = "pow(max(0\\,1-hypot(X-W/2\\,Y-H*0.4)/(0.72*W))\\,1.6)"
    channels = ":".join(f"{name}='{low}+{high - low}*{fade}'" for name, low, high in zip("rgb", base, glow))
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                    f"color=c=black:s=1920x1920:d=1,format=rgb24,geq={channels}", "-frames:v", "1", str(background)],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=settings["probe_timeout"], check=True)
    return {"visual": background, "fit": "Fill"}


def _encode_video(request, settings, final, duration, before, cancelled, progress, text=()):
    partial = final.with_suffix(".partial.mp4")
    stderr = final.with_suffix(".log")
    process = None
    stop_monitor = threading.Event()
    try:
        with stderr.open("xb") as errors:
            process = subprocess.Popen(command(request, settings, partial, duration, text),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, text=True)
            def monitor():
                while not stop_monitor.wait(0.1):
                    if cancelled():
                        stop(process)
                        return
            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            for line in process.stdout:
                if line.startswith("out_time_us="):
                    try:
                        progress(min(99, int(float(line.split("=", 1)[1]) / 10000 / duration)))
                    except ValueError:
                        pass
            code = process.wait()
        if cancelled():
            raise Cancelled("Rendering stopped. Originals and the unfinished output were kept.")
        if code:
            raise ValueError(f"Video creation failed. Details: {stderr}")
        if any(snapshot(path) != value for path, value in before.items()):
            raise ValueError("A source changed while rendering; this output cannot be published. Render again.")
        check_output(partial, duration, settings)
        os.link(partial, final)  # atomic no-overwrite publication of our new video
        partial.unlink()  # only our generated MP4 hard link; never source audio
        progress(100)
    finally:
        stop_monitor.set()
        if process:
            stop(process)
        if process and process.stdout:
            process.stdout.close()


def check_output(path, duration, settings):
    """A finished video must have picture, sound and the planned length before it can ever be posted."""
    info = media_info(path, settings)
    if not (info["video"] and info["audio"]):
        raise ValueError("The new video came out without " + ("sound" if info["video"] else "a picture") + ". Render again.")
    if info["duration"] is None or abs(info["duration"] - duration) > settings["duration_tolerance_seconds"]:
        raise ValueError(f"The new video is {info['duration'] or 0:.1f} s long instead of {duration:.1f} s. Render again.")


def main():
    import argparse
    from .storage import load_settings
    parser = argparse.ArgumentParser(description="Create wide and vertical videos from audio + a visual; default is dry-run.")
    parser.add_argument("audio", type=Path)
    parser.add_argument("visual", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", help="render one format for local use instead of the platform pair")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    formats = (args.format,) if args.format else posting_formats(settings)
    request = VideoRequest(args.audio.resolve(), args.visual.resolve(),
                           (args.output or Path(settings["output_folder"]).expanduser()).resolve(), formats[0])
    print(json.dumps(check_plan(request, settings, formats=formats), indent=2))
    if args.run:
        print(create_video(request, settings, formats=formats))


if __name__ == "__main__":
    main()
