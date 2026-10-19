"""Main window: navigation rail, disclaimer banner and page stack."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import DISCLAIMER, __version__, config
from . import style
from .about_page import AboutPage
from .cases_page import CasesPage
from .common import Session
from .dose_page import DosePage
from .drug_page import DrugPage
from .event_page import EventPage
from .export_page import ExportPage
from .guide_page import GuidePage
from .icons import icon, pixmap
from .overview_page import OverviewPage
from .playback_page import PlaybackPage
from .plot_page import PlotPage
from .tracks_page import TracksPage
from .workers import wait_all

# (section title, [(key, label, icon, needs_case)])
NAV = [
    ("EXPLORE", [("cases", "Cases", "cases", False), ("overview", "Case overview", "overview", True),
                 ("tracks", "Track browser", "tracks", True)]),
    ("ANALYSE", [("plot", "Plot workspace", "plot", True), ("playback", "Playback", "playback", True),
                 ("drugs", "Drug explorer", "drugs", True),
                 ("doses", "Dose events", "doses", True), ("events", "Event explorer", "events", True)]),
    ("OUTPUT", [("export", "Export", "export", True)]),
    ("REFERENCE", [("guide", "Dataset guide", "guide", False), ("about", "About and limits", "about", False)]),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{config.APP_NAME}  -  research use only")
        self.resize(1520, 940)
        self.setMinimumSize(1060, 660)
        self.session = Session()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- disclaimer banner (always visible)
        banner = QFrame()
        banner.setObjectName("banner")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(16, 6, 16, 6)
        bl.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(pixmap("shield", style.WARN_TEXT, 16, 2.0))
        txt = QLabel(DISCLAIMER)
        txt.setObjectName("bannerText")
        txt.setWordWrap(True)
        bl.addWidget(ic)
        bl.addWidget(txt, 1)
        outer.addWidget(banner)

        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)

        # --- navigation rail
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 18, 0, 14)
        sl.setSpacing(0)
        brand = QHBoxLayout()
        brand.setContentsMargins(18, 0, 14, 8)
        brand.setSpacing(10)
        mark = QLabel("V")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(34, 34)
        names = QVBoxLayout()
        names.setSpacing(0)
        t1 = QLabel("VitalDB Explorer")
        t1.setObjectName("brandTitle")
        t2 = QLabel("Clinical research viewer")
        t2.setObjectName("brandSub")
        names.addWidget(t1)
        names.addWidget(t2)
        brand.addWidget(mark)
        brand.addLayout(names, 1)
        sl.addLayout(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setIconSize(self.nav.iconSize().expandedTo(self.nav.iconSize()))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sl.addWidget(self.nav, 1)

        card = QFrame()
        card.setObjectName("caseCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(2)
        self.case_title = QLabel("No case open")
        self.case_title.setObjectName("caseCardTitle")
        self.case_sub = QLabel("Open a case from the Cases page")
        self.case_sub.setObjectName("caseCardSub")
        self.case_sub.setWordWrap(True)
        cl.addWidget(self.case_title)
        cl.addWidget(self.case_sub)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(12, 0, 12, 0)
        wrap.addWidget(card)
        sl.addLayout(wrap)
        body.addWidget(side)

        # --- pages
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.cases = CasesPage(self.session, self._case_opened)
        self.overview = OverviewPage(self.session)
        self.tracks = TracksPage(self.session)
        self.plot = PlotPage(self.session)
        self.playback = PlaybackPage(self.session)
        self.drugs = DrugPage(self.session)
        self.doses = DosePage(self.session, self.plot)
        self.events = EventPage(self.session, self.plot)
        self.export = ExportPage(self.session)
        self.guide = GuidePage()
        self.about = AboutPage()
        pages = {"cases": self.cases, "overview": self.overview, "tracks": self.tracks, "plot": self.plot, "playback": self.playback,
                 "drugs": self.drugs, "doses": self.doses, "events": self.events, "export": self.export,
                 "guide": self.guide, "about": self.about}
        self._needs_case: dict[str, bool] = {}
        self._stack_index: dict[str, int] = {}
        self._row_of: dict[str, int] = {}
        for section, entries in NAV:
            head = QListWidgetItem(section)
            head.setFlags(Qt.NoItemFlags)
            f = head.font()
            f.setPixelSize(10)
            f.setBold(True)
            head.setFont(f)
            head.setForeground(Qt.GlobalColor.gray)
            head.setSizeHint(head.sizeHint().expandedTo(head.sizeHint()))
            self.nav.addItem(head)
            for key, label, ico, needs in entries:
                wrapper = QWidget()
                wrapper.setObjectName("page")
                wl = QVBoxLayout(wrapper)
                wl.setContentsMargins(30, 22, 30, 12)
                wl.addWidget(pages[key])
                self._stack_index[key] = self.stack.addWidget(wrapper)
                it = QListWidgetItem(icon(ico, style.RAIL_TEXT, "#FFFFFF"), "  " + label)
                it.setData(Qt.UserRole, key)
                if needs:
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                    it.setToolTip("Open a case first")
                self.nav.addItem(it)
                self._row_of[key] = self.nav.count() - 1
                self._needs_case[key] = needs
        self.nav.currentItemChanged.connect(self._nav_changed)
        self.goto("cases")

        self.session.add_to_plot_requested.connect(lambda _n: self.goto("plot"))
        self.statusBar().showMessage("Ready. An internet connection is needed to download VitalDB data.")

    # ------------------------------------------------------------ navigation
    def _nav_changed(self, cur: Optional[QListWidgetItem], _prev) -> None:
        if cur is None:
            return
        key = cur.data(Qt.UserRole)
        if key in self._stack_index:
            self.stack.setCurrentIndex(self._stack_index[key])

    def goto(self, key: str) -> None:
        self.nav.setCurrentRow(self._row_of[key])

    def current_key(self) -> str:
        it = self.nav.currentItem()
        return it.data(Qt.UserRole) if it else ""

    def nav_enabled(self, key: str) -> bool:
        return bool(self.nav.item(self._row_of[key]).flags() & Qt.ItemIsEnabled)

    # ------------------------------------------------------------ case lifecycle
    def _case_opened(self, store) -> None:
        for key, needs in self._needs_case.items():
            if needs:
                it = self.nav.item(self._row_of[key])
                it.setFlags(it.flags() | Qt.ItemIsEnabled)
                it.setToolTip("")
        dur = store.header.duration_s
        self.case_title.setText(f"Case {store.case_id}")
        self.case_sub.setText(f"{len(store.header.tracks)} tracks" + (f"  -  {dur / 60:.0f} min" if dur else ""))
        self.statusBar().showMessage(f"Case {store.case_id} is open.")
        self.goto("overview")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_started", False):
            self._started = True
            self.cases.load_catalogue()

    def closeEvent(self, event) -> None:  # noqa: N802
        wait_all()
        super().closeEvent(event)
