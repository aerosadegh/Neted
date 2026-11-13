# -*- mode: python ; coding: utf-8 -*-
# Build with `just build`. UPX is used when found (`--upx-dir` or PATH); without it the
# build still works, the exe is just bigger. network_profiles.json is NOT bundled: the app
# reads and writes it next to the exe.

import os
import re
import tomllib
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

# --- Windows version resource (Properties > Details in Explorer) ---
# The version comes from pyproject.toml; CI adds its run number as the 4th part.
VERSION = tomllib.loads(Path(SPECPATH, 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
major, minor, patch = (int(n or 0) for n in re.match(r'(\d+)\.(\d+)(?:\.(\d+))?', VERSION).groups())
BUILD = int(os.environ.get('GITHUB_RUN_NUMBER', '0')) % 65536
FILE_VERSION = (major, minor, patch, BUILD)

version_info = VSVersionInfo(
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
                StringStruct('Comments', 'Switch Windows network adapters between saved IP/DNS profiles and DHCP.'),
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
    ['src\\main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets\\icon.svg', 'assets'),
        ('pyproject.toml', '.'),  # src/version.py reads the version from it
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\icon.ico',
    version=version_info,
    uac_admin=True,
)
