"""Case browser: searchable, sortable catalogue of the public VitalDB cases."""
from __future__ import annotations

from typing import Optional

import pandas as pd
from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QTableView, QVBoxLayout, QWidget)

from .. import data_access as da
from .common import PandasModel, Session, confirm, info, make_page_header, warn
from .workers import run_task


class _CaseFilter(QSortFilterProxyModel):
    """Text search (all or one column) plus an optional numeric range on one column."""

    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.text_col = -1            # -1 = any column
        self.num_col = -1
        self.num_min: Optional[float] = None
        self.num_max: Optional[float] = None
        self.setSortRole(Qt.UserRole)

    def set_filters(self, text, text_col, num_col, num_min, num_max) -> None:
        if hasattr(self, "beginFilterChange"):      # Qt 6.9+: invalidateFilter() is deprecated
            self.beginFilterChange()
        self.text, self.text_col = text.strip().lower(), text_col
        self.num_col, self.num_min, self.num_max = num_col, num_min, num_max
        if hasattr(self, "endFilterChange"):
            self.endFilterChange()
        else:
            self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        m = self.sourceModel()
        if self.text:
            cols = range(m.columnCount()) if self.text_col < 0 else [self.text_col]
            if not any(self.text in str(m.data(m.index(row, c), Qt.DisplayRole) or "").lower() for c in cols):
                return False
        if self.num_col >= 0 and (self.num_min is not None or self.num_max is not None):
            v = m.data(m.index(row, self.num_col), Qt.UserRole)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return False
            if self.num_min is not None and v < self.num_min:
                return False
            if self.num_max is not None and v > self.num_max:
                return False
        return True


class CasesPage(QWidget):
    def __init__(self, session: Session, on_case_opened):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        self._on_case_opened = on_case_opened
        self._busy = False
        self.df: Optional[pd.DataFrame] = None
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Cases", "Browse the public VitalDB case catalogue, then select a case and press Open case. "
                     "An internet connection is needed to download the catalogue and case data."))

        # search row
        r = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search (for example a case ID, or any text in the table)...")
        self.search.textChanged.connect(self._apply_filter)
        self.col_combo = QComboBox()
        self.col_combo.addItem("Any column", -1)
        self.col_combo.currentIndexChanged.connect(self._apply_filter)
        r.addWidget(self.search, 1)
        r.addWidget(QLabel("in"))
        r.addWidget(self.col_combo)
        lay.addLayout(r)

        # numeric filter row
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Numeric filter:"))
        self.num_combo = QComboBox()
        self.num_combo.addItem("(none)", -1)
        self.num_combo.currentIndexChanged.connect(self._apply_filter)
        self.min_edit, self.max_edit = QLineEdit(), QLineEdit()
        for e, ph in ((self.min_edit, "min"), (self.max_edit, "max")):
            e.setPlaceholderText(ph)
            e.setFixedWidth(80)
            e.textChanged.connect(self._apply_filter)
        r2.addWidget(self.num_combo)
        r2.addWidget(self.min_edit)
        r2.addWidget(QLabel("to"))
        r2.addWidget(self.max_edit)
        clear = QPushButton("Clear filters")
        clear.clicked.connect(self._clear_filters)
        r2.addWidget(clear)
        r2.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("note")
        r2.addWidget(self.count_label)
        lay.addLayout(r2)

        # table
        self.model = PandasModel()
        self.proxy = _CaseFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.doubleClicked.connect(lambda _i: self.open_selected())
        lay.addWidget(self.table, 1)

        # bottom row
        r3 = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh catalogue")
        self.refresh_btn.setToolTip("Download the catalogue again from VitalDB")
        self.refresh_btn.clicked.connect(lambda: self.load_catalogue(force=True))
        self.open_btn = QPushButton("Open case")
        self.open_btn.setObjectName("primary")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_selected)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("or type a case ID")
        self.id_edit.setFixedWidth(130)
        self.id_edit.returnPressed.connect(self.open_by_id)
        id_btn = QPushButton("Open by ID")
        id_btn.clicked.connect(self.open_by_id)
        r3.addWidget(self.refresh_btn)
        r3.addStretch(1)
        r3.addWidget(self.id_edit)
        r3.addWidget(id_btn)
        r3.addWidget(self.open_btn)
        lay.addLayout(r3)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.proxy.rowsInserted.connect(lambda *_: self._update_count())
        self.proxy.rowsRemoved.connect(lambda *_: self._update_count())
        self.proxy.modelReset.connect(self._update_count)
        self.proxy.layoutChanged.connect(lambda *_: self._update_count())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.open_btn.setEnabled(
            self.table.selectionModel().hasSelection() and not self._busy))

    # ------------------------------------------------------------ catalogue
    def load_catalogue(self, force: bool = False) -> None:
        if self._busy:
            return
        self._set_busy(True, "Loading the case catalogue...")

        def job(progress, token):
            return da.load_catalogue(force_refresh=force, cancel=token, progress=progress)

        def done(res):
            df, st = res
            self.df = df
            self.session.catalogue = df
            self._fill(df)
            self.status.setObjectName("statusOk" if st.source == "online" and not st.message else "statusWarn")
            self.status.setStyleSheet("")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            self.status.setText(f"{len(df):,} cases. " + st.describe())

        def err(msg):
            self.status.setObjectName("statusWarn")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            self.status.setText("The catalogue could not be loaded. " + msg + "  You can still open a case by "
                                "typing its ID if you are online.")

        run_task(self, job, done, on_error=err, on_progress=self.status.setText,
                 on_finished=lambda: self._set_busy(False))

    def _fill(self, df: pd.DataFrame) -> None:
        self.model.set_frame(df)
        self.col_combo.blockSignals(True)
        self.num_combo.blockSignals(True)
        self.col_combo.clear()
        self.num_combo.clear()
        self.col_combo.addItem("Any column", -1)
        self.num_combo.addItem("(none)", -1)
        for i, c in enumerate(df.columns):
            self.col_combo.addItem(str(c), i)
            if pd.api.types.is_numeric_dtype(df[c]):
                self.num_combo.addItem(str(c), i)
        self.col_combo.blockSignals(False)
        self.num_combo.blockSignals(False)
        self.table.resizeColumnsToContents()
        for c in range(df.shape[1]):
            if self.table.columnWidth(c) > 220:
                self.table.setColumnWidth(c, 220)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self._apply_filter()

    def _apply_filter(self) -> None:
        def num(e):
            try:
                return float(e.text()) if e.text().strip() else None
            except ValueError:
                return None
        self.proxy.set_filters(self.search.text(), self.col_combo.currentData() if self.col_combo.currentData() is not None else -1,
                               self.num_combo.currentData() if self.num_combo.currentData() is not None else -1,
                               num(self.min_edit), num(self.max_edit))
        self._update_count()

    def _update_count(self) -> None:
        self.count_label.setText(f"{self.proxy.rowCount():,} of {self.model.rowCount():,} cases shown")

    def _clear_filters(self) -> None:
        self.search.clear()
        self.min_edit.clear()
        self.max_edit.clear()
        self.col_combo.setCurrentIndex(0)
        self.num_combo.setCurrentIndex(0)

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.refresh_btn.setEnabled(not busy)
        self.open_btn.setEnabled(not busy and self.table.selectionModel().hasSelection())
        if msg:
            self.status.setText(msg)

    # ------------------------------------------------------------ opening
    def _selected_case_id(self) -> Optional[int]:
        idx = self.table.selectionModel().selectedRows()
        if not idx or self.df is None or "caseid" not in self.df.columns:
            return None
        col = list(self.df.columns).index("caseid")
        v = self.proxy.data(self.proxy.index(idx[0].row(), col), Qt.UserRole)
        return int(v) if v is not None else None

    def open_selected(self) -> None:
        cid = self._selected_case_id()
        if cid is None:
            info(self, "Select a case", "Click a row in the table first.")
            return
        self.open_case(cid)

    def open_by_id(self) -> None:
        text = self.id_edit.text().strip()
        if not text.isdigit() or int(text) <= 0:
            warn(self, "Case ID", "Please type a whole-number case ID, for example 1.")
            return
        cid = int(text)
        if self.df is not None and "caseid" in self.df.columns and cid not in set(self.df["caseid"].astype(int)):
            if not confirm(self, "Case not in catalogue",
                           f"Case {cid} is not in the loaded catalogue. VitalDB may not have it. Try anyway?",
                           yes_text="Try anyway"):
                return
        self.open_case(cid)

    def open_case(self, case_id: int) -> None:
        if self._busy:
            return
        clinical = {}
        if self.df is not None and "caseid" in self.df.columns:
            row = self.df[self.df["caseid"] == case_id]
            if len(row):
                clinical = row.iloc[0].to_dict()
        self._set_busy(True, f"Opening case {case_id}...")

        def job(progress, token):
            return da.CaseStore(da.fetch_case_header(case_id, progress=progress, clinical=clinical))

        def done(store):
            self.session.open_case(store)
            src = "from local cache" if store.header.from_cache else "freshly retrieved"
            self.status.setText(f"Case {case_id} opened ({src}).")
            self._on_case_opened(store)

        def err(msg):
            self.status.setText("")
            warn(self, f"Could not open case {case_id}", msg)

        run_task(self, job, done, on_error=err, on_progress=self.status.setText,
                 on_finished=lambda: self._set_busy(False))
