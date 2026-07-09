"""Data sources. LiveSource spawns Apple binaries with FIXED arguments
only (security section 7). FixtureSource replays captured files (dry-run).
--show-process-energy/--show-process-gpu match capture_fixtures.sh: without
them tasks have no energy_impact and attribution is all zeros
(fixture-verified, see tests/fixtures/NOTES.md and test_sources.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path

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


class FixtureSource:
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
