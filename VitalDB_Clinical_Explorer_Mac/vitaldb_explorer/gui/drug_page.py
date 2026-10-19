"""Drug-delivery explorer: identify drug-related tracks cautiously and overlay them with physiology."""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..categories import CAT_DRUG, drug_info
from ..data_access import KIND_STRING, CaseStore
from ..processing import ProcessingError, describe_track
from ..timeutil import TimeParseError, format_hms, parse_time
from .common import Session, TableDialog, info, make_page_header, warn
from .plot_panel import PlotPanel
from .tracks_page import TrackPickerDialog
from .workers import run_task

# Main TCI pump tracks from the supervisor's dictionary; listed so absence is explicit.
EXPECTED_ORCHESTRA = [f"Orchestra/{d}_{s}" for d in ("PPF20", "RFTN20") for s in ("CE", "CP", "CT", "RATE", "VOL")]

PHYSIO_PATTERNS = [
    ("Heart rate", r"(^|/)HR$"),
    ("Invasive mean BP", r"ART_MBP$"),
    ("Invasive systolic BP", r"ART_SBP$"),
    ("Non-invasive mean BP", r"NIBP_MBP$"),
    ("BIS", r"(^|/)BIS$"),
    ("SpO2", r"PLETH_SPO2$|(^|/)SPO2$"),
    ("ETCO2", r"ETCO2"),
]
DEFAULT_PHYSIO_ON = {"Heart rate", "Invasive mean BP", "BIS", "SpO2"}


class DrugPage(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Drug-delivery explorer", "Infusion-pump tracks are described using the supervisor's data dictionary. Anything not in "
                                      "the dictionary is flagged unverified. Pump tracks are not confirmed "
                                      "administration records."))

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)

        g1 = QGroupBox("Drug-related tracks found by name")
        g1l = QVBoxLayout(g1)
        self.drug_table = QTableWidget(0, 4)
        self.drug_table.setHorizontalHeaderLabels(["Use", "Track (original name)", "Unit", "Interpretation"])
        self.drug_table.verticalHeader().setVisible(False)
        self.drug_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.drug_table.setWordWrap(True)
        g1l.addWidget(self.drug_table)
        self.absent_label = QLabel("")
        self.absent_label.setWordWrap(True)
        self.absent_label.setObjectName("note")
        g1l.addWidget(self.absent_label)
        ll.addWidget(g1, 3)

        g2 = QGroupBox("Physiological signals to overlay")
        g2l = QVBoxLayout(g2)
        self.physio_list = QListWidget()
        g2l.addWidget(self.physio_list)
        r = QHBoxLayout()
        more = QPushButton("Add other tracks...")
        more.clicked.connect(self._add_other)
        r.addWidget(more)
        r.addStretch(1)
        g2l.addLayout(r)
        ll.addWidget(g2, 2)

        g3 = QGroupBox("Interval around a time point (optional)")
        g3l = QHBoxLayout(g3)
        self.centre = QLineEdit()
        self.centre.setPlaceholderText("time point e.g. 45:00")
        self.before = QLineEdit("5")
        self.after = QLineEdit("5")
        for e in (self.before, self.after):
            e.setFixedWidth(50)
        apply_btn = QPushButton("Set interval")
        apply_btn.clicked.connect(self._apply_interval)
        g3l.addWidget(self.centre, 1)
        g3l.addWidget(QLabel("before (min)"))
        g3l.addWidget(self.before)
        g3l.addWidget(QLabel("after (min)"))
        g3l.addWidget(self.after)
        g3l.addWidget(apply_btn)
        ll.addWidget(g3)

        br = QHBoxLayout()
        show = QPushButton("Plot selected tracks")
        show.setObjectName("primary")
        show.clicked.connect(self._plot_selected)
        summ = QPushButton("Drug-track summary for interval...")
        summ.clicked.connect(self._summary)
        br.addWidget(show)
        br.addWidget(summ)
        ll.addLayout(br)
        split.addWidget(left)

        self.panel = PlotPanel(session, allow_track_list=False)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([470, 880])
        lay.addWidget(split, 1)

        session.case_opened.connect(self._on_case)
        self.panel.clicked.connect(lambda t: self.centre.setText(format_hms(t, 1)))
        self._drug_names: list[str] = []

    # ------------------------------------------------------------ case
    def _on_case(self, store: CaseStore) -> None:
        names = [n for n, t in store.header.tracks.items() if t.category == CAT_DRUG]
        self._drug_names = sorted(names)
        self.drug_table.setRowCount(0)
        for n in self._drug_names:
            ti = store.header.tracks[n]
            di = drug_info(n)
            row = self.drug_table.rowCount()
            self.drug_table.insertRow(row)
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            use.setCheckState(Qt.Checked if (di.verified and any(k in n.upper() for k in ("PPF", "RFTN")) and
                                             n.upper().endswith(("_CE", "_CT", "_RATE"))) else Qt.Unchecked)
            self.drug_table.setItem(row, 0, use)
            self.drug_table.setItem(row, 1, QTableWidgetItem(n))
            self.drug_table.setItem(row, 2, QTableWidgetItem(ti.unit_text))
            lead = (di.drug + (f" {di.concentration}" if di.concentration else "") + ": ") if di.drug else ""
            text = f"{lead}{di.kind}" + (f"  [{di.drug_class}]" if di.drug_class else "") + \
                ("" if di.verified else "  [UNVERIFIED]")
            item = QTableWidgetItem(text)
            item.setToolTip((di.note + (" " + di.clinical_note if di.clinical_note else "")).strip())
            self.drug_table.setItem(row, 3, item)
        self.drug_table.resizeColumnsToContents()
        self.drug_table.horizontalHeader().setStretchLastSection(True)
        absent = [n for n in EXPECTED_ORCHESTRA if n not in store.header.tracks]
        self.absent_label.setText(
            ("Not present in this case: " + ", ".join(absent) + ". ") if absent else "")
        if not self._drug_names:
            self.absent_label.setText("No drug-related tracks were found by name in this case. " +
                                      self.absent_label.text())
        self.absent_label.setText(self.absent_label.text() + "Concentrations shown by TCI pumps are model "
                                  "predictions, not measured levels.")

        self.physio_list.clear()
        for label, pat in PHYSIO_PATTERNS:
            rx = re.compile(pat, re.I)
            for n in store.header.tracks:
                if rx.search(n):
                    it = QListWidgetItem(f"{label}: {n}")
                    it.setData(Qt.UserRole, n)
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Checked if label in DEFAULT_PHYSIO_ON else Qt.Unchecked)
                    self.physio_list.addItem(it)
        if self.physio_list.count() == 0:
            it = QListWidgetItem("(none of HR / MAP / BIS / SpO2 / ETCO2 found by name)")
            it.setFlags(Qt.NoItemFlags)
            self.physio_list.addItem(it)
        self.panel.clear_tracks()

    def _checked_drugs(self) -> list[str]:
        return [self._drug_names[r] for r in range(self.drug_table.rowCount())
                if self.drug_table.item(r, 0).checkState() == Qt.Checked]

    def _checked_physio(self) -> list[str]:
        out = []
        for i in range(self.physio_list.count()):
            it = self.physio_list.item(i)
            if it.data(Qt.UserRole) and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def _add_other(self) -> None:
        store = self.session.store
        if store is None:
            return warn(self, "No case open", "Open a case first.")
        dlg = TrackPickerDialog(self, store, "Add other tracks to the overlay")
        if dlg.exec():
            for n in dlg.chosen():
                it = QListWidgetItem(f"Other: {n}")
                it.setData(Qt.UserRole, n)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.physio_list.addItem(it)

    def _plot_selected(self) -> None:
        if self.session.store is None:
            return warn(self, "No case open", "Open a case first.")
        names = self._checked_drugs() + self._checked_physio()
        if not names:
            return info(self, "Nothing selected", "Tick at least one drug or physiological track.")
        self.panel.set_tracks(list(dict.fromkeys(names)))
        self.panel.plot()

    def _apply_interval(self) -> None:
        try:
            c = parse_time(self.centre.text())
            b = float(self.before.text()) * 60
            a = float(self.after.text()) * 60
            if b < 0 or a < 0:
                raise ValueError
        except TimeParseError as exc:
            return warn(self, "Time point", str(exc))
        except ValueError:
            return warn(self, "Interval", "Please enter 'before' and 'after' as numbers of minutes (0 or more).")
        t0 = max(0.0, c - b)
        t1 = c + a
        self.panel.set_window(t0, t1)
        self.panel.markers = [(t, l) for t, l in self.panel.markers if l != "time point"]
        self.panel.markers.append((c, "time point"))
        if self.panel.track_names:
            self.panel.plot()

    def _summary(self) -> None:
        store = self.session.store
        names = self._checked_drugs() or self._drug_names
        if store is None or not names:
            return info(self, "No drug tracks", "No drug-related tracks are available in this case.")
        try:
            t0, t1 = self.panel.window_from_inputs()
        except (TimeParseError, ProcessingError) as exc:
            return warn(self, "Check the time interval", str(exc))

        def job(progress, token):
            raws = store.ensure_tracks(names, token, progress)
            return [(n, describe_track(raws[n], t0, t1), raws[n].span()) for n in names]

        def done(res):
            rows = []
            for n, st, sp in res:
                di = drug_info(n)
                rows.append([n, di.drug or "unknown", di.kind + ("" if di.verified else " [UNVERIFIED]"),
                             store.header.tracks[n].unit_text, f"{st.n_valid:,}",
                             "n/a" if st.pct_missing is None else f"{st.pct_missing:.0f}%",
                             "n/a" if st.vmin is None else f"{st.vmin:.4g} to {st.vmax:.4g}",
                             "n/a" if st.median_interval_s is None else f"{st.median_interval_s:.3g} s",
                             f"{st.n_gaps}", "n/a" if sp is None else f"{format_hms(sp[0])} - {format_hms(sp[1])}",
                             "; ".join(st.warnings) or "none"])
            TableDialog(self, f"Drug-track summary, {format_hms(t0)} to {format_hms(t1)}",
                        ["Track", "Drug (from name)", "Interpretation", "Unit", "Valid samples", "Missing",
                         "Range in interval", "Median interval", "Gaps", "Recorded span", "Exploratory flags"],
                        rows, note="Interpretation comes from the track name only. No units were converted and no "
                                   "boluses were inferred. Target/effect-site/plasma concentrations from a TCI pump "
                                   "are model outputs, not measured concentrations.", width=1250, height=420).exec()

        run_task(self, job, done, on_error=lambda m: warn(self, "Could not summarise", m))
