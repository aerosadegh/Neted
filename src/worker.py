"""Runs blocking calls (PowerShell) off the GUI thread."""

import contextlib
from collections.abc import Callable
from concurrent import futures
from typing import Any

from PyQt6.QtCore import QObject, pyqtBoundSignal, pyqtSignal


def _run_job(job_id: int, fn: Callable[[], Any], finished: pyqtBoundSignal) -> None:
    try:
        result, ok = fn(), True
    except Exception as exc:  # handed to the GUI thread, which shows it
        result, ok = exc, False
    # RuntimeError: the runner (and its window) was deleted while this job ran.
    with contextlib.suppress(RuntimeError):
        finished.emit(job_id, ok, result)


class TaskRunner(QObject):
    """Runs a callable on a worker thread and calls back on the GUI thread.

    - Jobs report through one signal of this object; callbacks wait in a dict on the GUI
      thread. If the runner is deleted, Qt drops its queued results, so callbacks never
      run against a deleted window.
    - Workers are Python threads, not a QThreadPool: deleting a QThreadPool from Python
      waits for running jobs while holding the GIL those jobs need (a deadlock on exit).
    """

    _finished = pyqtSignal(int, bool, object)  # job id, succeeded, result or exception

    def __init__(self, parent: QObject | None = None, max_workers: int = 4):
        super().__init__(parent)
        self._executor = futures.ThreadPoolExecutor(max_workers, thread_name_prefix="neted-task")
        self._pending: set[futures.Future] = set()
        self._next_id = 0
        self._callbacks: dict[int, tuple[Callable[[Any], None], Callable[[Exception], None]]] = {}
        self._finished.connect(self._deliver)  # queued: emitted from worker threads

    def run(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._next_id += 1
        self._callbacks[self._next_id] = (on_success, on_error)
        future = self._executor.submit(_run_job, self._next_id, fn, self._finished)
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)

    def _deliver(self, job_id: int, ok: bool, payload: Any) -> None:
        on_success, on_error = self._callbacks.pop(job_id)
        (on_success if ok else on_error)(payload)

    def wait(self, msecs: int = -1) -> bool:
        """Blocks until all jobs are done (used by tests). False if the timeout passed."""
        _, not_done = futures.wait(list(self._pending), None if msecs < 0 else msecs / 1000)
        return not not_done

    def shutdown(self) -> None:
        """Drops queued jobs; a job already running finishes but its result is ignored."""
        self._executor.shutdown(wait=False, cancel_futures=True)
