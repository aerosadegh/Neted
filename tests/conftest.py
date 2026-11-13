import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from models import ProfileStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """Points ProfileStore at a temporary file."""
    path = tmp_path / "network_profiles.json"
    monkeypatch.setattr(ProfileStore, "FILE_PATH", path)
    return path
