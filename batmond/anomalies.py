"""Anomaly = today's attributed Wh > 2x trailing-7-day mean AND > 1.5 Wh.
Daemon only INSERTs rows; delivery is the SwiftBar plugin via the API (D5).
UNIQUE(day, app) enforces max one per app per day (D4). Cold start: app
needs >= 3 days of daily rollups before it can trigger (D7)."""
from __future__ import annotations

import json

from batmond.rollup import day_key

RATIO = 2.0
MIN_WH = 1.5
MIN_BASELINE_DAYS = 3
LOOKBACK_DAYS = 7
# Ratio is stored in SQLite and serialized by starlette with
# allow_nan=False: inf would 500 /api/anomalies. Cap keeps it JSON-safe.
RATIO_CAP = 999.0


def build_detail(conn, kind: str, now_ts: int, tz, ratio: float = None) -> str | None:
    try:
        culprits = []
        advice = ""
        SYSTEM_APPS = ("DEAD_TASKS", "(terminated)", "kernel_task")
        sys_ph = ",".join("?" * len(SYSTEM_APPS))
        
        if kind == "__SYSTEM_RAPID_DISCHARGE__":
            rows = conn.execute(
                "SELECT app, SUM(attributed_mwh) FROM app_energy "
                f"WHERE ts_minute >= ? AND app NOT IN ({sys_ph}) "
                "GROUP BY app ORDER BY 2 DESC LIMIT 3",
                (now_ts - 15 * 60,) + SYSTEM_APPS
            ).fetchall()
            culprits = [{"app": r[0], "wh": float(r[1] / 1000.0)} for r in rows if r[1] > 0]
            advice = "Pause/kill these in the Now tab, dim the display, or enable Low Power Mode."
            
        elif kind == "__SYSTEM_THERMAL__":
            # Rank by attributed energy (Wh), not raw energy_impact - the latter
            # is a large unitless score and would render as absurd "Wh" values.
            rows = conn.execute(
                "SELECT app, SUM(attributed_mwh) FROM app_energy "
                f"WHERE ts_minute >= ? AND app NOT IN ({sys_ph}) "
                "GROUP BY app ORDER BY 2 DESC LIMIT 3",
                (now_ts - 15 * 60,) + SYSTEM_APPS
            ).fetchall()
            culprits = [{"app": r[0], "wh": float(r[1] / 1000.0)} for r in rows if r[1] > 0]
            advice = "Close/pause these and let it cool; avoid soft surfaces / direct sun."
            
        elif kind == "__SYSTEM_SLEEP_DRAIN__":
            row = conn.execute("SELECT value FROM state WHERE key='dark_wakes'").fetchone()
            if row:
                dws = json.loads(row[0])
                counts = {}
                for dw in dws:
                    for c in dw.get("culprits", []):
                        counts[c["proc"]] = counts.get(c["proc"], 0) + c.get("n", 1)
                for p, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]:
                    culprits.append({"app": p, "wh": 0.0})
            advice = "A background app woke the Mac during sleep - quit it or check Login Items."
            
        elif kind == "__SYSTEM_WEAK_CHARGER__":
            advice = "Adapter/cable can't outpace the load - use a higher-wattage USB-C charger, check the cable, or close heavy apps while charging."
            
        elif kind == "__SYSTEM_FULL_PLUGGED__":
            advice = "Battery held at ~100% on AC for 3+ hours - this is the main aging driver. Unplug, or enable the native 80% charge limit (System Settings > Battery > Charging)."
            
        else:
            culprits = [{"app": kind, "wh": 0.0}]
            if ratio is not None:
                advice = f"Using {ratio:.1f}x its usual energy - pause/kill it from the Now tab if unexpected."
            else:
                advice = "Using unusually high energy - pause/kill it from the Now tab if unexpected."
            
        return json.dumps({"culprits": culprits, "advice": advice})
    except Exception:
        return None

def check_app_anomalies(conn, now_ts: int, tz=None) -> list[int]:
    today = day_key(now_ts, tz)
    rows = conn.execute(
        "SELECT hour, app, attributed_mwh FROM rollup_hourly_apps"
        " WHERE hour >= ?", (now_ts - 26 * 3600,)).fetchall()
    todays = {}
    for hour, app, mwh in rows:
        if day_key(hour, tz) == today:
            todays[app] = todays.get(app, 0.0) + mwh
    inserted = []
    for app, mwh_today in todays.items():
        wh_today = mwh_today / 1000.0
        n_days, avg_mwh = conn.execute(
            "SELECT COUNT(*), AVG(attributed_mwh) FROM rollup_daily_apps"
            f" WHERE app = ? AND day < ? AND day >= date(?, '-{LOOKBACK_DAYS} days')",
            (app, today, today)).fetchone()
        if n_days < MIN_BASELINE_DAYS or avg_mwh is None:
            continue
        wh_base = avg_mwh / 1000.0
        if wh_today > RATIO * wh_base and wh_today > MIN_WH:
            ratio = RATIO_CAP if wh_base == 0.0 else min(
                wh_today / wh_base, RATIO_CAP)
            detail = build_detail(conn, app, now_ts, tz, ratio=ratio)
            cur = conn.execute(
                "INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today,"
                " wh_baseline, ratio, detail) VALUES (?,?,?,?,?,?,?)",
                (now_ts, today, app, wh_today, wh_base, ratio, detail))
            if cur.rowcount:
                inserted.append(cur.lastrowid)
    return inserted


def check_system_anomalies(conn, now_ts: int, tz=None) -> list[int]:
    today = day_key(now_ts, tz)
    inserted = []

    # 1. Thermal Pressure (> 5 mins of Heavy/Trapping in last 15 mins)
    rows = conn.execute("""
        SELECT count(*) FROM component_power
        WHERE ts_minute >= ? AND thermal_pressure IN ('Heavy', 'Trapping')
    """, (now_ts - 15 * 60,)).fetchone()[0]
    if rows >= 5:
        detail = build_detail(conn, "__SYSTEM_THERMAL__", now_ts, tz)
        cur = conn.execute(
            "INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today, wh_baseline, ratio, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_ts, today, "__SYSTEM_THERMAL__", float(rows), 5.0, float(rows) / 5.0, detail)
        )
        if cur.rowcount:
            inserted.append(cur.lastrowid)

    # 2. Rapid Discharge (> 30W average in last 15 mins on battery)
    row = conn.execute("""
        SELECT AVG(watts) FROM battery_samples
        WHERE ts >= ? AND on_ac = 0
    """, (now_ts - 15 * 60,)).fetchone()
    if row and row[0] is not None:
        avg_watts = abs(row[0])
        if avg_watts >= 30.0:
            detail = build_detail(conn, "__SYSTEM_RAPID_DISCHARGE__", now_ts, tz)
            cur = conn.execute(
                "INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today, wh_baseline, ratio, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_ts, today, "__SYSTEM_RAPID_DISCHARGE__", avg_watts, 30.0, avg_watts / 30.0, detail)
            )
            if cur.rowcount:
                inserted.append(cur.lastrowid)

    # 3. Sleep Drain (gap > 1h and drop > 5%)
    sessions = conn.execute("""
        SELECT started, ended, soc_start, soc_end
        FROM sessions
        ORDER BY started DESC LIMIT 5
    """).fetchall()
    
    for i in range(len(sessions) - 1):
        s_curr, s_prev = sessions[i], sessions[i+1]
        if s_curr[0] and s_prev[1]:
            if s_curr[0] - s_prev[1] >= 3600:
                soc_drop = s_prev[3] - s_curr[2] if s_prev[3] is not None and s_curr[2] is not None else 0
                if soc_drop >= 5.0:
                    gap_day = day_key(s_curr[0], tz)
                    detail = build_detail(conn, "__SYSTEM_SLEEP_DRAIN__", s_curr[0], tz)
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today, wh_baseline, ratio, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (s_curr[0], gap_day, "__SYSTEM_SLEEP_DRAIN__", float(soc_drop), 5.0, float(soc_drop) / 5.0, detail)
                    )
                    if cur.rowcount:
                        inserted.append(cur.lastrowid)
                    break # Only alert once for the most recent valid sleep gap

    # 4. Weak Charger (plugged into AC but still net-discharging in last 15 mins)
    row = conn.execute("""
        SELECT COUNT(*), AVG(watts) FROM battery_samples
        WHERE ts >= ? AND on_ac = 1
    """, (now_ts - 15 * 60,)).fetchone()
    if row and row[0] >= 20 and row[1] is not None and row[1] < -5.0:
        deficit = abs(row[1])
        detail = build_detail(conn, "__SYSTEM_WEAK_CHARGER__", now_ts, tz)
        cur = conn.execute(
            "INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today, wh_baseline, ratio, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_ts, today, "__SYSTEM_WEAK_CHARGER__", deficit, 5.0, deficit / 5.0, detail)
        )
        if cur.rowcount:
            inserted.append(cur.lastrowid)

    # 5. Kept at full on AC: >= 3h continuously plugged at >= 97% SoC.
    # 15s cadence -> 720 samples per 3h; >= 600 tolerates short sampling gaps.
    row = conn.execute(
        "SELECT COUNT(*), MIN(soc_pct), MIN(on_ac) FROM battery_samples"
        " WHERE ts >= ?", (now_ts - 3 * 3600,)).fetchone()
    if row and row[0] >= 600 and row[1] is not None and row[1] >= 97.0 and row[2] == 1:
        detail = build_detail(conn, "__SYSTEM_FULL_PLUGGED__", now_ts, tz)
        cur = conn.execute(
            "INSERT OR IGNORE INTO anomalies(ts, day, app, wh_today, wh_baseline, ratio, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_ts, today, "__SYSTEM_FULL_PLUGGED__", 3.0, 3.0, 1.0, detail))
        if cur.rowcount:
            inserted.append(cur.lastrowid)

    return inserted


def check(conn, now_ts: int, tz=None) -> list[int]:
    inserted = []
    inserted.extend(check_app_anomalies(conn, now_ts, tz))
    inserted.extend(check_system_anomalies(conn, now_ts, tz))
    conn.commit()
    return inserted
