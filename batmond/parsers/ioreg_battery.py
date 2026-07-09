"""Parser for `ioreg -rn AppleSmartBattery -a` plist output.

FIXTURE-VERIFIED (D2): on Apple Silicon CurrentCapacity/MaxCapacity are
percent; real mAh are AppleRawCurrentCapacity/AppleRawMaxCapacity.
Temperature is hundredths of a degree C (verify NOTES.md).
"""
from __future__ import annotations

import plistlib
from dataclasses import dataclass
from typing import Optional, Tuple


def _signed(v: int, bits: int = 64) -> int:
    if v >= 1 << (bits - 1):
        v -= 1 << bits
    return v


@dataclass
class BatterySample:
    ts: int
    soc_pct: float
    current_ma: float
    voltage_mv: float
    watts: float
    is_charging: bool
    on_ac: bool
    temp_c: Optional[float]
    cycle_count: int
    design_capacity_mah: float
    raw_max_capacity_mah: float
    raw_current_capacity_mah: float
    max_capacity_pct: float
    cell_voltage_mv: Optional[Tuple[int, ...]] = None
    lifetime_temp_min: Optional[float] = None
    lifetime_temp_max: Optional[float] = None
    lifetime_temp_avg: Optional[float] = None
    operating_time_hours: Optional[float] = None


def parse_ioreg_battery(raw: bytes, ts: int) -> BatterySample:
    doc = plistlib.loads(raw)
    d = doc[0] if isinstance(doc, list) else doc
    amperage = float(_signed(int(d["Amperage"])))
    voltage_mv = float(d["Voltage"])
    watts = amperage / 1000.0 * (voltage_mv / 1000.0)
    design = float(d["DesignCapacity"])
    raw_max = float(d.get("AppleRawMaxCapacity", d["MaxCapacity"]))
    raw_cur = float(d.get("AppleRawCurrentCapacity", d["CurrentCapacity"]))
    temp = d.get("Temperature")
    
    cell_volts = d.get("BatteryData", {}).get("CellVoltage")
    if isinstance(cell_volts, list) or isinstance(cell_volts, tuple):
        cell_volts = tuple(int(v) for v in cell_volts)
    else:
        cell_volts = None
        
    lifetime = d.get("BatteryData", {}).get("LifetimeData", {})
    lt_min = lifetime.get("MinimumTemperature")
    lt_max = lifetime.get("MaximumTemperature")
    lt_avg = lifetime.get("AverageTemperature")
    op_time = lifetime.get("TotalOperatingTime")

    return BatterySample(
        ts=ts,
        soc_pct=float(d["CurrentCapacity"]),
        current_ma=amperage,
        voltage_mv=voltage_mv,
        watts=watts,
        is_charging=bool(d["IsCharging"]),
        on_ac=bool(d["ExternalConnected"]),
        temp_c=(float(temp) / 100.0) if temp is not None else None,
        cycle_count=int(d.get("CycleCount", 0)),
        design_capacity_mah=design,
        raw_max_capacity_mah=raw_max,
        raw_current_capacity_mah=raw_cur,
        max_capacity_pct=raw_max / design * 100.0 if design else 0.0,
        cell_voltage_mv=cell_volts,
        lifetime_temp_min=float(lt_min) if lt_min is not None else None,
        lifetime_temp_max=float(lt_max) if lt_max is not None else None,
        lifetime_temp_avg=float(lt_avg) / 10.0 if lt_avg is not None else None,
        operating_time_hours=float(op_time) if op_time is not None else None,
    )
