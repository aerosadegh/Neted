"""Linux backend. `ip` and `nmcli` are always faked, so these also run on Windows."""

import json
import subprocess

import pytest

import network
import network_linux
from models import NetworkProfile
from netcommon import Adapter, InterfaceStatus
from network_linux import CommandError, LinuxNetwork

STATIC = NetworkProfile(
    name="sw",
    ip_address="10.6.160.25",
    subnet_mask="255.255.255.192",
    gateway="10.6.160.62",
    dns_primary="10.6.160.1",
    dns_secondary="8.8.8.8",
)
DNS_ONLY = NetworkProfile(name="dns", dns_primary="1.1.1.1")

LINKS = [
    {"ifname": "lo", "flags": ["LOOPBACK", "UP", "LOWER_UP"], "link_type": "loopback"},
    {"ifname": "wlp2s0", "flags": ["BROADCAST", "UP", "LOWER_UP"], "operstate": "UP"},
    {"ifname": "enp3s0", "flags": ["NO-CARRIER", "BROADCAST", "UP"], "operstate": "DOWN"},
    {"ifname": "enx001", "flags": ["BROADCAST", "MULTICAST"], "operstate": "DOWN"},
    {"ifname": "docker0", "flags": ["UP", "LOWER_UP"], "operstate": "UP"},
    {"ifname": "veth12ab", "flags": ["UP", "LOWER_UP"], "operstate": "UP"},
]


class FakeSystem:
    """Answers `ip` / `nmcli` commands from a table and records every call."""

    def __init__(self, answers: dict):
        self.answers = answers  # command prefix -> output, exception, or callable()
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        for prefix, answer in self.answers.items():
            if tuple(args[: len(prefix)]) == prefix:
                if isinstance(answer, Exception):
                    raise answer
                return answer() if callable(answer) else answer
        raise AssertionError(f"unexpected command: {args}")

    def nmcli_calls(self, *verbs):
        return [c for c in self.calls if c[0] == "nmcli" and all(v in c for v in verbs)]


@pytest.fixture
def system(monkeypatch):
    def install(answers):
        fake = FakeSystem(answers)
        monkeypatch.setattr(LinuxNetwork, "_run", staticmethod(fake))
        monkeypatch.setattr(LinuxNetwork, "DEBUG_MODE", False)
        return fake

    return install


def no_such_connection():
    return CommandError("Error: enp3s0: no such connection profile.")


# --- reading -----------------------------------------------------------------------


def test_lists_adapters_like_windows_does(system):
    system({("ip", "-j", "link"): json.dumps(LINKS)})
    assert LinuxNetwork.get_network_interfaces() == [
        Adapter("docker0", "Up"),
        Adapter("wlp2s0", "Up"),
        Adapter("enp3s0", "Disconnected"),  # cable unplugged
        Adapter("enx001", "Disabled"),  # switched off
    ]


def test_status_reads_ipv4_and_dhcp_flag(system):
    dhcp = [{"ifname": "wlp2s0", "addr_info": [
        {"family": "inet", "local": "192.168.1.20", "prefixlen": 24, "dynamic": True}
    ]}]  # fmt: skip
    system({("ip", "-j", "-4", "addr", "show", "dev", "wlp2s0"): json.dumps(dhcp)})
    assert LinuxNetwork.get_interface_status("wlp2s0") == InterfaceStatus(True, ("192.168.1.20",))

    static = [{"ifname": "enp3s0", "addr_info": [{"family": "inet", "local": "10.0.0.5"}]}]
    system({("ip", "-j", "-4", "addr", "show", "dev", "enp3s0"): json.dumps(static)})
    assert LinuxNetwork.get_interface_status("enp3s0") == InterfaceStatus(False, ("10.0.0.5",))

    system({("ip", "-j", "-4", "addr", "show", "dev", "enp3s0"): "[]"})
    assert LinuxNetwork.get_interface_status("enp3s0") == InterfaceStatus(None, ())


@pytest.mark.parametrize("name", ["", "-rf", "eth0; rm -rf /", "a/b", "x" * 16, "eth0:1"])
def test_bad_interface_names_are_refused_before_running_anything(system, name):
    fake = system({})
    with pytest.raises(ValueError):
        LinuxNetwork.get_interface_status(name)
    with pytest.raises(ValueError):
        LinuxNetwork.apply_profile(name, DNS_ONLY)
    assert fake.calls == []


# --- building settings ---------------------------------------------------------------


def test_static_settings():
    assert LinuxNetwork.build_ipv4_settings(STATIC) == [
        "ipv4.method", "manual",
        "ipv4.addresses", "10.6.160.25/26",
        "ipv4.gateway", "10.6.160.62",
        "ipv4.dns", "10.6.160.1,8.8.8.8",
        "ipv4.ignore-auto-dns", "yes",
    ]  # fmt: skip


def test_dns_only_settings_leave_ip_alone():
    assert LinuxNetwork.build_ipv4_settings(DNS_ONLY) == [
        "ipv4.dns", "1.1.1.1", "ipv4.ignore-auto-dns", "yes"
    ]  # fmt: skip


def test_invalid_profile_is_rejected(system):
    fake = system({})
    bad = NetworkProfile(name="x", dns_primary="1.1.1.1; reboot")
    with pytest.raises(ValueError, match="Primary DNS is not valid"):
        LinuxNetwork.apply_profile("wlp2s0", bad)
    assert fake.calls == []


# --- applying through nmcli --------------------------------------------------------------

UUID = "6b1f1c1e-1111-4222-8333-944455556666"


def connected_device(state="100 (connected)", con_uuid=UUID, kind="wifi"):
    return {
        ("ip", "-j", "-4", "addr", "show"): "[]",
        ("nmcli", "-g", "GENERAL.STATE"): state,
        ("nmcli", "-g", "GENERAL.CON-UUID"): con_uuid,
        ("nmcli", "-g", "GENERAL.TYPE"): kind,
        ("nmcli", "connection", "modify"): "",
        ("nmcli", "--wait", "45", "connection", "up"): "",
    }


def test_apply_changes_the_active_connection_and_reactivates_it(system):
    fake = system(connected_device())
    assert LinuxNetwork.apply_profile("wlp2s0", STATIC) is None
    modify = fake.nmcli_calls("modify")[0]
    assert modify[:5] == ["nmcli", "connection", "modify", "uuid", UUID]
    assert modify[5:] == LinuxNetwork.build_ipv4_settings(STATIC)
    assert fake.nmcli_calls("up") == [["nmcli", "--wait", "45", "connection", "up", "uuid", UUID]]


def test_unplugged_ethernet_gets_its_own_connection_applied_on_plug_in(system):
    find = ("nmcli", "-g", "connection.uuid", "connection", "show", "id")
    answers = connected_device(state="20 (unavailable)", con_uuid="", kind="ethernet")
    answers[find] = no_such_connection()

    def add_connection():
        answers[find] = UUID + "\n"  # from now on NetworkManager knows it
        return "Connection 'Network Ed (enp3s0)' successfully added."

    answers[("nmcli", "connection", "add")] = add_connection
    fake = system(answers)

    note = LinuxNetwork.apply_profile("enp3s0", STATIC)

    assert note == "Saved. NetworkManager applies it when 'enp3s0' is connected."
    add = [c for c in fake.calls if c[:3] == ["nmcli", "connection", "add"]][0]
    assert add[3:] == ["type", "ethernet", "ifname", "enp3s0", "con-name", "Network Ed (enp3s0)"]
    assert fake.nmcli_calls("modify")[0][4] == UUID
    assert fake.nmcli_calls("up") == [], "can't activate without a cable"


def test_existing_own_connection_is_reused(system):
    answers = connected_device(state="30 (disconnected)", con_uuid="--", kind="ethernet")
    answers[("nmcli", "-g", "connection.uuid", "connection", "show", "id")] = UUID
    fake = system(answers)
    LinuxNetwork.apply_profile("enp3s0", STATIC)
    assert [c for c in fake.calls if "add" in c] == []
    assert fake.nmcli_calls("up")[0][-1] == UUID


def test_wifi_without_connection_asks_to_join_first(system):
    answers = connected_device(state="30 (disconnected)", con_uuid="", kind="wifi")
    answers[("nmcli", "-g", "connection.uuid", "connection", "show", "id")] = no_such_connection()
    system(answers)
    with pytest.raises(network.NetworkError, match="join the network"):
        LinuxNetwork.apply_profile("wlp2s0", STATIC)


def test_unmanaged_adapter_is_refused(system):
    system(connected_device(state="10 (unmanaged)"))
    with pytest.raises(network.NetworkError, match="not managed by NetworkManager"):
        LinuxNetwork.apply_profile("docker0", DNS_ONLY)


def test_ip_used_by_another_adapter_is_refused(system):
    answers = connected_device()
    answers[("ip", "-j", "-4", "addr", "show")] = json.dumps(
        [{"ifname": "enp3s0", "addr_info": [{"family": "inet", "local": "10.6.160.25"}]}]
    )
    fake = system(answers)
    with pytest.raises(network.NetworkError, match="already used by adapter 'enp3s0'"):
        LinuxNetwork.apply_profile("wlp2s0", STATIC)
    assert fake.nmcli_calls("modify") == []


def test_dhcp_resets_the_connection(system):
    fake = system(connected_device())
    LinuxNetwork.enable_dhcp("wlp2s0")
    assert fake.nmcli_calls("modify")[0][5:] == network_linux.DHCP_SETTINGS


def test_dhcp_without_any_connection_says_so(system):
    answers = connected_device(con_uuid="")
    answers[("nmcli", "-g", "connection.uuid", "connection", "show", "id")] = no_such_connection()
    system(answers)
    with pytest.raises(network.NetworkError, match="no active connection to switch to DHCP"):
        LinuxNetwork.enable_dhcp("wlp2s0")


def test_debug_mode_prints_instead_of_changing(system, monkeypatch, capsys):
    fake = system({("ip", "-j", "-4", "addr", "show"): "[]"})
    monkeypatch.setattr(LinuxNetwork, "DEBUG_MODE", True)
    LinuxNetwork.apply_profile("wlp2s0", STATIC)
    LinuxNetwork.enable_dhcp("wlp2s0")
    assert capsys.readouterr().out.count("DRY RUN") == 2
    assert fake.nmcli_calls() == []


# --- running commands --------------------------------------------------------------------


def test_missing_nmcli_explains_what_is_needed(monkeypatch):
    def run(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(CommandError, match="needs NetworkManager"):
        LinuxNetwork._run(["nmcli", "general"])


def test_command_errors_carry_the_tools_message(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(4, args[0], stderr="Error: Insufficient privileges.\n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(CommandError, match="^Error: Insufficient privileges.$"):
        LinuxNetwork._run(["nmcli", "connection", "up"])


def test_commands_run_without_a_shell_in_c_locale(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    LinuxNetwork.get_network_interfaces()
    assert seen["args"] == ["ip", "-j", "link", "show"]
    assert "shell" not in seen
    assert seen["env"]["LC_ALL"] == "C"


def test_facade_picks_the_backend_for_this_os():
    expected = "WindowsNetwork" if network.sys.platform == "win32" else "LinuxNetwork"
    assert network.NetworkManager.__name__ == expected
