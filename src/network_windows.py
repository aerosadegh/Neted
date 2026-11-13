"""Reads and changes Windows network adapter settings through PowerShell.

The PowerShell scripts below are fixed text. Values (adapter name, addresses) are passed
in through NETED_* environment variables, never pasted into the script, so nothing a
user types or a profile file contains can run as code in the elevated shell.
"""

import ctypes
import json
import os
import subprocess
from typing import Any

import netcommon
from models import NetworkProfile, clean, mask_to_prefix, parse_ipv4
from netcommon import Adapter, InterfaceStatus, NetworkError, sort_adapters

_POWERSHELL = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]

# UTF-8 output keeps non-ASCII adapter names intact; 'Stop' turns errors into failures.
_PRELUDE = """\
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$ErrorActionPreference = 'Stop'
"""

_LIST_ADAPTERS = """\
$items = @(Get-NetAdapter | Select-Object Name, @{ n = 'Status'; e = { "$($_.Status)" } })
ConvertTo-Json -InputObject $items -Compress
"""

_INTERFACE_STATUS = """\
$adapter = $env:NETED_ADAPTER
$ips = @(Get-NetIPAddress -InterfaceAlias $adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    ForEach-Object { $_.IPAddress })
$ipif = Get-NetIPInterface -InterfaceAlias $adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Select-Object -First 1
$dhcp = if ($ipif) { "$($ipif.Dhcp)" } else { $null }
ConvertTo-Json -InputObject @{ Dhcp = $dhcp; Addresses = $ips } -Compress
"""

_APPLY_PROFILE = """\
$adapter = $env:NETED_ADAPTER
$null = Get-NetAdapter -Name $adapter

if ($env:NETED_IP) {
    $ip = $env:NETED_IP
    $prefix = [int]$env:NETED_PREFIX
    $gateway = $env:NETED_GATEWAY

    # Refuse if another adapter already owns this IP.
    $conflict = @(Get-NetIPAddress -IPAddress $ip -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object InterfaceAlias -ne $adapter)
    if ($conflict.Count -gt 0) {
        throw "IP address $ip is already used by adapter '$($conflict[0].InterfaceAlias)'."
    }

    # Switch to static addressing and drop the old default route.
    Set-NetIPInterface -InterfaceAlias $adapter -AddressFamily IPv4 -Dhcp Disabled -ErrorAction SilentlyContinue
    Remove-NetRoute -InterfaceAlias $adapter -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' `
        -Confirm:$false -ErrorAction SilentlyContinue

    # Remove every other IPv4 address on this adapter.
    Get-NetIPAddress -InterfaceAlias $adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object IPAddress -ne $ip |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue

    $ipParams = @{ InterfaceAlias = $adapter; IPAddress = $ip; PrefixLength = $prefix }
    $existing = Get-NetIPAddress -InterfaceAlias $adapter -IPAddress $ip -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue
    if ($existing) {
        Set-NetIPAddress @ipParams | Out-Null
        if ($gateway) {
            New-NetRoute -InterfaceAlias $adapter -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' `
                -NextHop $gateway -Confirm:$false | Out-Null
        }
    } else {
        if ($gateway) { $ipParams['DefaultGateway'] = $gateway }
        New-NetIPAddress @ipParams | Out-Null
    }
}

if ($env:NETED_DNS) {
    Set-DnsClientServerAddress -InterfaceAlias $adapter -ServerAddresses ($env:NETED_DNS -split ',')
} elseif ($env:NETED_IP) {
    Set-DnsClientServerAddress -InterfaceAlias $adapter -ResetServerAddresses
}
"""

_ENABLE_DHCP = """\
$adapter = $env:NETED_ADAPTER
$null = Get-NetAdapter -Name $adapter

Remove-NetIPAddress -InterfaceAlias $adapter -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
Remove-NetRoute -InterfaceAlias $adapter -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' `
    -Confirm:$false -ErrorAction SilentlyContinue
Set-NetIPInterface -InterfaceAlias $adapter -AddressFamily IPv4 -Dhcp Enabled
Set-DnsClientServerAddress -InterfaceAlias $adapter -ResetServerAddresses
"""


class PowerShellError(NetworkError):
    """A PowerShell command failed. The message is meant for the user."""


def _error_text(stderr: str) -> str:
    """Keeps the message and drops PowerShell's 'At line:' / CategoryInfo trailer."""
    text = stderr.strip()
    return text.split("\nAt line:", 1)[0].strip() or text


class WindowsNetwork:
    """All system interaction for reading and changing adapter settings on Windows."""

    DEBUG_MODE = netcommon.DEBUG_MODE
    REQUIRES_ADMIN = True  # the exe asks for it through UAC

    @staticmethod
    def is_admin() -> bool:
        """Checks if the current process has Administrator privileges."""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    # --- Read-only queries (always run, also in debug mode) ---

    @classmethod
    def get_network_interfaces(cls) -> list[Adapter]:
        """All visible adapters, connected ones first."""
        items = cls._run_json(_LIST_ADAPTERS)
        if isinstance(items, dict):
            items = [items]
        adapters = [
            Adapter(name=str(item["Name"]), status=str(item.get("Status") or "Unknown"))
            for item in items or []
            if isinstance(item, dict) and item.get("Name")
        ]
        # "Not Present" adapters are leftovers of removed hardware/VPNs and can't be configured.
        return sort_adapters([a for a in adapters if a.status != "Not Present"])

    @classmethod
    def get_interface_status(cls, interface_name: str) -> InterfaceStatus:
        """Current IPv4 addresses of an adapter and whether it uses DHCP."""
        data = cls._run_json(_INTERFACE_STATUS, {"NETED_ADAPTER": interface_name})
        if not isinstance(data, dict):
            raise PowerShellError("Unexpected output from PowerShell while reading adapter status.")
        dhcp = data.get("Dhcp")
        addresses = data.get("Addresses") or []
        if isinstance(addresses, str):
            addresses = [addresses]
        return InterfaceStatus(
            dhcp_enabled=None if dhcp is None else dhcp == "Enabled",
            addresses=tuple(str(a) for a in addresses),
        )

    # --- Changes (need admin; printed instead of run in debug mode) ---

    @staticmethod
    def build_apply_command(
        interface_name: str, profile: NetworkProfile
    ) -> tuple[str, dict[str, str]]:
        """Validates the profile and returns the script plus the environment it reads."""
        if not interface_name:
            raise ValueError("No network adapter selected.")
        errors = profile.validate()
        if errors:
            raise ValueError("\n".join(errors))

        env = {
            "NETED_ADAPTER": interface_name,
            "NETED_IP": "",
            "NETED_PREFIX": "",
            "NETED_GATEWAY": "",
        }
        if not profile.is_dns_only:
            env["NETED_IP"] = parse_ipv4(profile.ip_address, "IP address")
            env["NETED_PREFIX"] = str(mask_to_prefix(profile.subnet_mask))
            if clean(profile.gateway):
                env["NETED_GATEWAY"] = parse_ipv4(profile.gateway, "Gateway")

        dns = [
            parse_ipv4(value, "DNS server")
            for value in (profile.dns_primary, profile.dns_secondary)
            if clean(value)
        ]
        env["NETED_DNS"] = ",".join(dns)
        return _APPLY_PROFILE, env

    @staticmethod
    def build_dhcp_command(interface_name: str) -> tuple[str, dict[str, str]]:
        if not interface_name:
            raise ValueError("No network adapter selected.")
        return _ENABLE_DHCP, {"NETED_ADAPTER": interface_name}

    @classmethod
    def apply_profile(cls, interface_name: str, profile: NetworkProfile) -> str | None:
        """Applies a profile to an adapter. DNS-only profiles leave IP settings alone.

        Returns an optional note for the user (always None on Windows).
        """
        cls._run_change(*cls.build_apply_command(interface_name, profile))
        return None

    @classmethod
    def enable_dhcp(cls, interface_name: str) -> str | None:
        """Switches an adapter back to DHCP for both IP and DNS."""
        cls._run_change(*cls.build_dhcp_command(interface_name))
        return None

    # --- Plumbing ---

    @classmethod
    def _run_change(cls, script: str, env: dict[str, str]) -> None:
        if cls.DEBUG_MODE:
            values = "\n".join(f"  {k}={v}" for k, v in env.items())
            print(f"--- DRY RUN ---\n{values}\n{script}---------------")
            return
        if not cls.is_admin():
            raise PermissionError("Administrator privileges are required.")
        cls._run(script, env)

    @classmethod
    def _run_json(cls, script: str, env: dict[str, str] | None = None) -> Any:
        out = cls._run(script, env).strip()
        if not out:
            return None
        try:
            return json.loads(out)
        except ValueError:
            raise PowerShellError(f"Unexpected output from PowerShell:\n{out[:500]}") from None

    @staticmethod
    def _run(script: str, env: dict[str, str] | None = None) -> str:
        try:
            result = subprocess.run(
                [*_POWERSHELL, _PRELUDE + script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, **(env or {})},
                check=True,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.CalledProcessError as e:
            raise PowerShellError(
                _error_text(e.stderr or "") or f"PowerShell exited with code {e.returncode}."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise PowerShellError("PowerShell did not finish within 2 minutes.") from e
        except OSError as e:
            raise PowerShellError(f"Could not start PowerShell: {e}") from e
        return result.stdout.lstrip("﻿")
