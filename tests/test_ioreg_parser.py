import time
from pathlib import Path

import pytest

from batmond.parsers.ioreg_battery import parse_ioreg_battery, _signed

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    p = FIXTURES / name
    if not p.exists():
        pytest.skip(f"{name} not captured")
    return p.read_bytes()


def test_signed_conversion():
    assert _signed(100) == 100
    assert _signed(2**64 - 500) == -500


def test_parse_primary_fixture():
    ts = int(time.time())
    s = parse_ioreg_battery(_load("ioreg_battery.plist"), ts)
    assert s.ts == ts
    assert 0 <= s.soc_pct <= 100
    assert 5000 < s.voltage_mv < 20000
    assert s.raw_max_capacity_mah > 1000          # mAh, not percent (D2)
    assert s.raw_current_capacity_mah <= s.raw_max_capacity_mah * 1.05
    assert s.design_capacity_mah > 1000
    assert 0 < s.max_capacity_pct <= 110
    assert s.cycle_count >= 0
    assert s.temp_c is None or 0 < s.temp_c < 60
    assert isinstance(s.is_charging, bool) and isinstance(s.on_ac, bool)
    # watts sign agrees with amperage sign
    assert (s.watts <= 0) == (s.current_ma <= 0)


def test_discharging_fixture_negative_watts():
    s = parse_ioreg_battery(_load("ioreg_battery_discharging.plist"), 0)
    assert s.watts < 0 and not s.on_ac


def test_charging_fixture_positive_watts():
    doc = _load("ioreg_battery_charging.plist")
    if b"<key>IsCharging</key>\n\t\t<false/>" in doc or b"<key>ExternalConnected</key>\n\t\t<false/>" in doc:
        pytest.skip("charging fixture was incorrectly captured as discharging")
    s = parse_ioreg_battery(doc, 0)
    assert s.on_ac and s.is_charging and s.watts > 0
