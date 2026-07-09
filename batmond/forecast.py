"""Discharge/charge forecast (design 5.1): EMA over ~10-15 min of current
draw + remaining raw mAh. Stored in state so readers never recompute.
Charging estimate ignores taper near 100% - v1 approximation."""
import json

from batmond.db import set_state

ALPHA = 0.05        # 15s samples -> effective window ~ 10 min
MIN_MA = 10.0       # below this, rate is noise: no estimate


class Forecaster:
    def __init__(self):
        self._ema_ma = None
        self._mode = None

    def update(self, s) -> dict:
        if s.on_ac:
            mode = "charging" if s.is_charging else "full"
        else:
            mode = "battery"
        if mode != self._mode:
            self._ema_ma = None
            self._mode = mode
        ma = abs(s.current_ma or 0.0)
        self._ema_ma = ma if self._ema_ma is None else (
            ALPHA * ma + (1 - ALPHA) * self._ema_ma)
        minutes = None
        if mode != "full" and self._ema_ma >= MIN_MA:
            if mode == "battery":
                remaining = s.raw_current_capacity_mah
            else:
                remaining = s.raw_max_capacity_mah - s.raw_current_capacity_mah
            minutes = int(remaining / self._ema_ma * 60)
        return {"mode": mode, "minutes": minutes}


def store(conn, forecast: dict) -> None:
    set_state(conn, "forecast", json.dumps(forecast))
    conn.commit()
