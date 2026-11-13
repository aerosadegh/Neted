# CLAUDE.md

Network Ed (repo and package name "Neted") is a Windows-only PyQt6 tray app that switches a network adapter between saved static IP/DNS profiles and DHCP. It does this by running PowerShell scripts with admin rights.

## Commands

All tasks go through `just` (see `justfile`), which wraps `uv`:

```sh
just sync           # uv sync: .venv with runtime + dev deps (Python 3.12)
just dev            # run with NETWORK_ED_DEBUG=true (dry run, no admin needed)
just check          # ruff check + ruff format --check + pytest
just test -k mask   # pytest, extra args passed through
just fmt            # ruff --fix and format
just build          # PyInstaller -> dist/NetworkEd.exe
just icon           # assets/icon.svg -> assets/icon.ico
```

Run `just check` before committing. CI (`.github/workflows/build.yml`, `windows-latest`) runs lint and tests on pull requests. On `v*` tags it also builds the exe (UPX, pinned and checksummed) and attaches it to a release. The tag must equal `v` + the `pyproject.toml` version.

## Layout

- `src/main.py`: entry point. Single-instance guard (QLocalServer: a second launch shows the running window), admin check, app icon.
- `src/ui.py`: `MainWindow`. Profile tree, editor form, adapter picker, status line, tray. Pure helpers `describe_status` and `find_profile` sit at the top.
- `src/network.py`: `NetworkManager`. Fixed PowerShell scripts plus the parsing of their JSON output.
- `src/models.py`: `NetworkProfile` (with `validate()`), `ProfileGroup`, `ProfileStore` (JSON load and save), and the IP/mask helpers.
- `src/worker.py`: `TaskRunner`. Runs blocking calls on a thread pool and calls back on the GUI thread.
- `src/resources.py`: bundled-file paths (`sys._MEIPASS` when frozen) and `app_icon()`, rendered from the SVG.
- `src/version.py`: `__version__`. It must equal `[project].version` in `pyproject.toml` (a test checks).
- `NetworkEd.spec`: PyInstaller build. It reads the version from `pyproject.toml` and writes the exe's version resource.
- `tests/`: pytest. `conftest.py` sets `QT_QPA_PLATFORM=offscreen` and provides the `qapp` and `store_path` fixtures.

## Rules for changing code

- **Never put values into PowerShell script text.** Scripts in `network.py` are constants. Pass values through `NETED_*` environment variables and validate them first with `models.parse_ipv4` / `mask_to_prefix`. Always call through `NetworkManager._run`, which adds `-NoProfile`, UTF-8 output and `CREATE_NO_WINDOW`.
- **Never call PowerShell on the GUI thread.** Use `self._tasks.run(fn, on_success, on_error)`. Changes to the system set `_busy`, which disables the action buttons.
- **Don't apply profiles or DHCP for real without asking the user.** These change the machine's live adapter. Test `network.py` changes with `just dev` (it prints the script and its env values) or with unit tests that fake `_run`. The read-only queries (list adapters, read status) are safe to run.
- **Profiles file.** `network_profiles.json` lives next to the exe (`models.app_dir()`); from source it is the repo root. It must stay next to the exe: don't move it to AppData. It contains real site addresses, so don't paste its contents anywhere.
- **Saving.** `ProfileStore.save` is atomic and raises `ProfileStoreError`; the UI shows that error. `load` backs up an unreadable file and raises, instead of returning an empty list that the next save would write over the user's data.
- **DNS-only.** A profile with an empty `ip_address` is DNS-only: apply sets DNS servers only. In the JSON, empty gateway/DNS fields are `null` and empty ip/mask fields are `""`.
- **Imports are flat** (`from models import ...`). `src/` is not a package: run `src/main.py` as a script. pytest gets `src` through `pythonpath` in `pyproject.toml`.
- **Naming.** "Network Ed" is the user-facing name, `NetworkEd.exe` the exe, `NETWORK_ED_DEBUG` the debug env var.
- **Commit messages** use the `feat:` / `fix:` / `build:` / `ci:` / `docs:` / `test:` prefix style.
