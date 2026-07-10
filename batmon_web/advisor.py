"""Battery Score and recommendation rules. Pure functions over dicts
produced by queries.py - no DB access, so tests run without SQLite.
Weights and thresholds are module constants: tune here, nowhere else."""

W_CAPACITY = 35   # design-capacity health
W_FULL = 25       # time held at full while plugged
W_TEMP = 20       # average battery temperature
W_DEEP = 10       # deep discharges
W_OVERNIGHT = 10  # overnight charging nights

CAP_FLOOR = 80.0        # max_capacity_pct at which capacity points hit 0
FULL_PCT_ZERO = 50.0    # % of AC time at full at which points hit 0
TEMP_FULL_C = 32.0      # avg temp at/below which temp points are full
TEMP_ZERO_C = 40.0      # avg temp at/above which temp points hit 0
DEEP_ZERO = 5           # deep discharges in 30d at which points hit 0
OVERNIGHT_ZERO = 10     # overnight charges in 30d at which points hit 0


def _clamp01(x):
    return max(0.0, min(1.0, x))


def compute_score(habits, health_history):
    parts = []

    cap = None
    if health_history:
        cap = health_history[-1].get("max_capacity_pct")
    if cap is not None:
        f = _clamp01((cap - CAP_FLOOR) / (100.0 - CAP_FLOOR))
        parts.append({"name": "Battery health", "points": round(f * W_CAPACITY, 1),
                      "max": W_CAPACITY,
                      "why": "capacity at %.0f%% of design" % cap})

    fp = habits.get("full_pct_of_ac")
    if fp is not None:
        f = _clamp01(1.0 - fp / FULL_PCT_ZERO)
        parts.append({"name": "Time at full charge", "points": round(f * W_FULL, 1),
                      "max": W_FULL,
                      "why": "%.0f%% of plugged time spent at 100%%" % fp})

    t = habits.get("avg_temp_c")
    if t is not None:
        f = _clamp01((TEMP_ZERO_C - t) / (TEMP_ZERO_C - TEMP_FULL_C))
        parts.append({"name": "Temperature", "points": round(f * W_TEMP, 1),
                      "max": W_TEMP,
                      "why": "30-day average %.1f C" % t})

    deep = habits.get("deep_discharges")
    if deep is not None:
        f = _clamp01(1.0 - float(deep) / DEEP_ZERO)
        parts.append({"name": "Deep discharges", "points": round(f * W_DEEP, 1),
                      "max": W_DEEP,
                      "why": "%d discharges below 10%% in 30 days" % deep})

    on = habits.get("overnight_sessions")
    if on is not None:
        f = _clamp01(1.0 - float(on) / OVERNIGHT_ZERO)
        parts.append({"name": "Overnight charging", "points": round(f * W_OVERNIGHT, 1),
                      "max": W_OVERNIGHT,
                      "why": "%d overnight charge sessions in 30 days" % on})

    denom = sum(p["max"] for p in parts)
    if denom == 0:
        return {"score": None, "grade": None, "components": []}
    score = int(round(sum(p["points"] for p in parts) / denom * 100.0))
    if score >= 90:
        grade = "excellent"
    elif score >= 75:
        grade = "good"
    elif score >= 50:
        grade = "fair"
    else:
        grade = "poor"
    return {"score": score, "grade": grade, "components": parts}

R_OVERNIGHT = 5
R_FULL_PCT = 30.0
R_HOT_C = 35.0
R_DEEP = 3
R_AC_SHARE = 95.0
R_APP_SHARE = 40.0
R_BRIGHTNESS = 80.0

_SEV_ORDER = ("high", "medium", "low")


def recommendations(ctx):
    h = ctx.get("habits") or {}
    recs = []

    on = h.get("overnight_sessions") or 0
    if on >= R_OVERNIGHT and (ctx.get("charge_limit") or {}).get("holding") is not True:
        recs.append({"id": "overnight_full", "severity": "high",
                     "title": "Charging overnight at 100%",
                     "body": "%d overnight charge sessions in 30 days. Holding a full battery for hours is the main aging driver - enable the native 80%% charge limit in System Settings > Battery > Charging." % on})

    fp = h.get("full_pct_of_ac")
    if fp is not None and fp > R_FULL_PCT:
        recs.append({"id": "parked_at_full", "severity": "high",
                     "title": "Battery parked at full charge",
                     "body": "%.0f%% of plugged-in time is spent at 100%%. Enable the 80%% charge limit or unplug once charged." % fp})

    t = h.get("avg_temp_c")
    if t is not None and t > R_HOT_C:
        recs.append({"id": "hot_battery", "severity": "medium",
                     "title": "Battery runs hot",
                     "body": "30-day average battery temperature is %.1f C (wear accelerates above 35 C). Avoid soft surfaces and direct sun; check the Apps tab for heavy processes while charging." % t})

    deep = h.get("deep_discharges") or 0
    if deep >= R_DEEP:
        recs.append({"id": "deep_discharges", "severity": "medium",
                     "title": "Frequent deep discharges",
                     "body": "%d discharges below 10%% in 30 days. Plugging in around 20%% is gentler on the cell." % deep})

    ac = h.get("ac_share_pct")
    if ac is not None and ac > R_AC_SHARE:
        recs.append({"id": "battery_unused", "severity": "low",
                     "title": "Battery almost never used",
                     "body": "Plugged in %.0f%% of the time. With the Mac docked, the 80%% charge limit costs nothing and slows aging." % ac})

    top = ctx.get("top_apps") or []
    if top and (top[0].get("share_pct") or 0) > R_APP_SHARE:
        a = top[0]
        recs.append({"id": "heavy_app", "severity": "medium",
                     "title": "One app dominates energy use",
                     "body": "%s used %.0f%% of attributed energy in the last 24h (%.1f Wh). Quit it when idle, or pause it from the Now tab." % (a["app"], a["share_pct"], a["attributed_wh"])})

    culprit = ctx.get("frequent_culprit")
    if culprit:
        recs.append({"id": "sleep_culprit", "severity": "medium",
                     "title": "A process keeps waking the Mac in sleep",
                     "body": "%s appeared in %d recent abnormal sleep-drain events. Quit it before closing the lid or check its background settings / Login Items." % (culprit["proc"], culprit["n"])})

    br = ctx.get("avg_brightness_7d")
    if br is not None and br > R_BRIGHTNESS:
        recs.append({"id": "high_brightness", "severity": "low",
                     "title": "Display brightness is high",
                     "body": "7-day average brightness is %.0f%%. The display is a top battery consumer - a small reduction buys real runtime." % br})

    recs.sort(key=lambda r: _SEV_ORDER.index(r["severity"]))
    return recs
