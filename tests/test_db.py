import sqlite3

import pytest

from batmond import db


def test_schema_created(tmp_path):
    path = str(tmp_path / "t.db")
    conn = db.open_rw(path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"battery_samples", "app_energy", "component_power",
            "rollup_hourly_battery", "rollup_hourly_apps",
            "rollup_daily_battery", "rollup_daily_apps", "sessions",
            "battery_health_daily", "anomalies", "state", "dark_wakes",
            "schema_version"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("SELECT v FROM schema_version").fetchone()[0] == 4


def test_open_rw_idempotent(tmp_path):
    path = str(tmp_path / "t.db")
    db.open_rw(path).close()
    conn = db.open_rw(path)  # second open must not fail or duplicate rows
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_anomalies_unique_day_app(tmp_path):
    conn = db.open_rw(str(tmp_path / "t.db"))
    q = ("INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today, "
         "wh_baseline, ratio) VALUES (1, '2026-07-07', 'X', 3, 1, 3)")
    conn.execute(q)
    conn.execute(q)
    assert conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 1


def test_sessions_kind_check(tmp_path):
    conn = db.open_rw(str(tmp_path / "t.db"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sessions(kind, started, soc_start) "
                     "VALUES ('bogus', 1, 50)")


def test_ro_reader_sees_writer_rows(tmp_path):
    path = str(tmp_path / "t.db")
    w = db.open_rw(path)
    w.execute("INSERT INTO state(key, value) VALUES ('x', '1')")
    w.commit()
    r = db.open_ro(path)
    assert r.execute("SELECT value FROM state WHERE key='x'").fetchone()[0] == "1"
    with pytest.raises(sqlite3.OperationalError):
        r.execute("INSERT INTO state(key, value) VALUES ('y', '2')")


def test_migration_v3_to_v4(tmp_path):
    path = str(tmp_path / "t.db")
    conn = sqlite3.connect(path)
    v3_ddl = """
    CREATE TABLE schema_version(v INTEGER NOT NULL);
    INSERT INTO schema_version(v) VALUES (3);
    CREATE TABLE component_power(ts_minute INTEGER PRIMARY KEY, package_mw REAL);
    CREATE TABLE rollup_hourly_battery(hour INTEGER PRIMARY KEY, avg_package_mw REAL);
    CREATE TABLE rollup_daily_battery(day TEXT PRIMARY KEY, avg_package_mw REAL);
    CREATE TABLE anomalies(id INTEGER PRIMARY KEY, ratio REAL);
    """
    conn.executescript(v3_ddl)
    conn.commit()
    conn.close()

    conn2 = db.open_rw(path)
    assert conn2.execute("SELECT v FROM schema_version").fetchone()[0] == 4

    cp_cols = [r[1] for r in conn2.execute("PRAGMA table_info(component_power)")]
    assert "soc_temp_c" in cp_cols
    assert "ssd_temp_c" in cp_cols

    rh_cols = [r[1] for r in conn2.execute("PRAGMA table_info(rollup_hourly_battery)")]
    assert "avg_temp_c" in rh_cols
    assert "avg_soc_temp_c" in rh_cols
    assert "avg_ssd_temp_c" in rh_cols

    rd_cols = [r[1] for r in conn2.execute("PRAGMA table_info(rollup_daily_battery)")]
    assert "avg_temp_c" in rd_cols
    assert "avg_soc_temp_c" in rd_cols
    assert "avg_ssd_temp_c" in rd_cols

    an_cols = [r[1] for r in conn2.execute("PRAGMA table_info(anomalies)")]
    assert "detail" in an_cols


def test_state_helpers(tmp_path):
    conn = db.open_rw(str(tmp_path / "t.db"))
    assert db.get_state(conn, "k") is None
    db.set_state(conn, "k", "v1")
    db.set_state(conn, "k", "v2")
    assert db.get_state(conn, "k") == "v2"
