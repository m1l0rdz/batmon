import pytest
from batmond import db
from batmon_web.queries import latest_component, history, anomalies_since

@pytest.fixture
def conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))

def test_latest_component_temps(conn):
    conn.execute(
        "INSERT INTO component_power(ts_minute, cpu_mw, gpu_mw, ane_mw, package_mw, thermal_pressure, soc_temp_c, ssd_temp_c) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1000, 100, 200, 300, 400, "Nominal", 45.5, 38.2)
    )
    res = latest_component(conn)
    assert res is not None
    assert res["soc_temp_c"] == 45.5
    assert res["ssd_temp_c"] == 38.2

def test_history_temps_24h(conn):
    now_ts = 2000
    conn.execute(
        "INSERT INTO battery_samples(ts, soc_pct, watts, assert_awake, temp_c, is_charging, on_ac) "
        "VALUES (?, 100, 10, 0, 35.5, 0, 0)", (1900,)
    )
    conn.execute(
        "INSERT INTO component_power(ts_minute, cpu_mw, gpu_mw, ane_mw, package_mw, soc_temp_c, ssd_temp_c) "
        "VALUES (?, 100, 200, 300, 400, 42.1, 37.8)", (1900,)
    )
    res = history(conn, "24h", now_ts)
    assert len(res["battery"]) == 1
    assert res["battery"][0]["temp_c"] == 35.5
    assert len(res["components"]) == 1
    assert res["components"][0]["soc_temp_c"] == 42.1
    assert res["components"][0]["ssd_temp_c"] == 37.8

def test_history_temps_7d(conn):
    now_ts = 1000000
    conn.execute(
        "INSERT INTO rollup_hourly_battery(hour, avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c) "
        "VALUES (?, 36.1, 44.4, 39.9)", (now_ts - 3600,)
    )
    res = history(conn, "7d", now_ts)
    assert len(res["battery"]) == 1
    assert res["battery"][0]["temp_c"] == 36.1
    assert len(res["components"]) == 1
    assert res["components"][0]["soc_temp_c"] == 44.4
    assert res["components"][0]["ssd_temp_c"] == 39.9


def test_history_temperature_series_24h(conn):
    # Minute-aligned ts so battery grouping (ts - ts%60) matches the component
    # ts_minute, exercising the temperature merge the History chart consumes.
    now_ts = 2000
    conn.execute(
        "INSERT INTO battery_samples(ts, soc_pct, watts, assert_awake, temp_c, is_charging, on_ac) "
        "VALUES (?, 100, 10, 0, 35.5, 0, 0)", (1860,))
    conn.execute(
        "INSERT INTO component_power(ts_minute, cpu_mw, gpu_mw, ane_mw, package_mw, soc_temp_c, ssd_temp_c) "
        "VALUES (?, 100, 200, 300, 400, 42.1, 37.8)", (1860,))
    res = history(conn, "24h", now_ts)
    assert "temperature" in res and len(res["temperature"]) == 1
    t = res["temperature"][0]
    assert t["soc_temp_c"] == 42.1
    assert t["ssd_temp_c"] == 37.8
    assert t["temp_c"] == 35.5


def test_history_temperature_series_7d(conn):
    now_ts = 1000000
    conn.execute(
        "INSERT INTO rollup_hourly_battery(hour, avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c) "
        "VALUES (?, 36.1, 44.4, 39.9)", (now_ts - 3600,))
    res = history(conn, "7d", now_ts)
    assert "temperature" in res and len(res["temperature"]) == 1
    t = res["temperature"][0]
    assert t["temp_c"] == 36.1
    assert t["soc_temp_c"] == 44.4
    assert t["ssd_temp_c"] == 39.9
