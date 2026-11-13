"""The app version. The only place it is set is [project].version in pyproject.toml.

Bump it with `uv version --bump patch|minor|major`. From source this reads the repo's
pyproject.toml; the exe has a copy bundled by NetworkEd.spec.
"""

import sys
import tomllib
from pathlib import Path


def _pyproject_path() -> Path:
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent
    return Path(base) / "pyproject.toml"


def read_version(path: Path | None = None) -> str:
    try:
        data = tomllib.loads((path or _pyproject_path()).read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, ValueError):
        return "unknown"


__version__ = read_version()
