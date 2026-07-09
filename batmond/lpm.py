import subprocess
import logging

log = logging.getLogger("batmond.lpm")

def set_low_power_mode(enabled: bool):
    val = "1" if enabled else "0"
    try:
        subprocess.run(["/usr/bin/pmset", "-a", "lowpowermode", val], check=True, capture_output=True)
        log.info(f"Set low power mode to {enabled}")
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to set low power mode: {e.stderr.decode()}")
