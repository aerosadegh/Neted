import json

import pytest

import ui
from models import NetworkProfile, ProfileGroup, ProfileStore
from network import Adapter, InterfaceStatus, NetworkManager
from resources import app_icon


@pytest.fixture
def window(qapp, store_path, monkeypatch):
    store_path.write_text(
        json.dumps(
            [
                {
                    "name": "Site",
                    "profiles": [
                        {"name": "sw", "ip_address": "10.0.0.5", "subnet_mask": "255.255.255.0"},
                        {"name": "dns", "ip_address": "", "dns_primary": "1.1.1.1"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        NetworkManager,
        "get_network_interfaces",
        classmethod(lambda cls: [Adapter("Ethernet", "Up"), Adapter("Wi-Fi", "Disconnected")]),
    )
    monkeypatch.setattr(
        NetworkManager,
        "get_interface_status",
        classmethod(lambda cls, name: InterfaceStatus(False, ("10.0.0.5",))),
    )
    w = ui.MainWindow()
    settle(qapp, w)
    yield w
    w._is_quitting = True
    w.tray_icon.hide()
    w.close()


def settle(qapp, w):
    """Waits for background jobs and delivers their results."""
    for _ in range(5):
        w._tasks.wait(5000)
        qapp.processEvents()


def select(w, group_index, profile_index):
    w.tree.setCurrentItem(w.tree.topLevelItem(group_index).child(profile_index))


def test_app_icon_renders(qapp):
    assert not app_icon().isNull()


def test_adapters_and_status_load_in_background(qapp, window):
    combo = window.interface_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Ethernet", "Wi-Fi (Disconnected)"]
    assert window.lbl_active_ip.text() == "Active: Site › sw (10.0.0.5)"


def test_describe_status():
    groups = [ProfileGroup("Site", [NetworkProfile("sw", ip_address="10.0.0.5")])]
    assert ui.describe_status(InterfaceStatus(True, ("192.168.1.9",)), groups) == (
        "Active: DHCP (192.168.1.9)"
    )
    assert ui.describe_status(InterfaceStatus(False, ("1.2.3.4",)), groups) == (
        "Active: Static, not in library (1.2.3.4)"
    )
    assert ui.describe_status(
        InterfaceStatus(True, ()), groups, Adapter("Wi-Fi", "Disconnected")
    ) == ("Active: No IPv4 address (DHCP) · adapter disconnected")


def test_save_rejects_invalid_input(qapp, window, monkeypatch):
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    select(window, 0, 0)
    window.txt_ip.setText("10.0.0.999")
    assert window._save_current_profile() is False
    assert "IP address is not valid" in warnings[0]
    assert ProfileStore.load()[0].profiles[0].ip_address == "10.0.0.5"


def test_save_writes_valid_input(qapp, window):
    select(window, 0, 0)
    window.txt_gateway.setText("10.0.0.1")
    assert window._save_current_profile() is True
    assert ProfileStore.load()[0].profiles[0].gateway == "10.0.0.1"


def test_switching_profiles_asks_about_unsaved_edits(qapp, window, monkeypatch):
    asked = []

    def answer(*args, **kwargs):
        asked.append(args[2])
        return ui.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(ui.QMessageBox, "question", answer)
    select(window, 0, 0)
    window.txt_name.setText("renamed")
    select(window, 0, 1)
    assert asked == ["Save changes to 'sw'?"]
    assert window.current_profile.name == "sw", "cancel keeps the edited profile open"
    assert window.txt_name.text() == "renamed"


def test_loading_dns_only_profile_is_not_an_edit(qapp, window):
    select(window, 0, 1)
    assert window.chk_dns_only.isChecked()
    assert not window.has_unsaved_changes()


def test_apply_runs_in_background_and_reports(qapp, window, monkeypatch):
    applied = []
    monkeypatch.setattr(
        NetworkManager, "apply_profile", classmethod(lambda cls, i, p: applied.append((i, p.name)))
    )
    select(window, 0, 0)
    window._apply_profile()
    assert window._busy
    settle(qapp, window)
    assert applied == [("Ethernet", "sw")]
    assert not window._busy
    assert "SUCCESS" in window.statusBar().currentMessage()


def test_apply_failure_is_shown(qapp, window, monkeypatch):
    errors = []
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *a: errors.append(a[2]))

    def fail(cls, interface, profile):
        raise RuntimeError("boom")

    monkeypatch.setattr(NetworkManager, "apply_profile", classmethod(fail))
    select(window, 0, 0)
    window._apply_profile()
    settle(qapp, window)
    assert errors == ["boom"]
    assert window.btn_apply.isEnabled()


def test_corrupt_library_warns_instead_of_starting_empty_silently(qapp, store_path, monkeypatch):
    store_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(NetworkManager, "get_network_interfaces", classmethod(lambda cls: []))
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a[1]))
    w = ui.MainWindow()
    settle(qapp, w)
    assert warnings == ["Profile library not loaded"]
    assert w.groups == []
    w._is_quitting = True
    w.tray_icon.hide()
    w.close()
