"""Collection loops (design 5.1). One tick = 15s. Every 4th tick runs the
powermetrics burst (60s cadence). Every step is individually wrapped:
one failing iteration never kills the daemon."""
import json
import logging
import time

from batmond import db as db_mod
from batmond.anomalies import check as check_anomalies
from batmond.attribution import attribute_minute
from batmond.forecast import Forecaster, store as store_forecast
from batmond.parsers.assertions import parse_assert_awake
from batmond.parsers.brightness import parse_brightness
from batmond.parsers.ioreg_battery import parse_ioreg_battery
from batmond.parsers.powermetrics import average_burst, parse_burst
from batmond.rollup import (day_key, prune, rollup_daily, rollup_hourly,
                            snapshot_health)
from batmond.sessions import SessionTracker

log = logging.getLogger("batmond")

PM_FAIL_BACKOFF_BASE = 60
PM_FAIL_BACKOFF_MAX = 600


class Collector:
    def __init__(self, conn, source, db_path, tz=None):
        self.conn = conn
        self.source = source
        self.db_path = db_path
        self.tz = tz
        self.sessions = SessionTracker(conn)
        self.forecaster = Forecaster()
        self._tick_no = 0
        self._last_hour = None
        self._last_sample = None
        self._pm_fails = 0
        self._pm_skip_until = 0
        self._health_day = None

    def tick(self, now_ts: int) -> None:
        self._battery_step(now_ts)
        if self._tick_no % 4 == 0:
            self._powermetrics_step(now_ts)
            self._devices_step(now_ts)
        if self._tick_no % 20 == 0:
            self._radios_step(now_ts)
        if self._tick_no % 60 == 0:
            try:
                check_anomalies(self.conn, now_ts, self.tz)
            except Exception:
                log.exception("anomalies check failed")
        self._heartbeat_step()
        self._maintenance_step(now_ts)
        self._ipc_step()

    def _ipc_step(self) -> None:
        from batmond.ipc import process_commands
        try:
            process_commands(self.conn)
        except Exception:
            log.exception("ipc step failed")

    def _devices_step(self, now_ts):
        try:
            from batmond.parsers.devices import get_connected_devices
            devs = get_connected_devices()
            db_mod.set_state(self.conn, "connected_devices", json.dumps(devs))
        except Exception:
            log.exception("devices step failed")

    def _radios_step(self, now_ts):
        if self._last_sample and self._last_sample.on_ac:
            return
        from batmond.parsers.radios import parse_radios
        try:
            warnings = parse_radios()
            if warnings:
                warnings_dict = [{"ts": now_ts, "reason": w} for w in warnings]
                db_mod.set_state(self.conn, "radio_warnings", json.dumps(warnings_dict))
            else:
                db_mod.set_state(self.conn, "radio_warnings", "[]")
        except Exception:
            log.exception("radios step failed")

    def _heartbeat_step(self):
        now_ts = int(time.time())
        try:
            db_mod.set_state(self.conn, "heartbeat", str(now_ts))
            self.conn.commit()
            db_mod.ensure_readable(self.db_path)
        except Exception:
            log.exception("heartbeat failed")
        self._tick_no += 1

    def _battery_step(self, now_ts):
        try:
            s = parse_ioreg_battery(self.source.ioreg_battery(), now_ts)
            brightness = parse_brightness(self.source.brightness_text())
            awake = parse_assert_awake(self.source.assertions_text())
            self.conn.execute(
                "INSERT OR REPLACE INTO battery_samples(ts, soc_pct,"
                " current_ma, voltage_mv, watts, is_charging, on_ac,"
                " temp_c, brightness_pct, assert_awake)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (s.ts, s.soc_pct, s.current_ma, s.voltage_mv, s.watts,
                 int(s.is_charging), int(s.on_ac), s.temp_c, brightness,
                 int(awake)))
            db_mod.set_state(self.conn, "health_now", json.dumps({
                "ts": s.ts, "cycle_count": s.cycle_count,
                "max_capacity_pct": s.max_capacity_pct,
                "design_capacity_mah": s.design_capacity_mah,
                "raw_max_capacity_mah": s.raw_max_capacity_mah,
                "raw_current_capacity_mah": s.raw_current_capacity_mah,
                "cell_voltage_mv": s.cell_voltage_mv,
                "lifetime_temp_min": s.lifetime_temp_min,
                "lifetime_temp_max": s.lifetime_temp_max,
                "lifetime_temp_avg": s.lifetime_temp_avg,
                "operating_time_hours": s.operating_time_hours}))

            # Auto LPM check
            auto_lpm = db_mod.get_state(self.conn, "auto_lpm_threshold")
            if auto_lpm is not None:
                threshold = int(auto_lpm)
                if threshold > 0 and s.soc_pct <= threshold and not s.on_ac:
                    cur_lpm = db_mod.get_state(self.conn, "lpm")
                    if cur_lpm != "1":
                        from batmond import lpm
                        lpm.set_low_power_mode(True)
                        db_mod.set_state(self.conn, "lpm", "1")
                        log.info(f"Auto LPM triggered (soc {s.soc_pct} <= {threshold})")

            self.conn.commit()
            self.sessions.feed(s.ts, s.soc_pct, s.on_ac, s.is_charging)
            store_forecast(self.conn, self.forecaster.update(s))
            self._last_sample = s
        except Exception:
            log.exception("battery step failed")

    def _powermetrics_step(self, now_ts):
        if now_ts < self._pm_skip_until:
            return
        try:
            burst = self.source.powermetrics_burst()
            avg = average_burst(parse_burst(burst))
            temps = self.source.temps()
            self._pm_fails = 0
        except Exception:
            self._pm_fails += 1
            log.exception("powermetrics failed (%d consecutive)",
                          self._pm_fails)
            if self._pm_fails >= 3:
                delay = min(PM_FAIL_BACKOFF_BASE
                            * 2 ** (self._pm_fails - 2),
                            PM_FAIL_BACKOFF_MAX)
                self._pm_skip_until = now_ts + delay
            return
        try:
            minute = now_ts - now_ts % 60
            self.conn.execute(
                "INSERT OR REPLACE INTO component_power(ts_minute, cpu_mw,"
                " gpu_mw, ane_mw, dram_mw, package_mw, thermal_pressure, soc_temp_c, ssd_temp_c)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (minute, avg.cpu_mw, avg.gpu_mw, avg.ane_mw, avg.dram_mw,
                 avg.package_mw, avg.thermal_pressure, temps.get("soc_temp_c"), temps.get("ssd_temp_c")))
            for a in attribute_minute(avg.package_mw, avg.procs):
                self.conn.execute(
                    "INSERT OR REPLACE INTO app_energy(ts_minute, app,"
                    " pid_count, energy_impact, cpu_ms_per_s, gpu_ms_per_s,"
                    " attributed_mwh) VALUES (?,?,?,?,?,?,?)",
                    (minute, a.app, a.pid_count, a.energy_impact,
                     a.cpu_ms_per_s, a.gpu_ms_per_s, a.attributed_mwh))
            self.conn.commit()
        except Exception:
            log.exception("powermetrics store failed")

    def _maintenance_step(self, now_ts):
        hour = now_ts - now_ts % 3600

        # One health snapshot per local day, including the day the daemon
        # starts (INSERT OR IGNORE keeps it idempotent across restarts).
        day = day_key(now_ts, self.tz)
        if self._last_sample is not None and day != self._health_day:
            try:
                snapshot_health(self.conn, self._last_sample, now_ts,
                                self.tz)
                self._health_day = day
            except Exception:
                log.exception("health snapshot failed")

        if self._last_hour is None:
            self._last_hour = hour
            return
        if hour == self._last_hour:
            return
        self._last_hour = hour
        for fn in (lambda: rollup_hourly(self.conn, now_ts),
                   lambda: rollup_daily(self.conn, now_ts, self.tz),
                   lambda: prune(self.conn, now_ts)):
            try:
                fn()
            except Exception:
                log.exception("maintenance step failed")

    def run_forever(self):
        while True:
            start = time.time()
            self.tick(int(start))
            time.sleep(max(0.0, 15.0 - (time.time() - start)))

    def run_dry(self, ticks: int):
        start = int(time.time()) - ticks * 15
        for i in range(ticks):
            self.tick(start + i * 15)
        rollup_hourly(self.conn, int(time.time()))
        rollup_daily(self.conn, int(time.time()), self.tz)
