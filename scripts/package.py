"""Packs the built exe for release.

Creates dist/package/NetworkEd-<version>/NetworkEd.exe and dist/NetworkEd-<version>.zip
(the zip holds that NetworkEd-<version> folder). Run with `just package`, which builds
first. In GitHub Actions it also writes the names and paths to $GITHUB_OUTPUT.
"""

import argparse
import os
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE_NAME = "NetworkEd.exe"


def project_version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


def package(dist: Path, version: str) -> tuple[Path, Path]:
    """Returns (release folder, zip path)."""
    exe = dist / EXE_NAME
    if not exe.is_file():
        sys.exit(f"{exe} not found. Run `just build` first.")

    name = f"NetworkEd-{version}"
    staging = dist / "package"
    if staging.exists():
        shutil.rmtree(staging)
    folder = staging / name
    folder.mkdir(parents=True)
    shutil.copy2(exe, folder / EXE_NAME)

    archive = dist / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(folder / EXE_NAME, f"{name}/{EXE_NAME}")
    return folder, archive


def _display(path: Path) -> str:
    """Forward-slash path, relative to the repo when possible (safe for Actions globs)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="folder with the exe")
    args = parser.parse_args()

    folder, archive = package(args.dist, project_version())
    print(f"Packed {_display(archive)} ({folder.name}/{EXE_NAME})")

    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as out:
            out.write(f"name={folder.name}\n")
            out.write(f"folder={_display(folder.parent)}\n")
            out.write(f"zip={_display(archive)}\n")


if __name__ == "__main__":
    main()
