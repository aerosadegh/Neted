import contextlib
import ctypes
import getpass
import sys

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMessageBox

import selftest
from network import NetworkManager
from resources import app_icon
from ui import APP_NAME, MainWindow

# One running copy per user. Debug runs use their own key so they don't hand off to the exe.
_INSTANCE_KEY = f"network-ed-{getpass.getuser()}" + ("-debug" if NetworkManager.DEBUG_MODE else "")


def _notify_running_instance() -> bool:
    """Asks an already running copy to show its window. Returns True if one answered."""
    socket = QLocalSocket()
    socket.connectToServer(_INSTANCE_KEY)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def _listen_for_new_instances(window: MainWindow) -> QLocalServer:
    """Shows the window whenever another copy of the app is started."""
    server = QLocalServer(window)
    if not server.listen(_INSTANCE_KEY):
        QLocalServer.removeServer(_INSTANCE_KEY)  # stale socket from a crash
        server.listen(_INSTANCE_KEY)

    def on_new_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnected.connect(connection.deleteLater)
        window.show_window()

    server.newConnection.connect(on_new_connection)
    return server


def main() -> None:
    """Main execution function for the Network Ed application."""
    # Own taskbar identity, so Windows shows our icon instead of python.exe's when run from source.
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NetworkEd.App")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    self_test = selftest.report_path(sys.argv)  # read-only startup check used by CI
    if self_test is None:
        if _notify_running_instance():
            return

        # Check system privileges before booting the UI
        if not NetworkManager.DEBUG_MODE and not NetworkManager.is_admin():
            QMessageBox.critical(
                None,
                "Privilege Error",
                "This application requires Administrator privileges to alter network adapters.",
            )
            sys.exit(1)

    window = MainWindow()
    if self_test is None:
        _listen_for_new_instances(window)  # parented to the window, so it lives with the app
    else:
        selftest.start(window, self_test)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
