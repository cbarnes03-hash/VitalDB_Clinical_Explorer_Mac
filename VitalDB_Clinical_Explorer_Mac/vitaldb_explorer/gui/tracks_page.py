"""Track browser: search, categorise, inspect and send tracks to the plot."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import knowledge as kn
from ..categories import CATEGORY_ORDER, drug_info, CAT_DRUG
from ..data_access import KIND_STRING, CaseStore
from ..processing import describe_track
from ..timeutil import format_hms
from .common import Session, TableDialog, info, make_page_header, warn
from .workers import run_task


class TrackTree(QTreeWidget):
    """Tracks grouped by category, with search and multi-selection."""

    COLS = ["Track (as named in VitalDB)", "Type", "Unit", "Sampling", "Loaded"]

    def __init__(self) -> None:
        super().__init__()
        self.setColumnCount(len(self.COLS))
        self.setHeaderLabels(self.COLS)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self._store: Optional[CaseStore] = None

    def load(self, store: Optional[CaseStore]) -> None:
        self._store = store
        self.clear()
        if store is None:
            return
        by_cat: dict[str, list] = {}
        for t in store.header.tracks.values():
            by_cat.setdefault(t.category, []).append(t)
        for cat in CATEGORY_ORDER:
            tracks = by_cat.get(cat)
            if not tracks:
                continue
            parent = QTreeWidgetItem([f"{cat}  ({len(tracks)})"])
            parent.setFlags(Qt.ItemIsEnabled)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.addTopLevelItem(parent)
            for t in sorted(tracks, key=lambda x: x.name.lower()):
                it = QTreeWidgetItem([t.name, t.kind, t.unit or "-", t.srate_text, ""])
                it.setData(0, Qt.UserRole, t.name)
                parent.addChild(it)
            parent.setExpanded(True)
        for c in range(len(self.COLS)):
            self.resizeColumnToContents(c)
        self.refresh_loaded()

    def refresh_loaded(self) -> None:
        if not self._store:
            return
        loaded = set(self._store.loaded_names())
        for i in range(self.topLevelItemCount()):
            p = self.topLevelItem(i)
            for j in range(p.childCount()):
                ch = p.child(j)
                ch.setText(4, "yes" if ch.data(0, Qt.UserRole) in loaded else "")

    def filter(self, text: str, category: Optional[str]) -> int:
        t = text.strip().lower()
        shown = 0
        for i in range(self.topLevelItemCount()):
            p = self.topLevelItem(i)
            cat_ok = category is None or p.text(0).startswith(category)
            vis_children = 0
            for j in range(p.childCount()):
                ch = p.child(j)
                ok = cat_ok and (not t or t in ch.text(0).lower())
                ch.setHidden(not ok)
                vis_children += ok
            p.setHidden(vis_children == 0)
            shown += vis_children
        return shown

    def selected_names(self) -> list[str]:
        return [i.data(0, Qt.UserRole) for i in self.selectedItems() if i.data(0, Qt.UserRole)]


class TrackPickerDialog(QDialog):
    """Choose any tracks of the open case (used by the Event and Drug pages)."""

    def __init__(self, parent, store: CaseStore, title: str = "Choose tracks"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search track names...")
        lay.addWidget(self.search)
        self.tree = TrackTree()
        self.tree.load(store)
        lay.addWidget(self.tree, 1)
        self.search.textChanged.connect(lambda t: self.tree.filter(t, None))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self) -> list[str]:
        return self.tree.selected_names()


class TracksPage(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Track browser", "All tracks recorded in the open case, grouped by name-based category. Names are shown "
                             "exactly as returned by VitalDB. Select one or more tracks, then Add to plot."))
        r = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search track names (for example HR, BIS, RFTN)...")
        self.search.textChanged.connect(self._filter)
        self.cat = QComboBox()
        self.cat.addItem("All categories", None)
        for c in CATEGORY_ORDER:
            self.cat.addItem(c, c)
        self.cat.currentIndexChanged.connect(self._filter)
        r.addWidget(self.search, 1)
        r.addWidget(self.cat)
        lay.addLayout(r)
        self.banner = QLabel("")
        self.banner.setObjectName("statusWarn")
        self.banner.setVisible(False)
        lay.addWidget(self.banner)
        self.tree = TrackTree()
        self.tree.itemDoubleClicked.connect(lambda *_: self._inspect())
        lay.addWidget(self.tree, 1)
        note = QLabel("Categories are guessed from track names to help navigation; they are not a verified "
                      "description of what a track measures. Sampling rates are nominal values from the file header.")
        note.setObjectName("note")
        note.setWordWrap(True)
        lay.addWidget(note)
        r2 = QHBoxLayout()
        self.add_btn = QPushButton("Add to plot")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add)
        self.inspect_btn = QPushButton("Inspect track (metadata and statistics)...")
        self.inspect_btn.clicked.connect(self._inspect)
        r2.addWidget(self.add_btn)
        r2.addWidget(self.inspect_btn)
        r2.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("note")
        r2.addWidget(self.status)
        lay.addLayout(r2)
        session.case_opened.connect(self._on_case)

    def _on_case(self, store: CaseStore) -> None:
        self.search.clear()
        self.cat.setCurrentIndex(0)
        self.tree.load(store)
        self.banner.setVisible(False)
        self.status.setText(f"{len(store.header.tracks)} tracks in case {store.case_id}.")

    def _filter(self) -> None:
        shown = self.tree.filter(self.search.text(), self.cat.currentData())
        txt = self.search.text().strip()
        if shown == 0 and self.session.store is not None:
            what = f"matching '{txt}'" if txt else "in this category"
            self.banner.setText(f"No track {what} is present in case {self.session.store.case_id}. "
                                "Other cases may have it; track availability differs between cases.")
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)

    def _add(self) -> None:
        names = self.tree.selected_names()
        if not names:
            info(self, "Select tracks", "Click one or more tracks in the list first (Ctrl+click for several).")
            return
        self.session.add_to_plot_requested.emit(names)
        self.status.setText(f"{len(names)} track(s) added to the Plot workspace.")

    def _inspect(self) -> None:
        store = self.session.store
        names = self.tree.selected_names()
        if store is None or not names:
            info(self, "Select a track", "Click a track in the list first.")
            return
        name = names[0]
        ti = store.header.tracks[name]
        dur = store.header.duration_s or 0.0
        self.status.setText(f"Loading {name}...")

        def job(progress, token):
            raws = store.ensure_tracks([name], token, progress)
            return describe_track(raws[name], 0.0, max(dur, 1.0)), raws[name].span()

        def done(res):
            st, span = res
            rows = [["Original track name", ti.name], ["Device", ti.device or "not provided"],
                    ["Type", ti.kind], ["Unit", ti.unit_text], ["Sampling", ti.srate_text],
                    ["Category (name-based guess)", ti.category]]
            dev_desc = kn.describe_device(ti.device)
            if dev_desc:
                rows.append(["Monitor (supervisor's dictionary)", dev_desc])
            if ti.category == CAT_DRUG:
                di = drug_info(ti.name)
                rows += [["Drug", (di.drug + (f" {di.concentration}" if di.concentration else "")) or "unknown"],
                         ["Interpretation", di.kind], ["Drug class", di.drug_class or "not classified"],
                         ["Defined in supervisor's dictionary", "yes" if di.verified else "NO - inferred from name"],
                         ["Caution", di.note]]
                if di.clinical_note:
                    rows.append(["Typical use (supervisor)", di.clinical_note])
            if span:
                rows.append(["First / last sample", f"{format_hms(span[0])} / {format_hms(span[1])}"])
            rows.append(["--- Statistics over the whole recording ---", ""])
            rows += [[k, v] for k, v in st.as_rows()[3:]]
            rows.append(["Exploratory flags", "; ".join(st.warnings) or "none"])
            self.tree.refresh_loaded()
            self.status.setText("")
            TableDialog(self, f"Track: {name}", ["Property", "Value"], rows,
                        note="Statistics are descriptive. Flags are exploratory checks, not clinically validated "
                             "signal-quality judgements.", width=720, height=620).exec()

        run_task(self, job, done, on_error=lambda m: (self.status.setText(""), warn(self, "Could not load track", m)),
                 on_progress=self.status.setText)
