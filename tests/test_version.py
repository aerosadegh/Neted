import tomllib
from pathlib import Path

from version import __version__


def test_app_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    assert __version__ == project["version"], "update src/version.py after bumping pyproject.toml"
