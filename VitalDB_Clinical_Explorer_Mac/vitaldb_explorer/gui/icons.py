"""Small built-in line icons (SVG, drawn with Qt; no external files or fonts needed)."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 viewBox, stroke-based (Feather-style geometry)
_PATHS = {
    "cases": "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "overview": "M3 3h7v9H3z M14 3h7v5h-7z M14 12h7v9h-7z M3 16h7v5H3z",
    "tracks": "M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01",
    "plot": "M22 12h-4l-3 9L9 3l-3 9H2",
    "drugs": "M12 2.7s7 6.3 7 11.3a7 7 0 0 1-14 0c0-5 7-11.3 7-11.3z",
    "playback": "M5 3l14 9-14 9V3z",
    "doses": "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
    "events": "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z M4 22v-7",
    "export": "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3",
    "guide": "M4 19.5A2.5 2.5 0 0 1 6.5 17H20 M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z",
    "about": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 16v-4 M12 8h.01",
    "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z M9 12l2 2 4-4",
    "search": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.35-4.35",
    "refresh": "M23 4v6h-6 M1 20v-6h6 M3.51 9a9 9 0 0 1 14.85-3.36L23 10 M1 14l4.64 4.36A9 9 0 0 0 20.49 15",
    "open": "M5 12h14 M12 5l7 7-7 7",
    "lock": "M5 11h14v10H5z M8 11V7a4 4 0 0 1 8 0v4",
    "image": "M3 3h18v18H3z M8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z M21 15l-5-5L5 21",
    "stats": "M18 20V10 M12 20V4 M6 20v-6",
    "file": "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6",
    "pin": "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z M12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
}


def _svg(name: str, color: str, width: float = 1.9) -> bytes:
    d = _PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"><path d="{d}"/></svg>').encode()


def pixmap(name: str, color: str = "#FFFFFF", size: int = 20, width: float = 1.9) -> QPixmap:
    scale = 2
    img = QImage(size * scale, size * scale, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(_svg(name, color, width)))
    p = QPainter(img)
    r.render(p)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str = "#5B6B7A", active_color: str | None = None, size: int = 20) -> QIcon:
    """Icon with a normal colour and (optionally) a different colour when selected/active."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal, QIcon.On)
    act = active_color or color
    ic.addPixmap(pixmap(name, act, size), QIcon.Selected, QIcon.Off)
    ic.addPixmap(pixmap(name, act, size), QIcon.Selected, QIcon.On)
    ic.addPixmap(pixmap(name, "#9AA7B2", size), QIcon.Disabled, QIcon.Off)
    return ic


def names() -> list[str]:
    return sorted(_PATHS)
