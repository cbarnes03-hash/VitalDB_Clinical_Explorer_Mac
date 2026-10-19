"""Application configuration: paths, limits and endpoints.

Everything user-specific is stored under a per-user folder so that no
administrator rights are needed and the project folder stays clean.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "VitalDB Clinical Explorer"

# Public VitalDB endpoints (documented at https://vitaldb.net/dataset).
API_URL = "https://api.vitaldb.net"
DATASET_VERSION = "1.0.1"  # matches the version used by the vitaldb package
CASES_URL = f"{API_URL}/cases"
TRKS_URL = f"{API_URL}/trks"


def case_url(case_id: int) -> str:
    return f"{API_URL}/{DATASET_VERSION}/{int(case_id)}.vital"


def data_dir() -> Path:
    """Per-user data folder (cache, settings). Created on demand."""
    import sys
    if os.environ.get("LOCALAPPDATA"):
        base = os.environ["LOCALAPPDATA"]
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home())
    path = Path(base) / "VitalDB_Clinical_Explorer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Network behaviour
HTTP_TIMEOUT_S = 60
CATALOGUE_MAX_AGE_DAYS = 30  # shown as "cached" with age; user can refresh anytime

# Display / export limits (points per track per plot, rows per export)
DEFAULT_MAX_PLOT_POINTS = 4000
PLOT_POINT_CHOICES = [1000, 2000, 4000, 8000, 20000]
EXPORT_WARN_ROWS = 2_000_000
EXPORT_WARN_CELLS = 10_000_000
PLOT_WARN_RAW_SAMPLES = 20_000_000

# A gap is "substantial" when the spacing between consecutive samples exceeds
# this multiple of the track's median spacing (and the absolute minimum below).
GAP_FACTOR = 5.0
GAP_MIN_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Tiny user settings store (last used folders etc.)
# ---------------------------------------------------------------------------
import json as _json


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return _json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_setting(key: str, value) -> None:
    s = load_settings()
    s[key] = value
    try:
        _settings_path().write_text(_json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass
