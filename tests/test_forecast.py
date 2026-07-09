import json

import pytest

from batmond import db
from batmond.forecast import Forecaster, store
from batmond.parsers.ioreg_battery import BatterySample


def _s(ma, on_ac, charging, cur_mah=2500.0, max_mah=5000.0):
    return BatterySample(ts=0, soc_pct=50, current_ma=ma, voltage_mv=12000,
                         watts=ma * 12 / 1000, is_charging=charging,
                         on_ac=on_ac, temp_c=30, cycle_count=100,
                         design_capacity_mah=5500,
                         raw_max_capacity_mah=max_mah,
                         raw_current_capacity_mah=cur_mah,
                         max_capacity_pct=90.9)


def test_steady_discharge():
    f = Forecaster()
    out = None
    for _ in range(60):
        out = f.update(_s(-1000.0, False, False))
    assert out["mode"] == "battery"
    assert out["minutes"] == pytest.approx(150, abs=2)  # 2500mAh / 1000mA


def test_time_to_full():
    f = Forecaster()
    out = None
    for _ in range(60):
        out = f.update(_s(2500.0, True, True))
    assert out["mode"] == "charging"
    assert out["minutes"] == pytest.approx(60, abs=2)  # 2500mAh / 2500mA


def test_flip_resets_ema():
    f = Forecaster()
    for _ in range(60):
        f.update(_s(-3000.0, False, False))
    out = f.update(_s(1000.0, True, True))
    assert out["mode"] == "charging"
    assert out["minutes"] == pytest.approx(150, abs=2)  # fresh EMA = 1000mA


def test_full_and_noise():
    f = Forecaster()
    assert f.update(_s(0.0, True, False))["mode"] == "full"
    assert f.update(_s(-5.0, False, False))["minutes"] is None  # < 10 mA


def test_store(tmp_path):
    conn = db.open_rw(str(tmp_path / "t.db"))
    store(conn, {"mode": "battery", "minutes": 42})
    raw = conn.execute("SELECT value FROM state WHERE key='forecast'").fetchone()[0]
    assert json.loads(raw) == {"mode": "battery", "minutes": 42}
