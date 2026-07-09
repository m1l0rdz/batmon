from zoneinfo import ZoneInfo

import pytest

from batmond import db
from batmond.rollup import (day_key, prune, rollup_daily, rollup_hourly,
                            snapshot_health)
from batmond.parsers.ioreg_battery import BatterySample

BERLIN = ZoneInfo("Europe/Berlin")
H = 3600


@pytest.fixture
def conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))


def _fill_hour(conn, hour_start, watts, on_ac=False, soc=50.0):
    for i in range(0, H, 15):
        conn.execute(
            "INSERT INTO battery_samples(ts, soc_pct, watts, is_charging,"
            " on_ac, brightness_pct, temp_c) VALUES (?,?,?,?,?,?,?)",
            (hour_start + i, soc, watts, 0, int(on_ac), 40.0, 32.5))
    conn.execute(
        "INSERT INTO component_power(ts_minute, cpu_mw, gpu_mw, package_mw, soc_temp_c, ssd_temp_c)"
        " VALUES (?, 1000, 500, 2000, 45.0, 40.0)", (hour_start,))
    conn.execute(
        "INSERT INTO app_energy(ts_minute, app, pid_count, energy_impact,"
        " attributed_mwh) VALUES (?, 'Chrome', 3, 100, 33.3)", (hour_start,))


def test_rollup_hourly(conn):
    _fill_hour(conn, 0, -7.2)     # steady -7.2 W whole hour
    conn.commit()
    rollup_hourly(conn, 2 * H)    # hour 0 complete
    row = conn.execute(
        "SELECT wh_out, wh_in, avg_watts, on_battery_sec, avg_brightness,"
        " avg_cpu_mw, avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c FROM rollup_hourly_battery WHERE hour=0").fetchone()
    assert row is not None
    wh_out, wh_in, avg_w, bat_sec, br, cpu, temp, soc_temp, ssd_temp = row
    # 239 sample pairs x 15s = 3585s integrated at 7.2 W = 7.17 Wh
    assert wh_out == pytest.approx(7.17, abs=0.05)
    assert wh_in == 0.0
    assert avg_w == pytest.approx(-7.2)
    assert bat_sec == pytest.approx(3585, abs=20)  # 239 gaps x 15s
    assert br == pytest.approx(40.0)
    assert cpu == pytest.approx(1000.0)
    assert temp == pytest.approx(32.5)
    assert soc_temp == pytest.approx(45.0)
    assert ssd_temp == pytest.approx(40.0)
    apps = conn.execute(
        "SELECT app, attributed_mwh FROM rollup_hourly_apps WHERE hour=0"
    ).fetchall()
    assert apps == [("Chrome", 33.3)]


def test_rollup_hourly_idempotent_and_incremental(conn):
    _fill_hour(conn, 0, -1.0)
    conn.commit()
    rollup_hourly(conn, 2 * H)
    rollup_hourly(conn, 2 * H)   # second run: no dup, no error
    n = conn.execute("SELECT COUNT(*) FROM rollup_hourly_battery").fetchone()[0]
    assert n == 1


def test_day_key_dst():
    # Europe/Berlin, Sun 2026-10-25: clocks fall back, 25-hour local day.
    # Berlin midnight = 2026-10-24 22:00 UTC = 1792879200.
    base = 1792879200
    days = {day_key(base + i * H, BERLIN) for i in range(25)}
    assert days == {"2026-10-25"}
    assert day_key(base + 25 * H, BERLIN) == "2026-10-26"


def test_rollup_daily_dst_25h_day(conn):
    base = 1792879200
    for i in range(26):  # 25h of Oct 25 + first hour of Oct 26
        conn.execute(
            "INSERT INTO rollup_hourly_battery(hour, soc_min, soc_max,"
            " wh_in, wh_out, avg_watts, on_battery_sec, on_ac_sec,"
            " avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c)"
            " VALUES (?, 40, 60, 0, 1.0, -1000, 3600, 0, 30.0, 45.0, 38.0)", (base + i * H,))
    conn.commit()
    rollup_daily(conn, base + 30 * H, tz=BERLIN)  # Oct 26 06:00: Oct 25 done
    row = conn.execute(
        "SELECT wh_out, on_battery_sec, avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c FROM rollup_daily_battery"
        " WHERE day='2026-10-25'").fetchone()
    assert row == (25.0, 25 * 3600, 30.0, 45.0, 38.0)
    assert conn.execute(
        "SELECT COUNT(*) FROM rollup_daily_battery WHERE day='2026-10-26'"
    ).fetchone()[0] == 0  # today: not rolled yet


def test_prune(conn):
    now = 100 * 86400
    conn.execute("INSERT INTO battery_samples(ts, soc_pct, is_charging,"
                 " on_ac) VALUES (?, 50, 0, 0)", (now - 49 * 3600,))
    conn.execute("INSERT INTO battery_samples(ts, soc_pct, is_charging,"
                 " on_ac) VALUES (?, 50, 0, 0)", (now - 3600,))
                 
    conn.execute("INSERT INTO app_energy(ts_minute, app, pid_count,"
                 " energy_impact, attributed_mwh) VALUES (?, 'A', 1, 1, 1)",
                 (now - 49 * 3600,))
    conn.execute("INSERT INTO app_energy(ts_minute, app, pid_count,"
                 " energy_impact, attributed_mwh) VALUES (?, 'A', 1, 1, 1)",
                 (now - 3600,))
                 
    conn.execute("INSERT INTO component_power(ts_minute, cpu_mw, gpu_mw,"
                 " package_mw) VALUES (?, 1, 1, 1)", (now - 49 * 3600,))
    conn.execute("INSERT INTO component_power(ts_minute, cpu_mw, gpu_mw,"
                 " package_mw) VALUES (?, 1, 1, 1)", (now - 3600,))

    conn.execute("INSERT INTO rollup_hourly_battery(hour) VALUES (?)",
                 (now - 91 * 86400,))
    conn.execute("INSERT INTO rollup_hourly_battery(hour) VALUES (?)",
                 (now - 86400,))
                 
    conn.execute("INSERT INTO rollup_hourly_apps(hour, app,"
                 " attributed_mwh, avg_energy_impact) VALUES (?, 'A', 1, 1)",
                 (now - 91 * 86400,))
    conn.execute("INSERT INTO rollup_hourly_apps(hour, app,"
                 " attributed_mwh, avg_energy_impact) VALUES (?, 'A', 1, 1)",
                 (now - 86400,))

    conn.commit()
    prune(conn, now)
    
    assert conn.execute("SELECT COUNT(*) FROM battery_samples").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM app_energy").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM component_power").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM rollup_hourly_battery").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM rollup_hourly_apps").fetchone()[0] == 1


def test_snapshot_health_once_per_day(conn):
    s = BatterySample(ts=0, soc_pct=50, current_ma=-1, voltage_mv=12000,
                      watts=-1, is_charging=False, on_ac=False, temp_c=30,
                      cycle_count=210, design_capacity_mah=6000,
                      raw_max_capacity_mah=5400,
                      raw_current_capacity_mah=2700, max_capacity_pct=90.0)
    snapshot_health(conn, s, 1000, tz=BERLIN)
    s.cycle_count = 999
    snapshot_health(conn, s, 2000, tz=BERLIN)  # same local day: ignored
    rows = conn.execute(
        "SELECT cycle_count, max_capacity_pct FROM battery_health_daily"
    ).fetchall()
    assert rows == [(210, 90.0)]
