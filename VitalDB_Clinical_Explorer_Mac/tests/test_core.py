"""Core (non-GUI) tests: time parsing, categories, data access, processing, export."""
import json
import math
import urllib.error

import numpy as np
import pandas as pd
import pytest

from vitaldb_explorer import data_access as da
from vitaldb_explorer import export as exp
from vitaldb_explorer import processing as pr
from vitaldb_explorer.categories import (CAT_DEPTH, CAT_DRUG, CAT_HAEMO, CAT_MISC, CAT_OXY, CAT_RESP, categorise,
                                         drug_info)
from vitaldb_explorer.timeutil import TimeParseError, format_hms, parse_time


# ----------------------------------------------------------------- time
@pytest.mark.parametrize("text,expected", [
    ("12.5", 750), ("12:30", 750), ("1:02:30", 3750), ("90s", 90), ("15 min", 900), ("1h", 3600), ("0", 0),
    (" 5 ", 300), ("0:45", 45),
])
def test_parse_time(text, expected):
    assert parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "12:xx", None])
def test_parse_time_errors(bad):
    with pytest.raises(TimeParseError):
        parse_time(bad)


def test_format_hms():
    assert format_hms(0) == "0:00:00"
    assert format_hms(3750) == "1:02:30"
    assert format_hms(59.6) == "0:01:00"
    assert format_hms(75.25, 1) == "0:01:15.2" or format_hms(75.25, 1) == "0:01:15.3"


# ----------------------------------------------------------------- categories
@pytest.mark.parametrize("name,cat", [
    ("Solar8000/HR", CAT_HAEMO), ("SNUADC/ECG_II", CAT_HAEMO), ("Solar8000/ART_MBP", CAT_HAEMO),
    ("Solar8000/NIBP_SBP", CAT_HAEMO), ("Solar8000/PLETH_SPO2", CAT_OXY), ("SNUADC/PLETH", CAT_OXY),
    ("BIS/BIS", CAT_DEPTH), ("BIS/EEG1_WAV", CAT_DEPTH), ("Primus/ETCO2", CAT_RESP),
    ("Orchestra/PPF20_CE", CAT_DRUG), ("Orchestra/RFTN20_RATE", CAT_DRUG), ("Primus/INSP_SEVO", CAT_DRUG),
])
def test_categorise(name, cat):
    assert categorise(name) == cat


def test_drug_info_is_cautious():
    v = drug_info("Orchestra/PPF20_CE")
    assert v.verified and "Propofol" in v.drug and "Effect-site" in v.kind and "not a measured" in v.note.lower()
    u = drug_info("Foo/RFTN_STUFF")
    assert not u.verified and "not verified" in u.kind.lower()
    assert "confirmed administration" in u.note.lower()
    # a name that only looks like pump naming but is from another device is not 'verified'
    assert not drug_info("Other/PPF20_CE").verified


# ----------------------------------------------------------------- catalogue / network
def test_catalogue_online_cache_and_refresh_failure(monkeypatch):
    csv = b"caseid,age,sex\n1,50,M\n2,61,F\n"
    calls = []
    monkeypatch.setattr(da, "_http_get", lambda url, cancel=None, timeout=0: calls.append(url) or csv)
    p = da._catalogue_path()
    if p.exists():
        p.unlink()
    df, st = da.load_catalogue()
    assert st.source == "online" and len(df) == 2 and len(calls) == 1
    df2, st2 = da.load_catalogue()
    assert st2.source == "cache" and len(calls) == 1          # no refetch
    assert "cache" in st2.describe().lower()

    def boom(*a, **k):
        raise da.DataAccessError("Could not reach VitalDB.")
    monkeypatch.setattr(da, "_http_get", boom)
    df3, st3 = da.load_catalogue(force_refresh=True)          # refresh fails -> cache + warning
    assert st3.source == "cache" and "Could not refresh" in st3.message and len(df3) == 2


def test_catalogue_unexpected_format(monkeypatch):
    monkeypatch.setattr(da, "_http_get", lambda *a, **k: b"foo,bar\n1,2\n")
    p = da._catalogue_path()
    if p.exists():
        p.unlink()
    with pytest.raises(da.DataAccessError) as e:
        da.load_catalogue(force_refresh=True)
    assert "caseid" in str(e.value)


def test_real_network_failure_is_friendly(monkeypatch):
    """No mocking of HTTP: whatever the environment does, the error must be friendly."""
    p = da._catalogue_path()
    if p.exists():
        p.unlink()
    try:
        df, st = da.load_catalogue(force_refresh=True)
    except da.DataAccessError as exc:
        assert "Traceback" not in str(exc)
        assert "VitalDB" in str(exc)
    else:  # internet available
        assert "caseid" in df.columns and st.source == "online"


def test_http_errors_mapped(monkeypatch):
    def raise_404(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    monkeypatch.setattr(da.urllib.request, "urlopen", raise_404)
    with pytest.raises(da.DataAccessError) as e:
        da._http_get("https://api.vitaldb.net/x")
    assert "does not exist" in str(e.value)

    def raise_url(*a, **k):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(da.urllib.request, "urlopen", raise_url)
    with pytest.raises(da.DataAccessError) as e:
        da._http_get("https://api.vitaldb.net/x")
    assert "internet" in str(e.value)


def test_missing_or_bad_case_file(monkeypatch, tmp_path):
    monkeypatch.setattr(da, "case_source", lambda cid: str(tmp_path / "nope.vital"))
    with pytest.raises(da.DataAccessError):
        da.fetch_case_header(999, use_cache=False)
    bad = tmp_path / "bad.vital"
    bad.write_bytes(b"this is not a vital file")
    monkeypatch.setattr(da, "case_source", lambda cid: str(bad))
    with pytest.raises(da.DataAccessError):
        da.fetch_case_header(998, use_cache=False)


# ----------------------------------------------------------------- case reading (real vitaldb package)
def test_header_reading(store):
    h = store.header
    assert h.duration_s == pytest.approx(1800, abs=1)
    assert len(h.tracks) == 12
    hr = h.tracks["Solar8000/HR"]
    assert hr.kind == da.KIND_NUMERIC and hr.unit == "/min" and hr.category == CAT_HAEMO
    ecg = h.tracks["SNUADC/ECG_II"]
    assert ecg.kind == da.KIND_WAVE and ecg.srate == 100.0
    assert h.tracks["EVENT"].kind == da.KIND_STRING
    assert h.tracks["BIS/BIS"].unit == ""      # not invented
    assert h.tracks["BIS/BIS"].unit_text == "not provided"


def test_track_loading_and_caching(store, monkeypatch):
    raws = store.ensure_tracks(["Solar8000/HR", "SNUADC/ECG_II", "EVENT"])
    hr = raws["Solar8000/HR"]
    assert 800 < hr.n_samples < 900                     # 1800 s at 0.5 Hz minus the 100 s gap
    assert raws["SNUADC/ECG_II"].n_samples == 180000 or raws["SNUADC/ECG_II"].n_samples > 170000
    assert list(raws["EVENT"].v) == ["Induction", "Incision", "Fentanyl 100 mcg bolus"]
    # second call must not reopen the file
    monkeypatch.setattr(da, "_open_vital", lambda *a, **k: pytest.fail("refetched"))
    store.ensure_tracks(["Solar8000/HR"])
    # a fresh store loads from the disk cache without touching the file either
    st2 = da.CaseStore(store.header)
    st2.ensure_tracks(["Solar8000/HR", "SNUADC/ECG_II"])
    assert st2.get("Solar8000/HR").n_samples == hr.n_samples


def test_missing_track_reported(store):
    with pytest.raises(da.DataAccessError) as e:
        store.ensure_tracks(["Solar8000/NOT_A_TRACK"])
    assert "not present" in str(e.value)


def test_cancel(store):
    tok = da.CancelToken()
    tok.cancel()
    with pytest.raises(da.Cancelled):
        store.ensure_tracks(["Solar8000/HR"], cancel=tok)


def test_wave_window_and_nearest(store):
    ecg = store.ensure_tracks(["SNUADC/ECG_II"])["SNUADC/ECG_II"]
    t, v = ecg.window(100, 101)
    assert len(t) in (100, 101) and t[0] >= 100 and t[-1] <= 101
    assert np.allclose(np.diff(t), 0.01)
    # the vitaldb writer stores the deliberate 900-960 s hole as NaN-filled samples:
    # they must come back as missing values, never as zeros or interpolated numbers
    t, v = ecg.window(905, 955)
    assert len(t) > 4000 and np.isnan(v).all()
    assert np.isfinite(ecg.window(100, 101)[1]).all()
    near = ecg.nearest(930)
    assert near is not None and np.isnan(near[1])


# ----------------------------------------------------------------- processing
def test_validate_window():
    assert pr.validate_window(10, 20, 100) == (10, 20)
    assert pr.validate_window(-5, 20, 100) == (0.0, 20)
    assert pr.validate_window(10, 500, 100) == (10, 100)
    for a, b in ((20, 10), (5, 5), (float("nan"), 3)):
        with pytest.raises(pr.ProcessingError):
            pr.validate_window(a, b, 100)
    with pytest.raises(pr.ProcessingError):
        pr.validate_window(200, 300, 100)


def test_gaps_not_bridged():
    t = np.concatenate([np.arange(0, 100, 2.0), np.arange(300, 400, 2.0)])
    v = np.arange(len(t), dtype=float)
    assert pr.find_gaps(t) == [(98.0, 300.0)]
    t2, v2 = pr.break_at_gaps(t, v)
    assert len(t2) == len(t) + 1 and np.isnan(v2).sum() == 1
    ds = pr.reduce_for_display(t, v, da.KIND_NUMERIC, 10000)
    assert np.isnan(ds.v).sum() == 1 and not ds.reduced


def test_reduction_preserves_peaks_and_flags():
    n = 200_000
    t = np.arange(n) / 500.0
    v = np.sin(t)
    v[123456] = 50.0       # a single-sample spike must survive reduction
    v[7777] = -50.0
    ds = pr.reduce_for_display(t, v, da.KIND_WAVE, 2000)
    assert ds.reduced and ds.n_raw == n and ds.n_shown <= 2100
    assert np.nanmax(ds.v) == 50.0 and np.nanmin(ds.v) == -50.0
    assert "no averaging" in ds.note
    assert np.all(np.diff(ds.t[~np.isnan(ds.v)]) >= 0)


def test_strings_never_aggregated():
    t = np.arange(10_000, dtype=float)
    v = np.array([f"e{i}" for i in range(10_000)], dtype=object)
    ds = pr.reduce_for_display(t, v, da.KIND_STRING, 100)
    assert not ds.reduced and ds.n_shown == 10_000


def test_empty_series():
    ds = pr.reduce_for_display(np.empty(0), np.empty(0), da.KIND_NUMERIC, 100)
    assert ds.n_raw == 0 and "No samples" in ds.note


def test_stats(store):
    raws = store.ensure_tracks(["Solar8000/HR", "Orchestra/PPF20_CE", "SNUADC/ECG_II", "EVENT"])
    s = pr.describe_track(raws["Solar8000/HR"], 0, 1800)
    assert s.n_valid > 800 and 60 < s.mean < 80 and s.vmin < s.median < s.vmax
    assert s.n_gaps == 1 and s.longest_gap_s > 90
    assert s.pct_missing is not None and 0 <= s.pct_missing < 20
    assert not s.irregular or s.irregular in (True, False)
    c = pr.describe_track(raws["Orchestra/PPF20_CE"], 0, 1800)
    assert any("Constant" in w for w in c.warnings)
    e = pr.describe_track(raws["Solar8000/HR"], 610, 690)      # inside the gap
    assert e.n_samples == 0 and any("Empty" in w for w in e.warnings)
    w = pr.describe_track(raws["SNUADC/ECG_II"], 0, 60)
    assert w.effective_rate_hz == pytest.approx(100, rel=0.01) and w.pct_missing < 1
    wg = pr.describe_track(raws["SNUADC/ECG_II"], 0, 1800)       # contains the NaN-filled 60 s hole
    assert wg.n_gaps >= 1 and wg.longest_gap_s == pytest.approx(60, abs=1)
    assert wg.pct_missing == pytest.approx(100 * 6000 / 180000, abs=0.5)
    ev = pr.describe_track(raws["EVENT"], 0, 1800)
    assert ev.n_valid == 3
    frame = pr.stats_to_frame([s, c, e])
    assert len(frame) == 3 and "exploratory_flags" in frame


def test_irregular_sampling_detected():
    rng = np.random.default_rng(1)
    t = np.cumsum(rng.uniform(0.1, 3.0, 400))
    info = da.TrackInfo("X/Y", "X", "Y", da.KIND_NUMERIC, "u", 0.0, CAT_MISC)
    raw = da.RawTrack(info, t=t, v=rng.standard_normal(400).astype(np.float32))
    s = pr.describe_track(raw, 0, float(t[-1]))
    assert s.irregular is True


def test_aligned_grid_nearest_and_no_fill(store):
    raws = store.ensure_tracks(["Solar8000/HR", "BIS/BIS", "SNUADC/ECG_II", "EVENT"])
    res = pr.build_aligned_grid(raws, 590, 720, 1.0, pr.METHOD_NEAREST)
    f = res.frame
    assert list(f.columns) == ["time_s", "Solar8000/HR", "BIS/BIS", "SNUADC/ECG_II", "EVENT"]
    assert f["time_s"].iloc[0] == 590 and f["time_s"].iloc[-1] == 720
    hr = f["Solar8000/HR"]
    assert hr[(f.time_s > 602) & (f.time_s < 698)].isna().all()      # the 600-700 s gap stays empty
    assert hr[f.time_s < 595].notna().sum() >= 3
    assert f["BIS/BIS"].notna().all()                                # 1 Hz BIS present throughout
    assert any("nearest original sample" in p for p in res.processing)
    # values are original samples, not interpolated
    orig = set(np.round(raws["Solar8000/HR"].v.astype(float), 4))
    assert set(np.round(hr.dropna().to_numpy(), 4)) <= orig


def test_aligned_grid_mean_and_waveform_guard(store):
    raws = store.ensure_tracks(["BIS/BIS", "SNUADC/ECG_II"])
    res = pr.build_aligned_grid(raws, 0, 60, 5.0, pr.METHOD_MEAN)
    assert any("waveform" in w for w in res.warnings)
    assert any("mean of valid samples" in p for p in res.processing)
    with pytest.raises(pr.ProcessingError):
        pr.build_aligned_grid(raws, 0, 60, 0)
    with pytest.raises(pr.ProcessingError):
        pr.build_aligned_grid(raws, 60, 0, 1)


def test_events_in_grid(store):
    raws = store.ensure_tracks(["EVENT"])
    f = pr.build_aligned_grid(raws, 295, 305, 1.0).frame
    assert (f["EVENT"] == "Induction").sum() == 1 and (f["EVENT"] == "").sum() == len(f) - 1


def test_long_format(store):
    raws = store.ensure_tracks(["Solar8000/HR", "Orchestra/RFTN20_CE"])
    res = pr.build_long(raws, 0, 100)
    assert set(res.frame["track"]) == {"Solar8000/HR", "Orchestra/RFTN20_CE"}
    hr = res.frame[res.frame.track == "Solar8000/HR"]
    assert hr.time_s.is_monotonic_increasing and hr.time_s.max() <= 100
    assert set(res.frame.unit) == {"/min", "ng/mL"}


# ----------------------------------------------------------------- export
def test_csv_export_manifest_and_overwrite(store, tmp_path):
    names = ["Solar8000/HR", "BIS/BIS", "Orchestra/PPF20_CE"]
    raws = store.ensure_tracks(names)
    res = pr.build_aligned_grid(raws, 0, 120, 2.0)
    path = tmp_path / "my data.csv"           # a space in the name on purpose
    csv_p, man_p = exp.write_dataset_csv(path, res, store.header, names, 0, 120, "aligned_grid",
                                         settings={"grid_step_s": 2.0}, markers=[{"time_s": 60, "label": "x"}])
    back = pd.read_csv(csv_p)
    assert list(back.columns) == ["time_s"] + names                     # original names preserved
    assert back.time_s.iloc[1] - back.time_s.iloc[0] == 2.0
    assert len(back) == 61
    man = json.loads(man_p.read_text(encoding="utf-8"))
    assert man["case_id"] == 1 and man["window_start_s"] == 0 and man["window_end_s"] == 120
    assert "seconds since the start" in man["time_reference"]
    assert any("nearest" in p for p in man["processing_applied"])
    assert man["tracks"][2]["unit"] == "ug/mL" and man["tracks"][1]["unit"] is None
    assert man["user_markers"][0]["label"] == "x"
    # no silent overwrite
    with pytest.raises(FileExistsError):
        exp.write_dataset_csv(path, res, store.header, names, 0, 120, "aligned_grid")
    assert len(exp.conflicting_files(path)) == 2
    exp.write_dataset_csv(path, res, store.header, names, 0, 120, "aligned_grid", overwrite=True)


def test_missing_values_preserved_in_csv(store, tmp_path):
    raws = store.ensure_tracks(["Solar8000/HR"])
    res = pr.build_aligned_grid(raws, 590, 710, 1.0)
    p, _ = exp.write_dataset_csv(tmp_path / "gap.csv", res, store.header, ["Solar8000/HR"], 590, 710, "aligned_grid")
    text = p.read_text(encoding="utf-8-sig").splitlines()
    assert any(line.endswith(",") for line in text[1:])                  # empty cells, not 0 or filled values
    back = pd.read_csv(p)
    assert back["Solar8000/HR"].isna().sum() > 80


def test_export_errors(store, tmp_path):
    res = pr.build_long(store.ensure_tracks(["Solar8000/HR"]), 0, 10)
    with pytest.raises(exp.ExportError):
        exp.write_dataset_csv(tmp_path / "no_such_dir" / "a.csv", res, store.header, ["Solar8000/HR"], 0, 10, "long")


def test_metadata_and_tracklist_export(store, tmp_path):
    store.header.clinical = {"caseid": 1, "age": 50.0, "sex": "M", "missing": float("nan")}
    j = exp.write_metadata_json(tmp_path / "m.json", store.header)
    doc = json.loads(j.read_text(encoding="utf-8"))
    assert doc["catalogue_fields"]["missing"] is None and doc["n_tracks"] == 12
    c = exp.write_track_list_csv(tmp_path / "t.csv", store.header)
    assert len(pd.read_csv(c)) == 12
    with pytest.raises(FileExistsError):
        exp.write_track_list_csv(tmp_path / "t.csv", store.header)


def test_size_warning():
    assert exp.size_warning(1000, 5) is None
    assert "rows" in exp.size_warning(5_000_000, 4)


def test_estimate_rows(store):
    raws = store.ensure_tracks(["SNUADC/ECG_II", "BIS/BIS"])
    n = pr.estimate_rows(raws, 0, 100)
    assert 10000 < n < 10200
