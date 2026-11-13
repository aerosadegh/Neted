"""Packs the built executable for release.

Windows: dist/NetworkEd-<version>-windows-x64.zip
Linux:   dist/NetworkEd-<version>-linux-x86_64.tar.gz  (tar keeps the executable bit)

Each archive holds one folder of the same name containing only the executable; the
folder is also left in dist/package/ (the CI artifact). Run with `just package`, which
builds first. In GitHub Actions it also writes the names and paths to $GITHUB_OUTPUT.
"""

import argparse
import os
import shutil
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# platform -> (executable, archive format, name suffix)
PLATFORMS = {
    "windows": ("NetworkEd.exe", "zip", "windows-x64"),
    "linux": ("NetworkEd", "tar.gz", "linux-x86_64"),
}


def current_platform() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def project_version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


def package(dist: Path, version: str, platform: str | None = None) -> tuple[Path, Path]:
    """Returns (release folder, archive path)."""
    exe_name, archive_format, suffix = PLATFORMS[platform or current_platform()]
    exe = dist / exe_name
    if not exe.is_file():
        sys.exit(f"{exe} not found. Run `just build` first.")

    name = f"NetworkEd-{version}-{suffix}"
    staging = dist / "package"
    if staging.exists():
        shutil.rmtree(staging)
    folder = staging / name
    folder.mkdir(parents=True)
    shutil.copy2(exe, folder / exe_name)

    archive = dist / f"{name}.{archive_format}"
    if archive_format == "zip":
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(folder / exe_name, f"{name}/{exe_name}")
    else:
        with tarfile.open(archive, "w:gz") as tf:
            info = tf.gettarinfo(folder / exe_name, f"{name}/{exe_name}")
            info.mode = 0o755  # runnable after extracting, even if built on Windows
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(folder / exe_name, "rb") as f:
                tf.addfile(info, f)
    return folder, archive


def _display(path: Path) -> str:
    """Forward-slash path, relative to the repo when possible (safe for Actions globs)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="folder with the build")
    parser.add_argument("--platform", choices=sorted(PLATFORMS), default=current_platform())
    args = parser.parse_args()

    folder, archive = package(args.dist, project_version(), args.platform)
    print(f"Packed {_display(archive)} ({folder.name}/{PLATFORMS[args.platform][0]})")

    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as out:
            out.write(f"name={folder.name}\n")
            out.write(f"folder={_display(folder.parent)}\n")
            out.write(f"archive={_display(archive)}\n")


if __name__ == "__main__":
    main()
