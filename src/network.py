"""The network backend for this operating system, plus the shared types.

The rest of the app only uses `NetworkManager` (the class for the current OS) and the
types re-exported here:

- Windows: network_windows.WindowsNetwork (PowerShell; needs admin).
- Linux:   network_linux.LinuxNetwork (`ip` to read, NetworkManager's `nmcli` to change).

Both offer the same class methods: get_network_interfaces, get_interface_status,
apply_profile, enable_dhcp, is_admin, plus DEBUG_MODE and REQUIRES_ADMIN.
"""

import sys

from netcommon import Adapter, InterfaceStatus, NetworkError

if sys.platform == "win32":
    from network_windows import WindowsNetwork as NetworkManager
else:
    from network_linux import LinuxNetwork as NetworkManager

__all__ = ["Adapter", "InterfaceStatus", "NetworkError", "NetworkManager"]
