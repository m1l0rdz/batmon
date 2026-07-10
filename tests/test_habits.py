import pytest
from batmond import db
from batmon_web.queries import charging_habits

DAY = 86400
NOW = 1_800_000_000  # fixed epoch for determinism


@pytest.fixture
def conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))


def test_habits_empty_db(conn):
    h = charging_habits(conn, NOW)
    assert h["window_days"] == 30
    assert h["full_pct_of_ac"] is None
    assert h["ac_share_pct"] is None
    assert h["deep_discharges"] == 0
    assert h["overnight_sessions"] == 0
    assert h["cycles_30d"] is None


def test_full_pct_and_ac_share(conn):
    # 10h on AC, 2h of it in a 'full' session, 10h on battery
    conn.execute(
        "INSERT INTO rollup_hourly_battery(hour, on_ac_sec, on_battery_sec)"
        " VALUES (?, ?, ?)", (NOW - 3600, 36000, 36000))
    conn.execute(
        "INSERT INTO sessions(kind, started, ended, soc_start, soc_end)"
        " VALUES ('full', ?, ?, 100, 100)", (NOW - 7200 - 60, NOW - 60))
    h = charging_habits(conn, NOW)
    assert h["ac_share_pct"] == pytest.approx(50.0)
    assert h["full_pct_of_ac"] == pytest.approx(7200 / 36000 * 100.0)


def test_deep_discharges_counted(conn):
    for i, end in enumerate([5, 8, 50]):
        conn.execute(
            "INSERT INTO sessions(kind, started, ended, soc_start, soc_end)"
            " VALUES ('battery', ?, ?, 90, ?)",
            (NOW - (i + 1) * DAY, NOW - (i + 1) * DAY + 3600, end))
    assert charging_habits(conn, NOW)["deep_discharges"] == 2


def test_overnight_sessions(conn):
    import datetime as dt
    # charging session starting 23:00 local, 3h long -> overnight
    start = int(dt.datetime.fromtimestamp(NOW).replace(
        hour=23, minute=0, second=0, microsecond=0).timestamp()) - 2 * DAY
    conn.execute(
        "INSERT INTO sessions(kind, started, ended, soc_start, soc_end)"
        " VALUES ('charging', ?, ?, 60, 100)", (start, start + 3 * 3600))
    # daytime charging session -> not overnight
    noon = start - 11 * 3600  # 12:00 local previous day
    conn.execute(
        "INSERT INTO sessions(kind, started, ended, soc_start, soc_end)"
        " VALUES ('charging', ?, ?, 60, 100)", (noon, noon + 3 * 3600))
    # short 22:30 top-up (< 2h) -> not counted
    conn.execute(
        "INSERT INTO sessions(kind, started, ended, soc_start, soc_end)"
        " VALUES ('charging', ?, ?, 90, 95)", (start - 1800, start - 900))
    assert charging_habits(conn, NOW)["overnight_sessions"] == 1


def test_cycles_30d(conn):
    conn.execute(
        "INSERT INTO battery_health_daily(day, cycle_count, max_capacity_pct)"
        " VALUES (date('now', '-20 days'), 100, 95)")
    conn.execute(
        "INSERT INTO battery_health_daily(day, cycle_count, max_capacity_pct)"
        " VALUES (date('now'), 112, 95)")
    assert charging_habits(conn, NOW)["cycles_30d"] == 12
