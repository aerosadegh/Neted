"""Reads and changes network settings on Linux.

- Reading (adapter list, addresses) uses `ip -j` from iproute2, which every distro has.
- Changing uses NetworkManager's `nmcli`. NetworkManager keeps settings per saved
  *connection*, so a profile is applied to the connection active on the adapter (for
  Wi-Fi: that network's saved settings). An Ethernet adapter without an active
  connection (cable unplugged) gets a "Network Ed (<adapter>)" connection, which
  NetworkManager activates as soon as the cable is connected.

Commands run as argument lists, never through a shell, and only with validated values.
NetworkManager (polkit) decides who may change settings: desktop users usually can
without root; otherwise nmcli reports "Insufficient privileges".
"""

import json
import os
import re
import shlex
import subprocess
from typing import Any

import netcommon
from models import NetworkProfile, clean, mask_to_prefix, parse_ipv4
from netcommon import Adapter, InterfaceStatus, NetworkError, sort_adapters

# Linux interface names: at most 15 characters, no whitespace, '/' or ':'. A leading '-'
# could be read as a command option, so it is refused too.
_INTERFACE_NAME = re.compile(r"^[^\s/:\-][^\s/:]{0,14}$")

# NetworkManager device states (NMDeviceState).
STATE_UNMANAGED = 10
STATE_UNAVAILABLE = 20  # no cable, Wi-Fi off, ...

DHCP_SETTINGS = [
    "ipv4.method", "auto",
    "ipv4.addresses", "",
    "ipv4.gateway", "",
    "ipv4.dns", "",
    "ipv4.ignore-auto-dns", "no",
]  # fmt: skip


class CommandError(NetworkError):
    """`ip` or `nmcli` failed. The message is meant for the user."""


def own_connection_name(interface: str) -> str:
    return f"Network Ed ({interface})"


def check_interface_name(name: str) -> str:
    if not name:
        raise ValueError("No network adapter selected.")
    if not _INTERFACE_NAME.match(name):
        raise ValueError(f"Not a valid network adapter name: {name!r}")
    return name


def link_status(link: dict) -> str:
    """Windows-style status for an `ip -j link` entry: Up, Disconnected or Disabled."""
    flags = set(link.get("flags") or [])
    if "UP" not in flags:
        return "Disabled"
    if "LOWER_UP" in flags or link.get("operstate") == "UP":
        return "Up"
    return "Disconnected"  # switched on, but no cable / not associated


class LinuxNetwork:
    """All system interaction for reading and changing adapter settings on Linux."""

    DEBUG_MODE = netcommon.DEBUG_MODE
    REQUIRES_ADMIN = False  # NetworkManager's own permissions apply

    @staticmethod
    def is_admin() -> bool:
        return hasattr(os, "geteuid") and os.geteuid() == 0

    # --- Read-only queries (always run, also in debug mode) ---

    @classmethod
    def get_network_interfaces(cls) -> list[Adapter]:
        """All adapters except loopback and container links, connected ones first."""
        links = cls._run_json(["ip", "-j", "link", "show"]) or []
        adapters = [
            Adapter(name=str(link["ifname"]), status=link_status(link))
            for link in links
            if isinstance(link, dict)
            and link.get("ifname")
            and link.get("link_type") != "loopback"
            and not str(link["ifname"]).startswith("veth")  # one per Docker container
        ]
        return sort_adapters(adapters)

    @classmethod
    def get_interface_status(cls, interface_name: str) -> InterfaceStatus:
        """Current IPv4 addresses of an adapter and whether they came from DHCP."""
        check_interface_name(interface_name)
        entries = cls._run_json(["ip", "-j", "-4", "addr", "show", "dev", interface_name]) or []
        infos = [
            info
            for entry in entries
            if isinstance(entry, dict)
            for info in entry.get("addr_info") or []
            if info.get("family") == "inet" and info.get("local")
        ]
        return InterfaceStatus(
            # The kernel marks DHCP leases "dynamic"; with no address it can't be told.
            dhcp_enabled=any(info.get("dynamic") for info in infos) if infos else None,
            addresses=tuple(str(info["local"]) for info in infos),
        )

    # --- Changes (through NetworkManager; printed instead of run in debug mode) ---

    @staticmethod
    def build_ipv4_settings(profile: NetworkProfile) -> list[str]:
        """Validates the profile and returns the `nmcli connection modify` properties."""
        errors = profile.validate()
        if errors:
            raise ValueError("\n".join(errors))
        dns = ",".join(
            parse_ipv4(value, "DNS server")
            for value in (profile.dns_primary, profile.dns_secondary)
            if clean(value)
        )
        if profile.is_dns_only:
            return ["ipv4.dns", dns, "ipv4.ignore-auto-dns", "yes"]
        ip = parse_ipv4(profile.ip_address, "IP address")
        gateway = parse_ipv4(profile.gateway, "Gateway") if clean(profile.gateway) else ""
        return [
            "ipv4.method", "manual",
            "ipv4.addresses", f"{ip}/{mask_to_prefix(profile.subnet_mask)}",
            "ipv4.gateway", gateway,
            "ipv4.dns", dns,
            "ipv4.ignore-auto-dns", "yes",
        ]  # fmt: skip

    @classmethod
    def apply_profile(cls, interface_name: str, profile: NetworkProfile) -> str | None:
        """Applies a profile. Returns a note for the user, or None."""
        check_interface_name(interface_name)
        settings = cls.build_ipv4_settings(profile)
        if not profile.is_dns_only:
            cls._refuse_ip_conflict(interface_name, parse_ipv4(profile.ip_address, "IP address"))
        return cls._change(interface_name, settings, create_if_missing=True)

    @classmethod
    def enable_dhcp(cls, interface_name: str) -> str | None:
        """Switches the adapter's connection back to DHCP for both IP and DNS."""
        check_interface_name(interface_name)
        return cls._change(interface_name, DHCP_SETTINGS, create_if_missing=False)

    @classmethod
    def _change(cls, interface: str, settings: list[str], create_if_missing: bool) -> str | None:
        if cls.DEBUG_MODE:
            props = " ".join(shlex.quote(s) for s in settings)
            print(
                f"--- DRY RUN ---\n"
                f"  nmcli connection modify <connection on {interface}> {props}\n"
                f"  nmcli connection up <that connection>\n---------------"
            )
            return None

        state = cls._device_state(interface)
        if state == STATE_UNMANAGED:
            raise NetworkError(
                f"'{interface}' is not managed by NetworkManager, so Network Ed can't change it."
            )
        uuid = cls._connection_for(interface, create_if_missing)
        cls._nmcli("connection", "modify", "uuid", uuid, *settings)
        if state <= STATE_UNAVAILABLE:
            return f"Saved. NetworkManager applies it when '{interface}' is connected."
        cls._nmcli("--wait", "45", "connection", "up", "uuid", uuid)
        return None

    @classmethod
    def _device_state(cls, interface: str) -> int:
        out = cls._nmcli("-g", "GENERAL.STATE", "device", "show", interface).strip()
        match = re.match(r"\d+", out)  # e.g. "100 (connected)"
        return int(match.group()) if match else 0

    @classmethod
    def _connection_for(cls, interface: str, create_if_missing: bool) -> str:
        """UUID of the connection to change: the active one, else Network Ed's own."""
        active = cls._nmcli("-g", "GENERAL.CON-UUID", "device", "show", interface).strip()
        if active and active != "--":
            return active

        name = own_connection_name(interface)
        own = cls._find_connection(name)
        if own:
            return own
        if not create_if_missing:
            raise NetworkError(f"'{interface}' has no active connection to switch to DHCP.")

        kind = cls._nmcli("-g", "GENERAL.TYPE", "device", "show", interface).strip()
        if kind != "ethernet":
            raise NetworkError(
                f"'{interface}' has no active connection. Connect it first "
                "(for Wi-Fi: join the network), then apply the profile."
            )
        cls._nmcli("connection", "add", "type", "ethernet", "ifname", interface, "con-name", name)
        created = cls._find_connection(name)
        if not created:
            raise NetworkError(f"NetworkManager did not create the connection '{name}'.")
        return created

    @classmethod
    def _find_connection(cls, name: str) -> str | None:
        try:
            out = cls._nmcli("-g", "connection.uuid", "connection", "show", "id", name)
        except CommandError:
            return None  # "no such connection profile"
        lines = out.strip().splitlines()
        return lines[0] if lines else None

    @classmethod
    def _refuse_ip_conflict(cls, interface: str, ip: str) -> None:
        for entry in cls._run_json(["ip", "-j", "-4", "addr", "show"]) or []:
            if not isinstance(entry, dict) or entry.get("ifname") == interface:
                continue
            if any(info.get("local") == ip for info in entry.get("addr_info") or []):
                raise NetworkError(
                    f"IP address {ip} is already used by adapter '{entry.get('ifname')}'."
                )

    # --- Plumbing ---

    @classmethod
    def _nmcli(cls, *args: str) -> str:
        return cls._run(["nmcli", *args])

    @classmethod
    def _run_json(cls, args: list[str]) -> Any:
        out = cls._run(args).strip()
        if not out:
            return None
        try:
            return json.loads(out)
        except ValueError:
            raise CommandError(
                f"Unexpected output from {' '.join(args[:3])}:\n{out[:500]}"
            ) from None

    @staticmethod
    def _run(args: list[str]) -> str:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "LC_ALL": "C"},  # stable, English output
                check=True,
                timeout=60,
            )
        except FileNotFoundError:
            hint = " Changing settings on Linux needs NetworkManager." if args[0] == "nmcli" else ""
            raise CommandError(f"'{args[0]}' was not found.{hint}") from None
        except subprocess.CalledProcessError as e:
            message = (e.stderr or e.stdout or "").strip()
            raise CommandError(message or f"{args[0]} exited with code {e.returncode}.") from e
        except subprocess.TimeoutExpired as e:
            raise CommandError(f"'{' '.join(args[:4])}' did not finish within a minute.") from e
        return result.stdout
