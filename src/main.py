import sys
from PyQt6.QtWidgets import QApplication, QMessageBox

# Local imports
from network import NetworkManager
from ui import NetworkGadgetUI


def main():
    """Main execution function for the Network Gadget application."""
    app = QApplication(sys.argv)
    
    app.setQuitOnLastWindowClosed(False)

    # Check system privileges before booting the UI
    if not NetworkManager.DEBUG_MODE and not NetworkManager.is_admin():
        QMessageBox.critical(
            None,
            "Privilege Error",
            "This application requires Administrator privileges to alter network adapters.",
        )
        sys.exit(1)

    window = NetworkGadgetUI()
    window.show()

    # Execute the Qt event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
