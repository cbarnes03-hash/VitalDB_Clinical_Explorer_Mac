"""Case overview: what the open case contains and which signals are worth exploring."""
from __future__ import annotations

import html
import re
from collections import Counter
from typing import Optional

import pandas as pd
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .. import doses
from .. import knowledge as kn
from ..categories import CATEGORY_ORDER, KEY_SIGNALS
from ..data_access import KIND_NUMERIC, KIND_WAVE, CaseStore
from ..processing import describe_track
from ..timeutil import format_duration
from .common import Session, make_page_header, warn
from .workers import run_task


def key_signal_matches(store: CaseStore) -> list[tuple[str, list[str]]]:
    out = []
    for label, pat in KEY_SIGNALS:
        rx = re.compile(pat, re.I)
        out.append((label, [n for n in store.header.tracks if rx.search(n)]))
    return out


class OverviewPage(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        self.coverage: dict[str, str] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(make_page_header(
            "Case overview", "A summary of what is recorded in the open case. Only information provided by "
                             "VitalDB is shown."))
        r = QHBoxLayout()
        self.scan_btn = QPushButton("Check data availability for key signals")
        self.scan_btn.setToolTip("Downloads the numeric key tracks (not waveforms) and measures how much of the "
                                 "recording each one covers.")
        self.scan_btn.clicked.connect(self.scan_coverage)
        self.scan_btn.setEnabled(False)
        self.scan_label = QLabel("")
        self.scan_label.setObjectName("note")
        r.addWidget(self.scan_btn)
        r.addWidget(self.scan_label, 1)
        lay.addLayout(r)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        lay.addWidget(self.view, 1)
        self.view.setHtml("<p style='color:#617080'>Open a case from the Cases page to see its overview.</p>")
        session.case_opened.connect(self._on_case)

    def _on_case(self, store: CaseStore) -> None:
        self.coverage = {}
        self.scan_btn.setEnabled(True)
        self.scan_label.setText("")
        self.render()

    # ------------------------------------------------------------ rendering
    def render(self) -> None:
        store = self.session.store
        if store is None:
            return
        h = store.header
        esc = html.escape
        dur = h.duration_s
        devices = sorted({t.device for t in h.tracks.values() if t.device})
        kinds = Counter(t.kind for t in h.tracks.values())
        cats = Counter(t.category for t in h.tracks.values())
        matches = key_signal_matches(store)

        parts = ["<h2 style='margin-bottom:4px'>Case %d</h2>" % h.case_id]
        parts.append("<table cellpadding='4'>")
        parts.append(f"<tr><td><b>Recording duration</b></td><td>{esc(format_duration(dur)) if dur else 'could not be derived'}</td></tr>")
        parts.append(f"<tr><td><b>Number of tracks</b></td><td>{len(h.tracks)} "
                     f"({', '.join(f'{n} {k}' for k, n in kinds.items())})</td></tr>")
        parts.append(f"<tr><td><b>Monitor / device sources</b></td><td>{self._device_html(devices) if devices else 'not provided'}</td></tr>")
        parts.append("<tr><td><b>Track categories</b></td><td>" + esc("; ".join(
            f"{c}: {cats[c]}" for c in CATEGORY_ORDER if cats.get(c))) + "</td></tr>")
        parts.append(f"<tr><td><b>Track list source</b></td><td>{'local cache' if h.from_cache else 'retrieved from VitalDB'}</td></tr>")
        parts.append("</table>")

        # key signals
        parts.append("<h3>Key signals</h3><p style='color:#617080'>Matching is by track name only. "
                     "'Not present' means no track in this case has a matching name.</p>")
        parts.append("<table border='0' cellpadding='5' cellspacing='0' width='100%' style='border:1px solid #E1E7ED'>")
        parts.append("<tr style='background:#F1F5F8'><th align='left'>Signal</th><th align='left'>Status</th>"
                     "<th align='left'>Track name(s) as in VitalDB</th><th align='left'>Data coverage</th></tr>")
        present, absent = [], []
        for label, names in matches:
            if names:
                present.append((label, names))
                cov = "<br>".join(esc(self.coverage.get(n, "not yet checked")) for n in names)
                parts.append(f"<tr><td>{esc(label)}</td><td style='color:#0A6E80'><b>present</b></td>"
                             f"<td>{'<br>'.join(esc(n) for n in names)}</td><td>{cov}</td></tr>")
            else:
                absent.append(label)
                parts.append(f"<tr style='color:#8795A3'><td>{esc(label)}</td><td>not present</td><td>-</td><td>-</td></tr>")
        parts.append("</table>")

        # worth exploring
        parts.append("<h3>Worth exploring in this case</h3>")
        if present:
            parts.append("<ul>")
            for label, names in present:
                parts.append(f"<li>{esc(label)}: {esc(', '.join(names[:3]))}{' ...' if len(names) > 3 else ''}</li>")
            parts.append("</ul>")
        else:
            parts.append("<p>None of the standard key signals were found by name. Use the Track browser to see "
                         "what this case contains.</p>")
        if absent:
            parts.append("<p style='color:#6F5300'><b>Not found by name:</b> " + esc(", ".join(absent)) + "</p>")
        drug_present = [l for l, n in present if "Orchestra" in l or "name-based" in l]
        if drug_present:
            parts.append("<p style='color:#617080'>Infusion tracks are interpreted with the supervisor's dictionary. "
                         "Open the Drug explorer to see what each track means and what remains unverified.</p>")
        if self.coverage:
            low = [n for n, c in self.coverage.items() if c.startswith("sparse")]
            if low:
                parts.append("<p style='color:#6F5300'><b>Sparse coverage:</b> " + esc(", ".join(low)) + "</p>")

        # dose evidence
        text_t, vol_t, rate_t = doses.candidate_tracks(store)
        emr = doses.emr_totals(h.clinical or {})
        parts.append("<h3>Dose and bolus evidence</h3><ul>")
        parts.append(f"<li>{len(text_t)} text/event track(s): {esc(', '.join(text_t)) or 'none'}</li>")
        parts.append(f"<li>{len(vol_t)} infusion-volume and {len(rate_t)} infusion-rate pump track(s) "
                     "(candidate dose steps can be derived)</li>")
        parts.append(f"<li>{len(emr)} whole-case drug/fluid total(s) in the catalogue (no timing)</li></ul>")
        parts.append("<p style='color:#617080'>Open <b>Dose events</b> to scan for stat/bolus evidence and see it on the "
                     "timeline. Only recorded text events are actual records; pump-derived steps are candidates.</p>")

        # catalogue metadata
        clin = {k: v for k, v in (h.clinical or {}).items() if not (isinstance(v, float) and pd.isna(v)) and v is not None}
        parts.append("<h3>Case metadata exposed by the dataset (VitalDB catalogue)</h3>")
        if clin:
            parts.append("<table border='0' cellpadding='3' cellspacing='0'>")
            for k, v in clin.items():
                parts.append(f"<tr><td style='color:#617080'>{esc(str(k))}</td><td>{esc(str(v))}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p style='color:#617080'>No catalogue metadata is available for this case "
                         "(the catalogue may not have loaded, or the case is not listed).</p>")
        self.view.setHtml("".join(parts))

    @staticmethod
    def _device_html(devices: list[str]) -> str:
        out = []
        for d in devices:
            desc = kn.describe_device(d)
            out.append(f"{html.escape(d)}" + (f" <span style='color:#617080'>({html.escape(desc)})</span>" if desc else ""))
        return ", ".join(out)

    # ------------------------------------------------------------ coverage scan
    def scan_coverage(self) -> None:
        store = self.session.store
        if store is None:
            return
        names = [n for _l, ns in key_signal_matches(store) for n in ns
                 if store.header.tracks[n].kind == KIND_NUMERIC]
        names = list(dict.fromkeys(names))
        dur = store.header.duration_s
        if not names or not dur:
            warn(self, "Nothing to check", "No numeric key tracks were found, or the duration is unknown.")
            return
        self.scan_btn.setEnabled(False)
        self.scan_label.setText("Downloading numeric key tracks...")

        def job(progress, token):
            raws = store.ensure_tracks(names, token, progress)
            out = {}
            for n, raw in raws.items():
                st = describe_track(raw, 0.0, dur)
                sp = raw.span()
                if st.n_valid == 0 or sp is None:
                    out[n] = "no valid samples"
                    continue
                covered = (sp[1] - sp[0]) / dur * 100
                miss = "n/a" if st.pct_missing is None else f"{st.pct_missing:.0f}% of expected samples missing"
                tag = "sparse: " if (st.pct_missing or 0) > 50 else ""
                out[n] = f"{tag}spans {covered:.0f}% of recording; {st.n_valid:,} valid samples; {miss}"
            return out

        def done(cov):
            self.coverage = cov
            self.render()
            self.scan_label.setText("Coverage checked. Percentages are exploratory estimates.")

        self._task = run_task(self, job, done, on_error=lambda m: (self.scan_label.setText(""), warn(self, "Could not check", m)),
                              on_progress=self.scan_label.setText,
                              on_finished=lambda: self.scan_btn.setEnabled(True))
