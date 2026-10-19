"""Shared GUI building blocks: session state, table model, dialogs, small helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import config
from ..data_access import CaseStore


class Session(QObject):
    """State shared by all pages: the open case and cross-page requests."""

    case_opened = Signal(object)          # CaseStore
    add_to_plot_requested = Signal(list)  # list of track names
    status_message = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.store: Optional[CaseStore] = None
        self.catalogue: Optional[pd.DataFrame] = None

    def open_case(self, store: CaseStore) -> None:
        self.store = store
        self.case_opened.emit(store)


class PandasModel(QAbstractTableModel):
    """Read-only table model over a DataFrame (sorting is done by a proxy model)."""

    def __init__(self, df: Optional[pd.DataFrame] = None):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()

    def set_frame(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._df.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        val = self._df.iat[index.row(), index.column()]
        if role == Qt.DisplayRole:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return ""
            if isinstance(val, float):
                return f"{val:.6g}"
            return str(val)
        if role == Qt.EditRole or role == Qt.UserRole:  # used for sorting
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None
            return val.item() if hasattr(val, "item") else val
        if role == Qt.TextAlignmentRole and pd.api.types.is_numeric_dtype(self._df.dtypes.iloc[index.column()]):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return str(self._df.columns[section]) if orientation == Qt.Horizontal else str(section + 1)
        return None

    def frame(self) -> pd.DataFrame:
        return self._df


def make_page_header(title: str, hint: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 10)
    lay.setSpacing(3)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    h = QLabel(hint)
    h.setObjectName("pageHint")
    h.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(h)
    return w


def info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def confirm(parent, title: str, text: str, yes_text: str = "Continue") -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, parent)
    yes = box.addButton(yes_text, QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is yes


def last_dir() -> str:
    d = config.load_settings().get("last_export_dir")
    return d if d and Path(d).is_dir() else str(Path.home())


def remember_dir(path: str | Path) -> None:
    config.save_setting("last_export_dir", str(Path(path).parent if Path(path).is_file() else path))


def choose_save_file(parent, title: str, default_name: str, filters: str) -> Optional[str]:
    start = str(Path(last_dir()) / default_name)
    path, _ = QFileDialog.getSaveFileName(parent, title, start, filters)
    if path:
        remember_dir(path)
        return path
    return None


class TableDialog(QDialog):
    """Simple dialog that shows rows of text in a table, with optional note."""

    def __init__(self, parent, title: str, headers: list[str], rows: list[list[str]], note: str = "",
                 width: int = 760, height: int = 520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)
        lay = QVBoxLayout(self)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setObjectName("note")
            lay.addWidget(n)
        tbl = QTableWidget(len(rows), len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tbl.setItem(r, c, QTableWidgetItem(str(val)))
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(tbl)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self.table = tbl


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: #d5dde3;")
    return f


def row_layout(*widgets, stretch_last: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(8)
    for w in widgets:
        if isinstance(w, int):
            lay.addSpacing(w)
        else:
            lay.addWidget(w)
    if stretch_last:
        lay.addStretch(1)
    return lay
