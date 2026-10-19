"""Plot workspace page: the PlotPanel plus page header."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .common import Session, make_page_header
from .plot_panel import PlotPanel


class PlotPage(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Plot workspace", "Compare several tracks over any interval. Each track has its own axis by default. "
                              "Use the toolbar to zoom and pan; detail is redrawn as you zoom. Add tracks from "
                              "the Track browser."))
        self.panel = PlotPanel(session)
        lay.addWidget(self.panel, 1)
        session.add_to_plot_requested.connect(self.panel.add_tracks)
