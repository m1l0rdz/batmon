"""batmon-web (design 5.2): read-only DB, 127.0.0.1 only (bind is in the
uvicorn args, not here). Only state-changing endpoint: POST /api/awake."""
import os
import re
import sqlite3
import time
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from batmond.db import open_ro
from batmon_web import queries
from batmon_web.awake import AwakeManager

STATIC = Path(__file__).parent / "static"


class AwakeBody(BaseModel):
    on: bool


def create_app(db_path: str,
               caffeinate_bin: str = "/usr/bin/caffeinate") -> FastAPI:
    app = FastAPI(title="batmon")
    awake = AwakeManager(binary=caffeinate_bin)

    @contextmanager
    def db():
        try:
            conn = open_ro(db_path)
        except sqlite3.OperationalError:
            raise HTTPException(503, "database unavailable")
        try:
            yield conn
        except sqlite3.OperationalError:
            raise HTTPException(503, "database unavailable")
        finally:
            conn.close()

    @app.get("/api/now")
    def now():
        with db() as conn:
            sample = queries.latest_sample(conn)
            if sample is None:
                raise HTTPException(503, "no data yet")
            now_ts = int(time.time())
            return {"sample": sample,
                    "staleness_sec": now_ts - sample["ts"],
                    "forecast": queries.forecast(conn),
                    "top_apps": queries.top_apps_last_hour(conn, now_ts),
                    "component": queries.latest_component(conn),
                    "health": queries.health_now(conn),
                    "devices": queries.connected_devices(conn),
                    "session": queries.current_session(conn, now_ts),
                    "awake": awake.is_on(),
                    "charge_limit": queries.charge_limit_status(conn, now_ts),
                    "recent_watts": queries.recent_watts(conn, now_ts),
                    "lpm": queries.get_state_val(conn, "lpm"),
                    "radio_warnings": queries.radio_warnings(conn),
                    "dark_wakes": queries.dark_wakes(conn),
                    "frequent_culprit": queries.frequent_culprit(conn)}

    @app.get("/api/history")
    def history(range: Literal["24h", "7d", "30d"] = "24h"):
        with db() as conn:
            return queries.history(conn, range, int(time.time()))

    @app.get("/api/apps")
    def apps(range: Literal["1h", "8h", "24h", "7d", "30d"] = "24h",
             include_system: bool = False):
        with db() as conn:
            return queries.apps(conn, range, int(time.time()),
                                include_system)

    @app.get("/api/energy")
    def energy(range: Literal["24h", "7d", "30d"] = "24h"):
        with db() as conn:
            return queries.energy(conn, range, int(time.time()))

    @app.get("/api/status")
    def status():
        with db() as conn:
            d = queries.status(conn, int(time.time()))
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(db_path + suffix)
            except OSError:
                pass
        d["db_size_bytes"] = size
        return d

    @app.get("/api/health")
    def health():
        with db() as conn:
            return queries.health(conn)

    @app.get("/api/charging")
    def charging():
        with db() as conn:
            return queries.charging(conn)

    @app.get("/api/habits")
    def habits():
        with db() as conn:
            return queries.charging_habits(conn, int(time.time()))

    @app.get("/api/anomalies")
    def anomalies(since: int = 0):
        with db() as conn:
            return queries.anomalies_since(conn, since)

    @app.post("/api/awake")
    def set_awake(body: AwakeBody):
        return {"awake": awake.set(body.on)}

    @app.get("/api/charge_limit")
    def charge_limit():
        # Read-only mirror. macOS enforces the native 80% limit; batmon reports
        # the level and whether it is holding. batmon cannot set the limit - the
        # user toggles it in System Settings (opened via /api/open_battery_settings).
        with db() as conn:
            return queries.charge_limit_status(conn, int(time.time()))

    @app.post("/api/open_battery_settings")
    def open_battery_settings(request: Request):
        # No request body means this is a CORS "simple" request that a visited
        # web page could POST cross-origin without a preflight. Require a custom
        # header (which forces a preflight our no-CORS server never approves) so
        # only the same-origin dashboard / menu app can trigger it.
        if request.headers.get("x-batmon-client") != "1":
            raise HTTPException(403, "missing client header")
        # User-level convenience: open the Battery pane so the user can flip the
        # native limit. Same class as the caffeinate/awake action - runs in the
        # user's session, no root, no DB write, fixed URL (no input).
        subprocess.run(
            ["/usr/bin/open",
             "x-apple.systempreferences:com.apple.Battery-Settings.extension"],
            check=False)
        return {"ok": True}

    class AppActionBody(BaseModel):
        app: str
        action: Literal["pause", "resume", "kill"]

    # Never signal batmon's own processes: `pkill -f` matches the whole command
    # line, so a name like "Python" would otherwise SIGSTOP the web server and
    # menu app themselves. Refuse anything that could hit our own stack.
    SELF_GUARD = ("caffeinate", "uvicorn", "batmon", "batmond",
                  "python", "python3")

    @app.post("/api/apps/action")
    def app_action(body: AppActionBody):
        sig = {"pause": "-STOP", "resume": "-CONT", "kill": "-TERM"}[body.action]
        name = body.app.strip()
        low = name.lower()
        if not name or low in SELF_GUARD or any(g in low for g in SELF_GUARD):
            raise HTTPException(400, "refusing to signal that process")
        # re.escape so regex metacharacters in a process name are matched
        # literally (a crafted name cannot broaden the `pkill -f` pattern).
        subprocess.run(["/usr/bin/pkill", sig, "-f", re.escape(name)],
                       check=False)
        return {"success": True, "app": body.app, "action": body.action}

    class CmdBody(BaseModel):
        cmd: str
        args: dict = {}

    @app.post("/api/cmd")
    def post_cmd(body: CmdBody):
        if body.cmd not in ("lpm", "auto_lpm_threshold"):
            raise HTTPException(400, "Invalid command")
        if body.cmd == "lpm":
            if "enabled" not in body.args or not isinstance(body.args["enabled"], bool):
                raise HTTPException(400, "Invalid args for lpm")
        elif body.cmd == "auto_lpm_threshold":
            pct = body.args.get("pct")
            if not isinstance(pct, int) or pct < 0 or pct > 100:
                raise HTTPException(400, "Invalid args for auto_lpm_threshold")
        from batmon_web.api_cmd import spool_command
        filename = spool_command(body.cmd, body.args)
        return {"success": True, "spooled_file": filename}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
