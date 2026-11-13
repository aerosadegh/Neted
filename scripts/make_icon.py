"""Builds assets/icon.ico (the exe icon) from assets/icon.svg.

Run with `just icon` after changing the SVG. The .ico holds PNG images at the sizes
Windows uses for Explorer, the taskbar and the title bar.
"""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtSvg import QSvgRenderer  # noqa: E402

from resources import ICON_SVG, render_svg  # noqa: E402

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(renderer: QSvgRenderer, size: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not render_svg(renderer, size).save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode the {size}px PNG")
    buffer.close()
    return bytes(data)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    """ICO container with PNG-compressed entries (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


def main() -> None:
    _app = QGuiApplication(sys.argv)  # noqa: F841  (Qt painting needs an application object)
    source = ROOT / ICON_SVG
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        sys.exit(f"Not a valid SVG: {source}")
    target = source.with_suffix(".ico")
    target.write_bytes(build_ico([(size, png_bytes(renderer, size)) for size in ICO_SIZES]))
    print(f"Wrote {target.relative_to(ROOT)} ({', '.join(map(str, ICO_SIZES))} px)")


if __name__ == "__main__":
    main()
