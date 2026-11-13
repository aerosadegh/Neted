"""Windows backend. PowerShell is always faked, so these also run on Linux."""

import subprocess

import pytest

import network_windows as network
from models import NetworkProfile
from netcommon import Adapter, InterfaceStatus
from network_windows import PowerShellError
from network_windows import WindowsNetwork as NetworkManager


@pytest.fixture
def no_powershell(monkeypatch):
    """Fails the test if anything tries to start a process."""

    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", boom)


def test_apply_command_passes_values_through_env_not_script():
    profile = NetworkProfile(
        name="sw",
        ip_address=" 10.6.160.25 ",
        subnet_mask="255.255.255.192",
        gateway="10.6.160.62",
        dns_primary="10.6.160.1",
        dns_secondary="8.8.8.8",
    )
    script, env = NetworkManager.build_apply_command("Ethernet 2", profile)
    assert env == {
        "NETED_ADAPTER": "Ethernet 2",
        "NETED_IP": "10.6.160.25",
        "NETED_PREFIX": "26",
        "NETED_GATEWAY": "10.6.160.62",
        "NETED_DNS": "10.6.160.1,8.8.8.8",
    }
    assert script is network._APPLY_PROFILE  # fixed text, no values pasted in


def test_dns_only_command_leaves_ip_settings_empty():
    _, env = NetworkManager.build_apply_command(
        "Wi-Fi", NetworkProfile(name="d", dns_primary="178.22.122.100")
    )
    assert env["NETED_IP"] == env["NETED_PREFIX"] == env["NETED_GATEWAY"] == ""
    assert env["NETED_DNS"] == "178.22.122.100"


def test_hostile_adapter_name_stays_data():
    name = 'Bob\'s "lab" $(Remove-Item C:\\)'
    script, env = NetworkManager.build_apply_command(
        name, NetworkProfile(name="d", dns_primary="1.1.1.1")
    )
    assert env["NETED_ADAPTER"] == name
    assert name not in script


def test_invalid_profile_is_rejected_before_powershell(no_powershell, monkeypatch):
    monkeypatch.setattr(NetworkManager, "DEBUG_MODE", False)
    monkeypatch.setattr(NetworkManager, "is_admin", staticmethod(lambda: True))
    bad = NetworkProfile(name="x", dns_primary='1.1.1.1"; Write-Host PWNED; "')
    with pytest.raises(ValueError, match="Primary DNS is not valid"):
        NetworkManager.apply_profile("Ethernet", bad)


def test_no_adapter_is_rejected():
    with pytest.raises(ValueError, match="No network adapter"):
        NetworkManager.build_apply_command("", NetworkProfile(name="d", dns_primary="1.1.1.1"))
    with pytest.raises(ValueError, match="No network adapter"):
        NetworkManager.build_dhcp_command("")


def test_debug_mode_prints_instead_of_running(no_powershell, monkeypatch, capsys):
    monkeypatch.setattr(NetworkManager, "DEBUG_MODE", True)
    NetworkManager.apply_profile("Ethernet", NetworkProfile(name="d", dns_primary="1.1.1.1"))
    NetworkManager.enable_dhcp("Ethernet")
    out = capsys.readouterr().out
    assert out.count("DRY RUN") == 2
    assert "NETED_DNS=1.1.1.1" in out


def test_changes_need_admin(no_powershell, monkeypatch):
    monkeypatch.setattr(NetworkManager, "DEBUG_MODE", False)
    monkeypatch.setattr(NetworkManager, "is_admin", staticmethod(lambda: False))
    with pytest.raises(PermissionError):
        NetworkManager.enable_dhcp("Ethernet")


def fake_run(monkeypatch, stdout):
    calls = []

    def run(script, env=None):
        calls.append((script, env))
        return stdout

    monkeypatch.setattr(NetworkManager, "_run", staticmethod(run))
    return calls


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ('{"Name":"Ethernet","Status":"Up"}', [Adapter("Ethernet", "Up")]),
        (
            '[{"Name":"Wi-Fi","Status":"Disconnected"},{"Name":"Ethernet 2","Status":"Up"},'
            '{"Name":"Ethernet","Status":"Up"}]',
            [
                Adapter("Ethernet", "Up"),
                Adapter("Ethernet 2", "Up"),
                Adapter("Wi-Fi", "Disconnected"),
            ],
        ),
        ('[{"Name":"OpenVPN DCO","Status":"Not Present"}]', []),
        ("[]", []),
        ("", []),
    ],
)
def test_get_network_interfaces_parses_and_sorts(monkeypatch, stdout, expected):
    fake_run(monkeypatch, stdout)
    assert NetworkManager.get_network_interfaces() == expected


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (
            '{"Dhcp":"Enabled","Addresses":["192.168.1.20"]}',
            InterfaceStatus(True, ("192.168.1.20",)),
        ),
        (
            '{"Dhcp":"Disabled","Addresses":["10.0.0.5","169.254.3.4"]}',
            InterfaceStatus(False, ("10.0.0.5", "169.254.3.4")),
        ),
        ('{"Dhcp":null,"Addresses":[]}', InterfaceStatus(None, ())),
    ],
)
def test_get_interface_status(monkeypatch, stdout, expected):
    calls = fake_run(monkeypatch, stdout)
    assert NetworkManager.get_interface_status("Ethernet") == expected
    assert calls[0][1] == {"NETED_ADAPTER": "Ethernet"}


def test_garbage_output_becomes_powershell_error(monkeypatch):
    fake_run(monkeypatch, "Welcome to my PowerShell profile!")
    with pytest.raises(PowerShellError, match="Unexpected output"):
        NetworkManager.get_network_interfaces()


def test_powershell_failure_message_is_trimmed(monkeypatch):
    stderr = (
        "IP address 10.0.0.5 is already used by adapter 'Wi-Fi'.\r\n"
        "At line:14 char:9\r\n+         throw ...\r\n"
    )

    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "powershell", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(PowerShellError) as info:
        NetworkManager._run("x")
    assert str(info.value) == "IP address 10.0.0.5 is already used by adapter 'Wi-Fi'."


def test_every_call_skips_the_user_profile(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    NetworkManager.get_network_interfaces()
    assert "-NoProfile" in seen["args"]
