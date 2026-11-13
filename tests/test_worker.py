import threading

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread

from worker import TaskRunner


def test_results_and_errors_arrive_on_the_gui_thread(qapp):
    runner = TaskRunner()
    got = []

    def record(kind):
        return lambda value: got.append((kind, value, QThread.currentThread() == qapp.thread()))

    runner.run(lambda: 42, record("ok"), record("error"))
    runner.run(lambda: 1 / 0, record("ok"), record("error"))
    runner.wait(5000)
    qapp.processEvents()

    assert ("ok", 42, True) in got
    errors = [g for g in got if g[0] == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0][1], ZeroDivisionError)
    assert errors[0][2] is True
    assert runner._callbacks == {}, "callbacks are released after delivery"


def test_runner_deleted_while_job_runs_does_not_crash(qapp):
    # Regression: a window closed while its PowerShell job was still running crashed the
    # process (access violation) when the job's result was delivered.
    gate = threading.Event()
    called = []
    owner = QObject()
    runner = TaskRunner(owner)
    runner.run(lambda: gate.wait(5) and "late result", called.append, called.append)

    sip.delete(owner)  # deletes the runner while the job is still running (must not block)
    gate.set()
    assert runner.wait(5000), "the job still finishes"
    qapp.processEvents()

    assert called == [], "no callback may run for a deleted runner"


def test_shutdown_drops_queued_jobs(qapp):
    gate = threading.Event()
    ran = []
    runner = TaskRunner(max_workers=1)
    runner.run(lambda: gate.wait(5), ran.append, ran.append)  # occupies the only worker
    runner.run(lambda: ran.append("queued job ran"), print, print)
    runner.shutdown()
    gate.set()
    runner.wait(5000)
    qapp.processEvents()
    assert "queued job ran" not in ran
