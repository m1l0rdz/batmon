import re
import stat
import time
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from batmond.__main__ import main as batmond_main
from batmon_web.app import create_app

FIXTURES = str(Path(__file__).parent / "fixtures")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    db_path = str(tmp / "api.db")
    batmond_main(["--dry-run", "--fixtures", FIXTURES, "--db", db_path,
                  "--ticks", "480"])
    stub = tmp / "caffeinate_stub"
    stub.write_text("#!/bin/bash\ntrap 'exit 0' TERM\n"
                    "while true; do sleep 1; done\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    app = create_app(db_path, caffeinate_bin=str(stub))
    with TestClient(app) as c:
        yield c


def test_now(client):
    d = client.get("/api/now").json()
    assert {"sample", "staleness_sec", "forecast", "top_apps",
            "awake", "component", "health", "session"} <= d.keys()
    assert 0 <= d["sample"]["soc_pct"] <= 100
    assert d["staleness_sec"] < 300
    assert isinstance(d["top_apps"], list)
    assert d["awake"] is False


def test_now_component_health_session(client):
    d = client.get("/api/now").json()
    comp = d["component"]
    assert comp is not None
    assert {"ts_minute", "cpu_mw", "gpu_mw", "ane_mw", "package_mw",
            "thermal_pressure"} <= comp.keys()
    h = d["health"]
    assert h is not None
    assert h["cycle_count"] >= 0
    assert h["max_capacity_pct"] > 0
    assert h["raw_current_capacity_mah"] > 0
    s = d["session"]
    assert s is not None
    assert s["kind"] in ("battery", "charging", "full")
    assert s["duration_sec"] >= 0


def test_now_top_apps_hide_system(client):
    d = client.get("/api/now").json()
    names = {a["app"] for a in d["top_apps"]}
    assert not names & {"DEAD_TASKS", "(terminated)", "kernel_task"}


def test_history_ranges(client):
    for rng in ("24h", "7d", "30d"):
        d = client.get(f"/api/history?range={rng}").json()
        assert "battery" in d and "components" in d
    assert client.get("/api/history?range=99y").status_code == 422


def test_apps(client):
    d = client.get("/api/apps?range=24h").json()
    assert isinstance(d, list) and d
    assert {"app", "attributed_wh", "share_pct"} <= d[0].keys()
    total_share = sum(a["share_pct"] for a in d)
    assert total_share == pytest.approx(100.0, abs=1.0)


def test_apps_all_ranges(client):
    for rng in ("1h", "8h", "24h", "7d", "30d"):
        d = client.get(f"/api/apps?range={rng}").json()
        assert isinstance(d, list), rng
    assert client.get("/api/apps?range=99y").status_code == 422


def test_apps_shorter_window_subset_of_longer(client):
    """1h window sums <= 8h window sums for the same app (monotone)."""
    h1 = {a["app"]: a["attributed_wh"]
          for a in client.get("/api/apps?range=1h").json()}
    h8 = {a["app"]: a["attributed_wh"]
          for a in client.get("/api/apps?range=8h").json()}
    for app, wh in h1.items():
        assert wh <= h8.get(app, 0) + 1e-9, app


def test_apps_include_system_param(client):
    hidden = client.get("/api/apps?range=24h").json()
    full = client.get("/api/apps?range=24h&include_system=true").json()
    assert len(full) >= len(hidden)
    assert not {a["app"] for a in hidden} & {
        "DEAD_TASKS", "(terminated)", "kernel_task"}


def test_energy(client):
    for rng in ("24h", "7d", "30d"):
        d = client.get(f"/api/energy?range={rng}").json()
        assert isinstance(d, list), rng
    d = client.get("/api/energy?range=24h").json()
    assert d
    assert {"ts", "wh_in", "wh_out", "on_battery_sec",
            "on_ac_sec"} <= d[0].keys()
    assert client.get("/api/energy?range=99y").status_code == 422


def test_status(client):
    d = client.get("/api/status").json()
    assert {"heartbeat", "last_sample_ts", "last_powermetrics_ts",
            "forecast", "db_size_bytes"} <= d.keys()
    assert d["heartbeat"] is not None
    assert d["db_size_bytes"] > 0


def test_health_charging_anomalies(client):
    assert isinstance(client.get("/api/health").json(), list)
    d = client.get("/api/charging").json()
    assert "sessions" in d and "aggregates" in d
    assert client.get("/api/anomalies?since=0").json() == []


def test_awake_toggle(client):
    assert client.post("/api/awake", json={"on": True}).json() == {
        "awake": True}
    assert client.get("/api/now").json()["awake"] is True
    assert client.post("/api/awake", json={"on": False}).json() == {
        "awake": False}


def test_charge_limit(client):
    # Read-only mirror of the native 80% limit.
    d = client.get("/api/charge_limit").json()
    assert d["level"] == 80
    assert d["control"] == "system_settings"
    assert "todays_peak_soc" in d
    assert d["holding"] in (True, False, None)
    # No write endpoint: POST must not be allowed.
    assert client.post("/api/charge_limit",
                       json={"enabled": True, "limit": 75}).status_code == 405
    # Same payload surfaces in /api/now.
    assert client.get("/api/now").json()["charge_limit"] == d


def test_todays_peak_soc_and_holding(tmp_path):
    from batmond import db
    from batmon_web import queries
    conn = db.open_rw(str(tmp_path / "peak.db"))
    now = int(time.time())
    # Empty DB: no peak, holding unknown.
    assert queries.todays_peak_soc(conn, now) is None
    st = queries.charge_limit_status(conn, now)
    assert st == {"level": 80, "control": "system_settings",
                  "todays_peak_soc": None, "holding": None}
    ins = ("INSERT INTO battery_samples(ts, soc_pct, is_charging, on_ac)"
           " VALUES (?, ?, 0, 1)")
    # Charge stays <=80: limit is holding.
    for i, soc in enumerate((70, 78, 80)):
        conn.execute(ins, (now - 600 + i, soc))
    conn.commit()
    assert queries.todays_peak_soc(conn, now) == 80
    assert queries.charge_limit_status(conn, now)["holding"] is True
    # A high sample appears: limit is off.
    conn.execute(ins, (now - 100, 92))
    conn.commit()
    assert queries.charge_limit_status(conn, now)["holding"] is False
    conn.close()


def test_apps_action(client, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(a[0]))
    r = client.post("/api/apps/action", json={"app": "testapp", "action": "pause"})
    assert r.status_code == 200
    assert called[0] == ["/usr/bin/pkill", "-STOP", "-f", "testapp"]


def test_apps_action_refuses_self(client, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(a[0]))
    # A name matching batmon's own stack must be rejected, not signalled.
    r = client.post("/api/apps/action", json={"app": "Python", "action": "kill"})
    assert r.status_code == 400
    assert called == []


def test_apps_action_escapes_regex(client, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(a[0]))
    # Regex metacharacters in a process name must be matched literally.
    r = client.post("/api/apps/action",
                    json={"app": "ev)il.*", "action": "pause"})
    assert r.status_code == 200
    assert called[0] == ["/usr/bin/pkill", "-STOP", "-f", re.escape("ev)il.*")]


def test_open_battery_settings(client, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(a[0]))
    r = client.post("/api/open_battery_settings",
                    headers={"X-Batmon-Client": "1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert called[0] == [
        "/usr/bin/open",
        "x-apple.systempreferences:com.apple.Battery-Settings.extension"]


def test_open_battery_settings_requires_client_header(client, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: called.append(a[0]))
    # No client header = a CORS-simple cross-origin POST; must be refused.
    r = client.post("/api/open_battery_settings")
    assert r.status_code == 403
    assert called == []


def test_db_unavailable_503(tmp_path):
    app = create_app(str(tmp_path / "missing.db"))
    with TestClient(app) as c:
        assert c.get("/api/now").status_code == 503

def test_habits(client):
    d = client.get("/api/habits").json()
    assert d["window_days"] == 30
    assert {"full_pct_of_ac", "ac_share_pct", "deep_discharges",
            "overnight_sessions", "cycles_30d", "avg_temp_c"} <= d.keys()
    assert isinstance(d["deep_discharges"], int)

def test_advisor(client):
    d = client.get("/api/advisor").json()
    assert {"score", "grade", "components", "recommendations",
            "habits"} <= d.keys()
    if d["score"] is not None:
        assert 0 <= d["score"] <= 100
    for r in d["recommendations"]:
        assert {"id", "severity", "title", "body"} <= r.keys()


def test_now_has_score(client):
    d = client.get("/api/now").json()
    assert "score" in d
    assert {"score", "grade"} <= d["score"].keys()

def test_health_forecast(client):
    d = client.get("/api/health/forecast").json()
    assert d["status"] in ("ok", "insufficient_data")

def test_report(client):
    d = client.get("/api/report").json()
    assert {"wh_in", "wh_out", "on_battery_h", "on_ac_h", "top_apps",
            "sessions_battery", "sessions_charging", "deep_discharges",
            "anomaly_count", "score"} <= d.keys()
    assert isinstance(d["top_apps"], list)
