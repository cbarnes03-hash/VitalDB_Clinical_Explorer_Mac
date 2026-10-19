"""Signal processing helpers: display reduction, gap handling, statistics, alignment.

Principles
----------
* Raw data are never modified. Display reduction produces a *copy* for plotting.
* Reduction of numeric/waveform tracks uses min/max decimation (each bin keeps
  its smallest and largest sample, in time order) so peaks are not averaged
  away. String/event tracks are never aggregated.
* Missing values stay missing; lines are broken across substantial gaps.
* Any resampling used for an aligned export is described in the manifest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import config
from .data_access import KIND_NUMERIC, KIND_STRING, KIND_WAVE, RawTrack


class ProcessingError(ValueError):
    """Invalid user input (e.g. end before start). Message is clinician-safe."""


def validate_window(t0: float, t1: float, duration: Optional[float] = None) -> tuple[float, float]:
    if t0 is None or t1 is None or not np.isfinite(t0) or not np.isfinite(t1):
        raise ProcessingError("Please enter valid start and end times.")
    if t1 <= t0:
        raise ProcessingError("The end time must be later than the start time.")
    if t0 < 0:
        t0 = 0.0
    if duration is not None:
        if t0 >= duration:
            raise ProcessingError("The start time is after the end of the recording "
                                  f"({duration / 60:.1f} min).")
        t1 = min(t1, duration)
    return float(t0), float(t1)


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------

def gap_threshold(t: np.ndarray) -> float:
    """Spacing above which consecutive samples are treated as a gap."""
    if len(t) < 3:
        return config.GAP_MIN_SECONDS
    d = np.diff(t)
    d = d[d > 0]
    if len(d) == 0:
        return config.GAP_MIN_SECONDS
    return max(config.GAP_FACTOR * float(np.median(d)), config.GAP_MIN_SECONDS)


def find_gaps(t: np.ndarray) -> list[tuple[float, float]]:
    """Start/end of substantial gaps between consecutive samples."""
    if len(t) < 2:
        return []
    thr = gap_threshold(t)
    d = np.diff(t)
    idx = np.nonzero(d > thr)[0]
    return [(float(t[i]), float(t[i + 1])) for i in idx]


def break_at_gaps(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Insert NaN between samples separated by a substantial gap so lines break."""
    if len(t) < 2:
        return t, v
    thr = gap_threshold(t)
    idx = np.nonzero(np.diff(t) > thr)[0]
    if len(idx) == 0:
        return t, v
    t_out = np.insert(t.astype(np.float64), idx + 1, [(t[i] + t[i + 1]) / 2 for i in idx])
    v_out = np.insert(v.astype(np.float64), idx + 1, np.nan)
    return t_out, v_out


def find_missing_runs(t: np.ndarray, v: np.ndarray, min_duration_s: float) -> list[tuple[float, float]]:
    """Runs of consecutive missing (NaN) values lasting longer than ``min_duration_s``.

    VitalDB waveform records can be stored contiguously with NaN where nothing was recorded, so a
    gap may appear as missing *values* rather than as a jump in timestamps.
    """
    if len(t) < 2:
        return []
    miss = ~np.isfinite(np.asarray(v, dtype=np.float64))
    if not miss.any():
        return []
    padded = np.concatenate([[False], miss, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[0::2], edges[1::2] - 1       # inclusive index range of each run
    step = float(np.median(np.diff(t))) if len(t) > 2 else 0.0
    out = []
    for a, b in zip(starts, ends):
        dur = float(t[b] - t[a]) + step
        if dur > min_duration_s:
            out.append((float(t[a]), float(t[b]) + step))
    return out


# ---------------------------------------------------------------------------
# Display reduction
# ---------------------------------------------------------------------------

@dataclass
class DisplaySeries:
    t: np.ndarray
    v: np.ndarray
    n_raw: int
    n_shown: int
    reduced: bool
    note: str
    kind: str


def reduce_for_display(t: np.ndarray, v: np.ndarray, kind: str,
                       max_points: int = config.DEFAULT_MAX_PLOT_POINTS) -> DisplaySeries:
    """Return a plot-ready series, using min/max decimation only if too long."""
    n = len(t)
    if kind == KIND_STRING:
        return DisplaySeries(t, v, n, n, False, "Event/text track: shown as recorded, never aggregated.", kind)
    if n == 0:
        return DisplaySeries(t, v.astype(float) if len(v) else v, 0, 0, False, "No samples in window.", kind)
    v = np.asarray(v, dtype=np.float64)
    if n <= max_points:
        tt, vv = break_at_gaps(np.asarray(t, dtype=np.float64), v)
        return DisplaySeries(tt, vv, n, n, False, "All original samples shown.", kind)
    nbins = max(1, max_points // 2)
    edges = np.linspace(0, n, nbins + 1).astype(np.int64)
    out_t = np.empty(nbins * 2)
    out_v = np.empty(nbins * 2)
    k = 0
    for b in range(nbins):
        lo, hi = edges[b], edges[b + 1]
        if hi <= lo:
            continue
        seg = v[lo:hi]
        if np.all(np.isnan(seg)):
            out_t[k], out_v[k] = t[lo], np.nan
            k += 1
            continue
        imin, imax = int(np.nanargmin(seg)), int(np.nanargmax(seg))
        first, second = (imin, imax) if imin <= imax else (imax, imin)
        out_t[k], out_v[k] = t[lo + first], seg[first]
        k += 1
        if second != first:
            out_t[k], out_v[k] = t[lo + second], seg[second]
            k += 1
    out_t, out_v = out_t[:k], out_v[:k]
    # break at gaps in the *original* spacing, using the original threshold
    thr = gap_threshold(np.asarray(t))
    idx = np.nonzero(np.diff(out_t) > thr)[0]
    if len(idx):
        out_t = np.insert(out_t, idx + 1, [(out_t[i] + out_t[i + 1]) / 2 for i in idx])
        out_v = np.insert(out_v, idx + 1, np.nan)
    note = (f"Display only: {n:,} original samples reduced to {k:,} points by min/max decimation "
            "(peaks preserved, no averaging). Exports use the original samples.")
    return DisplaySeries(out_t, out_v, n, k, True, note, kind)


# ---------------------------------------------------------------------------
# Descriptive statistics / exploratory checks
# ---------------------------------------------------------------------------

@dataclass
class TrackStats:
    name: str
    kind: str
    unit: str
    n_samples: int = 0
    n_valid: int = 0
    n_missing_values: int = 0
    pct_missing: Optional[float] = None
    pct_missing_basis: str = ""
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    mean: Optional[float] = None
    median: Optional[float] = None
    std: Optional[float] = None
    p5: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    p95: Optional[float] = None
    median_interval_s: Optional[float] = None
    effective_rate_hz: Optional[float] = None
    n_gaps: int = 0
    longest_gap_s: Optional[float] = None
    irregular: Optional[bool] = None
    n_unique_values: Optional[int] = None
    warnings: list[str] = field(default_factory=list)

    def as_rows(self) -> list[tuple[str, str]]:
        def f(x, d=3):
            return "n/a" if x is None else f"{x:.{d}g}" if isinstance(x, float) else str(x)
        rows = [
            ("Track", self.name), ("Type", self.kind), ("Unit", self.unit or "not provided"),
            ("Samples in window", f"{self.n_samples:,}"),
            ("Valid (non-missing) samples", f"{self.n_valid:,}"),
            ("Missing", "n/a" if self.pct_missing is None else f"{self.pct_missing:.1f}% ({self.pct_missing_basis})"),
        ]
        if self.kind != KIND_STRING:
            rows += [("Minimum", f(self.vmin)), ("Maximum", f(self.vmax)), ("Mean", f(self.mean)),
                     ("Median", f(self.median)), ("Std. deviation", f(self.std)),
                     ("5th / 25th percentile", f"{f(self.p5)} / {f(self.p25)}"),
                     ("75th / 95th percentile", f"{f(self.p75)} / {f(self.p95)}")]
        rows += [("Median sample interval", "n/a" if self.median_interval_s is None else f"{self.median_interval_s:.4g} s"),
                 ("Effective sampling rate", "n/a" if self.effective_rate_hz is None else f"{self.effective_rate_hz:.4g} Hz"),
                 ("Substantial gaps", f"{self.n_gaps}" + ("" if self.longest_gap_s is None
                                                          else f" (longest {self.longest_gap_s:.1f} s)")),
                 ("Irregular sampling", "n/a" if self.irregular is None else ("yes" if self.irregular else "no"))]
        return rows


def describe_track(raw: RawTrack, t0: float, t1: float) -> TrackStats:
    """Exploratory descriptive statistics for one track over [t0, t1] seconds."""
    info = raw.info
    t, v = raw.window(t0, t1)
    st = TrackStats(name=info.name, kind=info.kind, unit=info.unit, n_samples=int(len(t)))
    span = t1 - t0
    if len(t) == 0:
        st.warnings.append("Empty: no samples in this interval.")
        st.pct_missing, st.pct_missing_basis = (100.0, "no samples") if info.kind != KIND_STRING else (None, "")
        return st

    if info.kind == KIND_STRING:
        st.n_valid = int(len(t))
        st.n_unique_values = int(len(set(v.tolist())))
        return st

    vf = np.asarray(v, dtype=np.float64)
    finite = np.isfinite(vf)
    st.n_valid = int(finite.sum())
    st.n_missing_values = int((~finite).sum())
    if st.n_valid:
        x = vf[finite]
        st.vmin, st.vmax = float(x.min()), float(x.max())
        st.mean, st.median, st.std = float(x.mean()), float(np.median(x)), float(x.std())
        st.p5, st.p25, st.p75, st.p95 = (float(q) for q in np.percentile(x, [5, 25, 75, 95]))
        st.n_unique_values = int(len(np.unique(x)))
    # sampling
    if len(t) >= 2:
        d = np.diff(t)
        d = d[d > 0]
        if len(d):
            med = float(np.median(d))
            st.median_interval_s = med
            st.effective_rate_hz = 1.0 / med if med > 0 else None
            gaps = find_gaps(t)
            thr = max(config.GAP_FACTOR * med, config.GAP_MIN_SECONDS)
            gaps += find_missing_runs(t, vf, thr)
            st.n_gaps = len(gaps)
            st.longest_gap_s = max((b - a for a, b in gaps), default=None)
            st.irregular = bool(np.percentile(d, 95) > 1.5 * med or np.percentile(d, 5) < 0.5 * med)
    # missing percentage: expected samples from nominal rate if known, else from median interval
    expected = None
    if info.kind == KIND_WAVE and info.srate > 0:
        expected, basis = span * info.srate, f"vs {info.srate:g} Hz nominal rate"
    elif st.median_interval_s:
        expected, basis = span / st.median_interval_s, "vs observed median interval (estimate)"
    if expected and expected > 0:
        st.pct_missing = float(max(0.0, min(100.0, 100.0 * (1 - st.n_valid / expected))))
        st.pct_missing_basis = basis
    # exploratory warnings only (no physiological thresholds)
    if st.n_valid == 0:
        st.warnings.append("All values missing in this interval.")
    elif st.n_unique_values == 1:
        st.warnings.append("Constant: every valid value is identical.")
    if st.pct_missing is not None and st.pct_missing > 50:
        st.warnings.append("Sparse: more than half of the expected samples are missing.")
    if st.n_gaps:
        st.warnings.append(f"{st.n_gaps} substantial gap(s) in sampling.")
    if st.n_valid and 0 < st.n_valid < 10:
        st.warnings.append("Very few samples; summaries are unreliable.")
    return st


def stats_to_frame(stats: list[TrackStats]) -> pd.DataFrame:
    rows = []
    for s in stats:
        rows.append({
            "track": s.name, "type": s.kind, "unit": s.unit, "n_samples": s.n_samples, "n_valid": s.n_valid,
            "pct_missing": s.pct_missing, "pct_missing_basis": s.pct_missing_basis,
            "min": s.vmin, "max": s.vmax, "mean": s.mean, "median": s.median, "std": s.std,
            "median_interval_s": s.median_interval_s, "effective_rate_hz": s.effective_rate_hz,
            "n_gaps": s.n_gaps, "longest_gap_s": s.longest_gap_s,
            "exploratory_flags": "; ".join(s.warnings),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Aligned export
# ---------------------------------------------------------------------------

METHOD_NEAREST = "nearest"
METHOD_MEAN = "mean"
METHOD_LAST = "last"


@dataclass
class AlignedResult:
    frame: pd.DataFrame
    processing: list[str]      # human-readable description of what was done
    warnings: list[str]


def build_long(raws: dict[str, RawTrack], t0: float, t1: float) -> AlignedResult:
    """Original samples of all tracks, stacked (track, time_s, value). No resampling."""
    parts = []
    for name, raw in raws.items():
        t, v = raw.window(t0, t1)
        parts.append(pd.DataFrame({"track": name, "unit": raw.info.unit, "time_s": t, "value": v}))
    frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["track", "unit", "time_s", "value"])
    return AlignedResult(frame, ["Long format: original samples of every track, no resampling, "
                                 "no interpolation, no filling of missing values."], [])


def build_aligned_grid(raws: dict[str, RawTrack], t0: float, t1: float, step_s: float,
                       numeric_method: str = METHOD_NEAREST) -> AlignedResult:
    """Wide table on a regular time grid (columns = original track names).

    * ``nearest``: each grid time takes the single original sample closest in time,
      but only if one lies within half a grid step (otherwise missing). No averaging.
    * ``mean``: mean of valid samples inside each grid bin [t, t+step). Only offered for
      numeric trend tracks; waveforms always use ``nearest``.
    * string/event tracks: the last event in each bin (text), otherwise empty.

    No interpolation or forward-filling is ever applied.
    """
    if step_s <= 0:
        raise ProcessingError("The time step must be greater than zero.")
    t0, t1 = validate_window(t0, t1)
    n = int(np.floor((t1 - t0) / step_s)) + 1
    if n <= 0:
        raise ProcessingError("The chosen interval is shorter than one time step.")
    grid = t0 + np.arange(n) * step_s
    cols: dict[str, np.ndarray] = {"time_s": grid}
    proc: list[str] = [f"Regular grid from {t0:g} s to {t1:g} s with step {step_s:g} s ({n:,} rows)."]
    warns: list[str] = []
    for name, raw in raws.items():
        t, v = raw.window(t0 - step_s, t1 + step_s)
        kind = raw.info.kind
        if kind == KIND_STRING:
            col = np.full(n, "", dtype=object)
            if len(t):
                idx = np.floor((t - t0) / step_s).astype(int)
                for i, val in zip(idx, v):
                    if 0 <= i < n:
                        col[i] = str(val)
            cols[name] = col
            proc.append(f"{name}: event text, last event per bin; no aggregation of meaning.")
            continue
        col = np.full(n, np.nan)
        if len(t):
            vf = np.asarray(v, dtype=np.float64)
            method = numeric_method
            if kind == KIND_WAVE and method == METHOD_MEAN:
                method = METHOD_NEAREST
                warns.append(f"{name}: waveform, so nearest-sample selection was used instead of averaging.")
            if method == METHOD_MEAN:
                idx = np.floor((t - t0) / step_s).astype(int)
                ok = (idx >= 0) & (idx < n) & np.isfinite(vf)
                sums = np.bincount(idx[ok], weights=vf[ok], minlength=n)[:n]
                cnt = np.bincount(idx[ok], minlength=n)[:n]
                with np.errstate(invalid="ignore", divide="ignore"):
                    col = np.where(cnt > 0, sums / np.maximum(cnt, 1), np.nan)
                proc.append(f"{name}: mean of valid samples per {step_s:g} s bin (bin starts at the row time).")
            else:
                pos = np.searchsorted(t, grid)
                lo = np.clip(pos - 1, 0, len(t) - 1)
                hi = np.clip(pos, 0, len(t) - 1)
                choose_hi = np.abs(t[hi] - grid) < np.abs(t[lo] - grid)
                near = np.where(choose_hi, hi, lo)
                dist = np.abs(t[near] - grid)
                val = vf[near]
                col = np.where(dist <= step_s / 2 + 1e-9, val, np.nan)
                proc.append(f"{name}: nearest original sample within +/-{step_s / 2:g} s (no averaging, "
                            "no interpolation).")
        cols[name] = col
    return AlignedResult(pd.DataFrame(cols), proc, warns)


def estimate_rows(raws: dict[str, RawTrack], t0: float, t1: float) -> int:
    """Total number of original samples that fall in the window (for size warnings)."""
    total = 0
    for raw in raws.values():
        if raw.info.kind == KIND_WAVE and raw.info.srate > 0:
            total += int((t1 - t0) * raw.info.srate)
        elif raw.t is not None:
            lo, hi = np.searchsorted(raw.t, [t0, t1])
            total += int(hi - lo)
    return total
