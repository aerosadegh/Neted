import importlib.util
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "package.py"


@pytest.fixture(scope="module")
def packager():
    spec = importlib.util.spec_from_file_location("package_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_zip_holds_a_versioned_folder_with_only_the_exe(packager, tmp_path):
    (tmp_path / "NetworkEd.exe").write_bytes(b"MZ fake exe")
    (tmp_path / "network_profiles.json").write_text("[]")  # must not be packed

    folder, archive = packager.package(tmp_path, "1.2.3", "windows")

    assert folder == tmp_path / "package" / "NetworkEd-1.2.3-windows-x64"
    assert archive == tmp_path / "NetworkEd-1.2.3-windows-x64.zip"
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["NetworkEd-1.2.3-windows-x64/NetworkEd.exe"]
        assert zf.read("NetworkEd-1.2.3-windows-x64/NetworkEd.exe") == b"MZ fake exe"
    assert [p.name for p in folder.iterdir()] == ["NetworkEd.exe"]


def test_linux_tarball_keeps_the_binary_executable(packager, tmp_path):
    (tmp_path / "NetworkEd").write_bytes(b"\x7fELF fake")

    folder, archive = packager.package(tmp_path, "1.2.3", "linux")

    assert archive == tmp_path / "NetworkEd-1.2.3-linux-x86_64.tar.gz"
    with tarfile.open(archive) as tf:
        (member,) = tf.getmembers()
        assert member.name == "NetworkEd-1.2.3-linux-x86_64/NetworkEd"
        assert member.mode & 0o111 == 0o111, "must be executable after extracting"
        assert tf.extractfile(member).read() == b"\x7fELF fake"


def test_repacking_replaces_old_staging(packager, tmp_path):
    (tmp_path / "NetworkEd.exe").write_bytes(b"new")
    stale = tmp_path / "package" / "NetworkEd-0.0.1-windows-x64"
    stale.mkdir(parents=True)
    packager.package(tmp_path, "1.2.3", "windows")
    assert [p.name for p in (tmp_path / "package").iterdir()] == ["NetworkEd-1.2.3-windows-x64"]


def test_missing_executable_stops_with_a_hint(packager, tmp_path):
    with pytest.raises(SystemExit, match="just build"):
        packager.package(tmp_path, "1.2.3", "linux")


def test_version_comes_from_pyproject(packager):
    from version import __version__

    assert packager.project_version() == __version__
