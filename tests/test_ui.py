import json
import threading

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

import ui
from models import NetworkProfile, ProfileGroup, ProfileStore
from network import Adapter, InterfaceStatus, NetworkManager
from resources import app_icon


class FakeWatcher(QObject):
    """Stands in for NetworkChangeWatcher; tests emit `changed` themselves."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = True
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def fake_watcher(monkeypatch):
    monkeypatch.setattr(ui, "NetworkChangeWatcher", FakeWatcher)


@pytest.fixture
def adapters():
    """What the fake 'Get-NetAdapter' returns; tests can change it."""
    return {"list": [Adapter("Ethernet", "Up"), Adapter("Wi-Fi", "Disconnected")], "calls": 0}


@pytest.fixture
def window(qapp, store_path, monkeypatch, adapters):
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

    def list_adapters(cls):
        adapters["calls"] += 1
        return list(adapters["list"])

    monkeypatch.setattr(NetworkManager, "get_network_interfaces", classmethod(list_adapters))
    monkeypatch.setattr(
        NetworkManager,
        "get_interface_status",
        classmethod(lambda cls, name: InterfaceStatus(False, ("10.0.0.5",))),
    )
    w = ui.MainWindow()
    settle(qapp, w)
    yield w
    settle(qapp, w)  # no background job may outlive this test's fakes
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


def combo_texts(w):
    return [w.interface_combo.itemText(i) for i in range(w.interface_combo.count())]


# --- automatic adapter detection -------------------------------------------------


def test_network_change_rescans_adapters(qapp, window, adapters):
    calls = adapters["calls"]
    adapters["list"] = [
        Adapter("Ethernet", "Up"),
        Adapter("Ethernet 3", "Up"),  # cable plugged in
        Adapter("Wi-Fi", "Disconnected"),
    ]
    window._watcher.changed.emit()
    settle(qapp, window)
    assert adapters["calls"] == calls + 1
    assert combo_texts(window) == ["Ethernet", "Ethernet 3", "Wi-Fi (Disconnected)"]
    assert window._selected_adapter() == "Ethernet", "selection is kept"


def test_status_only_change_updates_labels_in_place(qapp, window, adapters):
    window.interface_combo.setCurrentIndex(1)
    settle(qapp, window)
    removed = []
    window.interface_combo.model().rowsRemoved.connect(lambda *a: removed.append(a))

    adapters["list"] = [Adapter("Ethernet", "Up"), Adapter("Wi-Fi", "Disabled")]
    window._watcher.changed.emit()
    settle(qapp, window)

    assert combo_texts(window) == ["Ethernet", "Wi-Fi (Disabled)"]
    assert removed == [], "items were rebuilt; an open dropdown would have closed"
    assert window._selected_adapter() == "Wi-Fi"


def test_removed_adapter_falls_back_and_says_so(qapp, window, adapters):
    window.interface_combo.setCurrentIndex(1)
    settle(qapp, window)
    adapters["list"] = [Adapter("Ethernet", "Up")]  # USB adapter unplugged
    window._watcher.changed.emit()
    settle(qapp, window)
    assert window._selected_adapter() == "Ethernet"
    assert "'Wi-Fi' is no longer available" in window.statusBar().currentMessage()


def test_network_change_during_apply_waits_until_done(qapp, window, adapters, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(
        NetworkManager, "apply_profile", classmethod(lambda cls, i, p: gate.wait(5))
    )
    select(window, 0, 0)
    window._apply_profile()
    calls = adapters["calls"]

    window._watcher.changed.emit()  # our own apply changes the network
    qapp.processEvents()
    assert adapters["calls"] == calls, "must not rescan while applying"

    gate.set()
    settle(qapp, window)
    assert adapters["calls"] == calls + 1, "rescan once the apply finished"


def test_overlapping_rescans_collapse(qapp, window, adapters, monkeypatch):
    gate = threading.Event()

    def slow(cls):
        adapters["calls"] += 1
        gate.wait(5)
        return list(adapters["list"])

    monkeypatch.setattr(NetworkManager, "get_network_interfaces", classmethod(slow))
    calls = adapters["calls"]
    for _ in range(3):
        window.refresh_adapters(quiet=True)
    gate.set()
    settle(qapp, window)
    assert adapters["calls"] == calls + 2, "the running scan plus one queued, not three"


def test_closing_stops_the_watcher(qapp, window):
    window._is_quitting = True
    window.close()
    assert window._watcher.stopped


# --- Refresh reloads network_profiles.json ---------------------------------------


def test_refresh_reloads_profiles_from_disk(qapp, window, store_path, adapters):
    select(window, 0, 0)
    data = json.loads(store_path.read_text(encoding="utf-8"))
    data[0]["profiles"][0]["gateway"] = "10.0.0.1"
    data.append({"name": "New site", "profiles": [{"name": "x", "dns_primary": "9.9.9.9"}]})
    store_path.write_text(json.dumps(data), encoding="utf-8")
    calls = adapters["calls"]

    window._on_refresh_clicked()
    settle(qapp, window)

    assert [g.name for g in window.groups] == ["Site", "New site"]
    assert window.tree.topLevelItemCount() == 2
    assert window.current_profile.name == "sw", "the open profile is selected again"
    assert window.txt_gateway.text() == "10.0.0.1"
    assert adapters["calls"] == calls + 1, "adapters are re-scanned too"


def test_refresh_asks_before_dropping_unsaved_edits(qapp, window, store_path, monkeypatch):
    asked = []

    def answer(*args, **kwargs):
        asked.append(args[2])
        return ui.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(ui.QMessageBox, "question", answer)
    select(window, 0, 0)
    window.txt_name.setText("edited")
    window._on_refresh_clicked()
    assert "discards your unsaved changes" in asked[0]
    assert window.txt_name.text() == "edited"


def test_refresh_keeps_profiles_when_file_is_broken(qapp, window, store_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a[1]))
    store_path.write_text("{broken", encoding="utf-8")
    window._on_refresh_clicked()
    settle(qapp, window)
    assert warnings == ["Profiles not reloaded"]
    assert [g.name for g in window.groups] == ["Site"]


# --- duplicate group / profile ----------------------------------------------------


def test_copy_name_skips_names_in_use():
    assert ui.copy_name("sw", {"sw"}) == "sw (copy)"
    assert ui.copy_name("sw", {"sw", "sw (copy)", "sw (copy 2)"}) == "sw (copy 3)"


def test_duplicate_profile(qapp, window):
    group_item = window.tree.topLevelItem(0)
    original = window.groups[0].profiles[0]
    window._duplicate_profile(group_item.child(0), original)

    profiles = window.groups[0].profiles
    assert [p.name for p in profiles] == ["sw", "sw (copy)", "dns"], "copy sits below original"
    copy = profiles[1]
    assert copy is not original
    assert (copy.ip_address, copy.subnet_mask) == (original.ip_address, original.subnet_mask)
    assert group_item.child(1).text(0) == "sw (copy)"
    assert window.current_profile is copy, "the copy is selected"
    assert [p.name for p in ProfileStore.load()[0].profiles] == ["sw", "sw (copy)", "dns"]

    window._duplicate_profile(group_item.child(0), original)
    assert window.groups[0].profiles[1].name == "sw (copy 2)"


def test_duplicate_group_copies_profiles(qapp, window):
    original = window.groups[0]
    window._duplicate_group(window.tree.topLevelItem(0), original)

    assert [g.name for g in window.groups] == ["Site", "Site (copy)"]
    copy = window.groups[1]
    assert [p.name for p in copy.profiles] == ["sw", "dns"]
    assert all(c is not o for c, o in zip(copy.profiles, original.profiles, strict=True))
    assert window.tree.topLevelItem(1).childCount() == 2
    assert [g.name for g in ProfileStore.load()] == ["Site", "Site (copy)"]


def test_delete_removes_the_clicked_profile_not_an_equal_one(qapp, window, monkeypatch):
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *a: ui.QMessageBox.StandardButton.Yes)
    group = window.groups[0]
    first = group.profiles[0]
    twin = NetworkProfile(**vars(first))  # equal fields, different object
    group.profiles.insert(1, twin)
    window._populate_tree()

    window._delete_profile(window.tree.topLevelItem(0).child(1), twin)
    assert group.profiles[0] is first
    assert len(group.profiles) == 2


# --- self-test mode (used by CI on the built exe) ---------------------------------


def test_self_test_report(qapp, window, tmp_path, monkeypatch):
    import selftest

    assert selftest.report_path(["exe", "--self-test=r.json"]).name == "r.json"
    assert selftest.report_path(["exe"]) is None

    exits = []
    monkeypatch.setattr(selftest.QApplication, "exit", lambda code=0: exits.append(code))
    report_file = tmp_path / "report.json"
    selftest.start(window, report_file)
    for _ in range(20):
        qapp.processEvents()
        if exits:
            break
        __import__("time").sleep(0.05)

    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["ok"] is True, report
    assert report["adapters"] == ["Ethernet", "Wi-Fi"]
    assert report["app_icon"] and report["fa5s_icon"]
    assert exits == [0]


# --- search box -----------------------------------------------------------------


def visible_names(w):
    """The tree as shown: group names, then '  profile' lines, skipping hidden items."""
    out = []
    for g in range(w.tree.topLevelItemCount()):
        group_item = w.tree.topLevelItem(g)
        if group_item.isHidden():
            continue
        out.append(group_item.text(0))
        for p in range(group_item.childCount()):
            if not group_item.child(p).isHidden():
                out.append("  " + group_item.child(p).text(0))
    return out


def add_lab_group(w):
    lab = NetworkProfile("printer", ip_address="192.168.1.50", subnet_mask="255.255.255.0")
    w.groups.append(ProfileGroup("Lab", [lab]))
    w._populate_tree()


def test_profile_matches_name_ip_gateway_dns_and_group():
    group = ProfileGroup("Kahak", [])
    sw = NetworkProfile(
        "sw", ip_address="10.6.160.25", gateway="10.6.160.62", dns_primary="1.1.1.1"
    )
    for terms in (["sw"], ["10.6.160"], ["160.62"], ["1.1.1.1"], ["kahak"], ["kahak", "10.6"]):
        assert ui.profile_matches(group, sw, terms), terms
    assert not ui.profile_matches(group, sw, ["kahak", "192.168"])
    assert not ui.profile_matches(group, sw, ["255"]), "subnet masks would match everything"


def test_search_filters_by_name_ip_and_dns(qapp, window):
    add_lab_group(window)
    window.txt_filter.setText("SW")
    assert visible_names(window) == ["Site", "  sw"]
    window.txt_filter.setText("192.168")
    assert visible_names(window) == ["Lab", "  printer"]
    window.txt_filter.setText("1.1.1.1")
    assert visible_names(window) == ["Site", "  dns"]
    assert window.tree.headerItem().text(0) == "1 of 3 profiles"
    window.txt_filter.clear()
    assert visible_names(window) == ["Site", "  sw", "  dns", "Lab", "  printer"]
    assert window.tree.headerItem().text(0) == "Profile Library"


def test_search_group_name_and_combined_words(qapp, window):
    add_lab_group(window)
    window.txt_filter.setText("site")
    assert visible_names(window) == ["Site", "  sw", "  dns"]
    window.txt_filter.setText("site 10.0")
    assert visible_names(window) == ["Site", "  sw"]
    window.txt_filter.setText("no-such-thing")
    assert visible_names(window) == []
    assert "Nothing matches 'no-such-thing'" in window.statusBar().currentMessage()


def test_open_profile_stays_visible_while_searching(qapp, window):
    select(window, 0, 0)  # sw
    window.txt_filter.setText("dns")
    assert visible_names(window) == ["Site", "  sw", "  dns"]
    select(window, 0, 1)  # move to dns: sw no longer matches, so it is hidden now
    assert visible_names(window) == ["Site", "  dns"]


def test_enter_opens_first_match(qapp, window):
    window.txt_filter.setText("dns")
    window.txt_filter.returnPressed.emit()
    assert window.current_profile.name == "dns"


def test_new_group_hidden_by_search_clears_it(qapp, window, monkeypatch):
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: ("Zeta", True))
    window.txt_filter.setText("sw")
    window._add_group()
    assert window.txt_filter.text() == ""
    assert "Zeta" in visible_names(window)


def test_search_survives_refresh(qapp, window):
    window.txt_filter.setText("dns")
    window._on_refresh_clicked()
    settle(qapp, window)
    assert visible_names(window) == ["Site", "  dns"]


def test_about_links_to_the_repo(qapp, window, monkeypatch):
    shown = []
    monkeypatch.setattr(ui.QMessageBox, "about", lambda *a: shown.append(a))
    window._show_about()
    title, html = shown[0][1], shown[0][2]
    assert title == "About Network Ed"
    assert '<a href="https://github.com/aerosadegh/Neted">' in html
    assert f"Version {ui.__version__}" in html
