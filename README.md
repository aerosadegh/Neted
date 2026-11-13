# Network Ed

Switch a Windows network adapter between saved IP/DNS profiles and DHCP in one click, from a small window that lives in the system tray.

- Keep profiles in groups (for example one group per site), each with IP address, subnet mask, gateway and DNS servers.
- **DNS-only** profiles change only the DNS servers and leave the IP settings alone.
- **DHCP** resets the adapter back to automatic IP and DNS.
- The status line shows the adapter's current address and which saved profile it matches.

Windows 10/11 only. The app needs administrator rights to change adapter settings, and the exe asks for them when it starts.

## Download

Each version tag (`v0.1.0`, ...) builds `NetworkEd.exe` in GitHub Actions and attaches it to a GitHub release. It is also on the run's page under **Actions → Build** as the `NetworkEd-<version>` artifact. Pull requests only run lint and tests.

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
just icon     # regenerate assets/icon.ico after editing assets/icon.svg
```

To release: bump the version with `uv version --bump patch` (also update `src/version.py`; a test checks they match), commit, then push a tag `v<version>`.

## License

[AGPL-3.0](LICENSE)
