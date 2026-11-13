set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List available recipes
default:
    @just --list

# Create or update .venv with runtime and dev dependencies
sync:
    uv sync

# Run the app for real (Windows: from an elevated terminal)
run:
    uv run python src/main.py

# Run in dry-run mode: no admin needed, changes are printed instead of made
dev $NETWORK_ED_DEBUG="true":
    uv run python src/main.py

# Run the tests (extra args go to pytest, e.g. `just test -k mask`)
test *args:
    uv run pytest {{ args }}

# Check lint and formatting without changing files
lint:
    uv run ruff check .
    uv run ruff format --check .

# Fix lint issues that can be fixed automatically, then format
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Lint and test (what CI runs before building)
check: lint test

# Folder containing upx.exe; override with the UPX_DIR environment variable
upx_dir := env("UPX_DIR", "C:/upx-5.1.1")

# Build dist/NetworkEd.exe (unused Qt parts trimmed; UPX for the files it can safely pack)
[windows]
build:
    uv run pyinstaller --noconfirm --clean --upx-dir "{{ upx_dir }}" NetworkEd.spec

# Build dist/NetworkEd (unused Qt parts trimmed)
[unix]
build:
    uv run pyinstaller --noconfirm --clean NetworkEd.spec

# Start the built exe headless, check it loads, write dist/self-test.json (read-only).
# Needs an elevated terminal, or a test build made with `$env:NETED_NO_UAC=1; just build`.
# Start the built exe headless and check it loads (elevated terminal, or NETED_NO_UAC=1 build)
[windows]
selftest:
    $env:QT_QPA_PLATFORM = "offscreen"; \
    $p = Start-Process dist/NetworkEd.exe -ArgumentList "--self-test=$PWD/dist/self-test.json" -PassThru -Wait; \
    Get-Content dist/self-test.json; \
    exit $p.ExitCode

# Start the built binary headless, check it loads, write dist/self-test.json (read-only)
[unix]
selftest:
    QT_QPA_PLATFORM=offscreen ./dist/NetworkEd --self-test="$PWD/dist/self-test.json"; \
    status=$?; cat dist/self-test.json; exit $status

# Build, then pack the release archive: NetworkEd-<version>-<platform>.zip / .tar.gz
package: build
    uv run python scripts/package.py

# Regenerate assets/icon.ico from assets/icon.svg
icon:
    uv run python scripts/make_icon.py
