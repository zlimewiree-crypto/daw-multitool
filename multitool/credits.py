"""Read collaborator credits out of file names, e.g. "@hauntshl 159 dmin +50cents" -> @hauntshl.

Shared by the organizer (Credits column) and Auto Poster ({credits} in titles/captions).
Settings live in the "credits" section of config.json: producer_name, known_producers, credit_format, ignore_words.
"""
from pathlib import Path
import re

from app_paths import load_section, update_config

HANDLE = re.compile(r"@([A-Za-z0-9_][A-Za-z0-9_.]*)")
PROJECT = re.compile(r"(?<![@\w])([A-Za-z0-9_]+)\.flp\b", re.I)  # "ar22.flp" -> ar22
BRACKET = re.compile(r"\[([^\]@]+)\]")  # "[kimbo]"
PROD = re.compile(r"\bprod\b\.?\s*(.+?)(?=[)\]]|$)", re.I)  # "prod. cari", "(Prod. Deezy)"
JOIN = re.compile(r"\s*(?:\s[x&+]\s|,|\band\b|\bw/)\s*", re.I)
# Words that end a "prod ..." run: tempo, key, tuning, numbers
STOP = re.compile(r"^(\d+(bpm)?|bpm|[a-g][#b]?(maj|min|m)?|[+-]?\d+\s*cents?|a4\d\d|\(\d+\))$", re.I)


def load_settings():
    return load_section("credits")


def producer_names(value):
    """Comma/newline-separated names, preserving display spelling and removing duplicates."""
    names = re.split(r"[,;\n]+", value) if isinstance(value, str) else value
    result, seen = [], set()
    for name in names:
        name = name.strip()
        key = name.casefold().lstrip("@")
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def save_settings(values):
    current = load_settings()
    values = dict(values)
    if "known_producers" in values:
        values["known_producers"] = producer_names(values["known_producers"])
    if "producer_name" in values:
        values["producer_name"] = values["producer_name"].strip()
    changes = {key: value for key, value in values.items() if current.get(key) != value}
    if changes:
        update_config("credits", changes)
    return {**current, **changes}


def _clean(name):
    return name.strip(" .-_()[]").removesuffix(".flp")


def collaborators(filename, settings):
    """Names in the order they appear; handles keep their @. The user's own name is left out."""
    stem = Path(filename).stem
    own = settings["producer_name"].strip().casefold().lstrip("@")
    ignore = {w.casefold() for w in settings["ignore_words"]}
    found = [(m.start(), "@" + _clean(m[1]), False) for m in HANDLE.finditer(stem)]
    found += [(m.start(), _clean(m[1]), False) for m in PROJECT.finditer(stem)]
    found += [(m.start(), _clean(m[1]), False) for m in BRACKET.finditer(stem)]
    for m in PROD.finditer(stem):
        for part in JOIN.split(m[1]):
            words = []
            for word in part.split():
                if STOP.match(word) or word.startswith("@"):
                    break
                words.append(word)
            if words:
                found.append((m.start(1) + m[1].find(part), _clean(" ".join(words[:2])), False))
    for name in producer_names(settings.get("known_producers", [])):
        # Explicit names are literal filename fragments, not regexes or guesses about unknown names.
        match = re.search(re.escape(name.lstrip("@")), stem, re.IGNORECASE)
        if match:
            found.append((match.start(), name, True))
    names, seen = [], set()
    for _, name, explicit in sorted(found):
        key = name.casefold().lstrip("@")
        if name and key != own and (explicit or key not in ignore) and key not in seen and (explicit or not STOP.match(key)):
            seen.add(key)
            names.append(name)
    return names


def credit_line(filename, settings):
    """e.g. "prod. kellan x @hauntshl"; empty when there is nobody to credit."""
    people = ([settings["producer_name"]] if settings["producer_name"].strip() else []) + collaborators(filename, settings)
    return settings["credit_format"].replace("{credits}", " x ".join(people)) if people else ""


def fill(template, filename, stem, settings, extra=None):
    """Fill {name}, {credits} and any `extra` placeholders ({title}, {bpm}, {key}) in a title or caption. With
    nobody to credit, "{name} ({credits})" becomes just the name, not "name ()"; an empty {bpm}/{key} at the end
    takes its "|" or "·" separator with it."""
    text = template.replace("{name}", stem).replace("{credits}", credit_line(filename, settings))
    for name, value in (extra or {}).items():
        text = text.replace("{" + name + "}", value)
    text = re.sub(r"\s*(\(\s*\)|\[\s*\]|\|\s*$)", "", text)
    text = re.sub(r"(\s*[|·])+\s*$", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
