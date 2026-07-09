"""Parser for connected bluetooth devices (AirPods, Keyboard, Mouse)."""
import json
import logging
import subprocess

log = logging.getLogger("batmond")

def get_connected_devices():
    try:
        # system_profiler SPBluetoothDataType routinely takes 2-5s; a 1s
        # timeout made this almost always fail (empty device list).
        out = subprocess.check_output(
            ["/usr/sbin/system_profiler", "SPBluetoothDataType", "-json"],
            timeout=8.0
        )
        data = json.loads(out)
        devices = []
        for bt in data.get("SPBluetoothDataType", []):
            connected = bt.get("device_connected", [])
            for item in connected:
                # item is a dict with one key (device name) mapping to properties
                for name, props in item.items():
                    # battery levels are usually strings like "100 %"
                    level = props.get("device_batteryLevelMain") or props.get("device_batteryLevel")
                    if level:
                        try:
                            pct = int(level.replace("%", "").strip())
                            devices.append({"name": name, "battery_pct": pct})
                        except ValueError:
                            pass
        return devices
    except Exception:
        log.debug("failed to parse bluetooth devices", exc_info=True)
        return []
