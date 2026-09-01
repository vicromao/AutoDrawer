"""Persisted user settings.

Every control keeps its last value between runs; the Reset Settings button is
the only thing that brings the defaults back. Reading is defensive on purpose -
a truncated or hand-edited file must never stop the app from starting, so any
key that fails validation silently falls back to its default.
"""

import json
import os
import re
import tempfile

DEFAULTS = {
    "channel": "All",
    "threshold": 125,
    "resolution": 100,
    "mode": "Outline",
    "simplify": 0.0,
    "min_detail": 0,
    "hatch_gap": 4,
    "pixel_size": 1,
    "time_budget": 180,
    "target_hz": 60,
    "lock_aspect": True,
    "ghost_fill": True,
    "geometry": "1060x680",
    "last_dir": "",
}

# Values that must come from a fixed set, so a stale file cannot put a bogus
# entry into a read-only combobox.
CHOICES = {
    "channel": ("All", "Red", "Green", "Blue"),
    "mode": ("Outline", "Outline + Hatch"),
    "target_hz": (30, 60, 120, 144),
}

# Only the size is persisted, never the +x+y offset: a window last closed on a
# second monitor would otherwise reopen off-screen once that monitor is gone.
GEOMETRY_RE = re.compile(r"^[1-9]\d{1,4}x[1-9]\d{1,4}$")

# Numeric ranges, mirroring the sliders in AutoDrawer.py.
RANGES = {
    "threshold": (0, 255),
    "resolution": (1, 100),
    "simplify": (0, 5),
    "min_detail": (0, 60),
    "hatch_gap": (1, 20),
    "pixel_size": (1, 20),
    "time_budget": (5, 600),
}


def config_path():
    """Config file location, following XDG on Linux and falling back to ~."""

    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "autodrawer", "settings.json")


def _clean(key, value):
    """Return ``value`` if it is usable for ``key``, otherwise the default."""

    default = DEFAULTS[key]

    if key in CHOICES:
        return value if value in CHOICES[key] else default

    if isinstance(default, bool):
        return bool(value) if isinstance(value, bool) else default

    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        low, high = RANGES.get(key, (None, None))
        if low is not None and not (low <= value <= high):
            return default
        return type(default)(value)

    if key == "geometry":
        return value if isinstance(value, str) and GEOMETRY_RE.match(value) else default

    if isinstance(default, str):
        return value if isinstance(value, str) else default

    return default


def sanitize(raw):
    """Merge ``raw`` onto the defaults, dropping unknown or invalid entries."""

    values = dict(DEFAULTS)

    if isinstance(raw, dict):
        for key in DEFAULTS:
            if key in raw:
                values[key] = _clean(key, raw[key])

    if values["last_dir"] and not os.path.isdir(values["last_dir"]):
        values["last_dir"] = DEFAULTS["last_dir"]

    return values


def load(path=None):
    path = path or config_path()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        raw = None

    return sanitize(raw)


def save(values, path=None):
    """Write settings atomically. Returns True on success, False if unwritable."""

    path = path or config_path()
    payload = sanitize(values)

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Write to a sibling temp file and rename, so an interrupted save can
        # never leave a half-written config behind.
        handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path),
                                             prefix=".settings-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except OSError:
        return False

    return True
