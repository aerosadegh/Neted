import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional


@dataclass
class NetworkProfile:
    """Represents a single network configuration profile."""

    name: str
    ip_address: Optional[str] = ""
    subnet_mask: Optional[str] = ""
    gateway: Optional[str] = ""
    dns_primary: Optional[str] = ""
    dns_secondary: Optional[str] = ""


@dataclass
class ProfileGroup:
    """Represents a logical grouping of network profiles."""

    name: str
    profiles: List[NetworkProfile]


class ProfileStore:
    """Handles persistence of Network Profiles to a JSON file."""

    FILE_PATH = Path("network_profiles.json")

    @classmethod
    def load(cls) -> List[ProfileGroup]:
        """Loads profile groups from the JSON file."""
        if not cls.FILE_PATH.exists():
            return []

        try:
            with open(cls.FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            groups = []
            for group_data in data:
                profiles = [NetworkProfile(**p) for p in group_data.get("profiles", [])]
                groups.append(
                    ProfileGroup(
                        name=group_data.get("name", "Unnamed"), profiles=profiles
                    )
                )
            return groups
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"Error loading JSON: {e}", file=sys.stderr)
            return []

    @classmethod
    def save(cls, groups: List[ProfileGroup]) -> None:
        """Saves profile groups to the JSON file."""
        data = [asdict(g) for g in groups]
        try:
            with open(cls.FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            print(f"Error saving to JSON: {e}", file=sys.stderr)
