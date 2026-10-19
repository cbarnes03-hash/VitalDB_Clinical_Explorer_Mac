"""Event-centred exploration: pick a time, view before/after, mark and export."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..data_access import KIND_STRING, CaseStore
from ..timeutil import TimeParseError, format_hms, parse_time
from .common import Session, info, make_page_header, warn
from .plot_panel import PlotPanel
from .tracks_page import TrackPickerDialog
from .workers import run_task


class EventPage(QWidget):
    def __init__(self, session: Session, plot_page=None):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        self.plot_page = plot_page
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Event-centred exploration", "Pick a time (click on the plot or type it), choose how much to show "
                                         "before and after, add a marker, then save the plot or export the data. "
                                         "A marker is only a label you place; it does not mean the application "
                                         "detected anything."))

        top = QGroupBox("Time point and interval")
        tl = QHBoxLayout(top)
        self.time_edit = QLineEdit()
        self.time_edit.setPlaceholderText("time e.g. 45:30 (or click on the plot)")
        self.before = QLineEdit("2")
        self.after = QLineEdit("2")
        for e in (self.before, self.after):
            e.setFixedWidth(50)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("optional annotation")
        show = QPushButton("Show window")
        show.setObjectName("primary")
        show.clicked.connect(self.show_window)
        mark = QPushButton("Add marker")
        mark.clicked.connect(self.add_marker)
        clr = QPushButton("Clear markers")
        clr.clicked.connect(lambda: self.panel.clear_markers())
        tl.addWidget(QLabel("Time:"))
        tl.addWidget(self.time_edit, 1)
        tl.addWidget(QLabel("before (min)"))
        tl.addWidget(self.before)
        tl.addWidget(QLabel("after (min)"))
        tl.addWidget(self.after)
        tl.addWidget(self.note_edit, 1)
        tl.addWidget(mark)
        tl.addWidget(clr)
        tl.addWidget(show)
        lay.addWidget(top)

        r = QHBoxLayout()
        pick = QPushButton("Choose tracks...")
        pick.clicked.connect(self._pick_tracks)
        copy = QPushButton("Use tracks from Plot workspace")
        copy.clicked.connect(self._copy_tracks)
        r.addWidget(pick)
        r.addWidget(copy)
        r.addStretch(1)
        lay.addLayout(r)

        split = QSplitter(Qt.Horizontal)
        self.panel = PlotPanel(session)
        split.addWidget(self.panel)

        right = QGroupBox("Events and annotations recorded in this case")
        rl = QVBoxLayout(right)
        self.load_btn = QPushButton("Load event/annotation tracks")
        self.load_btn.setToolTip("Downloads the text/event tracks of this case, if any")
        self.load_btn.clicked.connect(self._load_events)
        rl.addWidget(self.load_btn)
        self.ev_table = QTableWidget(0, 3)
        self.ev_table.setHorizontalHeaderLabels(["Time", "Track", "Text"])
        self.ev_table.verticalHeader().setVisible(False)
        self.ev_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ev_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.ev_table.cellDoubleClicked.connect(self._event_chosen)
        rl.addWidget(self.ev_table, 1)
        self.ev_status = QLabel("Double-click an event to centre the plot on it.")
        self.ev_status.setObjectName("note")
        self.ev_status.setWordWrap(True)
        rl.addWidget(self.ev_status)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setSizes([950, 330])
        lay.addWidget(split, 1)

        self._events: list[tuple[float, str, str]] = []
        self.panel.clicked.connect(lambda t: self.time_edit.setText(format_hms(t, 1)))
        session.case_opened.connect(self._on_case)

    def _on_case(self, store: CaseStore) -> None:
        self._events = []
        self.ev_table.setRowCount(0)
        self.time_edit.clear()
        self.ev_status.setText("Double-click an event to centre the plot on it.")

    # ------------------------------------------------------------ tracks
    def _pick_tracks(self) -> None:
        if self.session.store is None:
            return warn(self, "No case open", "Open a case first.")
        dlg = TrackPickerDialog(self, self.session.store, "Tracks around the event")
        if dlg.exec():
            self.panel.add_tracks(dlg.chosen())

    def _copy_tracks(self) -> None:
        if self.plot_page is None or not self.plot_page.panel.track_names:
            return info(self, "No tracks", "The Plot workspace has no tracks yet.")
        self.panel.set_tracks(list(self.plot_page.panel.track_names))

    # ------------------------------------------------------------ events
    def _load_events(self) -> None:
        store = self.session.store
        if store is None:
            return warn(self, "No case open", "Open a case first.")
        names = [n for n, t in store.header.tracks.items() if t.kind == KIND_STRING]
        if not names:
            self.ev_status.setText("This case has no text/event tracks.")
            return info(self, "No events", "This case does not contain any text/event tracks.")
        self.load_btn.setEnabled(False)
        self.ev_status.setText("Loading events...")

        def job(progress, token):
            store.ensure_tracks(names, token, progress)
            return store.events()

        def done(events):
            self._events = events
            self.ev_table.setRowCount(len(events))
            for r, (t, trk, text) in enumerate(events):
                for c, val in enumerate((format_hms(t, 1), trk, text)):
                    self.ev_table.setItem(r, c, QTableWidgetItem(val))
            self.ev_table.resizeColumnsToContents()
            self.ev_status.setText(f"{len(events)} event(s) from {len(names)} track(s). Event text is shown as "
                                   "recorded; it is not interpreted.")

        run_task(self, job, done, on_error=lambda m: (self.ev_status.setText(""), warn(self, "Could not load events", m)),
                 on_finished=lambda: self.load_btn.setEnabled(True))

    def _event_chosen(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._events):
            t, _trk, text = self._events[row]
            self.time_edit.setText(format_hms(t, 1))
            if not self.note_edit.text():
                self.note_edit.setText(text[:60])
            self.show_window()

    # ------------------------------------------------------------ actions
    def _time_and_span(self):
        t = parse_time(self.time_edit.text())
        b = float(self.before.text()) * 60
        a = float(self.after.text()) * 60
        if b < 0 or a < 0:
            raise ValueError
        return t, b, a

    def show_window(self) -> None:
        try:
            t, b, a = self._time_and_span()
        except TimeParseError as exc:
            return warn(self, "Time", str(exc))
        except ValueError:
            return warn(self, "Interval", "Enter 'before' and 'after' as numbers of minutes (0 or more).")
        if not self.panel.track_names:
            return info(self, "No tracks", "Choose tracks first (Choose tracks... or Use tracks from Plot workspace).")
        self.panel.set_window(max(0.0, t - b), t + a)
        if not any(abs(mt - t) < 1e-6 for mt, _ in self.panel.markers):
            self.panel.markers.append((t, self.note_edit.text().strip() or "event time"))
        self.panel.plot()

    def add_marker(self) -> None:
        try:
            t, _b, _a = self._time_and_span()
        except TimeParseError as exc:
            return warn(self, "Time", str(exc))
        except ValueError:
            t = parse_time(self.time_edit.text())
        self.panel.add_marker(t, self.note_edit.text().strip() or "marker")
