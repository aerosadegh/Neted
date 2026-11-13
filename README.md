# Network Ed

Switch a Windows network adapter between saved IP/DNS profiles and DHCP in one click, from a small window that lives in the system tray.

- Keep profiles in groups (for example one group per site), each with IP address, subnet mask, gateway and DNS servers.
- **DNS-only** profiles change only the DNS servers and leave the IP settings alone.
- **DHCP** resets the adapter back to automatic IP and DNS.
- The status line shows the adapter's current address and which saved profile it matches.
- Plugging in a cable or USB adapter, unplugging it, or getting a new address is picked up automatically, with no need to press Refresh.
- **Refresh** also reloads `network_profiles.json`, so edits made to the file outside the app show up.
- Right-click a profile or group to **duplicate** it (or to add or delete).
- **Search** above the list (Ctrl+F) filters groups and profiles by name, IP, gateway or DNS. Several words must all match, e.g. `kahak 10.6`.

Windows 10/11 only. The app needs administrator rights to change adapter settings, and the exe asks for them when it starts.

## Download

Each version tag (`v0.1.0`, ...) builds the app in GitHub Actions and attaches **`NetworkEd-<version>.zip`** to the GitHub release. The zip holds a `NetworkEd-<version>` folder containing `NetworkEd.exe`. The same folder is on the run's page under **Actions → Build** as an artifact. Pull requests only run lint and tests.

## Where profiles are stored

Profiles are saved in `network_profiles.json` **in the same folder as `NetworkEd.exe`**. To move the app or copy it to another PC, copy both files. If the file is ever unreadable, the app keeps a copy named `network_profiles.corrupt-<date>.json` and tells you, instead of starting with an empty library.

## Development

Needs [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

```sh
just sync     # create .venv with all dependencies
just dev      # run without admin; changes are printed, not applied
just run      # run for real (from an elevated terminal)
just check    # ruff lint + format check, then pytest
just fmt      # auto-fix lint issues and format
just build    # build dist/NetworkEd.exe (UPX from C:/upx-5.1.1 or $UPX_DIR, if present)
just package  # build, then pack dist/NetworkEd-<version>.zip like the release
just selftest # start the built exe headless and check it works (elevated terminal)
just icon     # regenerate assets/icon.ico after editing assets/icon.svg
```

The exe is about 22 MB, almost all of it Qt and Python. UPX can't shrink the Qt DLLs: they use Control Flow Guard, which UPX breaks, so PyInstaller skips them. `NetworkEd.spec` keeps the exe small by leaving out what the app never uses (software OpenGL, PDF support, OpenSSL, translations, extra icon fonts and plugins).

To release: `uv version --bump patch` (or `minor` / `major`; `pyproject.toml` is the only place the version lives), commit, then push a tag `v<version>`.

## License

[AGPL-3.0](LICENSE)
