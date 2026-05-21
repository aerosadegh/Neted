from typing import Optional

# Third-party imports
from PyQt6.QtCore import Qt
import qtawesome as qta
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QComboBox,
    QLabel,
    QCheckBox,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QFormLayout,
    QLineEdit,
    QGroupBox,
    QInputDialog,
    QMenu,
    QSystemTrayIcon,
    QStyle,
    QApplication,
)

# Local imports
from models import ProfileStore, ProfileGroup, NetworkProfile
from network import NetworkManager


class NetworkGadgetUI(QMainWindow):
    """Refactored UI featuring a Splitter layout and Live Status monitoring."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Network Ed - Pro Edition")

        self.resize(500, 350)
        self.setMinimumSize(450, 280)

        self.groups = ProfileStore.load()
        self.current_profile: Optional[NetworkProfile] = None

        self._setup_ui()
        self._populate_tree()
        self._setup_system_tray()
        self._is_quitting = False

    def _setup_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        # ---> ADD THIS: Global styling for input padding
        self.setStyleSheet("""
            QLineEdit { padding: 5px; border-radius: 3px; }
        """)

        # --- Top Bar: System Status ---
        status_group = QGroupBox("System Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(5, 5, 5, 5)

        top_row = QHBoxLayout()
        self.interface_combo = QComboBox()
        self.interface_combo.addItems(NetworkManager.get_network_interfaces())
        self.interface_combo.currentTextChanged.connect(self._update_system_status)

        self.btn_dhcp = QPushButton(" DHCP")
        self.btn_dhcp.setIcon(qta.icon("fa5s.network-wired", color="#3498DB"))
        # ---> UPDATED: Demoted to an outline button to reduce visual noise
        self.btn_dhcp.setStyleSheet("""
            QPushButton {
                background-color: transparent; 
                color: #3498DB; 
                border: 1px solid #3498DB;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(52, 152, 219, 0.1); }
        """)
        self.btn_dhcp.clicked.connect(self._set_to_dhcp)

        btn_refresh = QPushButton(" Refresh")
        btn_refresh.setIcon(qta.icon("fa5s.sync-alt", color="#888"))
        btn_refresh.clicked.connect(self._update_system_status)

        top_row.addWidget(QLabel("Interface:"))
        top_row.addWidget(self.interface_combo)
        top_row.addStretch()
        top_row.addWidget(self.btn_dhcp)
        top_row.addWidget(btn_refresh)

        self.lbl_active_ip = QLabel("Active IP: Scanning...")
        # Changed from dark #2C3E50 to a brighter accent blue
        self.lbl_active_ip.setStyleSheet("font-weight: bold; color: #5DADE2;")

        status_layout.addLayout(top_row)
        status_layout.addWidget(self.lbl_active_ip)
        main_layout.addWidget(status_group)

        # --- Main Splitter (Tree & Editor) ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left Side
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Profile Library")
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        left_layout.addWidget(self.tree)

        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        btn_add_group = QPushButton(" Add Group")
        btn_add_group.setIcon(qta.icon("fa5s.folder-plus", color="#2C3E50"))
        btn_add_group.setStyleSheet("""
            QPushButton {
                border-top-left-radius: 0px; 
                border-top-right-radius: 0px;
                padding: 6px;
            }
        """)
        btn_add_group.clicked.connect(self._add_group)
        left_layout.addWidget(btn_add_group)

        splitter.addWidget(left_widget)

        # Right Side
        right_widget = QWidget()
        right_main_layout = QVBoxLayout(right_widget)
        right_main_layout.setContentsMargins(5, 0, 0, 0)

        self.form_layout = QFormLayout()
        self.form_layout.setVerticalSpacing(4)
        self.form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.chk_dns_only = QCheckBox("DNS-Only")
        self.chk_dns_only.setStyleSheet("font-weight: bold; color: #5DADE2;")
        self.chk_dns_only.stateChanged.connect(self._toggle_dns_mode)


        self.txt_name = QLineEdit()
        self.txt_ip = QLineEdit()

        self.txt_ip.editingFinished.connect(self._auto_fill_subnet_mask)
        self.txt_mask = QLineEdit()
        self.txt_gateway = QLineEdit()
        self.txt_dns1 = QLineEdit()
        self.txt_dns2 = QLineEdit()

        # Add placeholders ONLY for optional items now
        self.txt_gateway.setPlaceholderText("Optional")
        self.txt_dns1.setPlaceholderText("Optional")
        self.txt_dns2.setPlaceholderText("Optional")

        ip_layout = QHBoxLayout()
        ip_layout.setContentsMargins(0, 0, 0, 0)
        ip_layout.addWidget(self.txt_ip)
        ip_layout.addWidget(self.chk_dns_only)

        # Assemble the form
        self.form_layout.addRow("Profile Name:", self.txt_name)
        self.form_layout.addRow("IP Address:", ip_layout)  # <--- Add the combined layout here
        self.form_layout.addRow("Subnet Mask:", self.txt_mask)
        self.form_layout.addRow("Gateway:", self.txt_gateway)
        self.form_layout.addRow("Primary DNS:", self.txt_dns1)
        self.form_layout.addRow("Secondary DNS:", self.txt_dns2)

        right_main_layout.addLayout(self.form_layout)

        # --- Bottom Action Bar ---
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 10, 0, 0)
        action_layout.setSpacing(10)

        self.btn_save = QPushButton(" Save")
        # Change the icon to a neutral gray so it is visible in both Light and Dark mode
        self.btn_save.setIcon(qta.icon("fa5s.save", color="#888888"))

        # Adaptive Outline Style: Transparent background with hover effects
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 2px solid #888888;
                border-radius: 5px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(136, 136, 136, 0.15);
            }
            QPushButton:pressed {
                background-color: rgba(136, 136, 136, 0.3);
            }
        """)
        self.btn_save.clicked.connect(self._save_current_profile)

        self.btn_apply = QPushButton(" APPLY TO SYSTEM")
        self.btn_apply.setIcon(qta.icon("fa5s.bolt", color="white"))

        # Modern Material Green with interactive hover and press states
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:pressed {
                background-color: #064e3b;
            }
        """)
        self.btn_apply.clicked.connect(self._apply_profile)

        action_layout.addWidget(self.btn_save, 1)
        action_layout.addWidget(self.btn_apply, 2)

        right_main_layout.addLayout(action_layout)
        right_main_layout.addStretch()

        right_main_layout.addStretch()

        right_widget.setEnabled(False)
        self.editor_widget = right_widget

        splitter.addWidget(right_widget)
        splitter.setSizes([200, 500])

        self.statusBar().showMessage("Ready. Select a profile or interface.")
        self._update_system_status()

    def _auto_fill_subnet_mask(self) -> None:
        """Mimics Windows behavior: Auto-fills the subnet mask based on IP class."""
        ip_text = self.txt_ip.text().strip()
        current_mask = self.txt_mask.text().strip()

        # Only auto-fill if they typed an IP and the mask box is currently empty
        if not ip_text or current_mask:
            return

        parts = ip_text.split(".")

        # Ensure it looks like a valid IP address before guessing
        if len(parts) == 4 and parts[0].isdigit():
            first_octet = int(parts[0])

            # Class A
            if 1 <= first_octet <= 126:
                self.txt_mask.setText("255.0.0.0")
            # Class B
            elif 128 <= first_octet <= 191:
                self.txt_mask.setText("255.255.0.0")
            # Class C (Most common for local networks)
            elif 192 <= first_octet <= 223:
                self.txt_mask.setText("255.255.255.0")
            # If it's a loopback (127) or experimental (>223), we leave it blank

    def _toggle_dns_mode(self, state: int) -> None:
        """Shows or hides IP fields based on the DNS-Only checkbox."""
        is_dns_only = state == 2

        # True if we should show the fields, False if we should hide them
        show_fields = not is_dns_only

        # 1. Hide/Show the input boxes
        self.txt_ip.setVisible(show_fields)
        self.txt_mask.setVisible(show_fields)
        self.txt_gateway.setVisible(show_fields)

        # 2. Hide/Show the labels for Mask and Gateway
        # (We use an 'if' check defensively to prevent crashes if the label isn't found)
        mask_label = self.form_layout.labelForField(self.txt_mask)
        if mask_label:
            mask_label.setVisible(show_fields)
            
        gateway_label = self.form_layout.labelForField(self.txt_gateway)
        if gateway_label:
            gateway_label.setVisible(show_fields)

        # NOTE: We intentionally do NOT hide the IP Address label because 
        # the DNS-Only checkbox still lives on that row and needs the context!

        # 3. Clear the data if we are hiding them
        if is_dns_only:
            self.txt_ip.clear()
            self.txt_mask.clear()
            self.txt_gateway.clear()

    def _setup_system_tray(self) -> None:
        """Initializes the system tray icon and context menu."""
        self.tray_icon = QSystemTrayIcon(self)

        # Use a built-in Windows network icon so you don't need external image files
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
        self.tray_icon.setIcon(icon)

        # Create the right-click menu
        tray_menu = QMenu()

        action_show = QAction("Show", self)
        action_show.triggered.connect(self._show_window)
        tray_menu.addAction(action_show)

        action_about = QAction("About", self)
        action_about.triggered.connect(self._show_about)
        tray_menu.addAction(action_about)

        tray_menu.addSeparator()

        action_quit = QAction("Quit", self)
        action_quit.triggered.connect(self._quit_app)
        tray_menu.addAction(action_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        # Allow double-clicking the tray icon to show the window
        self.tray_icon.activated.connect(self._on_tray_icon_activated)

    def _show_window(self) -> None:
        """Brings the application back to the foreground."""
        self.showNormal()
        self.activateWindow()

    def _show_about(self) -> None:
        """Displays the About dialog."""
        QMessageBox.information(
            self,
            "About Network Gadget",
            "Network Ed - Pro Edition\n\nQuickly swap Windows network profiles.",
        )

    def _on_tray_icon_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handles double-clicks on the system tray icon."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _quit_app(self) -> None:
        """Sets the flag to true and completely shuts down the application."""
        self._is_quitting = True
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        """Overrides the default close behavior to minimize to tray instead."""
        if self._is_quitting:
            # If the user clicked "Quit" from the tray, let the window close normally
            event.accept()
        else:
            # If the user clicked the "X", hide the window instead
            event.ignore()
            self.hide()

            self.tray_icon.showMessage(
                "Running in Background",
                "Network Ed is still running in your system tray.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )

    def _update_system_status(self) -> None:
        iface = self.interface_combo.currentText()
        if not iface:
            return

        active_ip = NetworkManager.get_active_ip(iface)

        if active_ip in ["Unknown", "Unconfigured / DHCP"]:
            self.lbl_active_ip.setText(f"Status: {active_ip}")
            return

        matched_profile_name = "Custom / Unsaved Profile"
        for group in self.groups:
            for profile in group.profiles:
                if profile.ip_address and profile.ip_address.strip() == active_ip:
                    matched_profile_name = f"{group.name}::{profile.name}"
                    break
            if matched_profile_name != "Custom / Unsaved Profile":
                break

        self.lbl_active_ip.setText(f"Active: `{matched_profile_name}` ({active_ip})")

    def _populate_tree(self) -> None:
        self.tree.clear()
        for group in self.groups:
            group_item = QTreeWidgetItem(self.tree, [group.name])
            group_item.setData(0, Qt.ItemDataRole.UserRole, group)
            group_item.setExpanded(True)
            for profile in group.profiles:
                profile_item = QTreeWidgetItem(group_item, [profile.name])
                profile_item.setData(0, Qt.ItemDataRole.UserRole, profile)

    def _add_group(self) -> None:
        name, ok = QInputDialog.getText(self, "New Group", "Enter group name:")
        if ok and name:
            new_group = ProfileGroup(name=name, profiles=[])
            self.groups.append(new_group)
            ProfileStore.save(self.groups)
            self._populate_tree()

    def _on_tree_selection(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            self.editor_widget.setEnabled(False)
            return

        item = selected[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)

        if isinstance(data, NetworkProfile):
            self.current_profile = data
            self.editor_widget.setEnabled(True)
            self.txt_name.setText(data.name)
            self.txt_ip.setText(data.ip_address)
            self.txt_mask.setText(data.subnet_mask)
            self.txt_gateway.setText(data.gateway or "")
            self.txt_dns1.setText(data.dns_primary or "")
            self.txt_dns2.setText(data.dns_secondary or "")

            is_dns_only = not bool(data.ip_address and data.ip_address.strip())
            self.chk_dns_only.setChecked(is_dns_only)
        else:
            self.editor_widget.setEnabled(False)
            self.current_profile = None

    def _save_current_profile(self) -> None:
        if not self.current_profile:
            return

        self.current_profile.name = self.txt_name.text()
        self.current_profile.ip_address = self.txt_ip.text()
        self.current_profile.subnet_mask = self.txt_mask.text()
        self.current_profile.gateway = self.txt_gateway.text().strip() or None
        self.current_profile.dns_primary = self.txt_dns1.text().strip() or None
        self.current_profile.dns_secondary = self.txt_dns2.text().strip() or None

        ProfileStore.save(self.groups)

        selected_items = self.tree.selectedItems()
        if selected_items:
            item = selected_items[0]
            item.setText(0, self.current_profile.name)

        self.statusBar().showMessage(
            f"Profile '{self.current_profile.name}' saved to library.", 4000
        )

    def _apply_profile(self) -> None:
        if not self.current_profile:
            return

        self._save_current_profile()

        interface = self.interface_combo.currentText()
        try:
            NetworkManager.apply_profile(interface, self.current_profile)
            self._update_system_status()
            self.statusBar().showMessage(
                f"SUCCESS: Profile applied to '{interface}'", 6000
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _show_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if not item:
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu()

        if isinstance(data, ProfileGroup):
            action_add = menu.addAction("Add New Profile Here")
            action_delete = menu.addAction("Delete Group")

            action = menu.exec(self.tree.viewport().mapToGlobal(position))
            if action == action_add:
                self._add_new_profile_to_group(item, data)
            elif action == action_delete:
                self._delete_group(item, data)

        elif isinstance(data, NetworkProfile):
            action_delete = menu.addAction("Delete Profile")

            action = menu.exec(self.tree.viewport().mapToGlobal(position))
            if action == action_delete:
                self._delete_profile(item, data)

    def _add_new_profile_to_group(
        self, group_item: QTreeWidgetItem, group: ProfileGroup
    ) -> None:
        new_profile = NetworkProfile(name="New Blank Profile")
        group.profiles.append(new_profile)
        ProfileStore.save(self.groups)

        profile_item = QTreeWidgetItem(group_item, [new_profile.name])
        profile_item.setData(0, Qt.ItemDataRole.UserRole, new_profile)
        group_item.setExpanded(True)

        self.tree.setCurrentItem(profile_item)

    def _delete_profile(self, item: QTreeWidgetItem, profile: NetworkProfile) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete profile '{profile.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            parent_item = item.parent()
            group: ProfileGroup = parent_item.data(0, Qt.ItemDataRole.UserRole)

            group.profiles.remove(profile)
            parent_item.removeChild(item)
            ProfileStore.save(self.groups)

            self.editor_widget.setEnabled(False)
            self.current_profile = None

    def _delete_group(self, item: QTreeWidgetItem, group: ProfileGroup) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete group '{group.name}' and all its profiles?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.groups.remove(group)
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
            ProfileStore.save(self.groups)

    def _set_to_dhcp(self) -> None:
        interface = self.interface_combo.currentText()
        if not interface:
            return

        reply = QMessageBox.question(
            self,
            "Confirm DHCP Reset",
            f"Are you sure you want to clear static settings and set '{interface}' to DHCP?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                NetworkManager.enable_dhcp(interface)
                self._update_system_status()
                self.statusBar().showMessage(
                    f"SUCCESS: '{interface}' reverted to DHCP", 6000
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to set DHCP:\n{str(e)}")
