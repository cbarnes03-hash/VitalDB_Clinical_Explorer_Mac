"""Dose events: view recorded bolus/dose evidence within a case on the timeline.

Three kinds of evidence are kept clearly apart:
  * recorded text events (as recorded; never interpreted),
  * derived candidates from infusion-pump tracks (exploratory; NOT confirmed administrations),
  * whole-case EMR totals (no timestamps; listed separately, never plotted).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import doses
from .. import knowledge as kn
from ..data_access import CaseStore
from ..timeutil import format_hms
from .common import Session, choose_save_file, info, make_page_header, warn
from .drug_page import PHYSIO_PATTERNS
from .plot_panel import PlotPanel
from .workers import run_task

SENSITIVITY = [("High sensitivity (3x typical)", 3.0), ("Standard (5x typical)", 5.0), ("Low sensitivity (8x typical)", 8.0)]
SOURCE_COLOURS = {doses.SRC_TEXT: "#B45309", doses.SRC_VOLUME: "#7C3AED", doses.SRC_RATE: "#0E7490"}


class DosePage(QWidget):
    def __init__(self, session: Session, plot_page=None):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        self.plot_page = plot_page
        self.events: list[doses.DoseEvent] = []
        self._shown: list[doses.DoseEvent] = []
        self._scanned = False
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Dose events", "Look for stat/bolus evidence inside the open case and see where it falls on the timeline. "
                           "Only recorded text events are actual records; everything derived from infusion pumps is a "
                           "candidate that you should check against the signals."))

        # --- whole-case totals card
        self.emr_card = QFrame()
        self.emr_card.setObjectName("card")
        el = QVBoxLayout(self.emr_card)
        el.setContentsMargins(14, 10, 14, 10)
        el.setSpacing(4)
        t = QLabel("Whole-case totals from the hospital record (no timing)")
        t.setObjectName("cardTitle")
        self.emr_label = QLabel("")
        self.emr_label.setWordWrap(True)
        self.emr_note = QLabel(doses.timing_hint())
        self.emr_note.setObjectName("note")
        self.emr_note.setWordWrap(True)
        el.addWidget(t)
        el.addWidget(self.emr_label)
        el.addWidget(self.emr_note)
        lay.addWidget(self.emr_card)

        # --- controls
        r = QHBoxLayout()
        self.scan_btn = QPushButton("Scan this case for dose evidence")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.clicked.connect(self.scan)
        self.sens = QComboBox()
        for lab, val in SENSITIVITY:
            self.sens.addItem(lab, val)
        self.sens.setCurrentIndex(1)
        self.sens.setToolTip("How far above its own typical rate an infusion must rise to be listed as a candidate")
        self.source = QComboBox()
        self.source.addItem("All evidence", None)
        for s in (doses.SRC_TEXT, doses.SRC_VOLUME, doses.SRC_RATE):
            self.source.addItem(s, s)
        self.source.currentIndexChanged.connect(self._refilter)
        self.only_drug = QCheckBox("Only recorded events that mention a drug")
        self.only_drug.setChecked(True)
        self.only_drug.toggled.connect(self._refilter)
        self.status = QLabel("")
        self.status.setObjectName("note")
        r.addWidget(self.scan_btn)
        r.addWidget(QLabel("Sensitivity"))
        r.addWidget(self.sens)
        r.addWidget(self.source)
        r.addWidget(self.only_drug)
        r.addStretch(1)
        lay.addLayout(r)
        lay.addWidget(self.status)

        # --- table + plot
        split = QSplitter(Qt.Horizontal)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Time", "Evidence", "Description", "Amount", "Track"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda r, _c: self.plot_selected())
        self.table.itemSelectionChanged.connect(self._show_detail)
        tl.addWidget(self.table, 1)
        self.detail = QLabel("Select an event to read its explanation. Double-click to plot around it.")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("note")
        tl.addWidget(self.detail)
        br = QHBoxLayout()
        self.before = QLineEdit("3")
        self.after = QLineEdit("3")
        for e in (self.before, self.after):
            e.setFixedWidth(46)
        b1 = QPushButton("Plot around selected event")
        b1.clicked.connect(self.plot_selected)
        b2 = QPushButton("Show all on timeline")
        b2.clicked.connect(self.plot_all)
        b3 = QPushButton("Export dose events (CSV)...")
        b3.clicked.connect(self.export_events)
        for w in (QLabel("Window: before (min)"), self.before, QLabel("after (min)"), self.after):
            br.addWidget(w)
        br.addStretch(1)
        tl.addLayout(br)
        br2 = QHBoxLayout()
        for w in (b1, b2, b3):
            br2.addWidget(w)
        br2.addStretch(1)
        tl.addLayout(br2)
        split.addWidget(top)
        self.panel = PlotPanel(session, allow_track_list=False)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([440, 800])
        lay.addWidget(split, 1)
        session.case_opened.connect(self._on_case)

    # ------------------------------------------------------------ case
    @property
    def store(self) -> Optional[CaseStore]:
        return self.session.store

    def _on_case(self, store: CaseStore) -> None:
        self.events, self._shown, self._scanned = [], [], False
        self.table.setRowCount(0)
        self.panel.set_overlays([])
        self.panel.clear_tracks()
        rows = doses.emr_totals(store.header.clinical or {})
        if rows:
            self.emr_label.setText("<br>".join(
                f"<b>{r['description']}</b> ({r['parameter']}): {r['value']} {r['unit']} - {r['class']}" for r in rows))
        else:
            self.emr_label.setText("The catalogue gives no whole-case drug or fluid totals for this case "
                                   "(or the catalogue has not loaded).")
        text, vol, rate = doses.candidate_tracks(store)
        self.status.setText(f"This case has {len(text)} text/event track(s), {len(vol)} infusion-volume and "
                            f"{len(rate)} infusion-rate track(s) that can show dose evidence. Press Scan.")
        self.detail.setText("Select an event to read its explanation. Double-click to plot around it.")

    # ------------------------------------------------------------ scan
    def scan(self) -> None:
        store = self.store
        if store is None:
            return warn(self, "No case open", "Open a case first.")
        sens = float(self.sens.currentData())
        self.scan_btn.setEnabled(False)
        self.status.setText("Scanning... (only small non-waveform tracks are downloaded)")

        def job(progress, token):
            return doses.scan_case(store, sens, token, progress)

        def done(events):
            self.events = events
            self._scanned = True
            self._refilter()

        run_task(self, job, done, on_error=lambda m: (self.status.setText(""), warn(self, "Could not scan", m)),
                 on_progress=self.status.setText, on_finished=lambda: self.scan_btn.setEnabled(True))

    def _refilter(self) -> None:
        src = self.source.currentData()
        recorded = [e for e in self.events if e.source == doses.SRC_TEXT]
        self._shown = [e for e in self.events
                       if (src is None or e.source == src)
                       and not (e.source == doses.SRC_TEXT and self.only_drug.isChecked() and not e.drug_related)]
        self.table.setRowCount(len(self._shown))
        for r, e in enumerate(self._shown):
            amount = "" if e.amount is None else f"{e.amount:.3g} {e.unit}".strip()
            vals = [format_hms(e.time_s, 1), e.source, e.label, amount, e.track]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setForeground(Qt.GlobalColor.black)
                    it.setData(Qt.DecorationRole, None)
                    it.setToolTip("A recorded text event" if e.confirmed else "Derived from an infusion pump track; "
                                  "not a confirmed administration")
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        n_rec = sum(1 for e in self._shown if e.confirmed)
        n_der = len(self._shown) - n_rec
        msg = f"{len(self._shown)} item(s) shown: {n_rec} recorded, {n_der} derived candidate(s)."
        hidden = len(recorded) - sum(1 for e in self._shown if e.source == doses.SRC_TEXT)
        if hidden > 0:
            msg += f" {hidden} recorded event(s) that do not mention a drug are hidden (untick the box to see them)."
        if not self.events:
            msg = ("No text events or infusion-pump steps were found in this case. Whole-case totals above (if any) "
                   "show that a drug was given but not when.")
        self.status.setText(msg)

    def _show_detail(self) -> None:
        e = self._selected()
        if e is None:
            return
        kind = "RECORDED EVENT" if e.confirmed else "DERIVED CANDIDATE (not a confirmed administration)"
        self.detail.setText(f"<b>{kind}</b> at {format_hms(e.time_s, 1)} ({e.time_s:.0f} s): {e.detail} "
                            f"Source track: {e.track}")

    def _selected(self) -> Optional[doses.DoseEvent]:
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._shown):
            return None
        return self._shown[rows[0].row()]

    # ------------------------------------------------------------ plotting
    def _default_tracks(self, focus: Optional[doses.DoseEvent]) -> list[str]:
        store = self.store
        names = list(store.header.tracks)
        out: list[str] = []
        if focus is not None:
            o = kn.parse_orchestra(focus.track)
            if o:
                for k in ("CE", "CT", "RATE", "VOL"):
                    n = f"Orchestra/{o.prefix}_{k}"
                    if n in store.header.tracks:
                        out.append(n)
        else:
            for pref in ("PPF20", "RFTN20", "RFTN50"):
                for k in ("CE", "RATE"):
                    n = f"Orchestra/{pref}_{k}"
                    if n in store.header.tracks:
                        out.append(n)
        for label, pat in PHYSIO_PATTERNS:
            if label in ("Heart rate", "Invasive mean BP", "BIS", "SpO2"):
                rx = re.compile(pat, re.I)
                hit = next((n for n in names if rx.search(n)), None)
                if hit:
                    out.append(hit)
        return list(dict.fromkeys(out))

    def plot_selected(self) -> None:
        e = self._selected()
        if e is None:
            return info(self, "Select an event", "Click a row in the table first.")
        try:
            b = float(self.before.text()) * 60
            a = float(self.after.text()) * 60
            if b < 0 or a < 0:
                raise ValueError
        except ValueError:
            return warn(self, "Window", "Enter 'before' and 'after' as numbers of minutes (0 or more).")
        t0, t1 = max(0.0, e.time_s - b), e.time_s + a
        tracks = self._default_tracks(e)
        if not tracks:
            return info(self, "Nothing to plot", "No drug or physiological tracks were found for this event.")
        self.panel.set_tracks(tracks)
        self.panel.set_window(t0, t1)
        self.panel.set_overlays([(x.time_s, self._short(x)) for x in self._shown if t0 <= x.time_s <= t1])
        self.panel.plot()

    def plot_all(self) -> None:
        store = self.store
        if store is None or not self._shown:
            return info(self, "No events", "Scan the case first, and make sure at least one event is listed.")
        tracks = self._default_tracks(None)
        if not tracks:
            return info(self, "Nothing to plot", "No drug or physiological tracks were found in this case.")
        self.panel.set_tracks(tracks)
        self.panel.set_window(0, store.header.duration_s or 3600)
        self.panel.set_overlays([(x.time_s, self._short(x)) for x in self._shown])
        self.panel.plot()

    @staticmethod
    def _short(e: doses.DoseEvent) -> str:
        tag = "" if e.confirmed else "~ "
        return tag + (e.label if len(e.label) <= 34 else e.label[:33] + "...")

    # ------------------------------------------------------------ export
    def export_events(self) -> None:
        store = self.store
        if store is None or not self._shown:
            return info(self, "Nothing to export", "Scan the case and list at least one event first.")
        path = choose_save_file(self, "Export dose events", f"case_{store.case_id}_dose_events.csv", "CSV (*.csv)")
        if not path:
            return
        df = doses.to_frame(self._shown)
        df.insert(0, "case_id", store.case_id)
        df["note"] = ("time_s = seconds since start of recording. confirmed_record=True only for recorded text events; "
                      "False rows are derived candidates, not confirmed administrations. No unit conversion.")
        try:
            df.to_csv(path, index=False, encoding="utf-8-sig")
        except OSError as exc:
            return warn(self, "Could not save", f"The file could not be saved:\n{exc}")
        info(self, "Export complete", f"{len(df)} event(s) saved to:\n{Path(path)}")
