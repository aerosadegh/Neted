"""Runs blocking calls (PowerShell, ip, nmcli) off the GUI thread."""

import itertools
from collections.abc import Callable
from concurrent import futures
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class _Bridge(QObject):
    """Carries every job's result back to the GUI thread. Created once, never deleted.

    Jobs must never emit on an object that could be deleted while they run: on Linux
    that crashes the process (segfault), on Windows it only raises. Emitting on this
    permanent object is always safe; Qt then drops results meant for a deleted runner.
    """

    finished = pyqtSignal(int, int, bool, object)  # runner id, job id, ok, result/error


_bridge: _Bridge | None = None


def _get_bridge() -> _Bridge:
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()  # first TaskRunner is made on the GUI thread
    return _bridge


def _run_job(bridge: _Bridge, runner_id: int, job_id: int, fn: Callable[[], Any]) -> None:
    try:
        result, ok = fn(), True
    except Exception as exc:  # handed to the GUI thread, which shows it
        result, ok = exc, False
    bridge.finished.emit(runner_id, job_id, ok, result)


class TaskRunner(QObject):
    """Runs a callable on a worker thread and calls back on the GUI thread.

    - Results travel through the permanent `_Bridge`; callbacks wait in a dict on the
      GUI thread. `_deliver` is a real Qt slot, so Qt disconnects it when the runner is
      deleted and results for a deleted window are dropped, never delivered.
    - Workers are Python threads, not a QThreadPool: deleting a QThreadPool from Python
      waits for running jobs while holding the GIL those jobs need (a deadlock on exit).
    """

    _ids = itertools.count(1)

    def __init__(self, parent: QObject | None = None, max_workers: int = 4):
        super().__init__(parent)
        self._id = next(TaskRunner._ids)
        self._bridge = _get_bridge()
        self._executor = futures.ThreadPoolExecutor(max_workers, thread_name_prefix="neted-task")
        self._pending: set[futures.Future] = set()
        self._next_job = 0
        self._callbacks: dict[int, tuple[Callable[[Any], None], Callable[[Exception], None]]] = {}
        self._bridge.finished.connect(self._deliver)  # queued: emitted from worker threads

    def run(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._next_job += 1
        self._callbacks[self._next_job] = (on_success, on_error)
        future = self._executor.submit(_run_job, self._bridge, self._id, self._next_job, fn)
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)

    @pyqtSlot(int, int, bool, object)
    def _deliver(self, runner_id: int, job_id: int, ok: bool, payload: Any) -> None:
        if runner_id != self._id:
            return  # another runner's job
        callbacks = self._callbacks.pop(job_id, None)
        if callbacks is not None:
            on_success, on_error = callbacks
            (on_success if ok else on_error)(payload)

    def wait(self, msecs: int = -1) -> bool:
        """Blocks until all jobs are done (used by tests). False if the timeout passed."""
        _, not_done = futures.wait(list(self._pending), None if msecs < 0 else msecs / 1000)
        return not not_done

    def shutdown(self) -> None:
        """Drops queued jobs; a job already running finishes but its result is ignored."""
        self._executor.shutdown(wait=False, cancel_futures=True)
