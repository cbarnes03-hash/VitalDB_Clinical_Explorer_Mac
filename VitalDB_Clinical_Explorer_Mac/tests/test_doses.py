"""Dose / bolus evidence and supervisor-dictionary tests."""
import numpy as np
import pytest

from vitaldb_explorer import doses, knowledge as kn
from vitaldb_explorer.categories import CAT_DRUG, categorise, drug_info


def test_dictionary_parsing_and_classes():
    o = kn.parse_orchestra("Orchestra/RFTN20_CE")
    assert o.drug == "Remifentanil" and o.concentration == "20 mcg/mL" and o.drug_class == "Analgesia"
    assert kn.parse_orchestra("Orchestra/DEX4_RATE").drug_class == "Sed/Analgesia"
    assert kn.parse_orchestra("Orchestra/AMD_CE") is None          # AMD has only RATE/VOL in the dictionary
    assert kn.parse_orchestra("Solar8000/HR") is None
    assert len(kn.all_orchestra_names()) == 5 * 3 + 2 * 18          # 3 TCI drugs x5, 18 infusions x2
    assert "analgesic drug (Analgesia)" in kn.class_label("Analgesia")
    assert "sedative (Sed)" in kn.class_label("Sed/Analgesia")
    assert kn.describe_device("BIS").startswith("BIS Vista")
    assert kn.describe_device("Solar8000") == ""
    assert "intraop_ftn" in kn.EMR_PARAMS and kn.EMR_PARAMS["intraop_ftn"][1] == "mcg"


def test_drug_info_verified_vs_unverified():
    v = drug_info("Orchestra/NEPI_RATE")
    assert v.verified and "Norepinephrine" in v.drug and v.drug_class.startswith("vasoactive")
    assert categorise("Orchestra/VASO_VOL") == CAT_DRUG
    u = drug_info("Other/PPF20_CE")
    assert not u.verified


def test_volume_step_detection():
    t = np.arange(0, 1800, 5.0)
    v = np.cumsum(np.full(len(t), 0.05))
    v[t >= 1200] += 8.0
    res = doses.find_volume_steps(t, v)
    assert len(res) == 1 and res[0][0] == pytest.approx(1200, abs=5) and res[0][1] == pytest.approx(8.0, abs=0.2)
    assert doses.find_volume_steps(t, np.cumsum(np.full(len(t), 0.05))) == []     # steady infusion: nothing
    assert doses.find_volume_steps(t[:3], v[:3]) == []
    # a jump across a long data gap cannot be localised -> skipped
    tg = np.concatenate([np.arange(0, 600, 5.0), np.arange(1200, 1800, 5.0)])
    vg = np.cumsum(np.full(len(tg), 0.05))
    vg[tg >= 1200] += 8.0
    assert doses.find_volume_steps(tg, vg) == []
    # counter reset (negative step) never produces a candidate
    vr = np.cumsum(np.full(len(t), 0.05)); vr[t >= 900] -= vr[t >= 900][0]
    assert doses.find_volume_steps(t, vr) == []


def test_rate_surge_detection_and_sensitivity():
    t = np.arange(0, 1800, 5.0)
    r = np.full(len(t), 0.15)
    r[(t >= 900) & (t < 915)] = 1.5
    res = doses.find_rate_surges(t, r)
    assert len(res) == 1 and res[0][0] == 900 and res[0][1] == pytest.approx(1.5) and res[0][2] == 3
    assert doses.find_rate_surges(t, r, factor=20) == []                           # less sensitive: nothing
    assert doses.find_rate_surges(t, np.full(len(t), 0.15)) == []
    assert doses.find_rate_surges(t, np.full(len(t), np.nan)) == []


def test_scan_case(store):
    events = doses.scan_case(store)
    by_src = {}
    for e in events:
        by_src.setdefault(e.source, []).append(e)
    texts = by_src[doses.SRC_TEXT]
    assert [e.label for e in texts] == ["Induction", "Incision", "Fentanyl 100 mcg bolus"]
    assert [e.drug_related for e in texts] == [False, False, True]
    assert all(e.confirmed for e in texts)
    vol = by_src[doses.SRC_VOLUME]
    assert len(vol) == 1 and vol[0].track == "Orchestra/PPF20_VOL" and vol[0].unit == "mL"
    assert vol[0].time_s == pytest.approx(1200, abs=5) and not vol[0].confirmed
    assert "Not a confirmed administration" in vol[0].detail
    surge = by_src[doses.SRC_RATE]
    assert any(e.track == "Orchestra/RFTN20_RATE" and e.time_s == pytest.approx(900, abs=6) for e in surge)
    assert [e.time_s for e in events] == sorted(e.time_s for e in events)
    frame = doses.to_frame(events)
    assert list(frame.columns)[:3] == ["time_s", "source", "track"] and len(frame) == len(events)
    assert doses.to_frame([]).empty


def test_emr_totals_have_no_time():
    clin = {"caseid": 1, "intraop_ftn": 200.0, "intraop_ppf": float("nan"), "intraop_crystalloid": 1500,
            "age": 60}
    rows = doses.emr_totals(clin)
    assert [r["parameter"] for r in rows] == ["intraop_crystalloid", "intraop_ftn"] or \
           {r["parameter"] for r in rows} == {"intraop_ftn", "intraop_crystalloid"}
    ftn = next(r for r in rows if r["parameter"] == "intraop_ftn")
    assert ftn["value"] == "200" and ftn["unit"] == "mcg" and "analgesic" in ftn["class"]
    assert "no timestamps" in doses.timing_hint()
    assert doses.emr_totals({}) == []
