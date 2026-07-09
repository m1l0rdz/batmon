import subprocess
import re

def parse_radios() -> list[str]:
    """Parse Wi-Fi and Bluetooth statuses to generate warnings."""
    warnings = []
    
    # Check Wi-Fi
    try:
        power_proc = subprocess.run(["/usr/sbin/networksetup", "-getairportpower", "en0"], capture_output=True, text=True, timeout=10)
        if "On" in power_proc.stdout:
            ifconfig_proc = subprocess.run(["/sbin/ifconfig", "en0"], capture_output=True, text=True, timeout=10)
            if "status: inactive" in ifconfig_proc.stdout:
                warnings.append("Wi-Fi is On but not connected")
    except Exception:
        pass
        
    # Check Bluetooth
    try:
        bt_proc = subprocess.run(["/usr/sbin/system_profiler", "SPBluetoothDataType"], capture_output=True, text=True, timeout=10)
        bt_out = bt_proc.stdout
        
        is_on = "State: On" in bt_out or "Power State: On" in bt_out or "Bluetooth Power: On" in bt_out
        
        if is_on:
            is_connected = False
            if "Connected: Yes" in bt_out or "State: Connected" in bt_out:
                is_connected = True
            elif re.search(r"^\s*Connected:\s*$", bt_out, re.MULTILINE):
                is_connected = True
                
            if not is_connected:
                warnings.append("Bluetooth is On but no devices connected")
    except Exception:
        pass
        
    return warnings
