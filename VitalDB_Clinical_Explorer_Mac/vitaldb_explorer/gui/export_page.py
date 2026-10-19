"""Export page and the reusable export form/dialog.

Exports never modify data: they write the original samples (long format) or an
explicitly documented regular-grid table, always with a manifest sidecar.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from .. import export as exp
from ..data_access import CaseStore
from ..processing import (METHOD_MEAN, METHOD_NEAREST, ProcessingError, build_aligned_grid, build_long,
                          estimate_rows, validate_window)
from ..timeutil import TimeParseError, format_hms, parse_time
from .common import Session, choose_save_file, confirm, info, last_dir, make_page_header, remember_dir, warn
from .workers import run_task

MODE_GRID = "grid"
MODE_LONG = "long"


class ExportForm(QWidget):
    """Form: choose tracks, interval, format, destination; then export."""

    def __init__(self, session: Session, parent: Optional[QWidget] = None, show_track_list: bool = True):
        super().__init__(parent)
        self.session = session
        self.markers: list = []
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        if show_track_list:
            g = QGroupBox("1. Tracks to export")
            gl = QVBoxLayout(g)
            self.search = QLineEdit()
            self.search.setPlaceholderText("Filter track list...")
            self.search.textChanged.connect(self._filter)
            gl.addWidget(self.search)
            self.track_list = QListWidget()
            self.track_list.setSelectionMode(QAbstractItemView.NoSelection)
            self.track_list.setMinimumHeight(160)
            gl.addWidget(self.track_list)
            sel = QHBoxLayout()
            for text, fn in (("Select all shown", lambda: self._set_all(True)),
                             ("Select none", lambda: self._set_all(False))):
                b = QPushButton(text)
                b.clicked.connect(fn)
                sel.addWidget(b)
            sel.addStretch(1)
            gl.addLayout(sel)
            lay.addWidget(g, 1)
        else:
            self.track_list = QListWidget()
            self.track_list.setVisible(False)
            self.search = None

        g2 = QGroupBox("2. Interval and format")
        f = QFormLayout(g2)
        row = QHBoxLayout()
        self.start_edit, self.end_edit = QLineEdit("0:00"), QLineEdit("15:00")
        for w in (self.start_edit, self.end_edit):
            w.setFixedWidth(100)
        row.addWidget(QLabel("Start"))
        row.addWidget(self.start_edit)
        row.addWidget(QLabel("End"))
        row.addWidget(self.end_edit)
        row.addWidget(QLabel("(elapsed time: mm:ss, h:mm:ss or minutes)"))
        row.addStretch(1)
        f.addRow("Interval:", row)
        self.mode = QComboBox()
        self.mode.addItem("Original samples, one row per sample per track (long format, no resampling)", MODE_LONG)
        self.mode.addItem("Aligned table on a regular time grid (one column per track)", MODE_GRID)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        f.addRow("Format:", self.mode)
        self.step = QDoubleSpinBox()
        self.step.setRange(0.002, 3600)
        self.step.setDecimals(3)
        self.step.setValue(1.0)
        self.step.setSuffix(" s")
        f.addRow("Grid step:", self.step)
        self.method = QComboBox()
        self.method.addItem("Nearest original sample (no averaging) - recommended", METHOD_NEAREST)
        self.method.addItem("Mean per bin (numeric trend tracks only; waveforms stay nearest-sample)", METHOD_MEAN)
        f.addRow("Grid method:", self.method)
        self.explain = QLabel("")
        self.explain.setWordWrap(True)
        self.explain.setObjectName("note")
        f.addRow(self.explain)
        lay.addWidget(g2)

        g3 = QGroupBox("3. Destination")
        f3 = QFormLayout(g3)
        self.folder = QLineEdit(last_dir())
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        fr = QHBoxLayout()
        fr.addWidget(self.folder, 1)
        fr.addWidget(browse)
        f3.addRow("Folder:", fr)
        self.filename = QLineEdit("")
        f3.addRow("File name:", self.filename)
        lay.addWidget(g3)

        r = QHBoxLayout()
        self.export_btn = QPushButton("Export data to CSV")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self.run_export)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        r.addWidget(self.export_btn)
        r.addWidget(self.status, 1)
        lay.addLayout(r)
        self._mode_changed()

    # ------------------------------------------------------------ helpers
    def load_case(self, store: Optional[CaseStore]) -> None:
        self.track_list.clear()
        if store is None:
            return
        for name in sorted(store.header.tracks, key=lambda n: (store.header.tracks[n].category, n)):
            ti = store.header.tracks[name]
            it = QListWidgetItem(f"{name}   [{ti.kind}, {ti.unit_text}]")
            it.setData(Qt.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.track_list.addItem(it)
        dur = store.header.duration_s
        self.filename.setText(f"case_{store.case_id}_export.csv")
        if dur:
            self.end_edit.setText(format_hms(min(dur, 900)))

    def set_selection(self, names: list[str], t0: Optional[float] = None, t1: Optional[float] = None,
                      markers: Optional[list] = None) -> None:
        wanted = set(names)
        for i in range(self.track_list.count()):
            it = self.track_list.item(i)
            if it.data(Qt.UserRole) in wanted:
                it.setCheckState(Qt.Checked)
        if t0 is not None:
            self.start_edit.setText(format_hms(t0, 1 if t0 % 1 else 0))
        if t1 is not None:
            self.end_edit.setText(format_hms(t1, 1 if t1 % 1 else 0))
        self.markers = list(markers or [])

    def _selected(self) -> list[str]:
        return [self.track_list.item(i).data(Qt.UserRole) for i in range(self.track_list.count())
                if self.track_list.item(i).checkState() == Qt.Checked]

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        for i in range(self.track_list.count()):
            it = self.track_list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _set_all(self, on: bool) -> None:
        for i in range(self.track_list.count()):
            it = self.track_list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _mode_changed(self) -> None:
        grid = self.mode.currentData() == MODE_GRID
        self.step.setEnabled(grid)
        self.method.setEnabled(grid)
        if grid:
            self.explain.setText(
                "Grid export resamples every track onto the same regular timestamps so that columns line up. "
                "Gaps stay empty (nothing is interpolated). Waveforms and drug/event tracks are never averaged "
                "unless you choose 'mean' for numeric trend tracks. The chosen method is recorded in the manifest.")
        else:
            self.explain.setText(
                "Long format keeps every original sample with its own timestamp, so different sampling rates "
                "are preserved exactly. Missing values are kept as missing. Best for later analysis in R/Python.")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose destination folder", self.folder.text() or last_dir())
        if d:
            self.folder.setText(d)
            remember_dir(d)

    # ------------------------------------------------------------ export
    def run_export(self) -> None:
        store = self.session.store
        if store is None:
            warn(self, "No case open", "Please open a case first.")
            return
        names = self._selected()
        if not names:
            warn(self, "No tracks selected", "Tick at least one track to export.")
            return
        try:
            t0 = parse_time(self.start_edit.text())
            t1 = parse_time(self.end_edit.text())
            t0, t1 = validate_window(t0, t1, store.header.duration_s)
        except (TimeParseError, ProcessingError) as exc:
            warn(self, "Check the time interval", str(exc))
            return
        mode = self.mode.currentData()
        step = float(self.step.value())
        method = self.method.currentData()
        fname = self.filename.text().strip() or f"case_{store.case_id}_export.csv"
        if not fname.lower().endswith(".csv"):
            fname += ".csv"
        folder = Path(self.folder.text().strip() or last_dir())
        path = folder / fname
        if not folder.is_dir():
            warn(self, "Folder not found", f"The folder does not exist:\n{folder}")
            return

        # size warning
        if mode == MODE_GRID:
            n_rows = int((t1 - t0) / step) + 1
            n_cols = len(names) + 1
        else:
            n_rows = self._estimate(store, names, t0, t1)
            n_cols = 4
        warning = exp.size_warning(n_rows, n_cols)
        if warning and not confirm(self, "Large export", warning + "\n\nContinue?"):
            return
        overwrite = False
        clash = exp.conflicting_files(path)
        if clash:
            if not confirm(self, "File already exists",
                           "These files already exist and would be replaced:\n\n" + "\n".join(str(p) for p in clash),
                           yes_text="Replace"):
                return
            overwrite = True

        markers = [{"time_s": t, "label": lab} for t, lab in self.markers]
        settings = {"mode": mode, "grid_step_s": step if mode == MODE_GRID else None,
                    "grid_method": method if mode == MODE_GRID else None}

        def job(progress, token):
            raws = store.ensure_tracks(names, token, progress)
            token.check()
            progress("Preparing table...")
            if mode == MODE_GRID:
                result = build_aligned_grid(raws, t0, t1, step, method)
                kind = "aligned_grid"
            else:
                result = build_long(raws, t0, t1)
                kind = "long_original_samples"
            progress("Writing file...")
            return exp.write_dataset_csv(path, result, store.header, names, t0, t1, kind, overwrite, settings,
                                         markers), len(result.frame), result.warnings

        def done(res):
            (csv_path, man_path), n, warns = res
            msg = (f"Export complete ({n:,} rows).\n\nData file:\n{csv_path}\n\nManifest (what was exported and how):"
                   f"\n{man_path}")
            if warns:
                msg += "\n\nNotes:\n- " + "\n- ".join(warns)
            self.status.setText(f"Saved: {csv_path}")
            info(self, "Export complete", msg)

        self._set_busy(True, "Exporting...")
        run_task(self, job, done, on_error=lambda m: (self.status.setText(""), warn(self, "Export failed", m)),
                 on_progress=self.status.setText, on_finished=lambda: self._set_busy(False))

    @staticmethod
    def _estimate(store: CaseStore, names: list[str], t0: float, t1: float) -> int:
        total = 0
        for n in names:
            ti = store.header.tracks[n]
            raw = store.get(n)
            if raw is not None:
                total += estimate_rows({n: raw}, t0, t1)
            elif ti.srate > 0:
                total += int((t1 - t0) * ti.srate)
            else:
                total += int((t1 - t0) / 2)  # rough: unknown until loaded
        return total

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self.export_btn.setEnabled(not busy)
        if msg:
            self.status.setText(msg)


class ExportDialog(QDialog):
    def __init__(self, parent, session: Session, names: list[str], t0: float, t1: float, markers: list):
        super().__init__(parent)
        self.setWindowTitle("Export data for this interval")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header("Export data", "Tracks and interval are taken from your current plot. "
                                       "You can change them below."))
        self.form = ExportForm(session, self, show_track_list=True)
        self.form.load_case(session.store)
        self.form.set_selection(names, t0, t1, markers)
        lay.addWidget(self.form)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)


def open_export_dialog(parent, session: Session, names: list[str], t0: float, t1: float,
                       markers: Optional[list] = None) -> None:
    ExportDialog(parent, session, names, t0, t1, markers or []).exec()


class ExportPage(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Export", "Save selected tracks and intervals as CSV for statistical or machine-learning work. "
                      "Every export is accompanied by a manifest file describing the case, tracks, interval and "
                      "any processing applied."))
        self.form = ExportForm(session, self)
        lay.addWidget(self.form, 1)

        g = QGroupBox("Case information")
        gl = QHBoxLayout(g)
        b1 = QPushButton("Save track list (CSV)...")
        b1.clicked.connect(self._save_tracklist)
        b2 = QPushButton("Save case metadata (JSON)...")
        b2.clicked.connect(self._save_meta)
        gl.addWidget(b1)
        gl.addWidget(b2)
        gl.addStretch(1)
        lay.addWidget(g)
        session.case_opened.connect(lambda s: self.form.load_case(s))

    def _save_tracklist(self) -> None:
        s = self.session.store
        if not s:
            return warn(self, "No case open", "Open a case first.")
        p = choose_save_file(self, "Save track list", f"case_{s.case_id}_tracks.csv", "CSV (*.csv)")
        if p:
            try:
                exp.write_track_list_csv(Path(p), s.header, overwrite=True)  # dialog already confirmed overwrite
                info(self, "Saved", f"Saved to:\n{p}")
            except exp.ExportError as exc:
                warn(self, "Could not save", str(exc))

    def _save_meta(self) -> None:
        s = self.session.store
        if not s:
            return warn(self, "No case open", "Open a case first.")
        p = choose_save_file(self, "Save case metadata", f"case_{s.case_id}_metadata.json", "JSON (*.json)")
        if p:
            try:
                exp.write_metadata_json(Path(p), s.header, overwrite=True)
                info(self, "Saved", f"Saved to:\n{p}")
            except exp.ExportError as exc:
                warn(self, "Could not save", str(exc))
