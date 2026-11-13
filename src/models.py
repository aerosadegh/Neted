"""Profile data model, validation and JSON persistence."""

import ipaddress
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

PROFILES_FILENAME = "network_profiles.json"


def app_dir() -> Path:
    """Folder that holds the profile library.

    For the built exe this is the folder the exe is in, wherever it was started from.
    When running from source it is the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def clean(value: Any) -> str:
    """Returns the value as a stripped string, treating None as empty."""
    return "" if value is None else str(value).strip()


def parse_ipv4(value: Any, label: str) -> str:
    """Returns the canonical form of an IPv4 address, or raises ValueError."""
    try:
        return str(ipaddress.IPv4Address(clean(value)))
    except ipaddress.AddressValueError:
        raise ValueError(f"{label} is not valid: {clean(value)!r}") from None


def mask_to_prefix(mask: Any) -> int:
    """Converts a dotted subnet mask such as 255.255.255.0 to a prefix length (24).

    Rejects masks whose 1-bits are not contiguous (255.0.255.0) and 0.0.0.0.
    """
    try:
        value = int(ipaddress.IPv4Address(clean(mask)))
    except ipaddress.AddressValueError:
        raise ValueError(f"Subnet mask is not valid: {clean(mask)!r}") from None
    prefix = bin(value).count("1")
    if prefix == 0 or value != (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF:
        raise ValueError(f"Subnet mask is not valid: {clean(mask)!r}")
    return prefix


@dataclass
class NetworkProfile:
    """A saved set of adapter settings. A profile without an IP address is DNS-only."""

    name: str
    ip_address: str | None = ""
    subnet_mask: str | None = ""
    gateway: str | None = ""
    dns_primary: str | None = ""
    dns_secondary: str | None = ""

    @classmethod
    def from_dict(cls, data: Any) -> "NetworkProfile":
        """Builds a profile from JSON data, ignoring keys this version doesn't know."""
        if not isinstance(data, dict):
            raise ValueError(f"a profile must be a JSON object, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        kwargs = {k: (None if v is None else str(v)) for k, v in data.items() if k in known}
        kwargs["name"] = kwargs.get("name") or "Unnamed"
        return cls(**kwargs)

    @property
    def is_dns_only(self) -> bool:
        return not clean(self.ip_address)

    def validate(self) -> list[str]:
        """Returns the problems that would stop this profile from being applied."""
        errors: list[str] = []

        if not self.is_dns_only:
            try:
                ip = ipaddress.IPv4Address(clean(self.ip_address))
                if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                    errors.append(f"IP address {ip} can't be assigned to an adapter.")
            except ipaddress.AddressValueError:
                errors.append(f"IP address is not valid: {clean(self.ip_address)!r}")

            if not clean(self.subnet_mask):
                errors.append("A subnet mask is required when an IP address is set.")
            else:
                try:
                    mask_to_prefix(self.subnet_mask)
                except ValueError as e:
                    errors.append(str(e))

            if clean(self.gateway):
                try:
                    parse_ipv4(self.gateway, "Gateway")
                except ValueError as e:
                    errors.append(str(e))

        for value, label in (
            (self.dns_primary, "Primary DNS"),
            (self.dns_secondary, "Secondary DNS"),
        ):
            if clean(value):
                try:
                    parse_ipv4(value, label)
                except ValueError as e:
                    errors.append(str(e))

        if clean(self.dns_secondary) and not clean(self.dns_primary):
            errors.append("Secondary DNS is set but primary DNS is empty.")
        if self.is_dns_only and not clean(self.dns_primary):
            errors.append("The profile is empty: set an IP address, a DNS server, or both.")

        return errors


@dataclass
class ProfileGroup:
    """A named folder of profiles."""

    name: str
    profiles: list[NetworkProfile]


class ProfileStoreError(Exception):
    """The profile library couldn't be read or written. The message is meant for the user."""


class ProfileStore:
    """Loads and saves the profile library as JSON next to the executable."""

    FILE_PATH = app_dir() / PROFILES_FILENAME

    @classmethod
    def load(cls) -> list[ProfileGroup]:
        """Returns the saved groups, or an empty list if there is no file yet.

        If the file exists but can't be read, a backup copy is made and
        ProfileStoreError is raised, so the caller can warn before anything overwrites it.
        """
        path = cls.FILE_PATH
        if not path.exists():
            return []
        try:
            return cls._parse(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as e:
            backup = cls._backup(path)
            where = f"A copy was saved as:\n{backup}" if backup else "No backup copy could be made."
            raise ProfileStoreError(
                f"Could not read the profile library:\n{path}\n\n{e}\n\n{where}"
            ) from e

    @classmethod
    def save(cls, groups: list[ProfileGroup]) -> None:
        """Writes the groups atomically: a crash mid-write leaves the old file intact."""
        path = cls.FILE_PATH
        text = json.dumps([asdict(g) for g in groups], indent=4, ensure_ascii=False) + "\n"
        try:
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError as e:
            raise ProfileStoreError(f"Could not save the profile library:\n{path}\n\n{e}") from e

    @staticmethod
    def _parse(data: Any) -> list[ProfileGroup]:
        if not isinstance(data, list):
            raise ValueError("the file must contain a JSON list of groups")
        groups = []
        for group in data:
            if not isinstance(group, dict):
                raise ValueError(f"a group must be a JSON object, got {type(group).__name__}")
            profiles = group.get("profiles") or []
            if not isinstance(profiles, list):
                raise ValueError(f"'profiles' of group {group.get('name')!r} must be a list")
            groups.append(
                ProfileGroup(
                    name=clean(group.get("name")) or "Unnamed",
                    profiles=[NetworkProfile.from_dict(p) for p in profiles],
                )
            )
        return groups

    @staticmethod
    def _backup(path: Path) -> Path | None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        try:
            shutil.copy2(path, backup)
        except OSError:
            return None
        return backup
