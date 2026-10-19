"""Dataset guide: the supervisor's cohort summary, drug dictionary and clinical notes."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QTabWidget, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import knowledge as kn
from .common import make_page_header


def _table(headers: list[str], rows: list[list[str]], stretch_last: bool = True) -> QTableWidget:
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setWordWrap(True)
    t.setShowGrid(False)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            it = QTableWidgetItem(str(val))
            it.setToolTip(str(val))
            t.setItem(r, c, it)
    t.resizeColumnsToContents()
    t.horizontalHeader().setStretchLastSection(stretch_last)
    t.resizeRowsToContents()
    return t


class GuidePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Dataset guide", "Reference information supplied by the supervising consultant anaesthetist: what the "
                             "VitalDB cohort contains, what each drug track means and how to read them. "
                             "Source: " + kn.SOURCE_NOTE + "."))
        tabs = QTabWidget()
        tabs.addTab(self._cohort_tab(), "Cohort summary")
        tabs.addTab(self._drug_tab(), "Drug and infusion tracks")
        tabs.addTab(self._emr_tab(), "Whole-case drug and fluid totals")
        tabs.addTab(self._notes_tab(), "Clinical interpretation notes")
        lay.addWidget(tabs, 1)

    def _cohort_tab(self) -> QWidget:
        w = QWidget()
        wl = QVBoxLayout(w)
        headers = ["", *kn.COHORT_COLUMNS, "Notes"]
        rows: list[list[str]] = []
        sections: list[int] = []
        for section, entries in kn.COHORT_ROWS:
            sections.append(len(rows))
            rows.append([section] + [""] * (len(headers) - 1))
            for name, vals, note in entries:
                rows.append(["    " + name, *vals, note])
        t = _table(headers, rows)
        for r in sections:
            for c in range(len(headers)):
                it = t.item(r, c)
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                it.setBackground(QBrush(QColor('#EAF1F6')))
        t.setAlternatingRowColors(False)
        wl.addWidget(t, 1)
        n = QLabel("Percentages are within each surgical specialty column. Counts describe the whole VitalDB "
                   "cohort, not the case currently open.")
        n.setObjectName("note")
        n.setWordWrap(True)
        wl.addWidget(n)
        return w

    def _drug_tab(self) -> QWidget:
        w = QWidget()
        wl = QVBoxLayout(w)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by drug, track name or class (for example remifentanil, VA, RATE)...")
        top.addWidget(self.search)
        wl.addLayout(top)
        rows = []
        for prefix, (drug, conc, cls, note, kinds) in kn.ORCHESTRA_DRUGS.items():
            for k in kinds:
                label, _ = kn.KIND_TEXT[k]
                rows.append([f"Orchestra/{prefix}_{k}", drug, conc, label, kn.class_label(cls), note])
        self._drug_rows = rows
        self.drug_table = _table(["Track name (as in VitalDB)", "Drug", "Preparation", "What the track is",
                                  "Drug class", "Supervisor's note"], rows)
        self.drug_table.setColumnWidth(0, 200)
        self.drug_table.setColumnWidth(5, 420)
        wl.addWidget(self.drug_table, 1)
        txt = QTextBrowser()
        txt.setMaximumHeight(150)
        txt.setHtml("<b>How to read the target / concentration tracks (supervisor's definitions)</b><br>" + "<br>".join(
            f"<b>{kn.KIND_TEXT[k][0]} ({k}):</b> {kn.KIND_TEXT[k][1]}" for k in ("CE", "CP", "CT", "RATE", "VOL")))
        wl.addWidget(txt)
        self.search.textChanged.connect(self._filter_drugs)
        return w

    def _filter_drugs(self, text: str) -> None:
        t = text.strip().lower()
        for r, row in enumerate(self._drug_rows):
            self.drug_table.setRowHidden(r, bool(t) and t not in " ".join(row).lower())

    def _emr_tab(self) -> QWidget:
        w = QWidget()
        wl = QVBoxLayout(w)
        rows = [[k, d, u, kn.class_label(c), n] for k, (d, u, c, n) in kn.EMR_PARAMS.items()]
        wl.addWidget(_table(["Catalogue column", "Description", "Unit", "Class", "Supervisor's note"], rows), 1)
        n = QLabel("These columns are whole-case totals recorded from the hospital record (EMR). They have no "
                   "timestamps, so they cannot be placed on a timeline and do not show when a bolus was given.")
        n.setObjectName("note")
        n.setWordWrap(True)
        wl.addWidget(n)
        return w

    def _notes_tab(self) -> QWidget:
        v = QTextBrowser()
        items = "".join(f"<li style='margin-bottom:8px'>{n}</li>" for n in kn.SUPERVISOR_NOTES)
        key = "".join(f"<li><b>{k}</b> - {v}</li>" for k, v in kn.CLASS_KEY.items())
        v.setHtml(f"<h3>Interpretation notes</h3><ul>{items}</ul><h3>Drug and fluid class key</h3><ul>{key}</ul>"
                  "<h3>Devices recorded in the dataset</h3><ul>" + "".join(
                      f"<li><b>{lab}</b>: {desc}</li>" for _p, lab, desc in kn.DEVICES) + "</ul>"
                  "<p style='color:#617080'>This application only shows these notes as reference. It does not "
                  "use them to score nociception, judge analgesia or recommend any treatment.</p>")
        return v
