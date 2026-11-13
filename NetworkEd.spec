# -*- mode: python ; coding: utf-8 -*-
# Build with `just build` on Windows (dist/NetworkEd.exe) or Linux (dist/NetworkEd).
# Windows only: UPX when found (`--upx-dir` or PATH), the .ico icon, the version
# resource and the UAC admin manifest. network_profiles.json is NOT bundled: the app
# reads and writes it next to the executable.

import os
import re
import tomllib
from pathlib import Path

from PyInstaller.compat import is_win

# The version comes from pyproject.toml; CI adds its run number as the 4th part.
VERSION = tomllib.loads(Path(SPECPATH, 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
major, minor, patch = (int(n or 0) for n in re.match(r'(\d+)\.(\d+)(?:\.(\d+))?', VERSION).groups())
BUILD = int(os.environ.get('GITHUB_RUN_NUMBER', '0')) % 65536
FILE_VERSION = (major, minor, patch, BUILD)


def windows_version_info():
    """Version resource shown in Properties > Details. Windows only: the module needs
    `pefile`, which PyInstaller installs only on Windows."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=FILE_VERSION,
            prodvers=FILE_VERSION,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,  # Windows NT, 32-bit+ API
            fileType=0x1,  # application
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable('040904B0', [
                    StringStruct('CompanyName', 'Neted'),
                    StringStruct('FileDescription', 'Network Ed'),  # Task Manager shows this as the process name
                    StringStruct('Comments', 'Switch network adapters between saved IP/DNS profiles and DHCP.'),
                    StringStruct('FileVersion', '.'.join(map(str, FILE_VERSION))),
                    StringStruct('InternalName', 'NetworkEd'),
                    StringStruct('LegalCopyright', 'Neted contributors. Licensed under AGPL-3.0.'),
                    StringStruct('OriginalFilename', 'NetworkEd.exe'),
                    StringStruct('ProductName', 'Network Ed'),
                    StringStruct('ProductVersion', VERSION),
                ])
            ]),
            VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
        ],
    )


a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/icon.svg', 'assets'),
        ('pyproject.toml', '.'),  # src/version.py reads the version from it
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Never imported at runtime (no HTTPS); each pulls in its own copy of OpenSSL.
    # Without _hashlib, hashlib uses Python's built-in hashes.
    excludes=['ssl', '_ssl', '_hashlib'],
    noarchive=False,
    optimize=1,
)

# --- Trim what the app never uses (the exe went from 38.5 MB to about half) ---
# UPX can't shrink Qt: its DLLs use Control Flow Guard, which UPX breaks, so PyInstaller
# skips them. Leaving unused files out is what makes the exe smaller.
# `NetworkEd.exe --self-test=report.json` (run in CI) checks the trimmed exe still starts.
DROP_FILES = {
    'opengl32sw.dll',  # software OpenGL fallback; the UI doesn't use OpenGL (5.5 MB)
    'Qt6Pdf.dll',  # only needed by the PDF image plugin (2.5 MB)
    'libQt6Pdf.so.6',  # the same on Linux
    'libcrypto-3-x64.dll',  # OpenSSL for Qt's TLS plugins; no HTTPS is used
    'libssl-3-x64.dll',
}
DROP_DIRS = (
    'PyQt6/Qt6/translations/',  # the UI is English only
    'PyQt6/Qt6/plugins/tls/',
    'PyQt6/Qt6/plugins/networkinformation/',
    'PyQt6/Qt6/plugins/generic/',
)
# For these plugin folders only files starting with one of these names are kept
# (offscreen: the headless self-test; xcb/wayland: X11 and Wayland desktops).
KEEP_PLUGINS = {
    'platforms': ('qwindows.dll', 'qoffscreen.dll', 'libqxcb', 'libqwayland', 'libqoffscreen'),
    'imageformats': ('qsvg.dll', 'qico.dll', 'libqsvg', 'libqico'),
}
# src/icons.py loads only Font Awesome 5 Solid; qtawesome ships ~10 fonts (2.5 MB).
KEEP_FONT_PREFIX = 'fontawesome5-solid-webfont'


def _keep(entry):
    name = entry[0].replace('\\', '/')
    base = name.rsplit('/', 1)[-1]
    if base in DROP_FILES or name.startswith(DROP_DIRS):
        return False
    plugin = re.match(r'PyQt6/Qt6/plugins/([^/]+)/(.+)$', name)
    if plugin and plugin.group(1) in KEEP_PLUGINS:
        return plugin.group(2).startswith(KEEP_PLUGINS[plugin.group(1)])
    if name.startswith('qtawesome/fonts/'):
        return base.startswith(KEEP_FONT_PREFIX)
    return True


a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION')],
    name='NetworkEd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=is_win,  # on Linux UPX'd Qt libraries tend to break; zlib already compresses
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if is_win else None,
    version=windows_version_info() if is_win else None,
    # NETED_NO_UAC=1 builds a test exe that can run from a non-elevated shell
    # (local self-test only; releases always require admin).
    uac_admin=is_win and os.environ.get('NETED_NO_UAC') != '1',
)
