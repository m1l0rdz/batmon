"""Read-only SQL for the API. Every function takes an open RO connection."""
import json
from datetime import datetime, timedelta
from statistics import median

from batmond.sessions import integrate
from batmon_web.charge_policy import read_policy

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
    """Read the native charge-limit policy without guessing from observed SoC.

    A low observed peak does not prove that a charge limit is configured.
    powerd drops the manualChargeLimit policy on unplug, so an empty archive
    while on battery means unknown, not off.
    """
    policy = read_policy()
    holding = policy['holding']
    source = "system_policy" if holding is not None else "unavailable"
    last = latest_sample(conn)
    if holding is False and last is not None and not last["on_ac"]:
        holding, source = None, "on_battery"
    peak = todays_peak_soc(conn, now_ts)
    return {"level": policy['level'], "control": "system_settings",
            "todays_peak_soc": peak, "holding": holding, "source": source}



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


def charging(conn, now_ts=None):
    import time
    now_ts = int(time.time()) if now_ts is None else now_ts
    since = now_ts - 30 * 86400
    sessions = [dict(zip(["id", "kind", "started", "ended", "soc_start",
                          "soc_end", "wh"], r))
                for r in conn.execute(
                    "SELECT id, kind, started, ended, soc_start, soc_end, wh"
                    " FROM sessions WHERE started < ? AND COALESCE(ended, ?) > ?"
                    " ORDER BY started DESC LIMIT 200", (now_ts, now_ts, since))]
    # Durations are clipped to the same 30-day window, unlike the old lifetime
    # aggregate. Open segments stop at the last recorded sample.
    last = latest_sample(conn)
    live_end = min(now_ts, last["ts"]) if last else now_ts
    bat = ac = charge_wh = charge_sec = 0
    depth_hist = {}
    for kind, started, ended, ss, se, wh in conn.execute(
            "SELECT kind, started, ended, soc_start, soc_end, wh FROM sessions"
            " WHERE started < ? AND COALESCE(ended, ?) > ?", (now_ts, live_end, since)):
        duration = max(0, min(ended if ended is not None else live_end, now_ts) - max(started, since))
        if kind == "battery":
            bat += duration
            if se is not None and ss - se > 0 and started >= since and ended <= now_ts:
                drop = min(99.999, ss - se)
                lo = int(drop // 10) * 10
                bucket = f"{lo}-{lo + 10}"
                depth_hist[bucket] = depth_hist.get(bucket, 0) + 1
        else:
            ac += duration
        if kind == "charging" and ended and started >= since and ended <= now_ts and wh is not None and duration > 0:
            charge_wh += wh
            charge_sec += duration
    return {"window_days": 30, "sessions": sessions,
            "aggregates": {"battery_sec": bat, "ac_sec": ac,
                           "avg_charge_watts": charge_wh / (charge_sec / 3600) if charge_sec else None,
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


OVERNIGHT_MIN_SEC = 2 * 3600  # ignore short evening top-ups


def charging_habits(conn, now_ts: int) -> dict:
    """30-day observed habits. 'full' means AC not charging, not 100% SoC.

    High-charge exposure is an endpoint-based estimate over noncharging AC
    segments with both endpoints >=95%; it cannot resolve within-segment dips.
    """
    from batmon_web.insights import low_charge_episodes
    since = now_ts - 30 * 86400
    last = latest_sample(conn)
    live_end = min(now_ts, last["ts"]) if last else now_ts
    full_sec = session_ac_sec = 0
    overnight = 0
    for kind, started, ended, ss, se in conn.execute(
            "SELECT kind, started, ended, soc_start, soc_end FROM sessions"
            " WHERE kind != 'battery' AND started < ? AND COALESCE(ended, ?) > ?",
            (now_ts, live_end, since)):
        stop = min(ended if ended is not None else live_end, now_ts)
        duration = max(0, stop - max(started, since))
        session_ac_sec += duration
        endpoint = se if ended is not None else (last["soc_pct"] if last else None)
        if kind == 'full' and endpoint is not None and min(ss, endpoint) >= 95:
            full_sec += duration
        hour = datetime.fromtimestamp(started).hour
        if started >= since and duration >= OVERNIGHT_MIN_SEC and (hour >= 22 or hour < 6):
            overnight += 1
    ac_sec, bat_sec = conn.execute(
        "SELECT COALESCE(SUM(on_ac_sec), 0), COALESCE(SUM(on_battery_sec), 0)"
        " FROM rollup_hourly_battery WHERE hour >= ? AND hour < ?", (since, now_ts)).fetchone()
    since_day = _day_key_local(since)
    today = _day_key_local(now_ts)
    cyc = conn.execute(
        "SELECT MIN(cycle_count), MAX(cycle_count), COUNT(cycle_count)"
        " FROM battery_health_daily WHERE day >= ? AND day <= ?"
        " AND cycle_count IS NOT NULL", (since_day, today)).fetchone()
    cycles_30d = cyc[1] - cyc[0] if cyc and cyc[2] >= 2 else None
    temp = conn.execute(
        "SELECT SUM(avg_temp_c * (COALESCE(on_ac_sec,0)+COALESCE(on_battery_sec,0))),"
        " SUM(COALESCE(on_ac_sec,0)+COALESCE(on_battery_sec,0))"
        " FROM rollup_hourly_battery WHERE hour >= ? AND hour < ? AND avg_temp_c IS NOT NULL",
        (since, now_ts)).fetchone()
    total = ac_sec + bat_sec
    has_battery_segments = conn.execute(
        "SELECT 1 FROM sessions WHERE kind='battery' AND started < ?"
        " AND COALESCE(ended, ?) > ? AND COALESCE(ended, ?) > started LIMIT 1",
        (now_ts, live_end, since, live_end)).fetchone() is not None
    return {
        "window_days": 30,
        "full_pct_of_ac": full_sec / session_ac_sec * 100 if session_ac_sec else None,
        "high_charge_h": full_sec / 3600,
        "high_charge_basis": "noncharging_ac_segment_endpoints_ge_95",
        "ac_share_pct": ac_sec / total * 100 if total else None,
        "deep_discharges": low_charge_episodes(conn, since, now_ts) if has_battery_segments else None,
        "overnight_sessions": overnight,
        "cycles_30d": cycles_30d,
        "avg_temp_c": temp[0] / temp[1] if temp[1] else None,
        "temperature_observed_h": (temp[1] or 0) / 3600,
        "observed_h": total / 3600,
    }


def avg_brightness_7d(conn, now_ts: int):
    return conn.execute(
        "SELECT AVG(avg_brightness) FROM rollup_hourly_battery"
        " WHERE hour >= ?", (now_ts - 7 * 86400,)).fetchone()[0]

MIN_PREDICTION_POINTS = 30
MIN_PREDICTION_SPAN_DAYS = 60
MIN_PREDICTION_WEEKS = 8
MIN_PREDICTION_R2 = 0.25
MAX_PREDICTION_SLOPE_PCT_PER_DAY = 0.05


def _prediction_coverage(rows):
    points = len(rows)
    first_day = rows[0][0] if rows else None
    last_day = rows[-1][0] if rows else None
    if rows:
        first = datetime.strptime(first_day, "%Y-%m-%d")
        last = datetime.strptime(last_day, "%Y-%m-%d")
        span_days = (last - first).days
    else:
        last = None
        span_days = 0
    remaining_points = max(0, MIN_PREDICTION_POINTS - points)
    remaining_span_days = max(0, MIN_PREDICTION_SPAN_DAYS - span_days)
    remaining_days = max(remaining_points, remaining_span_days)
    return {
        "days": points,
        "points": points,
        "span_days": span_days,
        "first_day": first_day,
        "last_day": last_day,
        "required_points": MIN_PREDICTION_POINTS,
        "required_span_days": MIN_PREDICTION_SPAN_DAYS,
        "required_weeks": MIN_PREDICTION_WEEKS,
        "remaining_points": remaining_points,
        "remaining_span_days": remaining_span_days,
        "estimated_ready_day": (
            (last + timedelta(days=remaining_days)).strftime("%Y-%m-%d")
            if last is not None and remaining_days else last_day
        ),
    }


def health_prediction(conn):
    """Robust linear trend over weekly median capacity observations."""
    rows = conn.execute(
        "SELECT day, max_capacity_pct FROM battery_health_daily"
        " WHERE max_capacity_pct IS NOT NULL ORDER BY day").fetchall()
    coverage = _prediction_coverage(rows)
    if not rows:
        return {"status": "insufficient_data", **coverage, "weeks": 0}

    d0 = datetime.strptime(rows[0][0], "%Y-%m-%d")
    xs = [(datetime.strptime(d, "%Y-%m-%d") - d0).days for d, _ in rows]
    ys = [c for _, c in rows]
    buckets = {}
    for x, y in zip(xs, ys):
        buckets.setdefault(x // 7, []).append((x, y))
    weekly = [(median(x for x, _ in values),
               median(y for _, y in values))
              for _, values in sorted(buckets.items())]
    coverage["weeks"] = len(weekly)
    coverage["remaining_weeks"] = max(
        0, MIN_PREDICTION_WEEKS - len(weekly))
    remaining_days = max(
        coverage["remaining_points"],
        coverage["remaining_span_days"],
        coverage["remaining_weeks"] * 7,
    )
    if coverage["last_day"] and remaining_days:
        last = datetime.strptime(coverage["last_day"], "%Y-%m-%d")
        coverage["estimated_ready_day"] = (
            last + timedelta(days=remaining_days)).strftime("%Y-%m-%d")
    if (len(rows) < MIN_PREDICTION_POINTS
            or coverage["span_days"] < MIN_PREDICTION_SPAN_DAYS
            or len(weekly) < MIN_PREDICTION_WEEKS):
        return {"status": "insufficient_data", **coverage}

    pair_slopes = [
        (y2 - y1) / (x2 - x1)
        for i, (x1, y1) in enumerate(weekly)
        for x2, y2 in weekly[i + 1:]
        if x2 != x1
    ]
    slope = median(pair_slopes) if pair_slopes else 0.0
    intercept = median(y - slope * x for x, y in weekly)
    residuals = [y - (intercept + slope * x) for x, y in weekly]
    mean_y = sum(y for _, y in weekly) / len(weekly)
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((y - mean_y) ** 2 for _, y in weekly)
    trend_r2 = 1.0 if ss_tot == 0 and ss_res == 0 else (
        1.0 - ss_res / ss_tot if ss_tot else 0.0)
    current_pct = median(ys[-7:])

    base = {
        **coverage,
        "current_pct": current_pct,
        "slope_pct_per_day": slope,
        "trend_r2": trend_r2,
        "method": "weekly_median_theil_sen",
    }
    if (abs(slope) > MAX_PREDICTION_SLOPE_PCT_PER_DAY
            or trend_r2 < MIN_PREDICTION_R2):
        return {"status": "unstable_trend", **base}

    def _proj(days_ahead):
        return max(0.0, min(100.0, current_pct + slope * days_ahead))

    return {"status": "ok", **base,
            "pct_in_1y": _proj(365), "pct_in_2y": _proj(730)}
