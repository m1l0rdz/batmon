"""Session = maximal run of samples with one power source (design 5.1, D1).
Gap > 90s (sleep/daemon stop) closes at the gap; honest holes, no
interpolation. wh is computed at close time by integrating battery_samples."""

import json

GAP_SEC = 90

# A dark wake is only worth surfacing when the sleep gap lost an ABNORMAL
# amount of charge. Healthy standby is ~1-2% per 8h; below this percent the
# drain is normal self-discharge plus routine maintenance (rtc, wifibt) and
# must not be flagged. Percent of SoC, so it is battery-size independent.
# (User choice 2026-07-09: "only abnormal drain".)
ABNORMAL_DRAIN_PCT = 5.0


def classify(on_ac: bool, is_charging: bool) -> str:
    if not on_ac:
        return "battery"
    return "charging" if is_charging else "full"


def integrate(conn, t0: int, t1: int):
    """Integrate signed watts over [t0, t1]. Returns
    (wh_in, wh_out, on_battery_sec, on_ac_sec). dt per consecutive pair
    capped at GAP_SEC."""
    rows = conn.execute(
        "SELECT ts, watts, on_ac FROM battery_samples "
        "WHERE ts >= ? AND ts <= ? ORDER BY ts", (t0, t1)).fetchall()
    wh_in = wh_out = 0.0
    bat_sec = ac_sec = 0
    for (ts, watts, on_ac), (nts, _, _) in zip(rows, rows[1:]):
        dt = min(nts - ts, GAP_SEC)
        if watts is not None:
            if watts > 0:
                wh_in += watts * dt / 3600.0
            else:
                wh_out += -watts * dt / 3600.0
        if on_ac:
            ac_sec += dt
        else:
            bat_sec += dt
    return wh_in, wh_out, bat_sec, ac_sec


class SessionTracker:
    def __init__(self, conn):
        self.conn = conn
        self._last_ts = None
        self._last_soc = None
        self._close_dangling()
        self._open_row = None  # (id, kind)

    def _close_dangling(self):
        row = self.conn.execute(
            "SELECT id, kind, started FROM sessions WHERE ended IS NULL"
        ).fetchone()
        if row is None:
            return
        sid, kind, started = row
        last = self.conn.execute(
            "SELECT ts, soc_pct FROM battery_samples WHERE ts >= ? "
            "ORDER BY ts DESC LIMIT 1", (started,)).fetchone()
        if last is None:
            self.conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        else:
            self._close(sid, kind, started, last[0], last[1])
        self.conn.commit()

    def _close(self, sid, kind, started, ended, soc_end):
        wh_in, wh_out, _, _ = integrate(self.conn, started, ended)
        wh = wh_out if kind == "battery" else wh_in
        self.conn.execute(
            "UPDATE sessions SET ended=?, soc_end=?, wh=? WHERE id=?",
            (ended, soc_end, wh, sid))

    def _open(self, kind, ts, soc):
        cur = self.conn.execute(
            "INSERT INTO sessions(kind, started, soc_start) VALUES (?,?,?)",
            (kind, ts, soc))
        self._open_row = (cur.lastrowid, kind)
        
    def _process_dark_wakes(self, t0, t1, soc0, soc1):
        try:
            from batmond.parsers.pmset_log import parse_pmset_log
            wakes = parse_pmset_log(t0, t1)
        except ImportError:
            return
            
        if not wakes:
            return
            
        soc_drop = max(0.0, soc0 - soc1)
        if soc_drop < ABNORMAL_DRAIN_PCT:
            return  # normal sleep drain, not worth a warning

        row = self.conn.execute("SELECT design_capacity_mah FROM battery_health_daily ORDER BY day DESC LIMIT 1").fetchone()
        cap = row[0] if row and row[0] else 6000.0

        # Assume ~11.4V nominal voltage for Mac
        wh_drained = (soc_drop / 100.0) * (cap / 1000.0) * 11.4

        # wh_drained is the whole-gap SoC loss, so record one row for the gap:
        # pair it with the gap duration (not a single ~5s wake, which would read
        # as "0h 0m") and label it with the wake that stayed up longest.
        primary = max(wakes, key=lambda w: w['duration_sec'])
        self.conn.execute(
            "INSERT INTO dark_wakes(ts, duration_sec, reason, wh_drained) VALUES (?,?,?,?)",
            (primary['ts'], t1 - t0, primary['reason'], wh_drained)
        )

        # Display feed (state table, like radio_warnings): who woke / held the
        # machine during this abnormal gap. Rolling, newest first, cap 10.
        from batmond.parsers.sleep_culprits import parse_sleep_culprits
        from batmond.db import get_state, set_state
        culprits = parse_sleep_culprits(t0, t1)
        raw = get_state(self.conn, "dark_wakes")
        feed = json.loads(raw) if raw else []
        feed.insert(0, {"ts": primary['ts'], "reason": primary['reason'],
                        "duration_sec": t1 - t0, "wh_drained": wh_drained,
                        "culprits": culprits})
        set_state(self.conn, "dark_wakes", json.dumps(feed[:10]))

    def feed(self, ts: int, soc_pct: float, on_ac: bool, is_charging: bool):
        kind = classify(on_ac, is_charging)
        if self._open_row is None:
            self._open(kind, ts, soc_pct)
        else:
            sid, cur_kind = self._open_row
            started = self.conn.execute(
                "SELECT started FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            gap = self._last_ts is not None and ts - self._last_ts > GAP_SEC
            if gap or kind != cur_kind:
                # Session ends at its own last sample; the new sample
                # (new source / after the gap) starts the next session.
                self._close(sid, cur_kind, started,
                            self._last_ts, self._last_soc)
                            
                if gap:
                    self._process_dark_wakes(self._last_ts, ts, self._last_soc, soc_pct)

                self._open(kind, ts, soc_pct)
        self._last_ts = ts
        self._last_soc = soc_pct
        self.conn.commit()
