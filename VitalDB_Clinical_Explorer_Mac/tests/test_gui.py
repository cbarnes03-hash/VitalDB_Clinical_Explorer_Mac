"""GUI tests: build the real window offscreen and drive it with a synthetic case."""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog

from vitaldb_explorer import data_access as da
from vitaldb_explorer.gui import dose_page as dose_page_mod
from vitaldb_explorer.gui import (cases_page, common, drug_page, event_page, export_page, overview_page,
                                  plot_panel, tracks_page)


def pump(cond=lambda: False, timeout=20.0):
    app = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return cond()


class Dialogs:
    def __init__(self):
        self.infos, self.warns, self.confirms = [], [], []
        self.confirm_answer = True


@pytest.fixture()
def dialogs(monkeypatch):
    d = Dialogs()
    mods = [cases_page, dose_page_mod, drug_page, event_page, export_page, overview_page, plot_panel, tracks_page, common]
    for m in mods:
        if hasattr(m, "info"):
            monkeypatch.setattr(m, "info", lambda parent, title, text, _d=d: _d.infos.append((title, text)))
        if hasattr(m, "warn"):
            monkeypatch.setattr(m, "warn", lambda parent, title, text, _d=d: _d.warns.append((title, text)))
        if hasattr(m, "confirm"):
            monkeypatch.setattr(m, "confirm", lambda parent, title, text, yes_text="Continue", _d=d:
                                (_d.confirms.append((title, text)), _d.confirm_answer)[1])
    shown = []
    monkeypatch.setattr(common.TableDialog, "exec", lambda self: shown.append(self) or 0)
    d.tables = shown
    return d


@pytest.fixture()
def win(offline_case, monkeypatch, dialogs):
    csv = (b"caseid,age,sex,department,anetype\n" + b"\n".join(
        f"{i},{40 + i},{'M' if i % 2 else 'F'},Surgery,General".encode() for i in range(1, 31)) + b"\n")
    monkeypatch.setattr(da, "_http_get", lambda url, cancel=None, timeout=0: csv)
    p = da._catalogue_path()
    if p.exists():
        p.unlink()
    from vitaldb_explorer.app import create_window
    app, w = create_window()
    w.tables = dialogs.tables
    w.show()
    w.cases.load_catalogue()
    assert pump(lambda: w.cases.model.rowCount() == 30 and w.cases.proxy.rowCount() == 30)
    yield w
    from vitaldb_explorer.gui.workers import wait_all
    wait_all()
    w.close()


def open_case(w, cid=7):
    w.cases.open_case(cid)
    assert pump(lambda: w.session.store is not None)
    return w.session.store


def plot_and_wait(panel):
    n = len(panel.figure.axes)
    panel.plot()
    assert pump(lambda: panel.plot_btn.isEnabled() and bool(panel._lines or panel._axes_by_track))
    pump(timeout=0.3)


def test_startup_state(win):
    assert win.current_key() == "cases" and len(win._stack_index) == 11
    assert not win.nav_enabled("overview") and win.nav_enabled("guide")          # needs a case
    assert win.findChild(common.QLabel, "bannerText").text().startswith("Research and exploratory data viewer only")
    assert "Freshly retrieved" in win.cases.status.text()
    assert "30 cases" in win.cases.status.text()


def test_catalogue_search_filter_sort(win):
    c = win.cases
    c.search.setText("12")
    assert pump(lambda: c.proxy.rowCount() == 1)                    # only the row with 12 in some column... id 12
    c.search.setText("")
    ncol = list(c.df.columns).index("age")
    c.num_combo.setCurrentIndex(c.num_combo.findData(ncol))
    c.min_edit.setText("60")
    c.max_edit.setText("65")
    assert c.proxy.rowCount() == 6
    c._clear_filters()
    assert c.proxy.rowCount() == 30
    c.col_combo.setCurrentIndex(c.col_combo.findData(list(c.df.columns).index("sex")))
    c.search.setText("F")
    assert c.proxy.rowCount() == 15
    c._clear_filters()
    c.table.sortByColumn(list(c.df.columns).index("age"), Qt.DescendingOrder)
    top = c.proxy.data(c.proxy.index(0, 0), Qt.UserRole)
    assert top == 30                                                  # numeric (not text) sorting


def test_open_case_and_overview(win):
    w = win
    w.cases.table.selectRow(2)
    assert w.cases._selected_case_id() == 3
    w.cases.open_selected()
    assert pump(lambda: w.session.store is not None)
    assert w.session.store.case_id == 3
    assert w.current_key() == "overview" and w.nav_enabled("tracks")
    html = w.overview.view.toHtml()
    assert "Case 3" in html and "Invasive BP" in html
    assert "Heart rate" in html and "present" in html
    assert "department" in html                                    # catalogue metadata exposed by the dataset
    assert "Desflurane" not in html and "diagnos" not in html.lower()  # nothing invented
    assert "not present" in html                                    # e.g. no desflurane/sevo in synthetic case
    w.overview.scan_coverage()
    assert pump(lambda: bool(w.overview.coverage))
    assert "valid samples" in " ".join(w.overview.coverage.values())


def test_open_by_id_and_failures(win, monkeypatch, dialogs):
    win.cases.id_edit.setText("abc")
    win.cases.open_by_id()
    assert dialogs.warns and "whole-number" in dialogs.warns[-1][1]
    # unavailable case -> friendly message, no traceback
    monkeypatch.setattr(da, "case_source", lambda cid: "/nonexistent/x.vital")
    win.cases.open_case(5)
    assert pump(lambda: any("Could not open case 5" in t for t, _ in dialogs.warns))
    assert "Traceback" not in dialogs.warns[-1][1]
    assert win.session.store is None
    # catalogue failure falls back to cache with a warning
    monkeypatch.setattr(da, "_http_get", lambda *a, **k: (_ for _ in ()).throw(da.DataAccessError("offline")))
    win.cases.load_catalogue(force=True)
    assert pump(lambda: "Could not refresh" in win.cases.status.text())


def test_track_browser(win):
    store = open_case(win)
    t = win.tracks
    assert t.tree.topLevelItemCount() >= 4
    t.search.setText("rftn")
    visible = [t.tree.topLevelItem(i).child(j).text(0) for i in range(t.tree.topLevelItemCount())
               for j in range(t.tree.topLevelItem(i).childCount()) if not t.tree.topLevelItem(i).child(j).isHidden()]
    assert visible == ["Orchestra/RFTN20_CE", "Orchestra/RFTN20_RATE"]  # names exactly as in the file
    t.search.setText("DESFLURANE_NOT_HERE")
    assert t.banner.isVisible() or not t.banner.isHidden()
    assert "No track matching" in t.banner.text() and "differs between cases" in t.banner.text()
    t.search.setText("")
    # select + add to plot
    p = t.tree.topLevelItem(0)
    for j in range(min(2, p.childCount())):
        p.child(j).setSelected(True)
    t._add()
    assert win.current_key() == "plot"
    assert len(win.plot.panel.track_names) == 2
    # inspect shows metadata + stats
    t.tree.clearSelection()
    for i in range(t.tree.topLevelItemCount()):
        for j in range(t.tree.topLevelItem(i).childCount()):
            ch = t.tree.topLevelItem(i).child(j)
            if ch.text(0) == "Orchestra/RFTN20_CE":
                ch.setSelected(True)
    t._inspect()
    assert pump(lambda: len(win.tables) > 0)
    cells = {win.tables[-1].table.item(r, 0).text(): win.tables[-1].table.item(r, 1).text()
             for r in range(win.tables[-1].table.rowCount())}
    assert cells["Unit"] == "ng/mL" and "Effect-site" in cells["Interpretation"]
    assert cells["Defined in supervisor's dictionary"] == "yes" and "Analgesia" in cells["Drug class"]


def test_plot_workspace_end_to_end(win, tmp_path, monkeypatch, dialogs):
    store = open_case(win)
    panel = win.plot.panel
    panel.add_tracks(["Solar8000/HR", "Solar8000/ART_MBP", "SNUADC/ECG_II", "EVENT", "Solar8000/NOT_THERE"])
    assert "Solar8000/NOT_THERE" not in panel.track_names                    # absent track is rejected visibly
    assert dialogs.warns and "not present" in dialogs.warns[-1][1]
    panel.start_edit.setText("0:00")
    panel.end_edit.setText("30:00")
    plot_and_wait(panel)
    axes = panel.figure.axes
    assert len(axes) == 4                                                    # separate aligned axis per track
    assert all(a.get_shared_x_axes().joined(a, axes[0]) for a in axes)
    assert "Elapsed time" in axes[-1].get_xlabel()
    def _label(ax):
        return " ".join(t.get_text() for t in ax.texts)
    assert "mmHg" in _label(axes[1]) and "/min" in _label(axes[0])
    hr = panel._lines["Solar8000/HR"]
    assert np.isnan(hr.get_ydata().astype(float)).sum() >= 1                 # 100 s gap not bridged
    ecg = panel._lines["SNUADC/ECG_II"]
    assert len(ecg.get_xdata()) <= 4100 and panel._series["SNUADC/ECG_II"].reduced     # 180k -> reduced
    assert "no averaging" in panel.note_label.text() and "Exports use the original samples" in panel.note_label.text()
    # zoom -> detail is re-drawn from the original samples
    before = len(ecg.get_xdata())
    axes[0].set_xlim(100, 104)
    assert pump(lambda: len(ecg.get_xdata()) != before and len(ecg.get_xdata()) <= 4100, timeout=5)
    assert 380 <= len(ecg.get_xdata()) <= 440                                # ~4 s * 100 Hz (+ margin), all original
    assert not panel._series["SNUADC/ECG_II"].reduced
    panel.figure.axes[0].set_xlim(0, 1800)
    # presets / validation
    panel._preset(0, 300)
    assert panel.end_edit.text() == "0:05:00"
    panel.start_edit.setText("20:00")
    panel.end_edit.setText("10:00")
    panel.plot()
    assert "later than the start" in dialogs.warns[-1][1]
    panel.start_edit.setText("5000:00")
    panel.end_edit.setText("5010:00")
    panel.plot()
    assert "after the end of the recording" in dialogs.warns[-1][1]
    panel.start_edit.setText("zzz")
    panel.plot()
    assert "Could not understand" in dialogs.warns[-1][1]
    panel._preset_all()
    assert panel.end_edit.text().startswith("0:30:")
    # reorder / remove
    panel.track_list.item(1).setSelected(True)
    panel._move(-1)
    assert panel.track_names[0] == "Solar8000/ART_MBP"
    panel.track_list.clearSelection()
    panel.track_list.item(3).setSelected(True)
    panel.remove_selected()
    assert "EVENT" not in panel.track_names
    # same-unit grouping option
    panel.group_units.setChecked(True)
    panel.set_tracks(["Solar8000/ART_MBP", "SNUADC/ART"])
    panel.set_window(0, 120)
    plot_and_wait(panel)
    assert len(panel.figure.axes) == 1                                       # both mmHg -> one shared axis
    panel.group_units.setChecked(False)
    # cursor readout shows nearest original sample for each track
    panel.set_tracks(["Solar8000/HR", "SNUADC/ART"])
    panel.set_window(0, 120)
    plot_and_wait(panel)
    ax = panel.figure.axes[0]
    px = ax.transData.transform((60.0, 70.0))

    class Ev:
        inaxes, xdata, button = ax, 60.0, 1
    panel._on_motion(Ev)
    txt = panel.readout.text()
    assert "0:01:00" in txt and "HR" in txt and "/min" in txt and "nearest original sample" in txt
    clicks = []
    panel.clicked.connect(clicks.append)
    panel._on_click(Ev)
    assert clicks == [60.0]
    # markers
    panel.add_marker(60.0, "test marker")
    assert panel.markers == [(60.0, "test marker")]
    assert any("test marker" in t.get_text() for t in panel.figure.axes[0].texts)
    # save plot in all three formats
    for ext, flt in ((".png", "PNG"), (".pdf", "PDF"), (".svg", "SVG")):
        target = tmp_path / f"plot{ext}"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, _t=target, _f=flt: (str(_t), _f)))
        panel.save_plot()
        assert target.exists() and target.stat().st_size > 1000
    assert (tmp_path / "plot.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # statistics dialog
    panel.show_stats()
    assert pump(lambda: len(win.tables) > 0)
    tbl = win.tables[-1].table
    assert tbl.columnCount() == 3 and "exploratory" in win.tables[-1].findChild(common.QLabel).text().lower()


def test_export_from_page_and_dialog(win, tmp_path, dialogs):
    store = open_case(win)
    form = win.export.form
    assert form.track_list.count() == 12
    form.set_selection(["Solar8000/HR", "BIS/BIS", "Orchestra/RFTN20_RATE"], 0, 300)
    form.folder.setText(str(tmp_path))
    form.filename.setText("aligned test")
    form.mode.setCurrentIndex(form.mode.findData(export_page.MODE_GRID))
    form.step.setValue(5.0)
    form.run_export()
    out = tmp_path / "aligned test.csv"
    assert pump(lambda: out.exists() and any("Export complete" in t for t, _ in dialogs.infos))
    df = pd.read_csv(out)
    assert df.columns[0] == "time_s"
    assert set(df.columns[1:]) == {"Solar8000/HR", "BIS/BIS", "Orchestra/RFTN20_RATE"}   # original names
    assert len(df) == 61 and df.time_s.iloc[-1] == 300
    assert (tmp_path / "aligned test_manifest.json").exists()
    assert str(out) in dialogs.infos[-1][1]                               # location shown
    # overwrite requires confirmation; refusing keeps the file untouched
    dialogs.confirm_answer = False
    mtime = out.stat().st_mtime_ns
    form.run_export()
    pump(timeout=0.5)
    assert any(t == "File already exists" for t, _ in dialogs.confirms) and out.stat().st_mtime_ns == mtime
    dialogs.confirm_answer = True
    # long format
    form.filename.setText("long.csv")
    form.mode.setCurrentIndex(form.mode.findData(export_page.MODE_LONG))
    n_before = len(dialogs.infos)
    form.run_export()
    assert pump(lambda: (tmp_path / "long.csv").exists() and len(dialogs.infos) > n_before)
    ldf = pd.read_csv(tmp_path / "long.csv")
    assert set(ldf.columns) == {"track", "unit", "time_s", "value"} and ldf.time_s.max() <= 300
    # invalid input is reported, nothing written
    form.start_edit.setText("10:00")
    form.end_edit.setText("5:00")
    form.filename.setText("bad.csv")
    form.run_export()
    assert "later than the start" in dialogs.warns[-1][1] and not (tmp_path / "bad.csv").exists()
    form.start_edit.setText("0")
    form.end_edit.setText("5")
    for i in range(form.track_list.count()):
        form.track_list.item(i).setCheckState(Qt.Unchecked)
    form.run_export()
    assert "at least one track" in dialogs.warns[-1][1]
    # metadata / track list
    from vitaldb_explorer import export as exp
    exp.write_metadata_json(tmp_path / "meta.json", store.header)
    assert (tmp_path / "meta.json").exists()


def test_large_export_warning(win, tmp_path, dialogs):
    open_case(win)
    form = win.export.form
    form.set_selection(["SNUADC/ECG_II"], 0, 1800)
    form.folder.setText(str(tmp_path))
    form.filename.setText("big.csv")
    form.mode.setCurrentIndex(form.mode.findData(export_page.MODE_GRID))
    form.step.setValue(0.002)                                             # 900k rows x 2 cols -> below limit
    form.run_export()
    assert pump(lambda: (tmp_path / "big.csv").exists() or dialogs.confirms)
    # now force a clearly large request and decline it
    form.filename.setText("huge.csv")
    form.end_edit.setText("1800")
    form.step.setValue(0.002)
    for i in range(form.track_list.count()):
        form.track_list.item(i).setCheckState(Qt.Checked)                # 12 tracks x 900k rows ~ 10M cells
    dialogs.confirm_answer = False
    form.run_export()
    assert any(t == "Large export" for t, _ in dialogs.confirms)
    assert not (tmp_path / "huge.csv").exists()


def test_drug_explorer(win):
    store = open_case(win)
    d = win.drugs
    names = [d.drug_table.item(r, 1).text() for r in range(d.drug_table.rowCount())]
    assert names == ["Orchestra/PPF20_CE", "Orchestra/PPF20_VOL", "Orchestra/RFTN20_CE", "Orchestra/RFTN20_RATE"]
    interp = {d.drug_table.item(r, 1).text(): d.drug_table.item(r, 3).text() for r in range(4)}
    assert "Propofol" in interp["Orchestra/PPF20_CE"] and "Effect-site" in interp["Orchestra/PPF20_CE"]
    assert "Infusion rate" in interp["Orchestra/RFTN20_RATE"]
    assert "UNVERIFIED" not in interp["Orchestra/PPF20_CE"]
    assert "Orchestra/PPF20_CP" in d.absent_label.text() and "model" in d.absent_label.text()
    assert d.physio_list.count() >= 4
    d._plot_selected()
    assert pump(lambda: bool(d.panel._lines))
    assert any(n.startswith("Orchestra/") for n in d.panel.track_names) and "Solar8000/HR" in d.panel.track_names
    d.centre.setText("10:00")
    d.before.setText("2")
    d.after.setText("3")
    d._apply_interval()
    assert d.panel.start_edit.text() == "0:08:00" and d.panel.end_edit.text() == "0:13:00"
    assert pump(lambda: any(l == "time point" for _t, l in d.panel.markers))
    d._summary()
    assert pump(lambda: len(win.tables) > 0)
    t = win.tables[-1].table
    heads = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert "Interpretation" in heads and "Missing" in heads and "Gaps" in heads
    flags = " ".join(t.item(r, heads.index("Exploratory flags")).text() for r in range(t.rowCount()))
    assert "Constant" in flags                                           # PPF20_CE is constant in the synthetic data


def test_event_explorer(win, tmp_path, monkeypatch):
    store = open_case(win)
    e = win.events
    e._load_events()
    assert pump(lambda: e.ev_table.rowCount() == 3)
    assert e.ev_table.item(0, 2).text() == "Induction"
    e.panel.add_tracks(["Solar8000/HR", "BIS/BIS", "Orchestra/RFTN20_CE"])
    e.ev_table.selectRow(1)
    e._event_chosen(1, 0)
    assert e.time_edit.text().startswith("0:15:00")
    assert e.panel.start_edit.text().startswith("0:13:00") and e.panel.end_edit.text().startswith("0:17:00")
    assert pump(lambda: bool(e.panel._lines))
    assert any("Incision" in lab for _t, lab in e.panel.markers)
    # clicking the plot fills the time box; annotation + marker; no automatic clinical labels anywhere
    e.panel.clicked.emit(1234.0)
    assert e.time_edit.text().startswith("0:20:34")
    e.note_edit.setText("surgeon request")
    e.add_marker()
    assert any(lab == "surgeon request" for _t, lab in e.panel.markers)
    page_text = " ".join(w.text().lower() for w in e.findChildren(common.QLabel))
    assert "pain response" not in page_text.replace("not a pain", "") or "does not mean" in page_text
    # export includes markers in the manifest
    from vitaldb_explorer import export as exp
    import json
    form = export_page.ExportForm(win.session)
    form.load_case(store)
    form.set_selection(["Solar8000/HR"], 780, 1020, e.panel.markers)
    form.folder.setText(str(tmp_path))
    form.filename.setText("event.csv")
    form.run_export()
    assert pump(lambda: (tmp_path / "event_manifest.json").exists())
    man = json.loads((tmp_path / "event_manifest.json").read_text(encoding="utf-8"))
    assert any(m["label"] == "surgeon request" for m in man["user_markers"])


def test_resizes_and_pages_render(win):
    open_case(win)
    for key in win._stack_index:
        win.goto(key)
        pump(timeout=0.05)
        assert win.current_key() == key and win.stack.currentIndex() == win._stack_index[key]
    win.resize(1000, 650)
    pump(timeout=0.1)
    win.resize(1920, 1080)
    pump(timeout=0.1)
    win.grab()                                                            # renders without error


def test_dose_events_page(win, tmp_path, monkeypatch, dialogs):
    store = open_case(win)
    store.header.clinical = {"caseid": 1, "intraop_ftn": 250.0, "intraop_ppf": 100.0, "age": 55}
    win.session.open_case(store)                      # re-announce so the page reads the catalogue row
    pump(timeout=0.3)
    d = win.doses
    assert "Fentanyl" in d.emr_label.text() and "250" in d.emr_label.text() and "mcg" in d.emr_label.text()
    assert "no timestamps" in d.emr_note.text()
    assert "text/event track" in d.status.text()
    d.scan()
    assert pump(lambda: len(d.events) > 0)
    # default: only drug-mentioning recorded events + all derived candidates
    srcs = [d.table.item(r, 1).text() for r in range(d.table.rowCount())]
    assert srcs.count("Recorded event text") == 1 and any("volume step" in s for s in srcs)
    assert any("rate surge" in s for s in srcs)
    assert "hidden" in d.status.text()
    d.only_drug.setChecked(False)
    assert [d.table.item(r, 2).text() for r in range(d.table.rowCount())].count("Induction") == 1
    d.source.setCurrentIndex(d.source.findData("Recorded event text"))
    assert d.table.rowCount() == 3
    d.source.setCurrentIndex(0)
    # select the recorded fentanyl event -> clear explanation, then plot around it with an overlay marker
    row = [d.table.item(r, 2).text() for r in range(d.table.rowCount())].index("Fentanyl 100 mcg bolus")
    d.table.selectRow(row)
    assert "RECORDED EVENT" in d.detail.text()
    d.plot_selected()
    assert pump(lambda: bool(d.panel._lines))
    assert any(lab.startswith("Fentanyl") for _t, lab in d.panel.overlays)
    assert any("Fentanyl" in t.get_text() for t in d.panel.figure.axes[0].texts)
    # derived candidate is described as unconfirmed
    vrow = next(r for r in range(d.table.rowCount()) if "volume step" in d.table.item(r, 1).text())
    d.table.selectRow(vrow)
    assert "DERIVED CANDIDATE" in d.detail.text() and "not a confirmed administration" in d.detail.text()
    d.plot_selected()
    assert pump(lambda: bool(d.panel._lines))
    assert any(n.startswith("Orchestra/PPF20") for n in d.panel.track_names)
    d.plot_all()
    assert pump(lambda: bool(d.panel._lines)) and len(d.panel.overlays) == d.table.rowCount()
    # export
    out = tmp_path / "dose.csv"
    monkeypatch.setattr(common, "choose_save_file", lambda *a, **k: str(out))
    monkeypatch.setattr(dose_page_mod, "choose_save_file", lambda *a, **k: str(out))
    d.export_events()
    import pandas as pd
    df = pd.read_csv(out)
    assert "confirmed_record" in df.columns and set(df.case_id) == {7} and "not confirmed" in df.note.iloc[0]
    assert df.confirmed_record.sum() >= 1 and (~df.confirmed_record).sum() >= 2


def test_guide_page_and_navigation(win):
    g = win.guide
    win.goto("guide")
    assert win.current_key() == "guide"
    assert g.drug_table.rowCount() == 15 + 36
    g.search.setText("remifentanil")
    visible = [r for r in range(g.drug_table.rowCount()) if not g.drug_table.isRowHidden(r)]
    assert len(visible) == 10
    g.search.setText("")
    assert not any(g.drug_table.isRowHidden(r) for r in range(g.drug_table.rowCount()))
    open_case(win)
    assert win.nav_enabled("doses") and win.case_title.text() == "Case 7" and "tracks" in win.case_sub.text()


def test_playback_monitor(win):
    from vitaldb_explorer.gui import playback_page as pb
    store = open_case(win)
    page = win.playback
    assert win.nav_enabled("playback")
    sel = pb.default_selection(store)
    assert "Solar8000/HR" in sel and "BIS/BIS" in sel and "EVENT" not in sel
    page.load_selected()
    assert pump(lambda: page._loaded)
    assert set(page.tiles) >= {"Solar8000/HR", "BIS/BIS"}
    assert page.slider.maximum() > 0

    page.seek(300.0)                                         # scrub
    v = page.tiles["Solar8000/HR"].value.text()
    assert v != "--" and 40 < float(v) < 100
    assert "Induction" in page.event_label.text()
    page.seek(650.0)                                         # inside the synthetic 600-700 s gap
    assert page.tiles["Solar8000/HR"].value.text() == "--"   # stale values are not shown
    assert page.tiles["BIS/BIS"].value.text() != "--"

    page.jump_event(1)
    assert abs(page.pos - 900.0) < 1.0                      # next recorded event (Incision)
    page.jump_event(-1)
    assert abs(page.pos - 300.0) < 1.0

    # play forward in fast motion, then in reverse, then pause at the ends
    page.seek(10.0)
    page.speed_box.setCurrentIndex(page.speed_box.findData(120.0))
    page.play()
    assert page.playing
    assert pump(lambda: page.pos > 40.0, timeout=10)
    page.toggle_play()
    assert not page.playing
    p = page.pos
    page.speed_box.setCurrentIndex(page.speed_box.findData(-10.0))
    page.play()
    assert pump(lambda: page.pos < p - 5, timeout=10)
    page.pause()
    page.seek(-5)
    assert page.pos == 0.0
    page.seek(1e9)
    assert page.pos == page.duration

    page.goto_edit.setText("5:00")
    page._goto_typed()
    assert page.pos == 300.0
    page.win_box.setCurrentIndex(page.win_box.findData(10.0))
    xs = list(page.axes.values())[0].get_xlim()
    assert abs((xs[1] - xs[0]) - 10.0) < 1e-6 and abs(xs[1] - 300.0) < 1e-6   # trailing window ends at playhead
    ln = page.lines["Solar8000/HR"]
    assert np.nanmax(ln.get_xdata()) <= 300.0                # nothing from the future is drawn
