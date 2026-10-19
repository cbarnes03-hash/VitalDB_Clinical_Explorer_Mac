"""Dose / bolus evidence within a case.

VitalDB does not store a dedicated, verified "bolus" record in the case file. Evidence of a bolus can only come from:

1. **Recorded text events** (string tracks such as ``EVENT``). Shown as recorded. Events that mention a drug word are
   flagged; their text is never interpreted.
2. **Derived candidates** from Orchestra pump tracks (exploratory, NOT confirmed administrations):
   * a *volume step*: the cumulative infused volume (``*_VOL``) rises much faster than its own typical rate;
   * a *rate surge*: the infusion rate (``*_RATE``) jumps well above its own typical rate.
   A TCI loading dose and a manual bolus cannot be told apart, so candidates are labelled accordingly.
3. **Whole-case EMR totals** (``intraop_*`` catalogue columns, e.g. fentanyl mcg). These have NO timing and are listed
   separately so they are never mistaken for timed boluses.

No unit conversion is performed; amounts are in the units given by the file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import knowledge as kn
from .data_access import KIND_NUMERIC, KIND_STRING, CancelToken, CaseStore, ProgressFn

SRC_TEXT = "Recorded event text"
SRC_VOLUME = "Derived candidate: volume step"
SRC_RATE = "Derived candidate: rate surge"

DEFAULT_SENSITIVITY = 5.0
MERGE_WITHIN_S = 15.0


@dataclass
class DoseEvent:
    time_s: float
    source: str
    track: str
    label: str
    detail: str
    confirmed: bool                     # True only for recorded text
    drug: str = ""
    amount: Optional[float] = None
    unit: str = ""
    drug_related: bool = True

    def as_row(self) -> dict:
        return {"time_s": self.time_s, "source": self.source, "track": self.track, "label": self.label,
                "drug": self.drug, "amount": self.amount, "unit": self.unit, "detail": self.detail,
                "confirmed_record": self.confirmed}


def mentions_drug(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in kn.BOLUS_DRUG_WORDS)


# --------------------------------------------------------------------------------------- derived candidates
def _clean(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(t, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    ok = np.isfinite(t) & np.isfinite(v)
    return t[ok], v[ok]


def _merge(cands: list[tuple[float, float]], within: float) -> list[tuple[float, float, int]]:
    """Merge (time, amount) candidates closer than ``within`` seconds -> (first_time, summed_amount, n)."""
    out: list[list] = []
    for t, a in cands:
        if out and t - out[-1][3] <= within:
            out[-1][1] += a
            out[-1][2] += 1
            out[-1][3] = t
        else:
            out.append([t, a, 1, t])
    return [(o[0], o[1], o[2]) for o in out]


def find_volume_steps(t: np.ndarray, v: np.ndarray, factor: float = DEFAULT_SENSITIVITY) -> list[tuple[float, float, int]]:
    """Candidate abrupt rises of a cumulative volume. Returns (time_s, volume_added, n_samples).

    A step is an increment whose implied rate (dV/dt) exceeds ``factor`` x the median implied rate of all positive
    increments. Increments across a long sampling gap are skipped because they cannot be localised in time.
    """
    t, v = _clean(t, v)
    if len(t) < 5:
        return []
    dt = np.diff(t)
    dv = np.diff(v)
    pos = (dv > 0) & (dt > 0)
    if pos.sum() < 3:
        return []
    rate = np.zeros_like(dv)
    rate[pos] = dv[pos] / dt[pos]
    base = float(np.median(rate[pos]))
    if base <= 0:
        return []
    typical_dt = float(np.median(dt[dt > 0]))
    ok = pos & (rate > factor * base) & (dt <= max(5 * typical_dt, 30.0))
    idx = np.nonzero(ok)[0]
    return _merge([(float(t[i]), float(dv[i])) for i in idx], MERGE_WITHIN_S)


def find_rate_surges(t: np.ndarray, v: np.ndarray, factor: float = DEFAULT_SENSITIVITY) -> list[tuple[float, float, int]]:
    """Candidate abrupt rises of an infusion rate. Returns (time_s, peak_rate, n_samples)."""
    t, v = _clean(t, v)
    if len(t) < 5:
        return []
    posv = v[v > 0]
    if len(posv) < 3:
        return []
    base = float(np.median(posv))
    thr = factor * base
    above = v > thr
    rising = above & ~np.concatenate([[False], above[:-1]])
    out = []
    for i in np.nonzero(rising)[0]:
        j = i
        while j + 1 < len(v) and above[j + 1]:
            j += 1
        out.append((float(t[i]), float(v[i:j + 1].max()), int(j - i + 1)))
    return _merge_rate(out)


def _merge_rate(items: list[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
    out: list[tuple[float, float, int]] = []
    for t, pk, n in items:
        if out and t - out[-1][0] <= MERGE_WITHIN_S:
            pt, ppk, pn = out[-1]
            out[-1] = (pt, max(ppk, pk), pn + n)
        else:
            out.append((t, pk, n))
    return out


# --------------------------------------------------------------------------------------- scanning a case
def candidate_tracks(store: CaseStore) -> tuple[list[str], list[str], list[str]]:
    """(text event tracks, Orchestra VOL tracks, Orchestra RATE tracks) present in the case."""
    names = list(store.header.tracks)
    text = [n for n in names if store.header.tracks[n].kind == KIND_STRING]
    vol = [n for n in names if kn.parse_orchestra(n) and n.endswith("_VOL")
           and store.header.tracks[n].kind == KIND_NUMERIC]
    rate = [n for n in names if kn.parse_orchestra(n) and n.endswith("_RATE")
            and store.header.tracks[n].kind == KIND_NUMERIC]
    return text, vol, rate


def scan_case(store: CaseStore, sensitivity: float = DEFAULT_SENSITIVITY, cancel: Optional[CancelToken] = None,
              progress: Optional[ProgressFn] = None) -> list[DoseEvent]:
    """Collect all dose-related evidence in the case (downloads the small non-waveform tracks needed)."""
    text, vol, rate = candidate_tracks(store)
    need = text + vol + rate
    raws = store.ensure_tracks(need, cancel, progress) if need else {}
    events: list[DoseEvent] = []
    for n in text:
        raw = raws[n]
        for t, v in zip(raw.t, raw.v):
            s = str(v)
            events.append(DoseEvent(float(t), SRC_TEXT, n, s, "Event text exactly as recorded; not interpreted.", True,
                                    drug_related=mentions_drug(s)))
    for n in vol:
        o = kn.parse_orchestra(n)
        unit = store.header.tracks[n].unit
        for t, amt, k in find_volume_steps(raws[n].t, raws[n].v, sensitivity):
            events.append(DoseEvent(
                t, SRC_VOLUME, n, f"{o.drug} {o.concentration}: infused volume rose by {amt:.3g} {unit or '(unit not provided)'}",
                f"Cumulative volume rose faster than {sensitivity:g}x its typical rate. Could be a manual bolus, a TCI "
                "loading dose or a pump priming/flush; the data cannot distinguish them. Not a confirmed administration.",
                False, o.drug, amt, unit))
    for n in rate:
        o = kn.parse_orchestra(n)
        unit = store.header.tracks[n].unit
        for t, pk, k in find_rate_surges(raws[n].t, raws[n].v, sensitivity):
            events.append(DoseEvent(
                t, SRC_RATE, n, f"{o.drug} {o.concentration}: infusion rate surge (peak {pk:.3g} {unit or '(unit not provided)'})",
                f"Rate exceeded {sensitivity:g}x its typical value for {k} sample(s). Could be a bolus, a TCI loading "
                "dose or a target change; the data cannot distinguish them. Not a confirmed administration.",
                False, o.drug, pk, unit))
    events.sort(key=lambda e: (e.time_s, e.source))
    return events


def to_frame(events: list[DoseEvent]) -> pd.DataFrame:
    cols = ["time_s", "source", "track", "label", "drug", "amount", "unit", "detail", "confirmed_record"]
    if not events:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([e.as_row() for e in events], columns=cols)


# --------------------------------------------------------------------------------------- EMR totals (no timing)
def emr_totals(clinical: dict) -> list[dict]:
    """Whole-case intra-operative totals present in the catalogue row. These have no timestamps."""
    out = []
    for key, (desc, unit, cls, note) in kn.EMR_PARAMS.items():
        if key not in clinical:
            continue
        val = clinical[key]
        if val is None or (isinstance(val, float) and np.isnan(val)):
            continue
        try:
            sval = f"{float(val):g}"
        except (TypeError, ValueError):
            sval = str(val)
        out.append({"parameter": key, "description": desc, "value": sval, "unit": unit,
                    "class": kn.class_label(cls), "note": note})
    return out


def timing_hint() -> str:
    return ("Whole-case totals have no timestamps, so they cannot be placed on the timeline. Timed evidence of a "
            "bolus exists only if the case has recorded text events or an infusion-pump step (below).")
