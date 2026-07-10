import json
from zoneinfo import ZoneInfo

import pytest

from batmond import db
from batmond.anomalies import build_detail, check
from batmond.rollup import day_key

UTC = ZoneInfo("UTC")
NOW = 90 * 86400 + 12 * 3600  # noon UTC, day 90


@pytest.fixture
def conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))


def _baseline(conn, app, days, mwh_per_day):
    for i in range(1, days + 1):
        conn.execute(
            "INSERT INTO rollup_daily_apps(day, app, attributed_mwh)"
            " VALUES (?,?,?)",
            (day_key(NOW - i * 86400, UTC), app, mwh_per_day))


def _today(conn, app, mwh):
    hour = NOW - NOW % 3600
    conn.execute(
        "INSERT INTO rollup_hourly_apps(hour, app, attributed_mwh)"
        " VALUES (?,?,?)", (hour, app, mwh))


def test_triggers_and_dedupes(conn):
    _baseline(conn, "Chrome", 7, 1000.0)   # 1 Wh/day baseline
    _today(conn, "Chrome", 2600.0)         # 2.6 Wh today: >2x and >1.5
    ids = check(conn, NOW, tz=UTC)
    assert len(ids) == 1
    row = conn.execute(
        "SELECT app, wh_today, wh_baseline, ratio FROM anomalies").fetchone()
    assert row[0] == "Chrome"
    assert row[1] == pytest.approx(2.6)
    assert row[2] == pytest.approx(1.0)
    assert row[3] == pytest.approx(2.6)
    assert check(conn, NOW + 60, tz=UTC) == []  # same day: no second row


def _app_energy(conn, ts_minute, app, energy_impact, attributed_mwh):
    conn.execute(
        "INSERT INTO app_energy(ts_minute, app, pid_count, energy_impact,"
        " attributed_mwh) VALUES (?,?,?,?,?)",
        (ts_minute, app, 1, energy_impact, attributed_mwh))


def test_build_detail_rapid_discharge_culprits(conn):
    m = NOW - NOW % 60
    _app_energy(conn, m, "Chrome", 100.0, 500.0)   # 0.5 Wh
    _app_energy(conn, m, "Zoom", 50.0, 200.0)      # 0.2 Wh
    d = json.loads(build_detail(conn, "__SYSTEM_RAPID_DISCHARGE__", NOW, UTC))
    apps = [c["app"] for c in d["culprits"]]
    assert apps[:2] == ["Chrome", "Zoom"]          # ranked by energy desc
    assert d["culprits"][0]["wh"] == pytest.approx(0.5)
    assert "Low Power Mode" in d["advice"]


def test_build_detail_thermal_uses_wh_not_energy_impact(conn):
    m = NOW - NOW % 60
    # Huge energy_impact, tiny attributed_mwh: the culprit "wh" must come from
    # attributed_mwh (real Wh), never the unitless energy_impact score.
    _app_energy(conn, m, "Compiler", 999999.0, 250.0)  # 0.25 Wh
    d = json.loads(build_detail(conn, "__SYSTEM_THERMAL__", NOW, UTC))
    assert d["culprits"][0]["app"] == "Compiler"
    assert d["culprits"][0]["wh"] == pytest.approx(0.25)


def test_build_detail_sleep_drain_ranks_by_n(conn):
    db.set_state(conn, "dark_wakes", json.dumps([
        {"culprits": [{"proc": "bluetoothd", "why": "woke", "n": 1}]},
        {"culprits": [{"proc": "backupd", "why": "kept-awake", "n": 5}]},
    ]))
    d = json.loads(build_detail(conn, "__SYSTEM_SLEEP_DRAIN__", NOW, UTC))
    apps = [c["app"] for c in d["culprits"]]
    assert apps[0] == "backupd"    # higher n wins (uses the "n" key, not "count")
    assert "Login Items" in d["advice"]


def test_build_detail_weak_charger_has_advice_no_culprits(conn):
    d = json.loads(build_detail(conn, "__SYSTEM_WEAK_CHARGER__", NOW, UTC))
    assert d["culprits"] == []
    assert "USB-C" in d["advice"]


def test_needs_min_baseline_days(conn):
    _baseline(conn, "New App", 2, 1000.0)  # only 2 days history
    _today(conn, "New App", 9000.0)
    assert check(conn, NOW, tz=UTC) == []


def test_absolute_floor(conn):
    _baseline(conn, "Tiny", 7, 100.0)      # 0.1 Wh/day
    _today(conn, "Tiny", 400.0)            # 4x but only 0.4 Wh
    assert check(conn, NOW, tz=UTC) == []


def test_zero_baseline_ratio_capped(conn):
    """ratio must stay JSON-serializable: starlette dumps with
    allow_nan=False, so inf in the DB would 500 /api/anomalies."""
    _baseline(conn, "Zero App", 7, 0.0)      # 0.0 Wh/day baseline
    _today(conn, "Zero App", 2000.0)         # 2.0 Wh today
    ids = check(conn, NOW, tz=UTC)
    assert len(ids) == 1
    row = conn.execute(
        "SELECT app, wh_today, wh_baseline, ratio FROM anomalies WHERE app='Zero App'").fetchone()
    assert row[0] == "Zero App"
    assert row[1] == pytest.approx(2.0)
    assert row[2] == pytest.approx(0.0)
    assert row[3] == pytest.approx(999.0)


def test_huge_ratio_capped(conn):
    _baseline(conn, "Spiky", 7, 0.001)       # ~0 baseline, not exactly 0
    _today(conn, "Spiky", 5000.0)            # 5 Wh today
    check(conn, NOW, tz=UTC)
    (ratio,) = conn.execute(
        "SELECT ratio FROM anomalies WHERE app='Spiky'").fetchone()
    assert ratio == pytest.approx(999.0)


def test_system_anomalies(conn):
    # 1. Thermal Pressure (5 Heavy in last 15 mins)
    for i in range(5):
        conn.execute("INSERT INTO component_power(ts_minute, thermal_pressure) VALUES (?, ?)", 
                     (NOW - i * 60, "Heavy"))
                     
    # 2. Rapid Discharge (> 30W avg)
    conn.execute("INSERT INTO battery_samples(ts, soc_pct, current_ma, voltage_mv, watts, is_charging, on_ac, temp_c, brightness_pct, assert_awake) VALUES (?, 100, 0, 0, ?, 0, 0, 30, 50, 0)",
                 (NOW - 60, -35.0))
                 
    # 3. Sleep Drain (Gap > 1hr, drop > 5%)
    conn.execute("INSERT INTO sessions(id, kind, started, ended, soc_start, soc_end) VALUES (1, 'battery', ?, ?, 100, 90)",
                 (NOW - 3600 * 5, NOW - 3600 * 2))
    conn.execute("INSERT INTO sessions(id, kind, started, ended, soc_start, soc_end) VALUES (2, 'battery', ?, ?, 80, 78)",
                 (NOW - 60, NOW)) # Gap from NOW-3600*2 to NOW-60 (approx 2 hours). Drop 90 -> 80 = 10%.
                 
    ids = check(conn, NOW, tz=UTC)
    assert len(ids) == 3
    
    apps = [r[0] for r in conn.execute("SELECT app FROM anomalies").fetchall()]
    assert "__SYSTEM_THERMAL__" in apps
    assert "__SYSTEM_RAPID_DISCHARGE__" in apps
    assert "__SYSTEM_SLEEP_DRAIN__" in apps


def _fill_ac_samples(conn, watts, is_charging, n=25):
    for i in range(n):
        conn.execute(
            "INSERT INTO battery_samples(ts, soc_pct, current_ma, voltage_mv,"
            " watts, is_charging, on_ac, temp_c, brightness_pct, assert_awake)"
            " VALUES (?, 50, 0, 0, ?, ?, 1, 30, 50, 0)",
            (NOW - i * 15, watts, is_charging))


def test_weak_charger_anomaly(conn):
    # Plugged into AC but still net-draining >5W over the window.
    _fill_ac_samples(conn, watts=-8.0, is_charging=0)
    check(conn, NOW, tz=UTC)
    apps = [r[0] for r in conn.execute("SELECT app FROM anomalies").fetchall()]
    assert "__SYSTEM_WEAK_CHARGER__" in apps


def test_weak_charger_not_triggered_when_charging(conn):
    # Plugged in and actually charging (positive watts) - not weak.
    _fill_ac_samples(conn, watts=20.0, is_charging=1)
    check(conn, NOW, tz=UTC)
    apps = [r[0] for r in conn.execute("SELECT app FROM anomalies").fetchall()]
    assert "__SYSTEM_WEAK_CHARGER__" not in apps


def _seed_full_plugged(conn, now_ts, n=650, soc=100.0, on_ac=1):
    rows = [(now_ts - i * 15, soc, on_ac, 1) for i in range(n)]
    conn.executemany(
        "INSERT INTO battery_samples(ts, soc_pct, on_ac, is_charging)"
        " VALUES (?,?,?,?)", rows)


def test_full_plugged_fires(conn):
    now_ts = 1_800_000_000
    _seed_full_plugged(conn, now_ts)
    inserted = check(conn, now_ts)
    kinds = [conn.execute("SELECT app FROM anomalies WHERE id=?", (i,)).fetchone()[0]
             for i in inserted]
    assert "__SYSTEM_FULL_PLUGGED__" in kinds


def test_full_plugged_once_per_day(conn):
    now_ts = 1_800_000_000
    _seed_full_plugged(conn, now_ts)
    check(conn, now_ts)
    again = check(conn, now_ts + 60)
    kinds = [conn.execute("SELECT app FROM anomalies WHERE id=?", (i,)).fetchone()[0]
             for i in again]
    assert "__SYSTEM_FULL_PLUGGED__" not in kinds


def test_full_plugged_needs_full_window(conn):
    now_ts = 1_800_000_000
    _seed_full_plugged(conn, now_ts, n=300)  # only 75 min of data
    inserted = check(conn, now_ts)
    kinds = [conn.execute("SELECT app FROM anomalies WHERE id=?", (i,)).fetchone()[0]
             for i in inserted]
    assert "__SYSTEM_FULL_PLUGGED__" not in kinds


def _seed_hot_charge(conn, now_ts, temp=40.0, n=30):
    rows = [(now_ts - i * 15, 60.0, 1, 1, temp) for i in range(n)]
    conn.executemany(
        "INSERT INTO battery_samples(ts, soc_pct, on_ac, is_charging, temp_c)"
        " VALUES (?,?,?,?,?)", rows)


def test_hot_charge_fires(conn):
    now_ts = 1_800_100_000
    _seed_hot_charge(conn, now_ts)
    inserted = check(conn, now_ts)
    rows = [conn.execute("SELECT app, ratio FROM anomalies WHERE id=?", (i,)).fetchone()
            for i in inserted]
    hot = [r for r in rows if r[0] == "__SYSTEM_HOT_CHARGE__"]
    assert hot and hot[0][1] == pytest.approx(40.0 / 38.0)


def test_hot_charge_silent_when_cool(conn):
    now_ts = 1_800_100_000
    _seed_hot_charge(conn, now_ts, temp=33.0)
    inserted = check(conn, now_ts)
    kinds = [conn.execute("SELECT app FROM anomalies WHERE id=?", (i,)).fetchone()[0]
             for i in inserted]
    assert "__SYSTEM_HOT_CHARGE__" not in kinds


def test_hot_charge_needs_enough_samples(conn):
    now_ts = 1_800_100_000
    _seed_hot_charge(conn, now_ts, n=5)
    inserted = check(conn, now_ts)
    kinds = [conn.execute("SELECT app FROM anomalies WHERE id=?", (i,)).fetchone()[0]
             for i in inserted]
    assert "__SYSTEM_HOT_CHARGE__" not in kinds


def test_full_plugged_silent_on_battery_dip(conn):
    now_ts = 1_800_000_000
    _seed_full_plugged(conn, now_ts)
    conn.execute(
        "INSERT INTO battery_samples(ts, soc_pct, on_ac, is_charging)"
        " VALUES (?, 90, 0, 0)", (now_ts - 3599,))
    inserted = check(conn, now_ts)
    kinds = [conn.execute("SELECT app FROM anomalies WHERE id=?", (i,)).fetchone()[0]
             for i in inserted]
    assert "__SYSTEM_FULL_PLUGGED__" not in kinds

