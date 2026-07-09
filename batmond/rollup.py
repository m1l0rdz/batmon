"""Hourly/daily rollups, retention prune, daily health snapshot.
hour = UTC epoch floored to 3600. day = local calendar day (tz param is
for tests; None = system local). Hours are bucketed into days by
day_key(hour_start): DST days get 23 or 25 hourly rows, correctly."""
from datetime import datetime

from batmond.db import get_state, set_state
from batmond.sessions import integrate

RAW_KEEP_SEC = 48 * 3600
HOURLY_KEEP_SEC = 90 * 86400
VACUUM_EVERY_SEC = 30 * 86400


def day_key(ts: int, tz=None) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def rollup_hourly(conn, now_ts: int) -> None:
    current_hour = now_ts - now_ts % 3600
    done = get_state(conn, "rollup_hourly_done")
    if done is None:
        first = conn.execute(
            "SELECT MIN(ts) FROM battery_samples").fetchone()[0]
        if first is None:
            return
        done = first - first % 3600 - 3600
    done = int(done)
    for hour in range(done + 3600, current_hour, 3600):
        _roll_one_hour(conn, hour)
    set_state(conn, "rollup_hourly_done", str(current_hour - 3600))
    conn.commit()


def _roll_one_hour(conn, hour: int) -> None:
    end = hour + 3600
    agg = conn.execute(
        "SELECT MIN(soc_pct), MAX(soc_pct), AVG(watts), AVG(brightness_pct), AVG(temp_c)"
        " FROM battery_samples WHERE ts >= ? AND ts < ?", (hour, end)
    ).fetchone()
    if agg[0] is None:
        has_apps = conn.execute(
            "SELECT 1 FROM app_energy WHERE ts_minute >= ? AND ts_minute < ?"
            " LIMIT 1", (hour, end)).fetchone()
        if not has_apps:
            return  # daemon was asleep/off this hour: honest hole
    wh_in, wh_out, bat_sec, ac_sec = integrate(conn, hour, end)
    comp = conn.execute(
        "SELECT AVG(cpu_mw), AVG(gpu_mw), AVG(ane_mw), AVG(package_mw), AVG(soc_temp_c), AVG(ssd_temp_c)"
        " FROM component_power WHERE ts_minute >= ? AND ts_minute < ?",
        (hour, end)).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO rollup_hourly_battery(hour, soc_min,"
        " soc_max, wh_in, wh_out, avg_watts, on_battery_sec, on_ac_sec,"
        " avg_brightness, avg_cpu_mw, avg_gpu_mw, avg_ane_mw,"
        " avg_package_mw, avg_temp_c, avg_soc_temp_c, avg_ssd_temp_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (hour, agg[0], agg[1], wh_in, wh_out, agg[2], bat_sec, ac_sec,
         agg[3], comp[0], comp[1], comp[2], comp[3], agg[4], comp[4], comp[5]))
    conn.execute(
        "INSERT OR REPLACE INTO rollup_hourly_apps(hour, app,"
        " attributed_mwh, avg_energy_impact)"
        " SELECT ?, app, SUM(attributed_mwh), AVG(energy_impact)"
        " FROM app_energy WHERE ts_minute >= ? AND ts_minute < ?"
        " GROUP BY app", (hour, hour, end))


def rollup_daily(conn, now_ts: int, tz=None) -> None:
    today = day_key(now_ts, tz)
    cutoff = now_ts - 89 * 86400
    hours = conn.execute(
        "SELECT hour FROM rollup_hourly_battery WHERE hour >= ? ORDER BY hour",
        (cutoff,)
    ).fetchall()
    days = {}
    for (hour,) in hours:
        d = day_key(hour, tz)
        if d < today:
            days.setdefault(d, []).append(hour)
    for d, hlist in days.items():
        ph = ",".join("?" * len(hlist))
        conn.execute(
            f"INSERT OR REPLACE INTO rollup_daily_battery"
            f" SELECT ?, MIN(soc_min), MAX(soc_max), SUM(wh_in),"
            f" SUM(wh_out), AVG(avg_watts), SUM(on_battery_sec),"
            f" SUM(on_ac_sec), AVG(avg_brightness), AVG(avg_cpu_mw),"
            f" AVG(avg_gpu_mw), AVG(avg_ane_mw), AVG(avg_package_mw),"
            f" AVG(avg_temp_c), AVG(avg_soc_temp_c), AVG(avg_ssd_temp_c)"
            f" FROM rollup_hourly_battery WHERE hour IN ({ph})",
            [d] + hlist)
        conn.execute(
            f"INSERT OR REPLACE INTO rollup_daily_apps"
            f" SELECT ?, app, SUM(attributed_mwh) FROM rollup_hourly_apps"
            f" WHERE hour IN ({ph}) GROUP BY app", [d] + hlist)
    conn.commit()


def prune(conn, now_ts: int) -> None:
    raw_cut = now_ts - RAW_KEEP_SEC
    conn.execute("DELETE FROM battery_samples WHERE ts < ?", (raw_cut,))
    conn.execute("DELETE FROM app_energy WHERE ts_minute < ?", (raw_cut,))
    conn.execute("DELETE FROM component_power WHERE ts_minute < ?",
                 (raw_cut,))
    hourly_cut = now_ts - HOURLY_KEEP_SEC
    conn.execute("DELETE FROM rollup_hourly_battery WHERE hour < ?",
                 (hourly_cut,))
    conn.execute("DELETE FROM rollup_hourly_apps WHERE hour < ?",
                 (hourly_cut,))
    last_vac = get_state(conn, "last_vacuum")
    if last_vac is None or now_ts - int(last_vac) > VACUUM_EVERY_SEC:
        conn.execute("PRAGMA incremental_vacuum")
        set_state(conn, "last_vacuum", str(now_ts))
    conn.commit()


def snapshot_health(conn, sample, now_ts: int, tz=None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO battery_health_daily(day, cycle_count,"
        " max_capacity_pct, design_capacity_mah) VALUES (?,?,?,?)",
        (day_key(now_ts, tz), sample.cycle_count, sample.max_capacity_pct,
         sample.design_capacity_mah))
    conn.commit()
