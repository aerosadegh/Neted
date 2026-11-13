# CLAUDE.md

Network Ed (repo and package name "Neted") is a PyQt6 tray app for **Windows and Linux** that switches a network adapter between saved static IP/DNS profiles and DHCP. On Windows it runs PowerShell scripts with admin rights; on Linux it reads with `ip -j` and changes settings through NetworkManager's `nmcli`.

## Commands

All tasks go through `just` (see `justfile`), which wraps `uv`. `build` and `selftest` have Windows and Unix variants:

```sh
just sync           # uv sync: .venv with runtime + dev deps (Python 3.12)
just dev            # run with NETWORK_ED_DEBUG=true (dry run, no admin needed)
just check          # ruff check + ruff format --check + pytest
just test -k mask   # pytest, extra args passed through
just fmt            # ruff --fix and format
just build          # PyInstaller -> dist/NetworkEd.exe (Windows) or dist/NetworkEd (Linux)
just package        # build + scripts/package.py -> dist/NetworkEd-<version>-<platform>.zip/.tar.gz
just selftest       # run the built program with --self-test headless (CI does this before release)
just icon           # assets/icon.svg -> assets/icon.ico
```

Run `just check` before committing. CI (`.github/workflows/build.yml`) runs lint and tests on `windows-latest` and `ubuntu-22.04` for pull requests. On `v*` tags each platform also runs `just package` and `just selftest` (Windows: UPX pinned and checksummed). One `release` job then attaches both archives to the GitHub release. The tag must equal `v` + the `pyproject.toml` version.

## Layout

- `src/main.py`: entry point. Single-instance guard (QLocalServer: a second launch shows the running window), admin check (only where `REQUIRES_ADMIN`), app icon.
- `src/ui.py`: `MainWindow`. Profile tree with search, editor form, adapter picker, status line, tray. Pure helpers (`describe_status`, `find_profile`, `profile_matches`, …) sit at the top.
- `src/network.py`: picks the backend for this OS as `NetworkManager` and re-exports the shared types. The UI only uses this.
  - `src/network_windows.py`: `WindowsNetwork`. Fixed PowerShell scripts plus the parsing of their JSON output.
  - `src/network_linux.py`: `LinuxNetwork`. Reads with `ip -j`; changes with `nmcli connection modify` + `up` on the adapter's active connection, or a "Network Ed (<adapter>)" connection for unplugged Ethernet.
  - `src/netcommon.py`: `Adapter`, `InterfaceStatus`, `NetworkError`, `DEBUG_MODE`.
- `src/models.py`: `NetworkProfile` (with `validate()`), `ProfileGroup`, `ProfileStore` (JSON load and save), and the IP/mask helpers.
- `src/worker.py`: `TaskRunner`. Runs blocking calls on Python worker threads and calls back on the GUI thread through one signal. Don't switch it to `QThreadPool` or per-job QObjects: those caused an exit deadlock and an access violation.
- `src/netwatch.py`: `NetworkChangeWatcher`. Windows: IP Helper change notifications (ctypes). Linux: the output of `ip monitor link address` (QProcess). Both are debounced (1 s) into one `changed` signal on the GUI thread, which makes the UI re-scan adapters.
- `src/icons.py`: Font Awesome 5 Solid icons through qtawesome's `IconicFont`, loading only that font.
- `src/selftest.py`: `--self-test=report.json`. Starts headless, waits for the adapter scan, writes a JSON report and exits 0 or 1. Read-only.
- `src/resources.py`: bundled-file paths (`sys._MEIPASS` when frozen) and `app_icon()`, rendered from the SVG.
- `src/version.py`: `__version__`, read from `pyproject.toml`, the only place the version is set (the spec bundles that file). Bump with `uv version --bump …`.
- `NetworkEd.spec`: PyInstaller build for both systems. Windows only: version resource, `.ico`, UAC manifest, UPX.
- `scripts/package.py`: release packing (folder + zip on Windows / tar.gz on Linux; writes `$GITHUB_OUTPUT` in CI). `scripts/make_icon.py`: SVG → ICO.
- `tests/`: pytest. `conftest.py` sets `QT_QPA_PLATFORM=offscreen` and provides the `qapp` and `store_path` fixtures. Both backends' tests fake their commands, so they run on either OS.

## Rules for changing code

- **Never put values into command text.** Windows: scripts in `network_windows.py` are constants; pass values through `NETED_*` environment variables. Linux: run commands as argument lists, never through a shell, and check adapter names with `check_interface_name`. Validate addresses first with `models.parse_ipv4` / `mask_to_prefix`. Always call through each backend's `_run`.
- **Keep the backends interchangeable.** Same class methods and return types (`apply_profile`/`enable_dhcp` return an optional note for the status bar). The UI must not check `sys.platform` for network logic.
- **Never run commands on the GUI thread.** Use `self._tasks.run(fn, on_success, on_error)`. Changes to the system set `_busy`, which disables the action buttons.
- **Network watcher callbacks** (Windows) run on Windows threads: only emit a signal there, never block or touch widgets. Each ctypes callback stays in `netwatch._registered_callbacks` until `CancelMibChangeNotify2`, because freeing it earlier crashes the process. Call `stop()` before the window goes away.
- **Icons.** Use `icons.icon("fa5s.…")`, never `qtawesome.icon()`. The build bundles only the Font Awesome 5 Solid font, and `qta.icon()` opens all of qtawesome's fonts, so it fails there.
- **Build size.** `NetworkEd.spec` drops unused Qt files (`DROP_FILES`, `DROP_DIRS`, `KEEP_PLUGINS`, with both Windows and Linux names) and excludes `ssl`/`_hashlib`. If new code needs one of them (an image format, HTTPS, a Qt module), update the spec, then check the build with `just selftest`. To self-test on Windows without elevation, build with `NETED_NO_UAC=1`. UPX can't compress Qt's CFG-protected DLLs; don't force it.
- **List membership by identity.** Duplicated profiles and groups compare equal, so find, remove and insert by identity (`ui.index_of`), never with `list.remove` or `list.index`.
- **Don't apply profiles or DHCP for real without asking the user.** These change the machine's live adapter. Test backend changes with `just dev` (prints what would run) or with unit tests that fake `_run`. The read-only queries (list adapters, read status) are safe to run.
- **Profiles file.** `network_profiles.json` lives next to the executable (`models.app_dir()`); from source it is the repo root. It must stay next to the executable: don't move it to AppData or `~/.config`. It contains real site addresses, so don't paste its contents anywhere.
- **Saving.** `ProfileStore.save` is atomic and raises `ProfileStoreError`; the UI shows that error. `load` backs up an unreadable file and raises, instead of returning an empty list that the next save would write over the user's data.
- **DNS-only.** A profile with an empty `ip_address` is DNS-only: apply sets DNS servers only. In the JSON, empty gateway/DNS fields are `null` and empty ip/mask fields are `""`.
- **No tray, no hiding.** Where there is no system tray (some Linux desktops), closing the window quits instead of hiding.
- **Imports are flat** (`from models import ...`). `src/` is not a package: run `src/main.py` as a script. pytest gets `src` through `pythonpath` in `pyproject.toml`.
- **Naming.** "Network Ed" is the user-facing name, `NetworkEd(.exe)` the program, `NETWORK_ED_DEBUG` the debug env var.
- **Commit messages** use the `feat:` / `fix:` / `build:` / `ci:` / `docs:` / `test:` prefix style.
