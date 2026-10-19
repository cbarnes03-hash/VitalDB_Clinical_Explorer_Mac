"""About, scientific limitations and housekeeping."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .. import DISCLAIMER, __version__, config, data_access
from .common import confirm, info, make_page_header

TEXT = f"""
<h3>What this application does</h3>
<p>It lets you browse the public VitalDB anaesthesia dataset, inspect the signals recorded in a case, look at
drug-delivery tracks next to physiology, examine the period around a chosen time point and export the data for
later statistical or machine-learning work.</p>

<h3>What it cannot do</h3>
<ul>
<li>It does <b>not</b> measure or score nociception or pain, and gives no analgesia or dosing recommendations.</li>
<li>A remifentanil change is at best a <i>potential proxy</i> for inadequate analgesia, not ground truth.</li>
<li>Infusion-pump tracks are interpreted with the supervisor's dictionary. They are not confirmed administration
records. A target or effect-site concentration reported by a TCI pump is a model output, not a measured
concentration.</li>
<li><b>Boluses:</b> only recorded text events are actual records. Steps derived from pump volume or rate are
<i>candidates</i> (a manual bolus, TCI loading dose and pump flush look alike). Whole-case totals from the hospital
record have no timing.</li>
<li>Correlation in a plot does not establish causation. Drug delivery and physiology are confounded by surgical
stimulation, other drugs, fluids, positioning and many other clinical factors.</li>
<li>Missing signals and differing sampling rates can bias later analyses. Check missingness before drawing
conclusions.</li>
<li>Signal-quality notes are simple exploratory checks (empty, constant, sparse, gaps). They are not clinically
validated and use no physiological thresholds.</li>
</ul>

<h3>About the time axis</h3>
<p>All times are elapsed time since the start of the VitalDB recording. VitalDB clock times are de-identified.</p>

<h3>Display versus data</h3>
<p>Long tracks are reduced for drawing only (min/max decimation, which keeps peaks). When you zoom, the plot is
redrawn from the original samples. Exports always use the original samples (or a regular grid that you choose
explicitly and which is documented in the manifest).</p>

<p style='color:#5d6f7c'>Version {__version__}. Data: VitalDB, https://vitaldb.net (public dataset).
Please cite VitalDB according to its terms of use when publishing.</p>
"""


class AboutPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header("About and limitations", DISCLAIMER))
        view = QTextBrowser()
        view.setHtml(TEXT)
        lay.addWidget(view, 1)
        r = QHBoxLayout()
        clear = QPushButton("Clear downloaded data cache")
        clear.setToolTip("Deletes locally cached catalogue and track data. They will be downloaded again when needed.")
        clear.clicked.connect(self._clear)
        r.addWidget(clear)
        self.path_label = QLabel(f"Local data folder: {config.data_dir()}")
        self.path_label.setObjectName("note")
        self.path_label.setWordWrap(True)
        r.addWidget(self.path_label, 1)
        lay.addLayout(r)

    def _clear(self) -> None:
        if confirm(self, "Clear cache", "Delete all locally cached VitalDB data? Nothing else is affected.",
                   yes_text="Delete cache"):
            n = data_access.clear_cache()
            info(self, "Cache cleared", f"{n} cached file(s) removed.")
