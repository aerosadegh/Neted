"""Tells the app when network adapters or their IPv4 addresses change.

Uses the Windows IP Helper change notifications (NotifyIpInterfaceChange and
NotifyUnicastIpAddressChange). Windows calls back on its own thread pool when an
adapter appears or disappears, connects or disconnects, or gets a new address.
Bursts of callbacks (plugging in a cable produces several) are folded into one
`changed` signal on the GUI thread. Nothing runs while the network is quiet.
"""

import contextlib
import ctypes
import sys
from ctypes import wintypes
from typing import Any

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

AF_UNSPEC = 0
AF_INET = 2
NO_ERROR = 0
DEBOUNCE_MS = 1000

# VOID (WINAPI *)(PVOID CallerContext, PMIB_..._ROW Row, MIB_NOTIFICATION_TYPE Type)
_CALLBACK = (
    ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
    if sys.platform == "win32"
    else None
)

# Windows keeps a raw pointer to each callback until it is cancelled, so the ctypes
# callback objects must never be garbage collected before that. They live here,
# keyed by id() because ctypes function objects aren't hashable.
_registered_callbacks: dict[int, Any] = {}


def _load_iphlpapi() -> Any | None:
    if _CALLBACK is None:
        return None
    try:
        api = ctypes.WinDLL("iphlpapi")
        for name in ("NotifyIpInterfaceChange", "NotifyUnicastIpAddressChange"):
            register = getattr(api, name)
            register.argtypes = [
                wintypes.USHORT,  # ADDRESS_FAMILY
                _CALLBACK,
                ctypes.c_void_p,  # CallerContext
                wintypes.BOOLEAN,  # InitialNotification
                ctypes.POINTER(wintypes.HANDLE),
            ]
            register.restype = wintypes.DWORD
        api.CancelMibChangeNotify2.argtypes = [wintypes.HANDLE]
        api.CancelMibChangeNotify2.restype = wintypes.DWORD
    except (OSError, AttributeError):
        return None
    return api


class NetworkChangeWatcher(QObject):
    """Emits `changed` (on the GUI thread) shortly after the network configuration changes."""

    changed = pyqtSignal()
    _windows_event = pyqtSignal()  # emitted from Windows' callback threads

    def __init__(
        self,
        parent: QObject | None = None,
        debounce_ms: int = DEBOUNCE_MS,
        initial_notification: bool = False,  # tests use this to get one real callback
    ):
        super().__init__(parent)
        self._active = False
        self._api: Any | None = None
        self._handles: list[wintypes.HANDLE] = []
        self._callback: Any | None = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(self._emit_changed)
        self._windows_event.connect(self._restart_debounce)  # queued to this object's thread

        self.available = self._register(initial_notification)

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    def _register(self, initial_notification: bool) -> bool:
        api = _load_iphlpapi()
        if api is None:
            return False
        self._api = api
        self._callback = _CALLBACK(self._on_windows_event)
        _registered_callbacks[id(self._callback)] = self._callback
        self._active = True

        subscriptions = (
            (api.NotifyIpInterfaceChange, AF_UNSPEC),  # adapter added/removed/(dis)connected
            (api.NotifyUnicastIpAddressChange, AF_INET),  # IPv4 address added/removed
        )
        for register, family in subscriptions:
            handle = wintypes.HANDLE()
            status = register(
                family, self._callback, None, initial_notification, ctypes.byref(handle)
            )
            if status == NO_ERROR:
                self._handles.append(handle)

        if not self._handles:
            self.stop()
            return False
        return True

    def _on_windows_event(self, _context: Any, _row: Any, _notification_type: int) -> None:
        # Runs on a Windows thread-pool thread: never block here, only hand off.
        if not self._active:
            return
        with contextlib.suppress(RuntimeError):  # Qt object already gone during shutdown
            self._windows_event.emit()

    def _restart_debounce(self) -> None:
        if self._active:
            self._debounce.start()

    def _emit_changed(self) -> None:
        if self._active:
            self.changed.emit()

    def stop(self) -> None:
        """Unsubscribes from Windows. Safe to call more than once."""
        self._active = False
        while self._handles and self._api is not None:
            # Waits for a callback that is already running; ctypes releases the GIL
            # meanwhile, so that callback can finish.
            self._api.CancelMibChangeNotify2(self._handles.pop())
        if self._callback is not None:
            _registered_callbacks.pop(id(self._callback), None)
            self._callback = None
