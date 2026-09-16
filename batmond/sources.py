"""Data sources. LiveSource spawns Apple binaries with FIXED arguments
only (security section 7). FixtureSource replays captured files (dry-run).
--show-process-energy/--show-process-gpu match capture_fixtures.sh: without
them tasks have no energy_impact and attribution is all zeros
(fixture-verified, see tests/fixtures/NOTES.md and test_sources.py)."""
from __future__ import annotations

import subprocess
from functools import lru_cache
import time

from batmond.parsers.charge_policy import read_policy
from pathlib import Path

from batmond.parsers.smc import read_battery_extras
from batmond.parsers.thermal import aggregate_temps, read_raw_sensors

POWERMETRICS_CMD = [
    "/usr/bin/powermetrics", "--samplers", "tasks,cpu_power,gpu_power,thermal",
    "--show-process-energy", "--show-process-gpu",
    "-i", "1000", "-n", "5", "--format", "plist",
]
IOREG_CMD = ["/usr/sbin/ioreg", "-rn", "AppleSmartBattery", "-a"]
BRIGHTNESS_CMD = ["/usr/libexec/corebrightnessdiag", "status-info"]
PMSET_CMD = ["/usr/bin/pmset", "-g", "assertions"]


class LiveSource:
    def charge_policy(self):
        now = time.monotonic()
        if now >= getattr(self, '_policy_next', 0):
            self._policy = read_policy()
            self._policy_next = now + 60
        return self._policy

    @lru_cache(maxsize=1)
    def charger_model(self):
        try:
            model = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.model'], timeout=3).decode().strip()
        except (OSError, subprocess.SubprocessError):
            return {}
        # Verified Apple model mapping; unknown models get no inferred wattage.
        if model == 'Mac16,8':
            return dict(identifier=model,name='MacBook Pro 14-inch (M4 Pro, 2024)',fast_charge_reference_w=96)
        return dict(identifier=model)

    def powermetrics_burst(self) -> bytes:
        return subprocess.run(POWERMETRICS_CMD, capture_output=True,
                              timeout=30, check=True).stdout

    def ioreg_battery(self) -> bytes:
        return subprocess.run(IOREG_CMD, capture_output=True,
                              timeout=10, check=True).stdout

    def brightness_text(self) -> str:
        try:
            out = subprocess.run(BRIGHTNESS_CMD, capture_output=True,
                                 timeout=10)
            return out.stdout.decode(errors="replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    def assertions_text(self) -> str:
        try:
            out = subprocess.run(PMSET_CMD, capture_output=True, timeout=10)
            return out.stdout.decode(errors="replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    def temps(self) -> dict[str, float | None]:
        return aggregate_temps(read_raw_sensors())

    def smc_battery(self) -> dict:
        return read_battery_extras()


class FixtureSource:
    def charge_policy(self):
        return {}

    def charger_model(self):
        return {}

    def __init__(self, fixtures_dir: str):
        d = Path(fixtures_dir)
        self._pm = (d / "powermetrics_burst.plist").read_bytes()
        self._ioreg = (d / "ioreg_battery.plist").read_bytes()
        b = d / "corebrightnessdiag.txt"
        self._bright = b.read_text(errors="replace") if b.exists() else ""
        self._pmset = (d / "pmset_assertions.txt").read_text()

    def powermetrics_burst(self) -> bytes:
        return self._pm

    def ioreg_battery(self) -> bytes:
        return self._ioreg

    def brightness_text(self) -> str:
        return self._bright

    def assertions_text(self) -> str:
        return self._pmset

    def temps(self) -> dict[str, float | None]:
        return {"soc_temp_c": 45.7, "ssd_temp_c": 37.0}

    def smc_battery(self) -> dict:
        # The ioreg fixtures predate macOS 27.0 and carry LifetimeData.
        return {}
