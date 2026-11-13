"""UI icons from Font Awesome 5 Solid (via qtawesome), loading only that one font.

qtawesome's own qta.icon() loads and hash-checks all ~10 icon fonts it ships. The exe
bundles only Font Awesome 5 Solid (NetworkEd.spec drops the rest, about 2.5 MB), so
always get icons from here, with "fa5s." names.
"""

from functools import cache

import qtawesome
from PyQt6.QtGui import QIcon
from qtawesome.iconic_font import IconicFont

FONT_PREFIX = "fa5s"


def font_files() -> tuple[str, str]:
    """(ttf, charmap) file names of the bundled Font Awesome 5 Solid font."""
    entry = next(f for f in qtawesome._BUNDLED_FONTS if f[0] == FONT_PREFIX)
    return entry[1], entry[2]


@cache
def _font() -> IconicFont:
    return IconicFont((FONT_PREFIX, *font_files()))


def icon(name: str, **options) -> QIcon:
    """An icon such as icon("fa5s.save", color="#888"). Needs a QApplication."""
    if not name.startswith(f"{FONT_PREFIX}."):
        raise ValueError(f"Only {FONT_PREFIX}.* icons are bundled, got {name!r}")
    return _font().icon(name, **options)
