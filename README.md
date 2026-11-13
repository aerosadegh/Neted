# Network Ed

Switch a network adapter between saved IP/DNS profiles and DHCP in one click, from a small window that lives in the system tray. Runs on **Windows 10/11** and **Linux**.

- Keep profiles in groups (for example one group per site), each with IP address, subnet mask, gateway and DNS servers.
- **DNS-only** profiles change only the DNS servers and leave the IP settings alone.
- **DHCP** resets the adapter back to automatic IP and DNS.
- The status line shows the adapter's current address and which saved profile it matches.
- Plugging in a cable or USB adapter, unplugging it, or getting a new address is picked up automatically, with no need to press Refresh.
- **Refresh** also reloads `network_profiles.json`, so edits made to the file outside the app show up.
- Right-click a profile or group to **duplicate** it (or to add or delete).
- **Search** above the list (Ctrl+F) filters groups and profiles by name, IP, gateway or DNS. Several words must all match, e.g. `kahak 10.6`.

On Windows the app needs administrator rights to change adapter settings, and the exe asks for them when it starts.

## Download

Each version tag (`v0.3.0`, ...) builds the app in GitHub Actions and attaches two archives to the GitHub release:

- **Windows:** `NetworkEd-<version>-windows-x64.zip`, a folder with `NetworkEd.exe`
- **Linux:** `NetworkEd-<version>-linux-x86_64.tar.gz`, a folder with the `NetworkEd` program (64-bit, built on Ubuntu 22.04, so it runs on that and newer distros)

The same folders are also on the run's page under **Actions → Build**. Pull requests only run lint and tests (on both systems).

## Linux

Extract the archive and run `./NetworkEd`.

- **Changing settings needs NetworkManager** (`nmcli`). It's the default on Ubuntu, Fedora, Mint, Debian desktop and most others. Without it the app still shows adapters and their addresses, but Apply and DHCP can't work.
- **No root needed.** NetworkManager decides who may change network settings. Desktop users can usually change them (sometimes after typing their password). If you see "Insufficient privileges", ask your administrator to allow it rather than running the app with `sudo`.
- **How profiles apply:** NetworkManager stores settings per saved connection, so a profile changes the connection active on the adapter (for Wi-Fi, that network's saved settings). An Ethernet port with no cable gets a connection named "Network Ed (<adapter>)", which NetworkManager switches on as soon as a cable is plugged in.
- **Tray icon:** GNOME needs the AppIndicator extension (Ubuntu has it). Without a tray, closing the window quits the app.
- **If it won't start on X11** with "could not load the Qt platform plugin xcb", install `libxcb-cursor0` (Ubuntu/Debian) or `xcb-util-cursor` (Fedora).

## Where profiles are stored

Profiles are saved in `network_profiles.json` **in the same folder as the program** (`NetworkEd.exe` or `NetworkEd`). To move the app or copy it to another PC, copy both files. If the file is ever unreadable, the app keeps a copy named `network_profiles.corrupt-<date>.json` and tells you, instead of starting with an empty library.

## Development

Needs [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

```sh
just sync     # create .venv with all dependencies
just dev      # run without admin; changes are printed, not applied
just run      # run for real (Windows: from an elevated terminal)
just check    # ruff lint + format check, then pytest
just fmt      # auto-fix lint issues and format
just build    # build dist/NetworkEd.exe or dist/NetworkEd for this system
just package  # build, then pack the release archive for this system
just selftest # start the built program headless and check it works
just icon     # regenerate assets/icon.ico after editing assets/icon.svg
```

Each system builds its own version: run the commands on Windows for the `.exe`, on Linux for the Linux program. On Windows, `just build` uses UPX from `C:/upx-5.1.1` (or `$UPX_DIR`) if present, and `just selftest` needs an elevated terminal.

The Windows exe is about 22 MB, almost all of it Qt and Python. UPX can't shrink the Qt DLLs: they use Control Flow Guard, which UPX breaks, so PyInstaller skips them. `NetworkEd.spec` keeps the exe small by leaving out what the app never uses (software OpenGL, PDF support, OpenSSL, translations, extra icon fonts and plugins).

To release: `uv version --bump patch` (or `minor` / `major`; `pyproject.toml` is the only place the version lives), commit, then push a tag `v<version>`.

## License

[AGPL-3.0](LICENSE)
