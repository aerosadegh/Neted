"""Runs blocking calls (PowerShell) off the GUI thread."""

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class _Signals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(object)
    finished = pyqtSignal()


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _Signals):
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # handed to the GUI thread, which shows it
            self._signals.failed.emit(exc)
        else:
            self._signals.succeeded.emit(result)
        finally:
            self._signals.finished.emit()


class TaskRunner(QObject):
    """Runs a callable on a worker thread and calls back on the GUI thread."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._live: set[_Signals] = set()

    def run(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        signals = _Signals()
        self._live.add(signals)  # keep it alive until the job reports back
        signals.succeeded.connect(on_success)
        signals.failed.connect(on_error)
        signals.finished.connect(lambda: self._live.discard(signals))
        self._pool.start(_Job(fn, signals))

    def wait(self, msecs: int = -1) -> bool:
        """Blocks until all jobs are done (used by tests and at shutdown)."""
        return self._pool.waitForDone(msecs)
