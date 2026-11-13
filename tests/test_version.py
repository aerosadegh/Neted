import sys
import tomllib
from pathlib import Path

import version

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_app_version_is_read_from_pyproject():
    expected = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert version.__version__ == expected


def test_exe_reads_its_bundled_pyproject(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert version.read_version() == "9.8.7"


def test_missing_or_broken_pyproject_gives_unknown(tmp_path):
    assert version.read_version(tmp_path / "missing.toml") == "unknown"
    broken = tmp_path / "broken.toml"
    broken.write_text("[project", encoding="utf-8")
    assert version.read_version(broken) == "unknown"
