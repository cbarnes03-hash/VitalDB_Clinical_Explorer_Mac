"""VitalDB data access: catalogue, case headers and track data.

All network I/O lives here. The GUI never touches the ``vitaldb`` package or
raw HTTP directly, and never sees tracebacks: every failure is converted into
a :class:`DataAccessError` carrying a plain-English message.

Source of truth
---------------
* Case catalogue: ``https://api.vitaldb.net/cases`` (CSV, one row per case).
* Case recording: ``https://api.vitaldb.net/<dataset version>/<caseid>.vital``,
  read with the official ``vitaldb.VitalFile`` class. Track names, units,
  nominal sampling rates, types and timestamps all come from this file.

Time reference
--------------
Every time returned by this module is **seconds since the start of the
recording** (``dtstart`` in the .vital header). The absolute clock values in
VitalDB are de-identified/shifted and are only kept as ``unix_start``.
"""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

from . import config
from .categories import categorise

ProgressFn = Callable[[str], None]


class DataAccessError(Exception):
    """An error whose message is safe to show to a clinician."""


class Cancelled(Exception):
    """Raised when the user cancels a long operation."""


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self._ev.is_set():
            raise Cancelled()


def case_source(case_id: int) -> str:
    """Location of a case's .vital file. Tests replace this with a local path."""
    return config.case_url(case_id)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

@dataclass
class CatalogueStatus:
    source: str            # "online" or "cache"
    fetched_at: datetime
    path: Path
    message: str = ""      # extra warning, e.g. refresh failed so cache used

    def describe(self) -> str:
        when = self.fetched_at.strftime("%d %b %Y %H:%M")
        if self.source == "online":
            text = f"Freshly retrieved from VitalDB ({when})."
        else:
            text = f"Loaded from local cache saved {when}. Use Refresh to download again."
        if self.message:
            text += " " + self.message
        return text


def _catalogue_path() -> Path:
    return config.cache_dir() / "cases.csv"


def _http_get(url: str, cancel: Optional[CancelToken] = None, timeout: float = config.HTTP_TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "VitalDB-Clinical-Explorer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks = []
            while True:
                if cancel:
                    cancel.check()
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DataAccessError("VitalDB reports that this item does not exist (HTTP 404).") from exc
        raise DataAccessError(f"VitalDB returned an error (HTTP {exc.code}). Please try again later.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DataAccessError(describe_network_error(exc)) from exc


def describe_network_error(exc: BaseException) -> str:
    return ("Could not reach VitalDB. Please check that this computer is connected to the "
            "internet and that a firewall or proxy is not blocking https://api.vitaldb.net. "
            f"(Technical detail: {type(exc).__name__}: {exc})")


def load_catalogue(force_refresh: bool = False, cancel: Optional[CancelToken] = None,
                   progress: Optional[ProgressFn] = None) -> tuple[pd.DataFrame, CatalogueStatus]:
    """Return the case catalogue and where it came from.

    Uses the local cache unless ``force_refresh`` is set or no cache exists.
    If a refresh fails but a cache exists, the cache is returned with a warning.
    """
    path = _catalogue_path()
    if path.exists() and not force_refresh:
        try:
            df = pd.read_csv(path)
            if not df.empty:
                return df, CatalogueStatus("cache", datetime.fromtimestamp(path.stat().st_mtime), path)
        except Exception:
            pass  # corrupt cache: fall through to download

    if progress:
        progress("Downloading the VitalDB case catalogue...")
    try:
        raw = _http_get(config.CASES_URL, cancel)
        df = pd.read_csv(io.BytesIO(raw))
        if df.empty or "caseid" not in df.columns:
            raise DataAccessError("VitalDB returned a catalogue in an unexpected format (no 'caseid' column). "
                                  "The VitalDB service may have changed.")
    except Cancelled:
        raise
    except Exception as exc:
        if path.exists():
            try:
                df = pd.read_csv(path)
                msg = f"Could not refresh ({_friendly(exc)}); showing the cached copy instead."
                return df, CatalogueStatus("cache", datetime.fromtimestamp(path.stat().st_mtime), path, msg)
            except Exception:
                pass
        raise DataAccessError(_friendly(exc)) from exc

    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)
    return df, CatalogueStatus("online", datetime.now(), path)


def _friendly(exc: BaseException) -> str:
    return str(exc) if isinstance(exc, DataAccessError) else (
        f"Unexpected problem while reading the catalogue ({type(exc).__name__}: {exc}).")


# ---------------------------------------------------------------------------
# Case structures
# ---------------------------------------------------------------------------

KIND_WAVE, KIND_NUMERIC, KIND_STRING, KIND_UNKNOWN = "waveform", "numeric", "string/event", "unknown"
_TYPE_TO_KIND = {1: KIND_WAVE, 2: KIND_NUMERIC, 5: KIND_STRING}


@dataclass
class TrackInfo:
    name: str                 # exactly as returned by VitalDB (Device/Track)
    device: str
    short_name: str
    kind: str
    unit: str                 # "" if not provided
    srate: float              # nominal Hz from file header; 0 if not provided
    category: str
    first_s: Optional[float] = None   # seconds since case start; None if unknown
    last_s: Optional[float] = None

    @property
    def unit_text(self) -> str:
        return self.unit if self.unit else "not provided"

    @property
    def srate_text(self) -> str:
        if self.kind == KIND_WAVE and self.srate > 0:
            return f"{self.srate:g} Hz (nominal)"
        return "irregular / not provided" if self.srate <= 0 else f"{self.srate:g} Hz (nominal)"


@dataclass
class CaseHeader:
    case_id: int
    unix_start: float
    unix_end: float
    dgmt_min: float
    tracks: dict[str, TrackInfo]
    clinical: dict = field(default_factory=dict)   # row from the catalogue, if any
    from_cache: bool = False

    @property
    def duration_s(self) -> Optional[float]:
        d = self.unix_end - self.unix_start
        return d if d > 0 else None

    def track_names(self) -> list[str]:
        return list(self.tracks)


@dataclass
class RawTrack:
    """Original-resolution samples of one track. Never resampled in place."""
    info: TrackInfo
    # numeric/string: t (s since start, float64) and v
    t: Optional[np.ndarray] = None
    v: Optional[np.ndarray] = None
    # waveform: contiguous segments (start second, samples)
    seg_t0: Optional[np.ndarray] = None
    segments: Optional[list] = None

    @property
    def n_samples(self) -> int:
        if self.info.kind == KIND_WAVE:
            return int(sum(len(s) for s in (self.segments or [])))
        return 0 if self.t is None else int(len(self.t))

    def window(self, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
        """Original samples with t0 <= t <= t1 (times in s since start)."""
        if self.info.kind == KIND_WAVE:
            return _wave_window(self, t0, t1)
        if self.t is None or len(self.t) == 0:
            return np.empty(0), np.empty(0)
        lo, hi = np.searchsorted(self.t, [t0, t1], side="left")[0], np.searchsorted(self.t, t1, side="right")
        return self.t[lo:hi], self.v[lo:hi]

    def nearest(self, t: float) -> Optional[tuple[float, object]]:
        """Original sample closest in time to ``t`` as (time_s, value), or None."""
        if self.info.kind == KIND_WAVE:
            sr = self.info.srate
            if not self.segments or sr <= 0:
                return None
            best = None
            for s0, seg in zip(self.seg_t0, self.segments):
                s1 = s0 + len(seg) / sr
                if t < s0:
                    cand = (s0 - t, s0, seg[0])
                elif t >= s1:
                    cand = (t - s1 + 1 / sr, s0 + (len(seg) - 1) / sr, seg[-1])
                else:
                    i = min(len(seg) - 1, max(0, int(round((t - s0) * sr))))
                    cand = (abs(s0 + i / sr - t), s0 + i / sr, seg[i])
                if best is None or cand[0] < best[0]:
                    best = cand
                if cand[0] == 0 or (best[0] < 1 / sr):
                    break
            return (float(best[1]), float(best[2])) if best else None
        if self.t is None or len(self.t) == 0:
            return None
        i = int(np.searchsorted(self.t, t))
        cands = [j for j in (i - 1, i) if 0 <= j < len(self.t)]
        j = min(cands, key=lambda k: abs(self.t[k] - t))
        v = self.v[j]
        return float(self.t[j]), (str(v) if self.info.kind == KIND_STRING else float(v))

    def span(self) -> Optional[tuple[float, float]]:
        if self.info.kind == KIND_WAVE:
            if not self.segments:
                return None
            sr = self.info.srate
            starts = self.seg_t0
            ends = [s0 + len(seg) / sr for s0, seg in zip(self.seg_t0, self.segments)]
            return float(min(starts)), float(max(ends))
        if self.t is None or len(self.t) == 0:
            return None
        return float(self.t[0]), float(self.t[-1])


def _wave_window(raw: RawTrack, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
    sr = raw.info.srate
    if not raw.segments or sr <= 0:
        return np.empty(0), np.empty(0)
    ts, vs = [], []
    for s0, seg in zip(raw.seg_t0, raw.segments):
        n = len(seg)
        s1 = s0 + n / sr
        if s1 < t0 or s0 > t1:
            continue
        i0 = max(0, int(np.ceil((t0 - s0) * sr)))
        i1 = min(n, int(np.floor((t1 - s0) * sr)) + 1)
        if i1 <= i0:
            continue
        ts.append(s0 + np.arange(i0, i1) / sr)
        vs.append(seg[i0:i1])
    if not ts:
        return np.empty(0), np.empty(0)
    return np.concatenate(ts), np.concatenate(vs)


# ---------------------------------------------------------------------------
# Opening cases through the official vitaldb package
# ---------------------------------------------------------------------------

def _import_vitaldb():
    try:
        import vitaldb  # noqa: WPS433
        return vitaldb
    except Exception as exc:  # pragma: no cover - environment problem
        raise DataAccessError(
            "The 'vitaldb' package is not installed correctly. Please run install_windows.bat again."
        ) from exc


def _open_vital(source: str, track_names: Optional[list[str]] = None, header_only: bool = False):
    vitaldb = _import_vitaldb()
    try:
        vf = vitaldb.VitalFile(source, track_names, header_only=header_only)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            raise DataAccessError("This case is not available from VitalDB (it may not exist in the "
                                  "public dataset).") from exc
        raise DataAccessError(f"VitalDB returned an error (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise DataAccessError(describe_network_error(exc)) from exc
    except FileNotFoundError as exc:
        raise DataAccessError("The case file could not be found.") from exc
    except Exception as exc:
        raise DataAccessError(f"The case file could not be read ({type(exc).__name__}: {exc}). "
                              "VitalDB may have changed its file format.") from exc
    if not getattr(vf, "trks", None):
        raise DataAccessError("The case file was retrieved but contains no tracks "
                              "(empty, truncated or an unsupported format).")
    return vf


def _trackinfo_from_vf(vf, dtname: str) -> TrackInfo:
    trk = vf.trks[dtname]
    kind = _TYPE_TO_KIND.get(int(trk.type), KIND_UNKNOWN)
    device = getattr(trk, "dname", "") or ""
    short = dtname.split("/", 1)[-1]
    return TrackInfo(
        name=dtname, device=device, short_name=short, kind=kind,
        unit=(trk.unit or "").strip(), srate=float(trk.srate or 0.0),
        category=categorise(dtname),
    )


def header_from_vf(case_id: int, vf, clinical: Optional[dict] = None) -> CaseHeader:
    tracks = {name: _trackinfo_from_vf(vf, name) for name in vf.trks}
    return CaseHeader(case_id=int(case_id), unix_start=float(vf.dtstart), unix_end=float(vf.dtend),
                      dgmt_min=float(vf.dgmt), tracks=tracks, clinical=clinical or {})


def _header_cache_path(case_id: int) -> Path:
    return config.cache_dir() / f"case_{int(case_id)}_header.json"


def fetch_case_header(case_id: int, use_cache: bool = True, progress: Optional[ProgressFn] = None,
                      clinical: Optional[dict] = None) -> CaseHeader:
    """Read the track list, units, rates and duration of a case."""
    cp = _header_cache_path(case_id)
    if use_cache and cp.exists():
        try:
            h = _header_from_json(json.loads(cp.read_text(encoding="utf-8")))
            h.clinical = clinical or {}
            h.from_cache = True
            return h
        except Exception:
            pass
    if progress:
        progress(f"Retrieving the track list for case {case_id}...")
    vf = _open_vital(case_source(case_id), header_only=True)
    h = header_from_vf(case_id, vf, clinical)
    try:
        cp.write_text(json.dumps(_header_to_json(h)), encoding="utf-8")
    except OSError:
        pass
    return h


def _header_to_json(h: CaseHeader) -> dict:
    return {"case_id": h.case_id, "unix_start": h.unix_start, "unix_end": h.unix_end,
            "dgmt_min": h.dgmt_min, "tracks": [t.__dict__ for t in h.tracks.values()]}


def _header_from_json(d: dict) -> CaseHeader:
    tracks = {t["name"]: TrackInfo(**t) for t in d["tracks"]}
    return CaseHeader(d["case_id"], d["unix_start"], d["unix_end"], d["dgmt_min"], tracks)


# ---------------------------------------------------------------------------
# Track data with memory + disk caching
# ---------------------------------------------------------------------------

def _raw_from_vf(vf, info: TrackInfo, unix_start: float) -> RawTrack:
    trk = vf.trks[info.name]
    recs = trk.recs
    if info.kind == KIND_WAVE:
        seg_t0, segs = [], []
        apply_gain = trk.fmt > 2
        for rec in recs:
            arr = np.asarray(rec["val"], dtype=np.float32)
            if apply_gain:
                arr = arr * np.float32(trk.gain) + np.float32(trk.offset)
            arr = np.where(np.isinf(arr) | (arr > 4e9), np.nan, arr).astype(np.float32)
            seg_t0.append(rec["dt"] - unix_start)
            segs.append(arr)
        order = np.argsort(seg_t0) if seg_t0 else []
        seg_t0 = np.asarray(seg_t0, dtype=np.float64)[order] if len(seg_t0) else np.empty(0)
        segs = [segs[i] for i in order]
        return RawTrack(info=info, seg_t0=seg_t0, segments=segs)
    if info.kind == KIND_NUMERIC:
        t = np.fromiter((r["dt"] for r in recs), dtype=np.float64, count=len(recs)) - unix_start
        v = np.fromiter((r["val"] for r in recs), dtype=np.float64, count=len(recs))
        # NB: the official vitaldb package applies gain/offset to waveforms only.
        v[np.isinf(v) | (v > 4e9)] = np.nan
        order = np.argsort(t, kind="stable")
        return RawTrack(info=info, t=t[order], v=v[order].astype(np.float32))
    # string / event
    t = np.fromiter((r["dt"] for r in recs), dtype=np.float64, count=len(recs)) - unix_start
    v = np.array([str(r["val"]) for r in recs], dtype=object)
    order = np.argsort(t, kind="stable")
    return RawTrack(info=info, t=t[order], v=v[order])


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _track_cache_path(case_id: int, name: str) -> Path:
    return config.cache_dir() / f"case_{int(case_id)}_{_safe(name)}.npz"


def _save_track(case_id: int, raw: RawTrack) -> None:
    p = _track_cache_path(case_id, raw.info.name)
    try:
        if raw.info.kind == KIND_WAVE:
            lens = np.array([len(s) for s in raw.segments], dtype=np.int64)
            data = np.concatenate(raw.segments) if raw.segments else np.empty(0, dtype=np.float32)
            np.savez_compressed(p, kind="wave", seg_t0=raw.seg_t0, lens=lens, data=data)
        elif raw.info.kind == KIND_NUMERIC:
            np.savez_compressed(p, kind="num", t=raw.t, v=raw.v)
        else:
            np.savez_compressed(p, kind="str", t=raw.t, v=raw.v.astype(str))
    except OSError:
        pass


def _load_track_cache(case_id: int, info: TrackInfo) -> Optional[RawTrack]:
    p = _track_cache_path(case_id, info.name)
    if not p.exists():
        return None
    try:
        z = np.load(p, allow_pickle=False)
        kind = str(z["kind"])
        if kind == "wave":
            lens = z["lens"]
            data = z["data"]
            segs, pos = [], 0
            for n in lens:
                segs.append(data[pos:pos + n])
                pos += n
            return RawTrack(info=info, seg_t0=z["seg_t0"], segments=segs)
        if kind == "num":
            return RawTrack(info=info, t=z["t"], v=z["v"])
        return RawTrack(info=info, t=z["t"], v=z["v"].astype(object))
    except Exception:
        return None


class CaseStore:
    """Holds the header and already-fetched tracks of one open case."""

    def __init__(self, header: CaseHeader):
        self.header = header
        self._tracks: dict[str, RawTrack] = {}
        self._lock = threading.RLock()

    @property
    def case_id(self) -> int:
        return self.header.case_id

    def has_loaded(self, name: str) -> bool:
        with self._lock:
            return name in self._tracks

    def loaded_names(self) -> list[str]:
        with self._lock:
            return list(self._tracks)

    def get(self, name: str) -> Optional[RawTrack]:
        with self._lock:
            return self._tracks.get(name)

    def ensure_tracks(self, names: Iterable[str], cancel: Optional[CancelToken] = None,
                      progress: Optional[ProgressFn] = None) -> dict[str, RawTrack]:
        """Make sure the given tracks are in memory, fetching only what is missing."""
        names = list(dict.fromkeys(names))
        missing_in_case = [n for n in names if n not in self.header.tracks]
        if missing_in_case:
            raise DataAccessError("These tracks are not present in case "
                                  f"{self.case_id}: {', '.join(missing_in_case)}")
        need = []
        for n in names:
            with self._lock:
                if n in self._tracks:
                    continue
            cached = _load_track_cache(self.case_id, self.header.tracks[n])
            if cached is not None:
                with self._lock:
                    self._tracks[n] = cached
            else:
                need.append(n)
        if need:
            if cancel:
                cancel.check()
            if progress:
                progress(f"Downloading {len(need)} track(s) for case {self.case_id} "
                         "(waveforms can take a minute)...")
            vf = _open_vital(case_source(self.case_id), need)
            if cancel:
                cancel.check()
            got = set(vf.trks)
            for n in need:
                if n not in got:
                    raise DataAccessError(f"Track '{n}' could not be retrieved from the case file.")
                raw = _raw_from_vf(vf, self.header.tracks[n], self.header.unix_start)
                sp = raw.span()
                if sp:
                    self.header.tracks[n].first_s, self.header.tracks[n].last_s = sp
                _save_track(self.case_id, raw)
                with self._lock:
                    self._tracks[n] = raw
        return {n: self._tracks[n] for n in names}

    def events(self) -> list[tuple[float, str, str]]:
        """All loaded string/event tracks as (time_s, track, text)."""
        out = []
        with self._lock:
            for name, raw in self._tracks.items():
                if raw.info.kind == KIND_STRING and raw.t is not None:
                    out.extend((float(t), name, str(v)) for t, v in zip(raw.t, raw.v))
        return sorted(out)


def clear_cache() -> int:
    n = 0
    for p in config.cache_dir().glob("*"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
