import pytest

from batmond import db
from batmond.sessions import SessionTracker, classify, integrate


def _insert(conn, ts, soc, watts, on_ac, charging):
    conn.execute(
        "INSERT INTO battery_samples(ts, soc_pct, watts, is_charging, on_ac)"
        " VALUES (?,?,?,?,?)", (ts, soc, watts, int(charging), int(on_ac)))


@pytest.fixture
def conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))


def test_classify():
    assert classify(False, False) == "battery"
    assert classify(True, True) == "charging"
    assert classify(True, False) == "full"


def test_integrate_caps_gaps(conn):
    _insert(conn, 0, 50, -7.2, False, False)     # -7.2 W for 15s
    _insert(conn, 15, 50, -7.2, False, False)    # then 1h sleep gap
    _insert(conn, 3615, 49, -7.2, False, False)
    wh_in, wh_out, bat_sec, ac_sec = integrate(conn, 0, 3616)
    # 15s at 7.2W = 0.03 Wh, plus capped 90s at 7.2W = 0.18 Wh
    assert wh_out == pytest.approx(0.21, abs=0.001)
    assert wh_in == 0.0
    assert bat_sec == 15 + 90
    assert ac_sec == 0


def test_source_flip_closes_and_opens(conn):
    tr = SessionTracker(conn)
    for i in range(4):                       # battery 0..45s
        _insert(conn, i * 15, 50 - i, -7.2, False, False)
        tr.feed(i * 15, 50 - i, False, False)
    _insert(conn, 60, 47, 10.0, True, True)   # plugged in
    tr.feed(60, 47, True, True)
    rows = conn.execute(
        "SELECT kind, started, ended, soc_start, soc_end FROM sessions "
        "ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "battery" and rows[0][2] == 45 and rows[0][4] == 47
    assert rows[1][0] == "charging" and rows[1][2] is None


def test_gap_splits_session(conn):
    tr = SessionTracker(conn)
    _insert(conn, 0, 50, -5.0, False, False)
    tr.feed(0, 50, False, False)
    _insert(conn, 15, 50, -5.0, False, False)
    tr.feed(15, 50, False, False)
    _insert(conn, 500, 48, -5.0, False, False)  # gap > 90s
    tr.feed(500, 48, False, False)
    rows = conn.execute("SELECT started, ended FROM sessions ORDER BY id").fetchall()
    assert rows == [(0, 15), (500, None)]


def test_dark_wakes_skips_zero_drain(conn, monkeypatch):
    # A sleep gap with maintenance wakes but no measurable charge lost must
    # not record dark wakes (README: "meaningful charge lost across a gap").
    import batmond.parsers.pmset_log as pl
    monkeypatch.setattr(
        pl, "parse_pmset_log",
        lambda t0, t1: [{"ts": 100, "duration_sec": 5, "reason": "rtc/Maintenance"}])
    tr = SessionTracker(conn)
    _insert(conn, 0, 50, -5.0, False, False)
    tr.feed(0, 50, False, False)
    _insert(conn, 15, 50, -5.0, False, False)
    tr.feed(15, 50, False, False)
    _insert(conn, 5000, 50, -5.0, False, False)  # gap > 90s, soc unchanged
    tr.feed(5000, 50, False, False)
    assert conn.execute("SELECT COUNT(*) FROM dark_wakes").fetchone()[0] == 0


def test_dark_wakes_records_abnormal_drain(conn, monkeypatch):
    # A gap with abnormal drain (>= ABNORMAL_DRAIN_PCT) is recorded as one row:
    # gap duration, and the longest wake as the primary reason.
    import batmond.parsers.pmset_log as pl
    monkeypatch.setattr(
        pl, "parse_pmset_log",
        lambda t0, t1: [
            {"ts": 100, "duration_sec": 40, "reason": "wifibt"},
            {"ts": 200, "duration_sec": 5, "reason": "rtc/Maintenance"},
        ])
    tr = SessionTracker(conn)
    _insert(conn, 0, 60, -5.0, False, False)
    tr.feed(0, 60, False, False)
    _insert(conn, 15, 60, -5.0, False, False)
    tr.feed(15, 60, False, False)
    _insert(conn, 5000, 53, -5.0, False, False)  # gap > 90s, 7% drop (abnormal)
    tr.feed(5000, 53, False, False)
    rows = conn.execute(
        "SELECT reason, duration_sec, wh_drained FROM dark_wakes ORDER BY ts").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "wifibt"          # longest wake
    assert rows[0][1] == 5000 - 15         # gap duration, not the 40s wake
    assert rows[0][2] > 0


def test_dark_wakes_skips_normal_drain(conn, monkeypatch):
    # A gap that lost only 2% (normal self-discharge) is not flagged.
    import batmond.parsers.pmset_log as pl
    monkeypatch.setattr(
        pl, "parse_pmset_log",
        lambda t0, t1: [{"ts": 100, "duration_sec": 40, "reason": "wifibt"}])
    tr = SessionTracker(conn)
    _insert(conn, 0, 60, -5.0, False, False)
    tr.feed(0, 60, False, False)
    _insert(conn, 15, 60, -5.0, False, False)
    tr.feed(15, 60, False, False)
    _insert(conn, 5000, 58, -5.0, False, False)  # gap > 90s, 2% drop (normal)
    tr.feed(5000, 58, False, False)
    assert conn.execute("SELECT COUNT(*) FROM dark_wakes").fetchone()[0] == 0


def test_dangling_session_closed_on_restart(conn):
    tr = SessionTracker(conn)
    _insert(conn, 0, 50, -5.0, False, False)
    tr.feed(0, 50, False, False)
    _insert(conn, 15, 49, -5.0, False, False)
    tr.feed(15, 49, False, False)
    del tr                                    # daemon "crash"
    tr2 = SessionTracker(conn)                # restart
    row = conn.execute(
        "SELECT ended, soc_end FROM sessions WHERE id=1").fetchone()
    assert row == (15, 49)


def test_dark_wake_feed_written_for_abnormal(conn, monkeypatch):
    import json
    import batmond.parsers.pmset_log as pl
    import batmond.parsers.sleep_culprits as sc
    from batmond.db import get_state
    monkeypatch.setattr(
        pl, "parse_pmset_log",
        lambda t0, t1: [{"ts": 100, "duration_sec": 40, "reason": "wifibt"}])
    monkeypatch.setattr(
        sc, "parse_sleep_culprits",
        lambda t0, t1: [{"proc": "MSTeams", "why": "kept-awake", "n": 2}])
    tr = SessionTracker(conn)
    _insert(conn, 0, 60, -5.0, False, False)
    tr.feed(0, 60, False, False)
    _insert(conn, 15, 60, -5.0, False, False)
    tr.feed(15, 60, False, False)
    _insert(conn, 5000, 53, -5.0, False, False)  # 7% drop -> abnormal
    tr.feed(5000, 53, False, False)
    feed = json.loads(get_state(conn, "dark_wakes"))
    assert len(feed) == 1
    assert feed[0]["reason"] == "wifibt"
    assert feed[0]["duration_sec"] == 5000 - 15
    assert feed[0]["culprits"] == [{"proc": "MSTeams", "why": "kept-awake", "n": 2}]


def test_dark_wake_feed_not_written_for_normal(conn, monkeypatch):
    import batmond.parsers.pmset_log as pl
    import batmond.parsers.sleep_culprits as sc
    from batmond.db import get_state
    monkeypatch.setattr(
        pl, "parse_pmset_log",
        lambda t0, t1: [{"ts": 100, "duration_sec": 40, "reason": "wifibt"}])
    monkeypatch.setattr(sc, "parse_sleep_culprits", lambda t0, t1: [])
    tr = SessionTracker(conn)
    _insert(conn, 0, 60, -5.0, False, False)
    tr.feed(0, 60, False, False)
    _insert(conn, 15, 60, -5.0, False, False)
    tr.feed(15, 60, False, False)
    _insert(conn, 5000, 58, -5.0, False, False)  # 2% drop -> normal
    tr.feed(5000, 58, False, False)
    assert get_state(conn, "dark_wakes") is None
