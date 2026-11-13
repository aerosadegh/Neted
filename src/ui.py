"""Main window: profile library, editor form, adapter status and tray icon."""

import ipaddress
from dataclasses import fields, replace

import qtawesome as qta
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import (
    NetworkProfile,
    ProfileGroup,
    ProfileStore,
    ProfileStoreError,
    clean,
)
from netwatch import NetworkChangeWatcher
from network import Adapter, InterfaceStatus, NetworkManager
from resources import app_icon
from version import __version__
from worker import TaskRunner

APP_NAME = "Network Ed"
_DATA = Qt.ItemDataRole.UserRole
_Button = QMessageBox.StandardButton


def find_profile(
    groups: list[ProfileGroup], addresses: tuple[str, ...]
) -> tuple[ProfileGroup, NetworkProfile] | None:
    """First saved profile whose IP is one of the given addresses."""
    for group in groups:
        for profile in group.profiles:
            if clean(profile.ip_address) in addresses:
                return group, profile
    return None


def describe_status(
    status: InterfaceStatus, groups: list[ProfileGroup], adapter: Adapter | None = None
) -> str:
    """One-line description of an adapter's current IPv4 state."""
    addresses = ", ".join(status.addresses)
    if not status.addresses:
        text = "No IPv4 address" + (" (DHCP)" if status.dhcp_enabled else "")
    elif status.dhcp_enabled:
        text = f"DHCP ({addresses})"
    else:
        match = find_profile(groups, status.addresses)
        name = f"{match[0].name} › {match[1].name}" if match else "Static, not in library"
        text = f"{name} ({addresses})"
    if adapter and not adapter.is_up:
        text += f" · adapter {adapter.status.lower()}"
    return f"Active: {text}"


def copy_name(name: str, taken: set[str]) -> str:
    """'sw' -> 'sw (copy)', then 'sw (copy 2)', 'sw (copy 3)', ... skipping names in use."""
    candidate, n = f"{name} (copy)", 2
    while candidate in taken:
        candidate, n = f"{name} (copy {n})", n + 1
    return candidate


def index_of(items: list, obj: object) -> int:
    """Position of this exact object (not just an equal one: duplicates compare equal)."""
    return next(i for i, item in enumerate(items) if item is obj)


def adapter_label(adapter: Adapter) -> str:
    return adapter.name if adapter.is_up else f"{adapter.name} ({adapter.status})"


def _comparable(profile: NetworkProfile) -> tuple[str, ...]:
    """Profile values as the editor shows them, for spotting unsaved edits."""
    ip = clean(profile.ip_address)
    return (
        clean(profile.name),
        ip,
        clean(profile.subnet_mask) if ip else "",
        clean(profile.gateway) if ip else "",
        clean(profile.dns_primary),
        clean(profile.dns_secondary),
    )


class MainWindow(QMainWindow):
    """Splitter layout: profile tree on the left, editor on the right, status on top."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - Pro Edition")
        self.setWindowIcon(self._icon())
        self.resize(500, 350)
        self.setMinimumSize(450, 280)

        self._is_quitting = False
        self._busy = False
        self._status_request = 0
        self._adapters_loading = False
        self._adapters_reload_queued = False
        self._refresh_after_busy = False
        self._adapters: dict[str, Adapter] = {}
        self.current_profile: NetworkProfile | None = None
        self._current_item: QTreeWidgetItem | None = None
        self._tasks = TaskRunner(self)

        self.groups: list[ProfileGroup] = []
        load_error = None
        try:
            self.groups = ProfileStore.load()
        except ProfileStoreError as e:
            load_error = str(e)

        self._setup_ui()
        self._populate_tree()
        self._setup_system_tray()
        self.refresh_adapters()

        # Re-scan by itself when adapters appear/disappear, (dis)connect or change address.
        self._watcher = NetworkChangeWatcher(self)
        self._watcher.changed.connect(self._on_network_changed)

        if load_error:
            QTimer.singleShot(
                0, lambda: QMessageBox.warning(self, "Profile library not loaded", load_error)
            )

    def _icon(self):
        icon = app_icon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
        return icon

    # ------------------------------------------------------------------ layout

    def _setup_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        self.setStyleSheet("QLineEdit { padding: 5px; border-radius: 3px; }")

        # --- Top bar: system status ---
        status_group = QGroupBox("System Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(5, 5, 5, 5)

        top_row = QHBoxLayout()
        self.interface_combo = QComboBox()
        self.interface_combo.setPlaceholderText("Loading adapters...")
        self.interface_combo.currentIndexChanged.connect(self.refresh_status)

        self.btn_dhcp = QPushButton(" DHCP")
        self.btn_dhcp.setIcon(qta.icon("fa5s.network-wired", color="#3498DB"))
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

        self.btn_refresh = QPushButton(" Refresh")
        self.btn_refresh.setIcon(qta.icon("fa5s.sync-alt", color="#888"))
        self.btn_refresh.setToolTip(
            "Reload profiles from network_profiles.json and re-scan network adapters"
        )
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)

        top_row.addWidget(QLabel("Interface:"))
        top_row.addWidget(self.interface_combo, 1)
        top_row.addWidget(self.btn_dhcp)
        top_row.addWidget(self.btn_refresh)

        self.lbl_active_ip = QLabel("Active: Scanning...")
        self.lbl_active_ip.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_active_ip.setStyleSheet("font-weight: bold; color: #5DADE2;")

        status_layout.addLayout(top_row)
        status_layout.addWidget(self.lbl_active_ip)
        main_layout.addWidget(status_group)

        # --- Main splitter: tree and editor ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Profile Library")
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        left_layout.addWidget(self.tree)

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

        right_widget = QWidget()
        right_main_layout = QVBoxLayout(right_widget)
        right_main_layout.setContentsMargins(5, 0, 0, 0)

        self.form_layout = QFormLayout()
        self.form_layout.setVerticalSpacing(4)
        self.form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.chk_dns_only = QCheckBox("DNS-Only")
        self.chk_dns_only.setStyleSheet("font-weight: bold; color: #5DADE2;")
        self.chk_dns_only.toggled.connect(self._on_dns_only_toggled)

        self.txt_name = QLineEdit()
        self.txt_ip = QLineEdit()
        self.txt_ip.editingFinished.connect(self._auto_fill_subnet_mask)
        self.txt_mask = QLineEdit()
        self.txt_gateway = QLineEdit()
        self.txt_dns1 = QLineEdit()
        self.txt_dns2 = QLineEdit()
        for optional in (self.txt_gateway, self.txt_dns1, self.txt_dns2):
            optional.setPlaceholderText("Optional")

        ip_layout = QHBoxLayout()
        ip_layout.setContentsMargins(0, 0, 0, 0)
        ip_layout.addWidget(self.txt_ip)
        ip_layout.addWidget(self.chk_dns_only)

        self.form_layout.addRow("Profile Name:", self.txt_name)
        self.form_layout.addRow("IP Address:", ip_layout)
        self.form_layout.addRow("Subnet Mask:", self.txt_mask)
        self.form_layout.addRow("Gateway:", self.txt_gateway)
        self.form_layout.addRow("Primary DNS:", self.txt_dns1)
        self.form_layout.addRow("Secondary DNS:", self.txt_dns2)
        right_main_layout.addLayout(self.form_layout)

        # --- Bottom action bar ---
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 10, 0, 0)
        action_layout.setSpacing(10)

        self.btn_save = QPushButton(" Save")
        self.btn_save.setIcon(qta.icon("fa5s.save", color="#888888"))
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 2px solid #888888;
                border-radius: 5px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(136, 136, 136, 0.15); }
            QPushButton:pressed { background-color: rgba(136, 136, 136, 0.3); }
        """)
        self.btn_save.clicked.connect(self._save_current_profile)

        self.btn_apply = QPushButton(" APPLY TO SYSTEM")
        self.btn_apply.setIcon(qta.icon("fa5s.bolt", color="white"))
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #047857; }
            QPushButton:pressed { background-color: #064e3b; }
            QPushButton:disabled { background-color: #6b7280; }
        """)
        self.btn_apply.clicked.connect(self._apply_profile)

        action_layout.addWidget(self.btn_save, 1)
        action_layout.addWidget(self.btn_apply, 2)
        right_main_layout.addLayout(action_layout)
        right_main_layout.addStretch()

        right_widget.setEnabled(False)
        self.editor_widget = right_widget
        splitter.addWidget(right_widget)
        splitter.setSizes([200, 500])

        self.statusBar().showMessage("Ready. Select a profile or interface.")

    def _setup_system_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self._icon(), self)
        self.tray_icon.setToolTip(f"{APP_NAME} {__version__}")

        tray_menu = QMenu(self)
        action_show = QAction("Show", self)
        action_show.triggered.connect(self.show_window)
        tray_menu.addAction(action_show)

        action_about = QAction("About", self)
        action_about.triggered.connect(self._show_about)
        tray_menu.addAction(action_about)

        tray_menu.addSeparator()

        action_quit = QAction("Quit", self)
        action_quit.triggered.connect(self._quit_app)
        tray_menu.addAction(action_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()

    # ------------------------------------------------------------- window/tray

    def show_window(self) -> None:
        """Brings the window to the foreground (also used when a second copy is started)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} - Pro Edition\nVersion {__version__}\n\n"
            "Quickly swap Windows network profiles.",
        )

    def _on_tray_icon_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def _quit_app(self) -> None:
        if not self._resolve_unsaved_changes():
            return
        self._is_quitting = True
        self._watcher.stop()
        self._tasks.shutdown()  # a running Apply still finishes before the process exits
        self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing the window hides it to the tray; Quit in the tray menu exits."""
        if self._is_quitting:
            self._watcher.stop()
            self._tasks.shutdown()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "Running in Background",
            f"{APP_NAME} is still running in your system tray.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    # ---------------------------------------------------------- adapter status

    def _selected_adapter(self) -> str:
        return self.interface_combo.currentData() or ""

    def _on_refresh_clicked(self) -> None:
        """Refresh button: re-read the profile library, then re-scan adapters."""
        if self.reload_profiles():
            self.statusBar().showMessage(
                f"Reloaded {len(self.groups)} group(s) from {ProfileStore.FILE_PATH.name}.", 4000
            )
        self.refresh_adapters()

    def _on_network_changed(self) -> None:
        """Windows reported an adapter or address change."""
        if self._busy:
            self._refresh_after_busy = True  # our own Apply/DHCP is still changing things
            return
        self.statusBar().showMessage("Network change detected, updating...", 3000)
        self.refresh_adapters(quiet=True)

    def refresh_adapters(self, quiet: bool = False) -> None:
        """Reloads the adapter list (in the background), then the status line.

        If a load is already running, one more runs after it, so the newest state wins.
        """
        if self._adapters_loading:
            self._adapters_reload_queued = True
            return
        self._adapters_loading = True
        if not quiet:
            self.lbl_active_ip.setText("Active: Scanning...")
        self._tasks.run(
            NetworkManager.get_network_interfaces,
            self._on_adapters_loaded,
            self._on_adapters_failed,
        )

    def _on_adapters_loaded(self, adapters: list[Adapter]) -> None:
        combo = self.interface_combo
        previous = self._selected_adapter()
        self._adapters = {a.name: a for a in adapters}
        names = [a.name for a in adapters]

        if names == [combo.itemData(i) for i in range(combo.count())]:
            # Same adapters in the same order: only update labels, so an open
            # dropdown stays open and the selection is untouched.
            for i, adapter in enumerate(adapters):
                combo.setItemText(i, adapter_label(adapter))
        else:
            combo.blockSignals(True)
            combo.clear()
            for adapter in adapters:
                combo.addItem(adapter_label(adapter), adapter.name)
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
            if previous and previous not in self._adapters:
                self.statusBar().showMessage(f"Adapter '{previous}' is no longer available.", 6000)

        if adapters:
            self.refresh_status()
        else:
            self.lbl_active_ip.setText("Active: No network adapters found")
        self._adapters_load_finished()

    def _on_adapters_failed(self, exc: Exception) -> None:
        self.lbl_active_ip.setText("Active: Could not list network adapters")
        self.statusBar().showMessage(f"Could not list network adapters: {exc}", 8000)
        self._adapters_load_finished()

    def _adapters_load_finished(self) -> None:
        self._adapters_loading = False
        if self._adapters_reload_queued:
            self._adapters_reload_queued = False
            self.refresh_adapters(quiet=True)

    def refresh_status(self) -> None:
        """Reads the selected adapter's current IP settings in the background."""
        iface = self._selected_adapter()
        if not iface:
            return
        self._status_request += 1
        request = self._status_request
        self._tasks.run(
            lambda: NetworkManager.get_interface_status(iface),
            lambda status: self._on_status(request, iface, status),
            lambda exc: self._on_status_failed(request, exc),
        )

    def _on_status(self, request: int, iface: str, status: InterfaceStatus) -> None:
        if request != self._status_request:
            return  # an older refresh finished after a newer one started
        self.lbl_active_ip.setText(describe_status(status, self.groups, self._adapters.get(iface)))

    def _on_status_failed(self, request: int, exc: Exception) -> None:
        if request != self._status_request:
            return
        self.lbl_active_ip.setText("Active: Unknown")
        self.statusBar().showMessage(f"Could not read adapter status: {exc}", 8000)

    # ------------------------------------------------------------------- tree

    def reload_profiles(self) -> bool:
        """Re-reads network_profiles.json. Returns False if the library was left as it was."""
        if self.has_unsaved_changes():
            choice = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Reloading discards your unsaved changes to '{self.current_profile.name}'.\n"
                "Reload anyway?",
                _Button.Discard | _Button.Cancel,
                _Button.Cancel,
            )
            if choice != _Button.Discard:
                return False
        try:
            groups = ProfileStore.load()
        except ProfileStoreError as e:
            QMessageBox.warning(
                self, "Profiles not reloaded", f"{e}\n\nThe profiles on screen were kept."
            )
            return False

        selected = self._selected_profile_key()
        self._clear_editor()  # before the tree is rebuilt, so nothing counts as unsaved
        self.groups = groups
        self._populate_tree()
        self._select_profile_key(selected)
        return True

    def _selected_profile_key(self) -> tuple[str, str] | None:
        item = self._current_item
        if item is None or item.parent() is None:
            return None
        return item.parent().text(0), item.text(0)

    def _select_profile_key(self, key: tuple[str, str] | None) -> None:
        if key is None:
            return
        for g in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(g)
            if group_item.text(0) != key[0]:
                continue
            for p in range(group_item.childCount()):
                if group_item.child(p).text(0) == key[1]:
                    self.tree.setCurrentItem(group_item.child(p))
                    return

    def _populate_tree(self) -> None:
        self.tree.clear()
        for group in self.groups:
            group_item = self._group_item(group)
            self.tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)

    @staticmethod
    def _profile_item(profile: NetworkProfile) -> QTreeWidgetItem:
        item = QTreeWidgetItem([profile.name])
        item.setData(0, _DATA, profile)
        return item

    @classmethod
    def _group_item(cls, group: ProfileGroup) -> QTreeWidgetItem:
        item = QTreeWidgetItem([group.name])
        item.setData(0, _DATA, group)
        for profile in group.profiles:
            item.addChild(cls._profile_item(profile))
        return item

    def _persist(self) -> bool:
        try:
            ProfileStore.save(self.groups)
        except ProfileStoreError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        return True

    def _add_group(self) -> None:
        name, ok = QInputDialog.getText(self, "New Group", "Enter group name:")
        name = name.strip()
        if ok and name:
            self.groups.append(ProfileGroup(name=name, profiles=[]))
            self._persist()
            self._populate_tree()

    def _on_tree_selection(self) -> None:
        selected = self.tree.selectedItems()
        item = selected[0] if selected else None
        if item is self._current_item:
            return
        if not self._resolve_unsaved_changes():
            self._select_silently(self._current_item)
            return

        data = item.data(0, _DATA) if item else None
        if isinstance(data, NetworkProfile):
            self._load_profile(item, data)
        else:
            self._clear_editor()

    def _select_silently(self, item: QTreeWidgetItem | None) -> None:
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        if item is not None:
            self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)

    def _show_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if not item:
            return

        data = item.data(0, _DATA)
        menu = QMenu(self)

        if isinstance(data, ProfileGroup):
            action_add = menu.addAction(qta.icon("fa5s.plus", color="#888"), "Add New Profile Here")
            action_copy = menu.addAction(qta.icon("fa5s.copy", color="#888"), "Duplicate Group")
            menu.addSeparator()
            action_delete = menu.addAction(qta.icon("fa5s.trash-alt", color="#888"), "Delete Group")
            action = menu.exec(self.tree.viewport().mapToGlobal(position))
            if action == action_add:
                self._add_new_profile_to_group(item, data)
            elif action == action_copy:
                self._duplicate_group(item, data)
            elif action == action_delete:
                self._delete_group(item, data)

        elif isinstance(data, NetworkProfile):
            action_copy = menu.addAction(qta.icon("fa5s.copy", color="#888"), "Duplicate Profile")
            menu.addSeparator()
            action_delete = menu.addAction(
                qta.icon("fa5s.trash-alt", color="#888"), "Delete Profile"
            )
            action = menu.exec(self.tree.viewport().mapToGlobal(position))
            if action == action_copy:
                self._duplicate_profile(item, data)
            elif action == action_delete:
                self._delete_profile(item, data)

    def _duplicate_profile(self, item: QTreeWidgetItem, profile: NetworkProfile) -> None:
        """Inserts a copy right below the profile and selects it."""
        if not self._resolve_unsaved_changes():  # so the copy includes saved edits
            return
        group_item = item.parent()
        group: ProfileGroup = group_item.data(0, _DATA)
        copy = replace(profile, name=copy_name(profile.name, {p.name for p in group.profiles}))
        index = index_of(group.profiles, profile) + 1
        group.profiles.insert(index, copy)
        self._persist()

        copy_item = self._profile_item(copy)
        group_item.insertChild(index, copy_item)
        self.tree.setCurrentItem(copy_item)
        self.statusBar().showMessage(f"Duplicated '{profile.name}' as '{copy.name}'.", 4000)

    def _duplicate_group(self, item: QTreeWidgetItem, group: ProfileGroup) -> None:
        """Inserts a copy of the group (with copies of all its profiles) right below it."""
        if not self._resolve_unsaved_changes():
            return
        copy = ProfileGroup(
            name=copy_name(group.name, {g.name for g in self.groups}),
            profiles=[replace(p) for p in group.profiles],
        )
        index = index_of(self.groups, group) + 1
        self.groups.insert(index, copy)
        self._persist()

        copy_item = self._group_item(copy)
        self.tree.insertTopLevelItem(index, copy_item)
        copy_item.setExpanded(True)
        self.tree.setCurrentItem(copy_item)
        self.statusBar().showMessage(f"Duplicated group '{group.name}' as '{copy.name}'.", 4000)

    def _add_new_profile_to_group(self, group_item: QTreeWidgetItem, group: ProfileGroup) -> None:
        new_profile = NetworkProfile(name="New Blank Profile")
        group.profiles.append(new_profile)
        self._persist()

        profile_item = self._profile_item(new_profile)
        group_item.addChild(profile_item)
        group_item.setExpanded(True)
        self.tree.setCurrentItem(profile_item)

    def _delete_profile(self, item: QTreeWidgetItem, profile: NetworkProfile) -> None:
        reply = QMessageBox.question(
            self, "Confirm Delete", f"Delete profile '{profile.name}'?", _Button.Yes | _Button.No
        )
        if reply != _Button.Yes:
            return
        if profile is self.current_profile:
            self._clear_editor()  # before removal, so it isn't reported as unsaved
        parent_item = item.parent()
        group: ProfileGroup = parent_item.data(0, _DATA)
        del group.profiles[index_of(group.profiles, profile)]
        parent_item.removeChild(item)
        self._persist()

    def _delete_group(self, item: QTreeWidgetItem, group: ProfileGroup) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete group '{group.name}' and all its profiles?",
            _Button.Yes | _Button.No,
        )
        if reply != _Button.Yes:
            return
        if any(p is self.current_profile for p in group.profiles):
            self._clear_editor()
        del self.groups[index_of(self.groups, group)]
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._persist()

    # ----------------------------------------------------------------- editor

    def _load_profile(self, item: QTreeWidgetItem, profile: NetworkProfile) -> None:
        self._current_item = item
        self.current_profile = profile
        self.editor_widget.setEnabled(True)

        self.txt_name.setText(profile.name)
        self.txt_ip.setText(clean(profile.ip_address))
        self.txt_mask.setText(clean(profile.subnet_mask))
        self.txt_gateway.setText(clean(profile.gateway))
        self.txt_dns1.setText(clean(profile.dns_primary))
        self.txt_dns2.setText(clean(profile.dns_secondary))

        # Set the checkbox without its handler: that would clear the fields.
        self.chk_dns_only.blockSignals(True)
        self.chk_dns_only.setChecked(profile.is_dns_only)
        self.chk_dns_only.blockSignals(False)
        self._set_ip_fields_visible(not profile.is_dns_only)

    def _clear_editor(self) -> None:
        self._current_item = None
        self.current_profile = None
        for field in (
            self.txt_name,
            self.txt_ip,
            self.txt_mask,
            self.txt_gateway,
            self.txt_dns1,
            self.txt_dns2,
        ):
            field.clear()
        self.editor_widget.setEnabled(False)

    def _form_profile(self) -> NetworkProfile:
        """The profile as currently typed in the editor."""
        dns_only = self.chk_dns_only.isChecked()

        def text(widget: QLineEdit) -> str:
            return widget.text().strip()

        return NetworkProfile(
            name=text(self.txt_name),
            ip_address="" if dns_only else text(self.txt_ip),
            subnet_mask="" if dns_only else text(self.txt_mask),
            gateway=None if dns_only else (text(self.txt_gateway) or None),
            dns_primary=text(self.txt_dns1) or None,
            dns_secondary=text(self.txt_dns2) or None,
        )

    def has_unsaved_changes(self) -> bool:
        if self.current_profile is None:
            return False
        return _comparable(self._form_profile()) != _comparable(self.current_profile)

    def _resolve_unsaved_changes(self) -> bool:
        """Asks what to do with unsaved edits. Returns False if the user cancelled."""
        if not self.has_unsaved_changes():
            return True
        self.show_window()
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to '{self.current_profile.name}'?",
            _Button.Save | _Button.Discard | _Button.Cancel,
            _Button.Save,
        )
        if choice == _Button.Save:
            return self._save_current_profile()
        if choice == _Button.Discard:
            self._load_profile(self._current_item, self.current_profile)  # back to saved values
            return True
        return False

    def _set_ip_fields_visible(self, visible: bool) -> None:
        for widget in (self.txt_ip, self.txt_mask, self.txt_gateway):
            widget.setVisible(visible)
        # The IP Address label stays: the DNS-Only checkbox lives on that row.
        for widget in (self.txt_mask, self.txt_gateway):
            label = self.form_layout.labelForField(widget)
            if label:
                label.setVisible(visible)

    def _on_dns_only_toggled(self, checked: bool) -> None:
        self._set_ip_fields_visible(not checked)
        if checked:
            self.txt_ip.clear()
            self.txt_mask.clear()
            self.txt_gateway.clear()

    def _auto_fill_subnet_mask(self) -> None:
        """Like Windows: suggests a classful mask when an IP is typed and the mask is empty."""
        if self.txt_mask.text().strip():
            return
        try:
            first_octet = ipaddress.IPv4Address(self.txt_ip.text().strip()).packed[0]
        except ipaddress.AddressValueError:
            return
        if 1 <= first_octet <= 126:
            self.txt_mask.setText("255.0.0.0")
        elif 128 <= first_octet <= 191:
            self.txt_mask.setText("255.255.0.0")
        elif 192 <= first_octet <= 223:
            self.txt_mask.setText("255.255.255.0")

    def _save_current_profile(self) -> bool:
        """Validates and saves the editor. Returns False if nothing was saved."""
        if self.current_profile is None:
            return False

        candidate = self._form_profile()
        errors = [] if candidate.name else ["Profile name is empty."]
        errors += candidate.validate()
        if errors:
            QMessageBox.warning(self, "Can't save profile", "\n".join(errors))
            return False

        for f in fields(NetworkProfile):
            setattr(self.current_profile, f.name, getattr(candidate, f.name))
        if not self._persist():
            return False

        if self._current_item is not None:
            self._current_item.setText(0, candidate.name)
        self.statusBar().showMessage(f"Profile '{candidate.name}' saved to library.", 4000)
        return True

    # ---------------------------------------------------------- system changes

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for widget in (self.btn_apply, self.btn_dhcp, self.btn_refresh, self.interface_combo):
            widget.setEnabled(not busy)
        if message:
            self.statusBar().showMessage(message)

    def _apply_profile(self) -> None:
        if self.current_profile is None or self._busy:
            return
        if not self._save_current_profile():
            return
        interface = self._selected_adapter()
        if not interface:
            QMessageBox.warning(self, "No adapter", "Select a network adapter first.")
            return

        profile = replace(self.current_profile)  # snapshot: the worker must not see later edits
        self._set_busy(True, f"Applying '{profile.name}' to '{interface}'...")
        self._tasks.run(
            lambda: NetworkManager.apply_profile(interface, profile),
            lambda _: self._on_change_done(f"SUCCESS: Profile applied to '{interface}'"),
            lambda exc: self._on_change_failed("Error", exc),
        )

    def _set_to_dhcp(self) -> None:
        interface = self._selected_adapter()
        if not interface or self._busy:
            return
        reply = QMessageBox.question(
            self,
            "Confirm DHCP Reset",
            f"Are you sure you want to clear static settings and set '{interface}' to DHCP?",
            _Button.Yes | _Button.No,
        )
        if reply != _Button.Yes:
            return

        self._set_busy(True, f"Switching '{interface}' to DHCP...")
        self._tasks.run(
            lambda: NetworkManager.enable_dhcp(interface),
            lambda _: self._on_change_done(f"SUCCESS: '{interface}' reverted to DHCP"),
            lambda exc: self._on_change_failed("Failed to set DHCP", exc),
        )

    def _on_change_done(self, message: str) -> None:
        self._set_busy(False)
        self.statusBar().showMessage(message, 6000)
        self._refresh_after_change()

    def _on_change_failed(self, title: str, exc: Exception) -> None:
        self._set_busy(False)
        self.statusBar().clearMessage()
        QMessageBox.critical(self, title, str(exc))
        self._refresh_after_change()

    def _refresh_after_change(self) -> None:
        if self._refresh_after_busy:
            self._refresh_after_busy = False
            self.refresh_adapters(quiet=True)  # also refreshes the status line
        else:
            self.refresh_status()
