"""`NetworkEd.exe --self-test=report.json`: start, load adapters, write a report, quit.

CI runs this on the built exe to prove it still starts after NetworkEd.spec trims unused
Qt files. It only reads (adapter list, status, profiles); it never applies a profile,
never saves, and skips the single-instance and admin checks.
"""

import json
import sys
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

ARG = "--self-test="


def report_path(argv: list[str]) -> Path | None:
    for arg in argv[1:]:
        if arg.startswith(ARG):
            return Path(arg[len(ARG) :])
    return None


def collect(window) -> dict:
    """What the report contains; `ok` is the overall verdict."""
    import icons
    from resources import app_icon
    from version import __version__

    combo = window.interface_combo
    report = {
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "app_icon": not app_icon().isNull(),
        "fa5s_icon": not icons.icon("fa5s.bolt").isNull(),
        "network_watcher": window._watcher.available,
        "adapters": [combo.itemData(i) for i in range(combo.count())],
        "status": window.lbl_active_ip.text(),
        "profile_groups": len(window.groups),
    }
    report["ok"] = bool(
        report["app_icon"]
        and report["fa5s_icon"]
        and report["adapters"]
        and not report["status"].endswith(("Scanning...", "Could not list network adapters"))
    )
    return report


def start(window, path: Path, timeout_s: float = 60.0) -> None:
    """Polls until the first adapter scan finished, then writes the report and exits."""
    started = time.monotonic()
    timer = QTimer(window)

    def check() -> None:
        loaded = window.interface_combo.count() > 0 and not window.lbl_active_ip.text().endswith(
            "Scanning..."
        )
        if not loaded and time.monotonic() - started < timeout_s:
            return
        timer.stop()
        try:
            report = collect(window)
        except Exception:
            report = {"ok": False, "error": traceback.format_exc()}
        report["seconds"] = round(time.monotonic() - started, 1)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        window._is_quitting = True
        window._watcher.stop()
        window._tasks.shutdown()
        QApplication.exit(0 if report["ok"] else 1)

    timer.timeout.connect(check)
    timer.start(250)
