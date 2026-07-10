import pytest
from batmond import db
from batmon_web.queries import health_prediction


@pytest.fixture
def conn(tmp_path):
    return db.open_rw(str(tmp_path / "t.db"))


def _seed(conn, days, start_pct, slope_per_day):
    from datetime import date, timedelta
    d0 = date(2026, 1, 1)
    for i in range(days):
        conn.execute(
            "INSERT INTO battery_health_daily(day, cycle_count, max_capacity_pct)"
            " VALUES (?, ?, ?)",
            ((d0 + timedelta(days=i)).isoformat(), 100 + i,
             start_pct + slope_per_day * i))


def test_insufficient_when_few_points(conn):
    _seed(conn, 5, 100.0, 0.0)
    r = health_prediction(conn)
    assert r["status"] == "insufficient_data"
    assert r["days"] == 5


def test_insufficient_when_short_span(conn):
    _seed(conn, 20, 100.0, 0.0)  # 20 points but only 19-day span
    assert health_prediction(conn)["status"] == "insufficient_data"


def test_linear_decline_extrapolates(conn):
    _seed(conn, 40, 100.0, -0.01)  # -0.01 %/day over 40 days
    r = health_prediction(conn)
    assert r["status"] == "ok"
    assert r["slope_pct_per_day"] == pytest.approx(-0.01, abs=1e-6)
    assert r["pct_in_1y"] == pytest.approx(r["current_pct"] - 3.65, abs=0.01)


def test_flat_history_predicts_flat(conn):
    _seed(conn, 40, 95.0, 0.0)
    r = health_prediction(conn)
    assert r["status"] == "ok"
    assert r["pct_in_1y"] == pytest.approx(95.0)


def test_prediction_clamped_to_0_100(conn):
    _seed(conn, 40, 85.0, -0.5)  # absurd decline
    r = health_prediction(conn)
    assert r["pct_in_2y"] == 0.0
