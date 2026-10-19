"""Synthetic test data: builds a realistic .vital file with the official vitaldb writer.

The file mimics the VitalDB naming convention (Solar8000/HR, SNUADC/ART,
Orchestra/PPF20_CE, BIS/BIS ...) with irregular sampling and a deliberate gap,
so the real read path (vitaldb.VitalFile) is exercised offline.
"""
from __future__ import annotations

import numpy as np


def make_vital_file(path, duration_s: float = 1800.0, seed: int = 0, start_unix: float = 4_000_000_000.0):
    import vitaldb

    rng = np.random.default_rng(seed)
    vf = vitaldb.VitalFile()
    t0 = start_unix

    def num_track(name, period, base, noise, unit, gap=None, jitter=0.0):
        t = np.arange(0, duration_s, period)
        if jitter:
            t = t + rng.uniform(-jitter, jitter, len(t))
        if gap:
            t = t[(t < gap[0]) | (t > gap[1])]
        vals = base + noise * rng.standard_normal(len(t))
        recs = [{"dt": t0 + float(x), "val": float(y)} for x, y in zip(t, vals)]
        vf.add_track(name, recs, srate=0, unit=unit)

    num_track("Solar8000/HR", 2.0, 70, 4, "/min", gap=(600, 700))
    num_track("Solar8000/ART_MBP", 2.0, 80, 6, "mmHg", gap=(600, 700))
    num_track("Solar8000/PLETH_SPO2", 2.0, 98, 0.5, "%")
    num_track("BIS/BIS", 1.0, 45, 3, "", jitter=0.05)
    num_track("Orchestra/PPF20_CE", 5.0, 3.5, 0.0, "ug/mL")       # constant on purpose
    num_track("Orchestra/RFTN20_CE", 5.0, 4.0, 0.2, "ng/mL")
    tr = np.arange(0, duration_s, 5.0)
    rate = 0.15 + 0.01 * rng.standard_normal(len(tr))
    rate[(tr >= 900) & (tr < 915)] = 1.5            # brief rapid-infusion surge at 15:00
    vf.add_track("Orchestra/RFTN20_RATE", [{"dt": t0 + float(x), "val": float(y)} for x, y in zip(tr, rate)],
                 srate=0, unit="mcg/kg/min")
    num_track("Primus/ETCO2", 3.0, 35, 2, "mmHg")

    # cumulative propofol volume: slow steady infusion with one abrupt 8 mL step at t = 1200 s
    tv = np.arange(0, duration_s, 5.0)
    vol = np.cumsum(np.full(len(tv), 0.05))
    vol[tv >= 1200] += 8.0
    vf.add_track("Orchestra/PPF20_VOL", [{"dt": t0 + float(x), "val": float(y)} for x, y in zip(tv, vol)],
                 srate=0, unit="mL")

    # waveform 100 Hz in two segments with a hole between them
    sr = 100.0
    def wave(name, f, unit):
        recs = []
        for seg_start, seg_end in ((0, 900), (960, duration_s)):
            n = int((seg_end - seg_start) * sr)
            tt = seg_start + np.arange(n) / sr
            vals = np.sin(2 * np.pi * f * tt) + 0.05 * rng.standard_normal(n)
            # one record per 10 s block, as in real files
            for i in range(0, n, int(10 * sr)):
                recs.append({"dt": t0 + float(tt[i]), "val": vals[i:i + int(10 * sr)].astype(np.float32)})
        vf.add_track(name, recs, srate=sr, unit=unit)
    wave("SNUADC/ECG_II", 1.2, "mV")
    wave("SNUADC/ART", 1.2, "mmHg")

    # events
    vf.add_track("EVENT", [{"dt": t0 + 300.0, "val": "Induction"}, {"dt": t0 + 900.0, "val": "Incision"},
                                {"dt": t0 + 1500.0, "val": "Fentanyl 100 mcg bolus"}], srate=0)

    vf.dtstart = t0
    vf.dtend = t0 + duration_s
    vf.to_vital(str(path))
    return str(path)
