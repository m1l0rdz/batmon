"""Read-only SQL for the API. Every function takes an open RO connection."""
import json
from datetime import datetime

from batmond.sessions import integrate

# Pseudo-processes: real energy (kept in attribution totals) but noise in
# "what is eating my battery" lists. Hidden unless include_system is set.
# DEAD_TASKS appears in rows written before the rename to "(terminated)".
SYSTEM_APPS = ("DEAD_TASKS", "(terminated)", "kernel_task")
_SYS_PH = ",".join("?" * len(SYSTEM_APPS))


def latest_sample(conn):
    row = conn.execute(
        "SELECT ts, soc_pct, current_ma, voltage_mv, watts, is_charging,"
        " on_ac, temp_c, brightness_pct, assert_awake FROM battery_samples"
        " ORDER BY ts DESC LIMIT 1").fetchone()
    if row is None:
        return None
    keys = ["ts", "soc_pct", "current_ma", "voltage_mv", "watts",
            "is_charging", "on_ac", "temp_c", "brightness_pct",
            "assert_awake"]
    return dict(zip(keys, row))

def recent_watts(conn, now_ts, minutes=60):
    rows = conn.execute(
        "SELECT watts FROM battery_samples WHERE ts >= ? ORDER BY ts",
        (now_ts - minutes * 60,)
    ).fetchall()
    return [r[0] for r in rows]


def get_state_val(conn, key: str):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

def todays_peak_soc(conn, now_ts):
    """Highest SoC seen since local midnight. None if no samples today."""
    midnight = datetime.fromtimestamp(now_ts).replace(
        hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        "SELECT MAX(soc_pct) FROM battery_samples WHERE ts >= ?",
        (int(midnight.timestamp()),)).fetchone()
    return row[0] if row and row[0] is not None else None


def charge_limit_status(conn, now_ts):
    """Read-only mirror of the native macOS 80% charge limit.

    batmon cannot set the limit on Apple Silicon (no accessible SMC key); the
    user toggles it in System Settings. We report the supported level and infer
    whether it is holding from today's peak charge: <=82 holding, >85 off, the
    band between is inconclusive (None)."""
    peak = todays_peak_soc(conn, now_ts)
    holding = None
    if peak is not None:
        if peak <= 82:
            holding = True
        elif peak > 85:
            holding = False
    return {"level": 80, "control": "system_settings",
            "todays_peak_soc": peak, "holding": holding}


def forecast(conn):
    row = conn.execute(
        "SELECT value FROM state WHERE key='forecast'").fetchone()
    return json.loads(row[0]) if row else None


def top_apps_last_hour(conn, now_ts, limit=5):
    rows = conn.execute(
        "SELECT app, SUM(attributed_mwh) AS mwh FROM app_energy"
        f" WHERE ts_minute >= ? AND app NOT IN ({_SYS_PH})"
        " GROUP BY app ORDER BY mwh DESC LIMIT ?",
        (now_ts - 3600,) + SYSTEM_APPS + (limit,)).fetchall()
    return [{"app": a, "attributed_wh": m / 1000.0} for a, m in rows]


def latest_component(conn):
    row = conn.execute(
        "SELECT ts_minute, cpu_mw, gpu_mw, ane_mw, package_mw,"
        " thermal_pressure, soc_temp_c, ssd_temp_c FROM component_power"
        " ORDER BY ts_minute DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return dict(zip(["ts_minute", "cpu_mw", "gpu_mw", "ane_mw",
                     "package_mw", "thermal_pressure", "soc_temp_c", "ssd_temp_c"], row))


def health_now(conn):
    row = conn.execute(
        "SELECT value FROM state WHERE key='health_now'").fetchone()
    return json.loads(row[0]) if row else None


def connected_devices(conn):
    row = conn.execute(
        "SELECT value FROM state WHERE key='connected_devices'").fetchone()
    return json.loads(row[0]) if row else []

def radio_warnings(conn):
    row = conn.execute(
        "SELECT value FROM state WHERE key='radio_warnings'").fetchone()
    return json.loads(row[0]) if row else []


def dark_wakes(conn):
    row = conn.execute(
        "SELECT value FROM state WHERE key='dark_wakes'").fetchone()
    return json.loads(row[0]) if row else []


def frequent_culprit(conn):
    # Repeat offender across the recent feed: the process appearing in the most
    # records; reported only when it shows up in >= 2 of them.
    from collections import Counter
    seen = Counter()
    for rec in dark_wakes(conn):
        for proc in {c["proc"] for c in rec.get("culprits", [])}:
            seen[proc] += 1
    if not seen:
        return None
    proc, n = seen.most_common(1)[0]
    return {"proc": proc, "n": n} if n >= 2 else None


def current_session(conn, now_ts):
    row = conn.execute(
        "SELECT kind, started, soc_start FROM sessions WHERE ended IS NULL"
        " ORDER BY started DESC LIMIT 1").fetchone()
    if row is None:
        return None
    kind, started, soc_start = row
    wh_in, wh_out, _, _ = integrate(conn, started, now_ts)
    last = conn.execute(
        "SELECT soc_pct FROM battery_samples ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    return {"kind": kind, "started": started,
            "duration_sec": now_ts - started, "soc_start": soc_start,
            "soc_now": last[0] if last else None,
            "wh": wh_out if kind == "battery" else wh_in}


def history(conn, rng: str, now_ts: int):
    if rng == "24h":
        battery = [dict(zip(["ts", "soc_pct", "watts", "assert_awake", "temp_c"], r))
                   for r in conn.execute(
                       "SELECT ts - ts % 60, AVG(soc_pct), AVG(watts), MAX(assert_awake), AVG(temp_c)"
                       " FROM battery_samples WHERE ts >= ?"
                       " GROUP BY ts - ts % 60 ORDER BY 1",
                       (now_ts - 86400,))]
        components = [dict(zip(
            ["ts", "cpu_mw", "gpu_mw", "ane_mw", "package_mw", "soc_temp_c", "ssd_temp_c"], r))
            for r in conn.execute(
                "SELECT ts_minute, cpu_mw, gpu_mw, ane_mw, package_mw, soc_temp_c, ssd_temp_c"
                " FROM component_power WHERE ts_minute >= ?"
                " ORDER BY ts_minute", (now_ts - 86400,))]
        temp_by_min = {b["ts"]: b["temp_c"] for b in battery}
        temperature = [{"ts": c["ts"], "soc_temp_c": c["soc_temp_c"],
                        "ssd_temp_c": c["ssd_temp_c"],
                        "temp_c": temp_by_min.get(c["ts"])} for c in components]
        return {"battery": battery, "components": components,
                "temperature": temperature}
    days = 7 if rng == "7d" else 30
    rows = conn.execute(
        "SELECT hour, soc_min, soc_max, avg_watts, avg_cpu_mw, avg_gpu_mw,"
        " avg_ane_mw, avg_package_mw, wh_in, wh_out, avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c"
        " FROM rollup_hourly_battery WHERE hour >= ? ORDER BY hour",
        (now_ts - days * 86400,)).fetchall()
    battery = [dict(zip(["ts", "soc_min", "soc_max", "watts", "temp_c"], (r[0], r[1], r[2], r[3], r[10])))
               for r in rows]
    components = [dict(zip(
        ["ts", "cpu_mw", "gpu_mw", "ane_mw", "package_mw", "soc_temp_c", "ssd_temp_c"],
        (r[0],) + r[4:8] + (r[11], r[12]))) for r in rows]
    temperature = [{"ts": r[0], "temp_c": r[10], "soc_temp_c": r[11],
                    "ssd_temp_c": r[12]} for r in rows]
    return {"battery": battery, "components": components,
            "temperature": temperature}


# Ranges served from raw app_energy (kept 48h) vs hourly rollups (90d).
APP_RAW_WINDOWS = {"1h": 3600, "8h": 8 * 3600, "24h": 86400}


def apps(conn, rng: str, now_ts: int, include_system: bool = False):
    exclude = "" if include_system else f" AND app NOT IN ({_SYS_PH})"
    params = () if include_system else SYSTEM_APPS
    if rng in APP_RAW_WINDOWS:
        rows = conn.execute(
            "SELECT app, SUM(attributed_mwh) FROM app_energy"
            f" WHERE ts_minute >= ?{exclude} GROUP BY app ORDER BY 2 DESC",
            (now_ts - APP_RAW_WINDOWS[rng],) + params).fetchall()
    else:
        days = 7 if rng == "7d" else 30
        rows = conn.execute(
            "SELECT app, SUM(attributed_mwh) FROM rollup_hourly_apps"
            f" WHERE hour >= ?{exclude} GROUP BY app ORDER BY 2 DESC",
            (now_ts - days * 86400,) + params).fetchall()
    total = sum((m for _, m in rows)) if rows else 1.0
    if total == 0:
        total = 1.0
    return [{"app": a, "attributed_wh": m / 1000.0,
             "share_pct": m / total * 100.0} for a, m in rows]


def _day_key_local(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def energy(conn, rng: str, now_ts: int):
    """Energy in/out buckets: hourly for 24h/7d, daily for 30d. The
    current bucket is integrated live from raw samples (rollups lag up
    to an hour) and flagged partial."""
    hour = now_ts - now_ts % 3600
    keys = ["ts", "wh_in", "wh_out", "on_battery_sec", "on_ac_sec",
            "avg_brightness"]
    wh_in, wh_out, bat, ac = integrate(conn, hour, now_ts)
    br = conn.execute(
        "SELECT AVG(brightness_pct) FROM battery_samples WHERE ts >= ?",
        (hour,)).fetchone()[0]
    partial = dict(zip(keys, (hour, wh_in, wh_out, bat, ac, br)))
    partial["partial"] = True
    if rng in ("24h", "7d"):
        since = now_ts - (86400 if rng == "24h" else 7 * 86400)
        out = [dict(zip(keys, r)) for r in conn.execute(
            "SELECT hour, wh_in, wh_out, on_battery_sec, on_ac_sec,"
            " avg_brightness FROM rollup_hourly_battery"
            " WHERE hour >= ? AND hour < ? ORDER BY hour", (since, hour))]
        if bat or ac:
            out.append(partial)
        return out
    today = _day_key_local(now_ts)
    out = [dict(zip(["day"] + keys[1:], r)) for r in conn.execute(
        "SELECT day, wh_in, wh_out, on_battery_sec, on_ac_sec,"
        " avg_brightness FROM rollup_daily_battery WHERE day >= ?"
        " AND day < ? ORDER BY day",
        (_day_key_local(now_ts - 30 * 86400), today))]
    midnight = int(datetime.fromtimestamp(now_ts).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    trow = conn.execute(
        "SELECT SUM(wh_in), SUM(wh_out), SUM(on_battery_sec),"
        " SUM(on_ac_sec), AVG(avg_brightness) FROM rollup_hourly_battery"
        " WHERE hour >= ? AND hour < ?",
        (midnight, hour)).fetchone()
    tvals = [v or 0 for v in trow[:4]]
    if any(tvals) or bat or ac:
        out.append({"day": today,
                    "wh_in": tvals[0] + wh_in, "wh_out": tvals[1] + wh_out,
                    "on_battery_sec": tvals[2] + bat,
                    "on_ac_sec": tvals[3] + ac,
                    "avg_brightness": trow[4] if trow[4] is not None else br,
                    "partial": True})
    for row in out:
        row["ts"] = row.pop("day") if "day" in row else row["ts"]
    return out


def status(conn, now_ts: int):
    hb = conn.execute(
        "SELECT value FROM state WHERE key='heartbeat'").fetchone()
    last_sample = conn.execute(
        "SELECT MAX(ts) FROM battery_samples").fetchone()[0]
    last_pm = conn.execute(
        "SELECT MAX(ts_minute) FROM component_power").fetchone()[0]
    rolled = conn.execute(
        "SELECT value FROM state WHERE key='rollup_hourly_done'").fetchone()
    return {"heartbeat": int(hb[0]) if hb else None,
            "last_sample_ts": last_sample,
            "last_powermetrics_ts": last_pm,
            "rollup_hourly_done": int(rolled[0]) if rolled else None,
            "forecast": forecast(conn),
            "now_ts": now_ts}


def health(conn):
    return [dict(zip(["day", "cycle_count", "max_capacity_pct",
                      "design_capacity_mah"], r))
            for r in conn.execute(
                "SELECT day, cycle_count, max_capacity_pct,"
                " design_capacity_mah FROM battery_health_daily"
                " ORDER BY day")]


def charging(conn):
    sessions = [dict(zip(["id", "kind", "started", "ended", "soc_start",
                          "soc_end", "wh"], r))
                for r in conn.execute(
                    "SELECT id, kind, started, ended, soc_start, soc_end,"
                    " wh FROM sessions ORDER BY started DESC LIMIT 200")]
    agg = conn.execute(
        "SELECT SUM(CASE WHEN kind='battery' THEN ended-started END),"
        " SUM(CASE WHEN kind!='battery' THEN ended-started END),"
        " AVG(CASE WHEN kind='charging' AND ended>started"
        "     THEN wh/((ended-started)/3600.0) END)"
        " FROM sessions WHERE ended IS NOT NULL").fetchone()
    depth_hist = {}
    for (drop,) in conn.execute(
            "SELECT soc_start - soc_end FROM sessions"
            " WHERE kind='battery' AND soc_end IS NOT NULL"):
        bucket = f"{int(drop // 10) * 10}-{int(drop // 10) * 10 + 10}%"
        depth_hist[bucket] = depth_hist.get(bucket, 0) + 1
    return {"sessions": sessions,
            "aggregates": {"battery_sec": agg[0] or 0,
                           "ac_sec": agg[1] or 0,
                           "avg_charge_watts": agg[2],
                           "discharge_depth_hist": depth_hist}}


def anomalies_since(conn, since_id: int):
    results = []
    for r in conn.execute(
        "SELECT id, ts, day, app, wh_today, wh_baseline, ratio, detail"
        " FROM anomalies WHERE id > ? ORDER BY id", (since_id,)
    ):
        d = dict(zip(["id", "ts", "day", "app", "wh_today", "wh_baseline", "ratio", "detail"], r))
        if d["detail"] is not None:
            try:
                d["detail"] = json.loads(d["detail"])
            except json.JSONDecodeError:
                d["detail"] = None
        results.append(d)
    return results
