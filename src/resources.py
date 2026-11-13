"""Bundled files (works from source and from the PyInstaller exe) and the app icon."""

import sys
from functools import cache
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ICON_SVG = "assets/icon.svg"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def resource_path(relative: str) -> Path:
    """Path of a file shipped with the app (inside the exe's unpack folder when frozen)."""
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent
    return Path(base) / relative


def render_svg(renderer: QSvgRenderer, size: int) -> QImage:
    """Renders an SVG into a square transparent image."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


@cache
def app_icon() -> QIcon:
    """The app icon at the common sizes. Null if the SVG is missing or broken.

    Needs a QApplication to exist.
    """
    renderer = QSvgRenderer(str(resource_path(ICON_SVG)))
    icon = QIcon()
    if renderer.isValid():
        for size in ICON_SIZES:
            icon.addPixmap(QPixmap.fromImage(render_svg(renderer, size)))
    return icon
