"""Shared resources and writable settings for source runs and macOS bundles."""
import hashlib
import json
import fcntl
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = Path(os.environ.get("DAW_MULTITOOL_DATA_DIR") or (
    Path.home() / "Library" / "Application Support" / "DAW Multitool" if FROZEN else RESOURCE_DIR
)).expanduser().resolve()
if FROZEN:
    os.environ.setdefault("NUMBA_CACHE_DIR", str(DATA_DIR / "numba-cache"))

_CONFIG_LOCK = threading.RLock()


def initialize_data():
    """Seed defaults once; never bundle or replace personal settings and labels."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for source_name, destination_name in (
        ("config.example.json", "config.json"), ("drum_model.json", "drum_model.json")
    ):
        source, destination = RESOURCE_DIR / source_name, DATA_DIR / destination_name
        model = destination_name == "drum_model.json"
        if model and source.exists() and destination.exists() and source.resolve() != destination.resolve():
            _upgrade_shipped_model(source, destination)
        if destination.exists() or not source.exists():
            continue
        try:
            with destination.open("xb") as target:
                data = source.read_bytes()
                if model:  # stamp the copy so a later app version knows it may replace it
                    data = json.dumps({**json.loads(data), "origin": "shipped"}, indent=2).encode()
                target.write(data)
        except FileExistsError:
            pass


# Models shipped before copies were stamped with "origin": "shipped" (v2 default, v3 default).
LEGACY_SHIPPED_MODELS = {"54192bd9bb3779ee", "a037b83868f9bb4e"}


def model_fingerprint(model):
    core = {key: model[key] for key in ("weights", "means", "scales", "bias")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def _upgrade_shipped_model(source, destination):
    """Swap in a newer shipped drum model only when the current one provably came from the app
    (stamped "shipped" or a known shipped fingerprint), never a model the user trained.
    The replaced model is kept as drum_model.previous-<time>.json. Never blocks startup."""
    try:
        shipped, current = json.loads(source.read_text()), json.loads(destination.read_text())
        if not (isinstance(shipped, dict) and isinstance(current, dict)):
            return
        new, old = shipped.get("feature_version", 0), current.get("feature_version", 0)
        if not (isinstance(new, int) and isinstance(old, int) and new > old):
            return
        if current.get("origin") != "shipped" and model_fingerprint(current) not in LEGACY_SHIPPED_MODELS:
            return  # trained by the user (window or check_drums): keep it
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with destination.with_name(f"drum_model.previous-{stamp}-{os.getpid()}.json").open("x") as backup:
            json.dump(current, backup, indent=2)
        atomic_json(destination, {**shipped, "origin": "shipped"})
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return  # a damaged or odd copy is ignored here; drums.load_model falls back safely


def atomic_json(path, value, indent=2):
    """One durable JSON writer shared by settings, labels, models and receipts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=indent)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()  # only our temporary JSON; never a media file


def load_section(section, path=None):
    """One tool's settings: the shipped defaults with the user's saved values on top. A missing or damaged
    config.json counts as nothing saved."""
    defaults = json.loads((RESOURCE_DIR / "config.example.json").read_text())[section]
    try:
        saved = json.loads(Path(path or DATA_DIR / "config.json").read_text()).get(section, {})
    except (OSError, ValueError, AttributeError):  # AttributeError: the file holds a list, not settings
        saved = {}
    return {**defaults, **(saved if isinstance(saved, dict) else {})}


def update_config(section, values, path=None):
    """Lock the complete read/merge/write transaction across threads and app processes."""
    path = Path(path or DATA_DIR / "config.json").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A separate lock file keeps its identity when config.json is atomically replaced.
    with _CONFIG_LOCK, path.with_name(f".{path.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                config = json.loads(path.read_text())
                if not isinstance(config, dict):
                    raise ValueError("Config must contain an object")
            except FileNotFoundError:
                config = {}
            except ValueError:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                aside = path.with_name(f"config.damaged-{stamp}.json")
                n = 2
                while aside.exists():
                    aside = path.with_name(f"config.damaged-{stamp} ({n}).json")
                    n += 1
                path.rename(aside)
                config = {}
            config[section] = {**config.get(section, {}), **values}
            atomic_json(path, config)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
