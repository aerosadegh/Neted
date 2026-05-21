import ctypes
import os
import subprocess
from typing import List

# Local imports
from models import NetworkProfile


class NetworkManager:
    """Handles all low-level system interactions for network configuration."""

    DEBUG_MODE = os.getenv("NETWORK_GADGET_DEBUG", "False").lower() == "true"

    @staticmethod
    def is_admin() -> bool:
        """Checks if the current process has Administrator privileges."""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    @staticmethod
    def get_network_interfaces() -> List[str]:
        """Retrieves active network interface aliases via PowerShell."""
        ps_command = "(Get-NetAdapter | Where-Object Status -eq 'Up').Name"
        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_command],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return [line.strip() for line in result.stdout.split("\n") if line.strip()]
        except subprocess.CalledProcessError:
            return ["Ethernet", "Wi-Fi"]  # Fallback for UI testing if PS fails

    @staticmethod
    def get_active_ip(interface_name: str) -> str:
        """Fetches the currently applied IPv4 address for a given interface."""
        ps_command = f"(Get-NetIPAddress -InterfaceAlias '{interface_name}' -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress"
        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_command],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ip = result.stdout.strip()
            return ip if ip else "Unconfigured / DHCP"
        except subprocess.CalledProcessError:
            return "Unknown"

    @staticmethod
    def _convert_mask_to_prefix(subnet_mask: str) -> int:
        """Converts dotted decimal mask to CIDR prefix length."""
        try:
            return sum([bin(int(x)).count("1") for x in subnet_mask.split(".")])
        except (ValueError, AttributeError):
            raise ValueError(f"Invalid subnet mask: {subnet_mask}")

    @classmethod
    def apply_profile(cls, interface_name: str, profile: NetworkProfile) -> None:
        """Applies a NetworkProfile to the system, gracefully handling existing IP objects."""
        if not cls.DEBUG_MODE and not cls.is_admin():
            raise PermissionError("Administrator privileges are required.")

        ps_script = f"""
        $ErrorActionPreference = "Stop"
        $AdapterName = "{interface_name}"
        """

        is_dns_only = not (profile.ip_address and profile.ip_address.strip())

        # --- BLOCK 1: IP & ROUTING ---
        if not is_dns_only:
            if not profile.subnet_mask or not profile.subnet_mask.strip():
                raise ValueError(
                    "A Subnet Mask is required if an IP Address is specified."
                )

            prefix_length = cls._convert_mask_to_prefix(profile.subnet_mask)
            target_ip = profile.ip_address.strip()

            ps_script += f"""
        # 1. Defensively check for cross-adapter IP conflicts
        $conflictIP = Get-NetIPAddress -IPAddress "{target_ip}" -AddressFamily IPv4 -ErrorAction SilentlyContinue
        if ($conflictIP -and $conflictIP.InterfaceAlias -ne $AdapterName) {{
            Write-Error "IP Address {target_ip} is already in use by another adapter: '$($conflictIP.InterfaceAlias)'. Cannot apply."
            exit 1
        }}

        # 2. Disable DHCP to unlock the adapter completely
        Set-NetIPInterface -InterfaceAlias $AdapterName -Dhcp Disabled -AddressFamily IPv4 -ErrorAction SilentlyContinue

        # 3. Clear existing Gateway Routes
        Remove-NetRoute -InterfaceAlias $AdapterName -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue

        # 4. Check if target IP already exists on THIS adapter
        $existingIP = Get-NetIPAddress -InterfaceAlias $AdapterName -IPAddress "{target_ip}" -AddressFamily IPv4 -ErrorAction SilentlyContinue
        
        # 5. Purge any OTHER IPs on this adapter to ensure a clean slate
        Get-NetIPAddress -InterfaceAlias $AdapterName -AddressFamily IPv4 | Where-Object IPAddress -ne "{target_ip}" | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        
        $ipParams = @{{
            InterfaceAlias = $AdapterName
            IPAddress = "{target_ip}"
            PrefixLength = {prefix_length}
        }}
            """

            if profile.gateway and profile.gateway.strip():
                gateway = profile.gateway.strip()
                ps_script += f"""
        if ($existingIP) {{
            # IP already exists: Modify the prefix and manually inject the Gateway route
            Set-NetIPAddress @ipParams -ErrorAction Stop | Out-Null
            New-NetRoute -InterfaceAlias $AdapterName -DestinationPrefix "0.0.0.0/0" -NextHop "{gateway}" -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
        }} else {{
            # Fresh injection
            $ipParams["DefaultGateway"] = "{gateway}"
            New-NetIPAddress @ipParams -ErrorAction Stop | Out-Null
        }}
                """
            else:
                ps_script += """
        if ($existingIP) {
            Set-NetIPAddress @ipParams -ErrorAction Stop | Out-Null
        } else {
            New-NetIPAddress @ipParams -ErrorAction Stop | Out-Null
        }
                """
        else:
            ps_script += "\n        # DNS-Only Mode: Skipping IP and Routing wipe.\n"

        # --- BLOCK 2: DNS CONFIGURATION ---
        if profile.dns_primary and profile.dns_primary.strip():
            dns_servers = f'"{profile.dns_primary.strip()}"'
            if profile.dns_secondary and profile.dns_secondary.strip():
                dns_servers += f', "{profile.dns_secondary.strip()}"'
            ps_script += f"\n        Set-DnsClientServerAddress -InterfaceAlias $AdapterName -ServerAddresses {dns_servers}"
        else:
            if is_dns_only:
                raise ValueError(
                    "Cannot apply a completely blank profile. Specify IP settings, DNS settings, or both."
                )

            ps_script += "\n        Set-DnsClientServerAddress -InterfaceAlias $AdapterName -ResetServerAddresses"

        if cls.DEBUG_MODE:
            print(f"--- DRY RUN PS SCRIPT ---\n{ps_script}\n-----------------------")
            return

        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            print(out)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"PowerShell Failed: {e.stderr.strip()}") from e

    @classmethod
    def enable_dhcp(cls, interface_name: str) -> None:
        """Reverts the specified interface back to DHCP for both IP and DNS."""
        if not cls.DEBUG_MODE and not cls.is_admin():
            raise PermissionError("Administrator privileges are required.")

        ps_script = f"""
        $ErrorActionPreference = "Stop"
        $AdapterName = "{interface_name}"

        # 1. Clear any existing static IPs and Gateway routes
        Remove-NetIPAddress -InterfaceAlias $AdapterName -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
        Remove-NetRoute -InterfaceAlias $AdapterName -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue

        # 2. Enable DHCP for IP address
        Set-NetIPInterface -InterfaceAlias $AdapterName -Dhcp Enabled -AddressFamily IPv4 -ErrorAction Stop

        # 3. Reset DNS to automatic (DHCP)
        Set-DnsClientServerAddress -InterfaceAlias $AdapterName -ResetServerAddresses -ErrorAction Stop
        """

        if cls.DEBUG_MODE:
            print(f"--- DRY RUN DHCP SCRIPT ---\n{ps_script}\n-----------------------")
            return

        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"PowerShell Failed: {e.stderr.strip()}") from e
