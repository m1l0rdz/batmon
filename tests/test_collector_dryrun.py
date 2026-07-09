from pathlib import Path

from batmond import db
from batmond.__main__ import main

FIXTURES = str(Path(__file__).parent / "fixtures")


def test_dry_run_fills_db(tmp_path):
    path = str(tmp_path / "dry.db")
    main(["--dry-run", "--fixtures", FIXTURES, "--db", path,
          "--ticks", "480"])  # 480 x 15s = 2h synthetic
    conn = db.open_ro(path)
    n_samples = conn.execute(
        "SELECT COUNT(*) FROM battery_samples").fetchone()[0]
    assert n_samples == 480
    n_app_minutes = conn.execute(
        "SELECT COUNT(DISTINCT ts_minute) FROM app_energy").fetchone()[0]
    assert n_app_minutes >= 118  # ~one per minute over 2h
    assert conn.execute(
        "SELECT COUNT(*) FROM component_power").fetchone()[0] >= 118
    assert conn.execute(
        "SELECT COUNT(*) FROM rollup_hourly_battery").fetchone()[0] >= 1
    assert conn.execute(
        "SELECT COUNT(*) FROM sessions").fetchone()[0] >= 1
    assert conn.execute(
        "SELECT value FROM state WHERE key='forecast'").fetchone() is not None
    assert conn.execute(
        "SELECT value FROM state WHERE key='heartbeat'").fetchone() is not None
    # health snapshot must exist right after startup, not only after the
    # first local midnight
    assert conn.execute(
        "SELECT COUNT(*) FROM battery_health_daily").fetchone()[0] >= 1
    # current battery health is published for /api/now
    assert conn.execute(
        "SELECT value FROM state WHERE key='health_now'").fetchone() is not None


def test_collector_survives_broken_source(tmp_path, caplog):
    """Unhandled exception in one loop iteration must never kill the
    daemon (global constraint)."""
    from batmond.collector import Collector

    class BrokenSource:
        def powermetrics_burst(self):
            raise RuntimeError("boom")
        def ioreg_battery(self):
            raise RuntimeError("boom")
        def brightness_text(self):
            raise RuntimeError("boom")
        def assertions_text(self):
            raise RuntimeError("boom")

    path = str(tmp_path / "b.db")
    conn = db.open_rw(path)
    c = Collector(conn, BrokenSource(), path)
    for i in range(8):
        c.tick(1000 + i * 15)   # must not raise
    assert conn.execute("SELECT COUNT(*) FROM battery_samples").fetchone()[0] == 0
