"""Interactive multi-track plotting workspace (used by the Plot, Drug and Event pages).

Key behaviours
--------------
* One vertically aligned axis per track by default (optionally tracks with the
  same unit share an axis) so unrelated scales are never mixed.
* Time axis is elapsed time since the start of the recording (h:mm:ss), shared
  by all axes.
* Long tracks are reduced for *display only* with min/max decimation; when the
  user zooms or pans, the visible range is re-reduced from the original
  in-memory samples, so detail returns as you zoom in.
* Lines are broken across substantial gaps; missing data are never bridged.
* The cursor shows the nearest *original* sample for every plotted track.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter, QVBoxLayout, QWidget, QAbstractItemView)

from .. import config
from ..data_access import KIND_STRING, KIND_WAVE, CaseStore, DataAccessError, RawTrack
from ..processing import (DisplaySeries, ProcessingError, describe_track, estimate_rows, gap_threshold,
                          reduce_for_display, validate_window)
from ..timeutil import TimeParseError, format_hms, parse_time
from . import style
from .common import Session, TableDialog, confirm, info, make_page_header, warn
from .workers import TaskHandle, run_task


def _hms_formatter() -> FuncFormatter:
    return FuncFormatter(lambda x, _pos: format_hms(x))


class PlotPanel(QWidget):
    clicked = Signal(float)     # time (s) clicked on the plot
    plotted = Signal()          # a new plot has been drawn

    def __init__(self, session: Session, parent: Optional[QWidget] = None, allow_track_list: bool = True):
        super().__init__(parent)
        self.session = session
        self.track_names: list[str] = []
        self.markers: list[tuple[float, str]] = []
        self.overlays: list[tuple[float, str]] = []     # read-only event markers supplied by other pages
        self._series: dict[str, DisplaySeries] = {}
        self._raws: dict[str, RawTrack] = {}
        self._axes_by_track: dict[str, object] = {}
        self._lines: dict[str, object] = {}
        self._cursor_lines: list = []
        self._window: Optional[tuple[float, float]] = None
        self._task: Optional[TaskHandle] = None
        self._render_gen = 0
        self._plot_gen = 0
        self._plotted: list[str] = []
        self._suppress_limits = False
        self._rerender_timer = QTimer(self)
        self._rerender_timer.setSingleShot(True)
        self._rerender_timer.setInterval(350)
        self._rerender_timer.timeout.connect(self._rerender_visible)
        self._build_ui(allow_track_list)
        session.case_opened.connect(self._on_case_opened)

    # ------------------------------------------------------------------ UI
    def _build_ui(self, allow_track_list: bool) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # row 1: time window
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Start:"))
        self.start_edit = QLineEdit("0:00")
        self.start_edit.setFixedWidth(90)
        r1.addWidget(self.start_edit)
        r1.addWidget(QLabel("End:"))
        self.end_edit = QLineEdit("15:00")
        self.end_edit.setFixedWidth(90)
        r1.addWidget(self.end_edit)
        compact = not allow_track_list
        hint = QLabel("elapsed time since start of recording: mm:ss, h:mm:ss, or minutes")
        hint.setObjectName("note")
        hint.setVisible(not compact)
        r1.addWidget(hint)
        r1.addStretch(1)
        presets = (("5 min", lambda: self._preset(0, 300)), ("15 min", lambda: self._preset(0, 900)),
                   ("1 hour", lambda: self._preset(0, 3600)), ("All", self._preset_all)) if compact else (
                   ("First 5 min", lambda: self._preset(0, 300)), ("First 15 min", lambda: self._preset(0, 900)),
                   ("First hour", lambda: self._preset(0, 3600)), ("Entire recording", self._preset_all))
        for label, fn in presets:
            b = QPushButton(label)
            b.setObjectName("chipButton")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            r1.addWidget(b)
        root.addLayout(r1)

        # row 2: display options and actions
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Detail (points):" if compact else "Display detail (points per track):"))
        self.points_combo = QComboBox()
        for n in config.PLOT_POINT_CHOICES:
            self.points_combo.addItem(f"{n:,}", n)
        self.points_combo.setCurrentIndex(config.PLOT_POINT_CHOICES.index(config.DEFAULT_MAX_PLOT_POINTS))
        self.points_combo.setToolTip("Only affects what is drawn. Exports always use the original samples.")
        r2.addWidget(self.points_combo)
        self.group_units = QCheckBox("Share axis for same unit" if compact else "Share one axis between tracks with the same unit")
        r2.addWidget(self.group_units)
        r2.addStretch(1)
        self.plot_btn = QPushButton("Plot")
        self.plot_btn.setObjectName("primary")
        self.plot_btn.clicked.connect(self.plot)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        r2.addWidget(self.plot_btn)
        r2.addWidget(self.cancel_btn)
        root.addLayout(r2)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("note")
        root.addWidget(self.progress_label)

        split = QSplitter(Qt.Horizontal)
        # left: track list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Tracks in this plot (top to bottom):"))
        self.track_list = QListWidget()
        self.track_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.track_list.setWordWrap(True)
        ll.addWidget(self.track_list, 1)
        br = QHBoxLayout()
        for text, fn in (("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1)),
                         ("Remove", self.remove_selected), ("Clear", self.clear_tracks)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            br.addWidget(b)
        ll.addLayout(br)
        left.setVisible(allow_track_list)
        split.addWidget(left)

        # right: canvas
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(9, 6), dpi=100, facecolor="white")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        plot_card = QFrame()
        plot_card.setObjectName("card")
        pcl = QVBoxLayout(plot_card)
        pcl.setContentsMargins(6, 6, 6, 6)
        pcl.setSpacing(0)
        pcl.addWidget(self.toolbar)
        pcl.addWidget(self.canvas, 1)
        rl.addWidget(plot_card, 1)
        self.readout = QLabel("Move the mouse over the plot to read values. Click to select a time.")
        self.readout.setWordWrap(True)
        self.readout.setMinimumHeight(38)
        rl.addWidget(self.readout)
        self.note_label = QLabel("")
        self.note_label.setObjectName("note")
        self.note_label.setWordWrap(True)
        rl.addWidget(self.note_label)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 900])
        root.addWidget(split, 1)

        # bottom actions
        r3 = QHBoxLayout()
        self.save_btn = QPushButton("Save plot...")
        self.save_btn.clicked.connect(self.save_plot)
        self.view_btn = QPushButton("Use current view as interval")
        self.view_btn.setToolTip("Copy the zoomed-in time range into the Start/End boxes")
        self.view_btn.clicked.connect(self._use_view)
        self.stats_btn = QPushButton("Statistics for plotted tracks...")
        self.stats_btn.clicked.connect(self.show_stats)
        self.export_btn = QPushButton("Export data for this interval...")
        self.export_btn.clicked.connect(self.export_data)
        if compact:
            self.save_btn.setText("Save...")
            self.view_btn.setText("View as interval")
            self.stats_btn.setText("Statistics...")
            self.export_btn.setText("Export...")
        for b in (self.save_btn, self.view_btn, self.stats_btn, self.export_btn):
            r3.addWidget(b)
        r3.addStretch(1)
        root.addLayout(r3)

        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.canvas.mpl_connect("figure_leave_event", lambda e: self._hide_cursor())
        self._draw_placeholder("Open a case and choose tracks, then press Plot.")

    # ------------------------------------------------------------ case handling
    def _on_case_opened(self, store: CaseStore) -> None:
        self.track_names.clear()
        self.markers.clear()
        self.overlays.clear()
        self._series.clear()
        self._raws.clear()
        self.track_list.clear()
        self._window = None
        self.start_edit.setText("0:00")
        self.end_edit.setText("15:00")
        self._draw_placeholder("Choose tracks (Track browser -> Add to plot), then press Plot.")

    @property
    def store(self) -> Optional[CaseStore]:
        return self.session.store

    # ------------------------------------------------------------ track list
    def add_tracks(self, names: list[str]) -> None:
        store = self.store
        if store is None:
            warn(self, "No case open", "Please open a case first.")
            return
        absent = [n for n in names if n not in store.header.tracks]
        if absent:
            warn(self, "Track not in this case",
                 "These tracks are not present in the open case and were not added:\n\n" + "\n".join(absent))
        for n in names:
            if n in store.header.tracks and n not in self.track_names:
                self.track_names.append(n)
        self._refresh_list()

    def set_tracks(self, names: list[str]) -> None:
        self.track_names = []
        self.add_tracks(names)

    def _refresh_list(self) -> None:
        self.track_list.clear()
        store = self.store
        for n in self.track_names:
            ti = store.header.tracks[n] if store else None
            text = n if ti is None else f"{n}\n   {ti.kind}, unit: {ti.unit_text}"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, n)
            self.track_list.addItem(it)

    def remove_selected(self) -> None:
        sel = {i.data(Qt.UserRole) for i in self.track_list.selectedItems()}
        self.track_names = [n for n in self.track_names if n not in sel]
        self._refresh_list()

    def clear_tracks(self) -> None:
        self.track_names = []
        self._refresh_list()

    def _move(self, delta: int) -> None:
        sel = [i.data(Qt.UserRole) for i in self.track_list.selectedItems()]
        if not sel:
            return
        names = self.track_names
        idxs = sorted(names.index(n) for n in sel)
        if delta < 0:
            for i in idxs:
                if i > 0 and names[i - 1] not in sel:
                    names[i - 1], names[i] = names[i], names[i - 1]
        else:
            for i in reversed(idxs):
                if i < len(names) - 1 and names[i + 1] not in sel:
                    names[i + 1], names[i] = names[i], names[i + 1]
        self._refresh_list()
        for r in range(self.track_list.count()):
            if self.track_list.item(r).data(Qt.UserRole) in sel:
                self.track_list.item(r).setSelected(True)

    # ------------------------------------------------------------ window
    def set_window(self, t0: float, t1: float) -> None:
        self.start_edit.setText(format_hms(t0, 1 if t0 % 1 else 0))
        self.end_edit.setText(format_hms(t1, 1 if t1 % 1 else 0))

    def _preset(self, a: float, b: float) -> None:
        dur = self.store.header.duration_s if self.store else None
        if dur:
            b = min(b, dur)
        self.set_window(a, b)

    def _preset_all(self) -> None:
        dur = self.store.header.duration_s if self.store else None
        if not dur:
            warn(self, "Duration unknown", "The recording duration could not be determined for this case.")
            return
        self.set_window(0, dur)

    def window_from_inputs(self) -> tuple[float, float]:
        t0 = parse_time(self.start_edit.text())
        t1 = parse_time(self.end_edit.text())
        dur = self.store.header.duration_s if self.store else None
        return validate_window(t0, t1, dur)

    def _use_view(self) -> None:
        if not self.figure.axes:
            return
        lo, hi = self.figure.axes[0].get_xlim()
        self.set_window(max(0.0, lo), hi)

    # ------------------------------------------------------------ plotting
    def plot(self) -> None:
        store = self.store
        if store is None:
            warn(self, "No case open", "Please open a case first (Cases page).")
            return
        if not self.track_names:
            info(self, "No tracks selected", "Add at least one track first (Track browser -> Add to plot).")
            return
        try:
            t0, t1 = self.window_from_inputs()
        except (TimeParseError, ProcessingError) as exc:
            warn(self, "Check the time interval", str(exc))
            return
        names = list(self.track_names)
        max_pts = int(self.points_combo.currentData())
        approx = sum(int((t1 - t0) * store.header.tracks[n].srate) for n in names
                     if store.header.tracks[n].kind == KIND_WAVE)
        if approx > config.PLOT_WARN_RAW_SAMPLES:
            if not confirm(self, "Large request",
                           f"This interval contains roughly {approx:,} waveform samples. Loading may take a "
                           "while and use a lot of memory. Consider a shorter interval.\n\nContinue anyway?"):
                return

        def job(progress, token):
            raws = store.ensure_tracks(names, token, progress)
            token.check()
            progress("Preparing display...")
            series = {}
            for n in names:
                t, v = raws[n].window(t0, t1)
                series[n] = reduce_for_display(t, v, raws[n].info.kind, max_pts)
            return raws, series

        self._plot_gen += 1
        gen = self._plot_gen
        self._set_busy(True, "Loading data...")
        self._task = run_task(
            self, job,
            on_done=lambda res: self._draw(res[0], res[1], t0, t1, names) if gen == self._plot_gen else None,
            on_error=lambda m: self._on_error(m) if gen == self._plot_gen else None,
            on_progress=lambda m: self.progress_label.setText(m) if gen == self._plot_gen else None,
            on_cancelled=lambda: self.progress_label.setText("Cancelled."),
            on_finished=lambda: self._set_busy(False) if gen == self._plot_gen else None)

    def _cancel(self) -> None:
        if self._task:
            self._task.cancel()
            self.progress_label.setText("Cancelling...")

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self.plot_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if msg:
            self.progress_label.setText(msg)
        elif not busy and self.progress_label.text() in ("Loading data...", "Preparing display..."):
            self.progress_label.setText("")

    def _on_error(self, msg: str) -> None:
        self.progress_label.setText("")
        warn(self, "Could not plot", msg)

    def _draw_placeholder(self, text: str) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.axis("off")
        ax.text(0.5, 0.5, text, ha="center", va="center", color=style.MUTED, fontsize=12, transform=ax.transAxes)
        self.canvas.draw_idle()
        self._axes_by_track = {}
        self._lines = {}

    def _draw(self, raws: dict, series: dict, t0: float, t1: float, names: list[str]) -> None:
        self._plotted = list(names)
        self._raws = raws
        self._series = series
        self._window = (t0, t1)
        fig = self.figure
        fig.clear()
        self._cursor_lines = []
        self._lines = {}
        self._axes_by_track = {}
        store = self.store

        # group tracks into axes
        groups: list[list[str]] = []
        key_to_idx: dict = {}
        for n in names:
            ti = raws[n].info
            key = ("unit", ti.unit) if (self.group_units.isChecked() and ti.unit and ti.kind != KIND_STRING) else ("trk", n)
            if key not in key_to_idx:
                key_to_idx[key] = len(groups)
                groups.append([])
            groups[key_to_idx[key]].append(n)
        ratios = [1.6 if all(raws[n].info.kind == KIND_STRING for n in g) else 3 for g in groups]
        axes = fig.subplots(len(groups), 1, sharex=True, gridspec_kw={"height_ratios": ratios}, squeeze=False)[:, 0]
        colour_i = 0
        self._suppress_limits = True
        for ax, group in zip(axes, groups):
            for n in group:
                ti = raws[n].info
                col = style.SERIES_COLOURS[colour_i % len(style.SERIES_COLOURS)]
                colour_i += 1
                ds = series[n]
                self._axes_by_track[n] = ax
                if ti.kind == KIND_STRING:
                    self._draw_events(ax, raws[n], col, t0, t1)
                    continue
                lw = 0.8 if ti.kind == KIND_WAVE else 1.2
                (line,) = ax.plot(ds.t, ds.v, color=col, lw=lw, label=n,
                                  marker="." if (ti.kind != KIND_WAVE and ds.n_shown < 400) else None, ms=3)
                self._lines[n] = line
            ax.grid(True)
            first = raws[group[0]].info
            if first.kind == KIND_STRING:
                ax.set_yticks([])
                ax.text(0.005, 0.94, "Events", transform=ax.transAxes, va="top", fontsize=8, fontweight="bold",
                        color=style.NAVY)
            else:
                units = sorted({raws[n].info.unit for n in group if raws[n].info.unit})
                unit_txt = ", ".join(units) if units else "no unit"
                label = first.short_name if len(group) == 1 else "shared axis"
                lab_col = ax.lines[0].get_color() if (len(group) == 1 and ax.lines) else style.NAVY
                ax.text(0.005, 0.94, f"{label} ({unit_txt})", transform=ax.transAxes, va="top",
                        fontsize=8 if len(groups) <= 6 else 7, fontweight="bold", color=lab_col,
                        bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=1.5))
                if len(group) > 1:
                    ax.legend(loc="upper right", fontsize=7.5, frameon=False, handlelength=1.4)
            ax.tick_params(labelsize=8)
        axes[-1].xaxis.set_major_formatter(_hms_formatter())
        axes[-1].set_xlabel("Elapsed time since start of recording (h:mm:ss)", fontsize=9)
        axes[0].set_xlim(t0, t1)
        fig.suptitle(f"Case {store.case_id}   {format_hms(t0)} to {format_hms(t1)}", fontsize=10, fontweight="bold", color=style.NAVY, x=0.012, ha="left")
        fig.subplots_adjust(left=0.055, right=0.985, top=0.925, bottom=0.09, hspace=0.12)
        self._draw_markers()
        self._make_cursor()
        axes[0].callbacks.connect("xlim_changed", self._on_xlim_changed)
        self._suppress_limits = False
        self.canvas.draw_idle()
        self._update_note()
        self.progress_label.setText("")
        self.plotted.emit()

    def _draw_events(self, ax, raw: RawTrack, col: str, t0: float, t1: float) -> None:
        t, v = raw.window(t0, t1)
        for tt, text in zip(t, v):
            ax.axvline(tt, color=col, lw=1.0, alpha=0.8)
            ax.text(tt, 0.5, " " + str(text)[:40], rotation=90, va="center", ha="left", fontsize=8,
                    transform=ax.get_xaxis_transform(), color=col, clip_on=True)
        ax.set_ylim(0, 1)

    def _update_note(self) -> None:
        reduced = [n for n, s in self._series.items() if s.reduced]
        if reduced:
            self.note_label.setText(
                "Display only: long tracks are drawn with min/max decimation (peaks preserved, no averaging) and "
                "redrawn in more detail as you zoom. Reduced: " + "; ".join(
                    f"{n} ({self._series[n].n_raw:,} -> {self._series[n].n_shown:,} points)" for n in reduced)
                + ". Gaps in the data are left as gaps. Exports use the original samples.")
        else:
            self.note_label.setText("All original samples in this interval are drawn. Gaps in the data are left "
                                    "as gaps; nothing is interpolated.")

    # ------------------------------------------------------------ zoom re-render
    def _on_xlim_changed(self, _ax) -> None:
        if self._suppress_limits or not self._lines:
            return
        self._rerender_timer.start()

    def _rerender_visible(self) -> None:
        if not self.figure.axes or not self._lines:
            return
        lo, hi = self.figure.axes[0].get_xlim()
        if hi <= lo:
            return
        pad = (hi - lo) * 0.05
        lo, hi = lo - pad, hi + pad
        max_pts = int(self.points_combo.currentData())
        raws = self._raws
        names = list(self._lines)
        self._render_gen += 1
        gen = self._render_gen

        def job(progress, token):
            out = {}
            for n in names:
                t, v = raws[n].window(lo, hi)
                out[n] = reduce_for_display(t, v, raws[n].info.kind, max_pts)
            return out

        def done(series):
            if gen != self._render_gen:
                return
            self._suppress_limits = True
            for n, ds in series.items():
                line = self._lines.get(n)
                if line is not None:
                    line.set_data(ds.t, ds.v)
                    self._series[n] = ds
            self._suppress_limits = False
            self._update_note()
            self.canvas.draw_idle()

        run_task(self, job, done, on_error=lambda m: None)

    # ------------------------------------------------------------ cursor / markers
    def _make_cursor(self) -> None:
        self._cursor_lines = []
        for ax in self.figure.axes:
            ln = ax.axvline(0, color="#444444", lw=0.8, ls="--", visible=False, zorder=5)
            self._cursor_lines.append(ln)

    def _hide_cursor(self) -> None:
        for ln in self._cursor_lines:
            ln.set_visible(False)
        self.canvas.draw_idle()

    def _on_motion(self, event) -> None:
        if event.inaxes is None or event.xdata is None or not self._raws or not self._lines and not self._axes_by_track:
            return
        t = float(event.xdata)
        for ln in self._cursor_lines:
            ln.set_xdata([t, t])
            ln.set_visible(True)
        parts = [f"<b>{format_hms(t, 1)}</b> ({t:.1f} s)"]
        for n in self._plotted:
            raw = self._raws.get(n)
            if raw is None:
                continue
            near = raw.nearest(t)
            ti = raw.info
            if near is None:
                parts.append(f"{ti.short_name}: no data")
                continue
            tn, val = near
            tol = max(2.0 / ti.srate if ti.srate > 0 else config.GAP_MIN_SECONDS * 2, 1.0)
            if ti.kind != KIND_WAVE and raw.t is not None:
                tol = max(tol, gap_threshold(raw.t[:5000]))
            if abs(tn - t) > tol:
                parts.append(f"{ti.short_name}: no sample within {tol:.0f} s")
            elif isinstance(val, str):
                parts.append(f"{ti.short_name}: \"{val}\"")
            elif not np.isfinite(val):
                parts.append(f"{ti.short_name}: missing")
            else:
                parts.append(f"{ti.short_name}: {val:.4g} {ti.unit}".rstrip() + f" @ {format_hms(tn, 1)}")
        self.readout.setText("  |  ".join(parts) + "<br><span style='color:#5d6f7c'>(nearest original sample for "
                             "each track)</span>")
        self.canvas.draw_idle()

    def _on_click(self, event) -> None:
        if event.button != 1 or event.inaxes is None or event.xdata is None:
            return
        if getattr(self.toolbar, "mode", None):  # zoom/pan tool active
            return
        self.clicked.emit(float(event.xdata))

    def add_marker(self, t: float, label: str = "") -> None:
        self.markers.append((float(t), label))
        self._draw_markers()
        self.canvas.draw_idle()

    def clear_markers(self) -> None:
        self.markers.clear()
        self._draw_markers()
        self.canvas.draw_idle()

    def set_overlays(self, items: list[tuple[float, str]]) -> None:
        """Read-only dashed event markers (for example detected dose evidence), drawn on every axis."""
        self.overlays = list(items)
        if self.figure.axes:
            self._draw_markers()
            self.canvas.draw_idle()

    def _draw_markers(self) -> None:
        for ax in self.figure.axes:
            for art in list(getattr(ax, "_marker_artists", [])):
                try:
                    art.remove()
                except Exception:
                    pass
            ax._marker_artists = []
        axes = self.figure.axes
        if not axes:
            return
        lo, hi = axes[0].get_xlim()
        for t, label in self.overlays:
            if not (lo <= t <= hi):
                continue
            for k, ax in enumerate(axes):
                ln = ax.axvline(t, color="#B45309", lw=1.1, ls=(0, (4, 3)), zorder=3, alpha=0.9)
                ax._marker_artists.append(ln)
                if k == 0:
                    tx = ax.text(t, 1.0, " " + label, transform=ax.get_xaxis_transform(), va="bottom", ha="left",
                                 fontsize=7, color="#B45309", rotation=35, rotation_mode="anchor", clip_on=False)
                    ax._marker_artists.append(tx)
        for t, label in self.markers:
            for k, ax in enumerate(axes):
                ln = ax.axvline(t, color="#B3261E", lw=1.4, zorder=4)
                ax._marker_artists.append(ln)
                if k == 0:
                    txt = ax.text(t, 1.0, f" {label or 'marker'}", transform=ax.get_xaxis_transform(), va="bottom",
                                  ha="left", fontsize=8, color="#B3261E", clip_on=False)
                    ax._marker_artists.append(txt)

    # ------------------------------------------------------------ actions
    def save_plot(self) -> None:
        if not self._lines and not self._axes_by_track:
            info(self, "Nothing to save", "Draw a plot first.")
            return
        from .common import last_dir, remember_dir
        default = f"case_{self.store.case_id}_plot.png" if self.store else "plot.png"
        path, selected = QFileDialog.getSaveFileName(
            self, "Save plot", str(__import__("pathlib").Path(last_dir()) / default),
            "PNG image (*.png);;PDF document (*.pdf);;SVG vector graphic (*.svg)")
        if not path:
            return
        low = path.lower()
        if not low.endswith((".png", ".pdf", ".svg")):
            ext = ".pdf" if "PDF" in selected else ".svg" if "SVG" in selected else ".png"
            path += ext
        footer = self.figure.text(0.01, 0.005, "Research and exploratory use only. Elapsed time since start of "
                                  "recording. Display may be reduced; underlying data are unchanged.",
                                  fontsize=6, color="#666666")
        try:
            self.figure.savefig(path, dpi=300, facecolor="white")
        except OSError as exc:
            warn(self, "Could not save", f"The plot could not be saved:\n{exc}")
            return
        finally:
            footer.remove()
            self.canvas.draw_idle()
        remember_dir(path)
        info(self, "Plot saved", f"Saved to:\n{path}")

    def show_stats(self) -> None:
        store = self.store
        if store is None or not self.track_names:
            info(self, "Nothing to summarise", "Add tracks to the plot first.")
            return
        try:
            t0, t1 = self.window_from_inputs()
        except (TimeParseError, ProcessingError) as exc:
            warn(self, "Check the time interval", str(exc))
            return
        names = list(self.track_names)

        def job(progress, token):
            raws = store.ensure_tracks(names, token, progress)
            return [describe_track(raws[n], t0, t1) for n in names]

        def done(stats):
            labels = [lab for lab, _ in stats[0].as_rows()]
            rows = []
            for i, lab in enumerate(labels):
                rows.append([lab] + [s.as_rows()[i][1] for s in stats])
            rows.append(["Exploratory flags"] + ["; ".join(s.warnings) or "none" for s in stats])
            dlg = TableDialog(self, f"Descriptive statistics, {format_hms(t0)} to {format_hms(t1)}",
                              ["Statistic"] + [s.name for s in stats], rows,
                              note="Exploratory checks only: these flags describe the recording, they are not "
                                   "clinically validated judgements of signal quality.", width=980, height=620)
            dlg.exec()

        self._set_busy(True, "Calculating statistics...")
        self._task = run_task(self, job, done, on_error=self._on_error,
                              on_progress=lambda m: self.progress_label.setText(m),
                              on_finished=lambda: self._set_busy(False))

    def export_data(self) -> None:
        from .export_page import open_export_dialog
        if self.store is None or not self.track_names:
            info(self, "Nothing to export", "Add tracks to the plot first.")
            return
        try:
            t0, t1 = self.window_from_inputs()
        except (TimeParseError, ProcessingError) as exc:
            warn(self, "Check the time interval", str(exc))
            return
        open_export_dialog(self, self.session, list(self.track_names), t0, t1, list(self.markers))
