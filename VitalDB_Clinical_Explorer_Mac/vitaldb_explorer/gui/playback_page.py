"""Playback: replay a case like a bedside monitor, with play/pause, speed, reverse and scrubbing.

The display is a trailing window that ends at the playhead. Values shown in the tiles are the most recent
recorded sample at or before the playhead; if the last sample is older than a limit the tile shows "--" rather
than a stale number. Nothing is interpolated, smoothed or judged against clinical limits (no alarm colours).
"""
from __future__ import annotations

import re
import time
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSlider, QVBoxLayout, QWidget)

from .. import knowledge as kn
from ..categories import KEY_SIGNALS
from ..data_access import KIND_NUMERIC, KIND_STRING, KIND_WAVE, CaseStore, RawTrack
from ..processing import reduce_for_display
from ..timeutil import TimeParseError, format_hms, parse_time
from .common import Session, make_page_header, warn
from .workers import TaskHandle, run_task

BG = "#0B1620"
PANEL = "#101F2C"
GRID = "#1F3345"
TXT = "#9FB3C4"
# classic monitor colours first, then a distinguishable fallback set
PALETTE = ["#4ADE80", "#F87171", "#38BDF8", "#FACC15", "#C084FC", "#FB923C", "#2DD4BF", "#F472B6"]
SPEEDS = [("Reverse 10x", -10.0), ("Reverse 5x", -5.0), ("Reverse 1x", -1.0), ("1x (real time)", 1.0), ("2x", 2.0),
          ("5x", 5.0), ("10x", 10.0), ("30x", 30.0), ("60x", 60.0), ("120x", 120.0)]
WINDOWS = [("10 seconds", 10.0), ("30 seconds", 30.0), ("1 minute", 60.0), ("5 minutes", 300.0),
           ("15 minutes", 900.0), ("1 hour", 3600.0)]
MAX_DRAW_POINTS = 1600
FRAME_MS = 60
SLIDER_STEPS_PER_S = 4
MAX_WAVE_DEFAULT = 3


def default_selection(store: CaseStore) -> list[str]:
    """Key monitor signals that exist in this case (a few waveforms, the numerics and pump tracks)."""
    names = list(store.header.tracks)
    picks: list[str] = []
    waves = 0
    for _label, pat in KEY_SIGNALS:
        rx = re.compile(pat, re.I)
        for n in names:
            if n in picks or not rx.search(n):
                continue
            ti = store.header.tracks[n]
            if ti.kind == KIND_STRING:
                continue
            if ti.kind == KIND_WAVE:
                if waves >= MAX_WAVE_DEFAULT or "EEG" in n.upper():
                    continue
                waves += 1
            picks.append(n)
    return picks[:12]


def tile_text(value: float) -> str:
    if value != value:
        return "--"
    a = abs(value)
    if a >= 1000:
        return f"{value:,.0f}"
    if a >= 100:
        return f"{value:.0f}"
    if a >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def current_value(raw: RawTrack, t: float) -> Optional[tuple[float, float]]:
    """(sample time, value) of the latest numeric sample at or before ``t``; None if none or too old."""
    if raw.t is None or len(raw.t) == 0:
        return None
    i = int(np.searchsorted(raw.t, t, side="right")) - 1
    if i < 0:
        return None
    ts, v = float(raw.t[i]), float(raw.v[i])
    spacing = float(np.median(np.diff(raw.t[: min(len(raw.t), 2000)]))) if len(raw.t) > 2 else 10.0
    limit = max(15.0, 6.0 * spacing)
    if t - ts > limit or v != v:
        return None
    return ts, v


class _Tile(QFrame):
    def __init__(self, title: str, unit: str, colour: str, tip: str):
        super().__init__()
        self.setObjectName("monTile")
        self.setStyleSheet(f"#monTile{{background:{PANEL};border:1px solid {GRID};border-radius:8px;}}")
        self.setToolTip(tip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{colour};font-weight:600;font-size:11px;background:transparent;")
        self.value = QLabel("--")
        self.value.setStyleSheet(f"color:{colour};font-size:28px;font-weight:600;background:transparent;")
        self.unit = QLabel(unit)
        self.unit.setStyleSheet(f"color:{TXT};font-size:10px;background:transparent;")
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.unit)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class PlaybackPage(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        self.store: Optional[CaseStore] = None
        self.names: list[str] = []
        self.raws: dict[str, RawTrack] = {}
        self.colours: dict[str, str] = {}
        self.lines = {}
        self.axes = {}
        self.tiles: dict[str, _Tile] = {}
        self.ylims: dict[str, tuple[float, float]] = {}
        self.event_list: list[tuple[float, str, str]] = []
        self._event_texts: list = []
        self.duration = 0.0
        self.pos = 0.0                      # playhead, seconds since start of recording
        self.speed = 1.0
        self.window_s = 60.0
        self.playing = False
        self._last_tick = 0.0
        self._handle: Optional[TaskHandle] = None
        self._loaded = False
        self._status_cache = ""

        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Playback", "Replay the case like a bedside monitor. Press Play, scrub the slider, run it in reverse or "
                        "jump between recorded events. Values shown are the latest recorded sample at the playhead; "
                        "nothing is interpolated and no alarm limits are applied."))

        # ---- track chooser
        top = QHBoxLayout()
        self.pick_btn = QPushButton("Choose tracks...")
        self.pick_btn.clicked.connect(self._toggle_chooser)
        self.load_btn = QPushButton("Load monitor")
        self.load_btn.setObjectName("primary")
        self.load_btn.clicked.connect(self.load_selected)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(lambda: self._handle and self._handle.cancel())
        self.status = QLabel("Open a case to begin.")
        self.status.setObjectName("note")
        top.addWidget(self.pick_btn)
        top.addWidget(self.load_btn)
        top.addWidget(self.cancel_btn)
        top.addWidget(self.status, 1)
        lay.addLayout(top)

        self.chooser = QListWidget()
        self.chooser.setSelectionMode(QAbstractItemView.NoSelection)
        self.chooser.setMaximumHeight(170)
        self.chooser.setVisible(False)
        lay.addWidget(self.chooser)

        # ---- monitor: tiles above, traces below
        self.tile_host = QWidget()
        self.tile_grid = QGridLayout(self.tile_host)
        self.tile_grid.setContentsMargins(0, 0, 0, 0)
        self.tile_grid.setSpacing(8)
        lay.addWidget(self.tile_host)

        self.figure = Figure(figsize=(9, 5), facecolor=BG)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(260)
        frame = QFrame()
        frame.setStyleSheet(f"background:{BG};border-radius:10px;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4)
        fl.addWidget(self.canvas)
        lay.addWidget(frame, 1)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_click)
        self._placeholder("Open a case, then press Load monitor.")

        # ---- transport
        tr = QHBoxLayout()
        self.b_start = QPushButton("|<")
        self.b_back = QPushButton("-10 s")
        self.b_play = QPushButton("Play")
        self.b_play.setObjectName("primary")
        self.b_fwd = QPushButton("+10 s")
        self.b_end = QPushButton(">|")
        self.b_prev_ev = QPushButton("< Event")
        self.b_next_ev = QPushButton("Event >")
        self.b_start.setToolTip("Go to the start")
        self.b_end.setToolTip("Go to the end")
        self.b_prev_ev.setToolTip("Jump to the previous recorded event")
        self.b_next_ev.setToolTip("Jump to the next recorded event")
        self.b_start.clicked.connect(lambda: self.seek(0.0))
        self.b_end.clicked.connect(lambda: self.seek(self.duration))
        self.b_back.clicked.connect(lambda: self.seek(self.pos - 10))
        self.b_fwd.clicked.connect(lambda: self.seek(self.pos + 10))
        self.b_play.clicked.connect(self.toggle_play)
        self.b_prev_ev.clicked.connect(lambda: self.jump_event(-1))
        self.b_next_ev.clicked.connect(lambda: self.jump_event(1))
        for b in (self.b_start, self.b_back, self.b_play, self.b_fwd, self.b_end, self.b_prev_ev, self.b_next_ev):
            tr.addWidget(b)
        tr.addSpacing(10)
        tr.addWidget(QLabel("Speed"))
        self.speed_box = QComboBox()
        for lab, v in SPEEDS:
            self.speed_box.addItem(lab, v)
        self.speed_box.setCurrentIndex(3)
        self.speed_box.currentIndexChanged.connect(self._speed_changed)
        tr.addWidget(self.speed_box)
        tr.addWidget(QLabel("Window"))
        self.win_box = QComboBox()
        for lab, v in WINDOWS:
            self.win_box.addItem(lab, v)
        self.win_box.setCurrentIndex(2)
        self.win_box.currentIndexChanged.connect(self._window_changed)
        tr.addWidget(self.win_box)
        tr.addWidget(QLabel("Go to"))
        self.goto_edit = QLineEdit()
        self.goto_edit.setPlaceholderText("e.g. 12:30")
        self.goto_edit.setFixedWidth(90)
        self.goto_edit.returnPressed.connect(self._goto_typed)
        tr.addWidget(self.goto_edit)
        tr.addStretch(1)
        lay.addLayout(tr)

        sr = QHBoxLayout()
        self.time_label = QLabel("0:00:00 / 0:00:00")
        self.time_label.setMinimumWidth(150)
        self.time_label.setStyleSheet("font-weight:600;")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(self._slider_moved)
        sr.addWidget(self.time_label)
        sr.addWidget(self.slider, 1)
        lay.addLayout(sr)
        self.event_label = QLabel("")
        self.event_label.setObjectName("note")
        self.event_label.setWordWrap(True)
        lay.addWidget(self.event_label)

        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._tick)

        sc_ctx = Qt.WidgetWithChildrenShortcut
        for key, fn in (("Space", self.toggle_play), ("Left", lambda: self.seek(self.pos - 5)),
                        ("Right", lambda: self.seek(self.pos + 5))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(sc_ctx)
            sc.activated.connect(fn)

        self._set_transport_enabled(False)
        session.case_opened.connect(self._on_case)

    # ------------------------------------------------------------------ case / loading
    def _on_case(self, store: CaseStore) -> None:
        self.pause()
        self.store = store
        self.raws = {}
        self.names = []
        self._loaded = False
        self.duration = float(store.header.duration_s or 0.0)
        self.pos = 0.0
        self._set_transport_enabled(False)
        self.chooser.clear()
        for n, ti in store.header.tracks.items():
            if ti.kind == KIND_STRING:
                continue
            it = QListWidgetItem(f"{n}   ({'waveform' if ti.kind == KIND_WAVE else 'numeric'}"
                                 f"{', ' + ti.unit if ti.unit else ''})")
            it.setData(Qt.UserRole, n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.chooser.addItem(it)
        sel = set(default_selection(store))
        for i in range(self.chooser.count()):
            it = self.chooser.item(i)
            if it.data(Qt.UserRole) in sel:
                it.setCheckState(Qt.Checked)
        self.status.setText(f"{len(sel)} default monitor track(s) selected. Press Load monitor (waveforms can take "
                            "a minute to download the first time).")
        self.load_btn.setEnabled(True)
        self._clear_tiles()
        self._placeholder("Press Load monitor to download the selected tracks.")

    def _toggle_chooser(self) -> None:
        self.chooser.setVisible(not self.chooser.isVisible())

    def selected_names(self) -> list[str]:
        return [self.chooser.item(i).data(Qt.UserRole) for i in range(self.chooser.count())
                if self.chooser.item(i).checkState() == Qt.Checked]

    def load_selected(self) -> None:
        if self.store is None:
            return
        names = self.selected_names()
        if not names:
            warn(self, "No tracks selected", "Tick at least one track with Choose tracks...")
            return
        self.pause()
        store = self.store
        self.load_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status.setText("Loading tracks...")

        def work(progress, token):
            return names, store.ensure_tracks(names, cancel=token, progress=progress)

        def done(res):
            if store is not self.store:
                return
            self._build(res[0], res[1])

        def failed(msg):
            warn(self, "Could not load tracks", msg)
            self.status.setText(msg)

        self._handle = run_task(self, work, done, on_error=failed, on_progress=self.status.setText,
                                on_cancelled=lambda: self.status.setText("Loading cancelled."),
                                on_finished=self._load_finished)

    def _load_finished(self) -> None:
        self.load_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    # ------------------------------------------------------------------ build the monitor
    def _clear_tiles(self) -> None:
        for t in self.tiles.values():
            t.setParent(None)
            t.deleteLater()
        self.tiles = {}

    def _robust_limits(self, raw: RawTrack) -> tuple[float, float]:
        if raw.info.kind == KIND_WAVE:
            parts = [np.asarray(s[:: max(1, len(s) // 20000)], dtype=float) for s in (raw.segments or [])]
            v = np.concatenate(parts) if parts else np.empty(0)
        else:
            v = np.asarray(raw.v, dtype=float) if raw.v is not None else np.empty(0)
        v = v[np.isfinite(v)]
        if len(v) == 0:
            return 0.0, 1.0
        lo, hi = np.percentile(v, [0.5, 99.5])
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = 0.08 * (hi - lo)
        return float(lo - pad), float(hi + pad)

    def _build(self, names: list[str], raws: dict[str, RawTrack]) -> None:
        self.names = sorted([n for n in names if n in raws], key=lambda n: raws[n].info.kind != KIND_WAVE)   # waveforms on top, as on a monitor
        self.raws = raws
        spans = [raws[n].span() for n in self.names if raws[n].span()]
        if not self.duration and spans:
            self.duration = max(s[1] for s in spans)
        self.event_list = []
        if self.store is not None:
            ev_tracks = [n for n, ti in self.store.header.tracks.items() if ti.kind == KIND_STRING]
            if ev_tracks:
                try:
                    self.store.ensure_tracks(ev_tracks)
                except Exception:        # events are optional context; playback works without them
                    pass
            self.event_list = self.store.events()
        # colours: classic monitor colours for ECG/ART/PLETH, then the palette
        self.colours = {}
        pal = list(PALETTE)
        for n in self.names:
            u = n.upper()
            if "ECG" in u:
                self.colours[n] = PALETTE[0]
            elif re.search(r"(^|/)ART($|_WAV)", u) or re.search(r"ART_(SBP|DBP|MBP)", u):
                self.colours[n] = PALETTE[1]
            elif "PLETH" in u or "SPO2" in u:
                self.colours[n] = PALETTE[2]
        i = 3
        for n in self.names:
            if n not in self.colours:
                self.colours[n] = pal[i % len(pal)]
                i += 1
        self.ylims = {n: self._robust_limits(raws[n]) for n in self.names}

        # tiles for numeric tracks
        self._clear_tiles()
        nums = [n for n in self.names if raws[n].info.kind == KIND_NUMERIC]
        for k, n in enumerate(nums):
            ti = raws[n].info
            title = self._tile_title(n)
            tip = n
            o = kn.parse_orchestra(n)
            if o:
                tip = f"{n}\n{kn.KIND_TEXT[o.kind][1]}"
            tile = _Tile(title, ti.unit or "", self.colours[n], tip)
            self.tiles[n] = tile
            self.tile_grid.addWidget(tile, k // 6, k % 6)

        # figure
        self.figure.clear()
        self.figure.set_facecolor(BG)
        n_ax = len(self.names)
        if n_ax == 0:
            self._placeholder("No tracks loaded.")
            return
        axs = self.figure.subplots(n_ax, 1, sharex=True)
        axs = np.atleast_1d(axs)
        self.axes, self.lines = {}, {}
        for ax, n in zip(axs, self.names):
            ti = raws[n].info
            ax.set_facecolor(BG)
            for sp in ax.spines.values():
                sp.set_visible(False)
            ax.grid(True, color=GRID, lw=0.6)
            ax.tick_params(colors=TXT, labelsize=7, length=0)
            ax.set_ylim(*self.ylims[n])
            ax.yaxis.set_major_locator(__import__("matplotlib.ticker", fromlist=["MaxNLocator"]).MaxNLocator(3))
            col = self.colours[n]
            (ln,) = ax.plot([], [], color=col, lw=1.1 if ti.kind == KIND_WAVE else 1.6,
                            drawstyle="steps-post" if ti.kind == KIND_NUMERIC else "default")
            ax.text(0.004, 0.93, f"{self._tile_title(n)}  {ti.unit}".strip(), transform=ax.transAxes, va="top",
                    fontsize=8, fontweight="bold", color=col)
            self.axes[n], self.lines[n] = ax, ln
        self.playhead = [ax.axvline(0, color="#E2E8F0", lw=1.0, alpha=0.9) for ax in axs]
        # events as faint dashed verticals (one collection per axis) + labels on the top axis
        self._event_texts = []
        if self.event_list:
            times = [e[0] for e in self.event_list]
            for ax, n in zip(axs, self.names):
                y0, y1 = self.ylims[n]
                segs = [[(t, y0), (t, y1)] for t in times]
                ax.add_collection(LineCollection(segs, colors="#F59E0B", linestyles="dashed", linewidths=0.8,
                                                 alpha=0.8))
            for t, _trk, text in self.event_list:
                tx = axs[0].text(t, 0.04, " " + text[:40], transform=axs[0].get_xaxis_transform(), rotation=90,
                                 va="bottom", ha="right", fontsize=7, color="#F59E0B", clip_on=True)
                self._event_texts.append((t, tx))
        axs[-1].tick_params(labelsize=8)
        from matplotlib.ticker import FuncFormatter
        axs[-1].xaxis.set_major_formatter(FuncFormatter(lambda x, _p: format_hms(max(0.0, x))))
        self.figure.subplots_adjust(left=0.04, right=0.995, top=0.985, bottom=0.07, hspace=0.12)

        self._loaded = True
        n_ev = len(self.event_list)
        self.slider.blockSignals(True)
        self.slider.setRange(0, int(self.duration * SLIDER_STEPS_PER_S))
        self.slider.blockSignals(False)
        self._set_transport_enabled(True)
        self.b_prev_ev.setEnabled(n_ev > 0)
        self.b_next_ev.setEnabled(n_ev > 0)
        self.status.setText(f"Monitor ready: {len(self.names)} track(s), {n_ev} recorded event(s). "
                            "Space = play/pause, Left/Right = 5 s.")
        self.seek(self.pos)

    @staticmethod
    def _tile_title(name: str) -> str:
        o = kn.parse_orchestra(name)
        if o:
            return f"{o.drug} {o.kind}"
        return name.split("/", 1)[-1]

    def _placeholder(self, text: str) -> None:
        self.figure.clear()
        self.figure.set_facecolor(BG)
        self.figure.text(0.5, 0.5, text, color=TXT, ha="center", va="center", fontsize=11)
        self.canvas.draw_idle()
        self.axes, self.lines = {}, {}
        self._event_texts = []

    # ------------------------------------------------------------------ transport
    def _set_transport_enabled(self, on: bool) -> None:
        for w in (self.b_start, self.b_back, self.b_play, self.b_fwd, self.b_end, self.b_prev_ev, self.b_next_ev,
                  self.slider, self.speed_box, self.win_box, self.goto_edit):
            w.setEnabled(on)

    def toggle_play(self) -> None:
        if not self._loaded:
            return
        self.pause() if self.playing else self.play()

    def play(self) -> None:
        if not self._loaded:
            return
        if self.speed > 0 and self.pos >= self.duration:
            self.pos = 0.0
        if self.speed < 0 and self.pos <= 0:
            self.pos = self.duration
        self.playing = True
        self._last_tick = time.monotonic()
        self.b_play.setText("Pause")
        self.timer.start()

    def pause(self) -> None:
        self.playing = False
        self.timer.stop()
        self.b_play.setText("Play")

    def _speed_changed(self) -> None:
        self.speed = float(self.speed_box.currentData())
        self._last_tick = time.monotonic()

    def _window_changed(self) -> None:
        self.window_s = float(self.win_box.currentData())
        self._refresh()

    def _goto_typed(self) -> None:
        try:
            t = parse_time(self.goto_edit.text())
        except (TimeParseError, ValueError) as exc:
            warn(self, "Time not understood", str(exc))
            return
        self.seek(t)

    def seek(self, t: float) -> None:
        self.pos = float(min(max(0.0, t), self.duration))
        self._refresh()

    def jump_event(self, direction: int) -> None:
        if not self.event_list:
            return
        times = sorted({e[0] for e in self.event_list})
        eps = 0.5
        if direction > 0:
            cand = [t for t in times if t > self.pos + eps]
            target = cand[0] if cand else times[-1]
        else:
            cand = [t for t in times if t < self.pos - eps]
            target = cand[-1] if cand else times[0]
        self.seek(target)

    def _slider_moved(self, v: int) -> None:
        self.seek(v / SLIDER_STEPS_PER_S)

    def _on_canvas_click(self, event) -> None:
        if self._loaded and event.xdata is not None and event.inaxes is not None:
            self.seek(float(event.xdata))

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        new = self.pos + dt * self.speed
        if new >= self.duration and self.speed > 0:
            self.pos = self.duration
            self.pause()
        elif new <= 0 and self.speed < 0:
            self.pos = 0.0
            self.pause()
        else:
            self.pos = new
        self._refresh()

    # ------------------------------------------------------------------ drawing
    def _refresh(self) -> None:
        self.time_label.setText(f"{format_hms(self.pos)} / {format_hms(self.duration)}")
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(self.pos * SLIDER_STEPS_PER_S)))
        self.slider.blockSignals(False)
        if not self._loaded:
            return
        w = self.window_s
        x1 = max(self.pos, w) if self.pos < w else self.pos
        x0 = x1 - w
        for n in self.names:
            raw = self.raws[n]
            t, v = raw.window(x0, x1)
            if len(t):
                ds = reduce_for_display(t, v, raw.info.kind, MAX_DRAW_POINTS)
                tt, vv = ds.t, ds.v
                mask = tt <= self.pos
                tt, vv = tt[mask], vv[mask]
            else:
                tt, vv = np.empty(0), np.empty(0)
            self.lines[n].set_data(tt, vv)
        ax0 = next(iter(self.axes.values()))
        ax0.set_xlim(x0, x1)                     # axes share x
        for ph in self.playhead:
            ph.set_xdata([self.pos, self.pos])
        for t, tx in self._event_texts:
            tx.set_visible(x0 <= t <= x1)
        for n, tile in self.tiles.items():
            cv = current_value(self.raws[n], self.pos)
            tile.set_value("--" if cv is None else tile_text(cv[1]))
        self._update_event_label()
        self.canvas.draw_idle()

    def _update_event_label(self) -> None:
        past = [e for e in self.event_list if e[0] <= self.pos + 0.5]
        if past:
            t, trk, text = past[-1]
            self.event_label.setText(f"Latest recorded event: {format_hms(t)}  {text}")
        else:
            self.event_label.setText("No recorded event yet at this point." if self.event_list else
                                     "No recorded event (text) tracks in this case.")
