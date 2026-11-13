import sys
import threading
import time

import pytest
from PyQt6.QtCore import QThread

import netwatch
from netwatch import NetworkChangeWatcher

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="uses Windows notifications")


def wait_for(qapp, predicate, timeout=3.0):
    """Processes Qt events until predicate() is true or the timeout passes."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@windows_only
def test_registers_with_windows_and_stops(qapp):
    w = NetworkChangeWatcher()
    try:
        assert w.available
        assert len(w._handles) == 2
        assert netwatch._registered_callbacks[id(w._callback)] is w._callback
    finally:
        w.stop()
    assert w._handles == []
    assert w._callback is None
    w.stop()  # a second stop is harmless


@windows_only
def test_real_windows_callback_reaches_gui_thread(qapp, monkeypatch):
    # InitialNotification makes Windows call back once right after registering.
    callback_threads = []
    original = NetworkChangeWatcher._on_windows_event

    def spy(self, *args):
        callback_threads.append(threading.get_ident())
        original(self, *args)

    monkeypatch.setattr(NetworkChangeWatcher, "_on_windows_event", spy)
    w = NetworkChangeWatcher(debounce_ms=50, initial_notification=True)
    signal_threads = []
    w.changed.connect(lambda: signal_threads.append(QThread.currentThread()))
    try:
        assert wait_for(qapp, lambda: signal_threads), "no change signal from Windows"
        assert callback_threads, "Windows never called back"
        assert signal_threads[0] == qapp.thread(), "changed must arrive on the GUI thread"
    finally:
        w.stop()


@windows_only
def test_bursts_from_other_threads_fold_into_one_signal(qapp):
    w = NetworkChangeWatcher(debounce_ms=50)
    hits = []
    w.changed.connect(lambda: hits.append(QThread.currentThread()))
    try:
        workers = [
            threading.Thread(target=w._on_windows_event, args=(None, None, 0)) for _ in range(5)
        ]
        for t in workers:
            t.start()
        for t in workers:
            t.join()
        assert wait_for(qapp, lambda: hits)
        wait_for(qapp, lambda: False, timeout=0.2)  # nothing more should arrive
        assert len(hits) == 1
        assert hits[0] == qapp.thread()
    finally:
        w.stop()


@windows_only
def test_no_signal_after_stop(qapp):
    w = NetworkChangeWatcher(debounce_ms=20)
    hits = []
    w.changed.connect(lambda: hits.append(1))
    w.stop()
    w._on_windows_event(None, None, 0)
    wait_for(qapp, lambda: False, timeout=0.1)
    assert hits == []


def test_without_windows_api_it_is_a_quiet_no_op(qapp, monkeypatch):
    monkeypatch.setattr(netwatch, "_load_iphlpapi", lambda: None)
    w = NetworkChangeWatcher()
    assert not w.available
    w.stop()
