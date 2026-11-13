"""Types and settings shared by the Windows and Linux network backends."""

import os
from dataclasses import dataclass

# NETWORK_ED_DEBUG=true: dry run. Read-only queries run; changes are printed, not made.
DEBUG_MODE = os.getenv("NETWORK_ED_DEBUG", "").lower() == "true"


class NetworkError(RuntimeError):
    """Reading or changing network settings failed. The message is meant for the user."""


@dataclass(frozen=True)
class Adapter:
    name: str
    status: str  # "Up", "Disconnected", "Disabled", ...

    @property
    def is_up(self) -> bool:
        return self.status == "Up"


@dataclass(frozen=True)
class InterfaceStatus:
    dhcp_enabled: bool | None  # None when the system didn't say
    addresses: tuple[str, ...]


def sort_adapters(adapters: list[Adapter]) -> list[Adapter]:
    """Connected adapters first, then by name."""
    return sorted(adapters, key=lambda a: (not a.is_up, a.name.lower()))
