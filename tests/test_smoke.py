from pathlib import Path

from fastapi.testclient import TestClient

from batmond.__main__ import main as batmond_main
from batmon_web.app import create_app

FIXTURES = str(Path(__file__).parent / "fixtures")


def test_dry_run_to_api_smoke(tmp_path):
    db_path = str(tmp_path / "smoke.db")
    batmond_main(["--dry-run", "--fixtures", FIXTURES, "--db", db_path,
                  "--ticks", "480"])
    with TestClient(create_app(db_path)) as client:
        for ep in ("/api/now", "/api/history?range=24h",
                   "/api/history?range=7d", "/api/history?range=30d",
                   "/api/apps?range=24h", "/api/apps?range=30d",
                   "/api/health", "/api/charging", "/api/anomalies?since=0"):
            r = client.get(ep)
            assert r.status_code == 200, ep
        now = client.get("/api/now").json()
        assert now["staleness_sec"] < 300
        assert now["forecast"] is not None
