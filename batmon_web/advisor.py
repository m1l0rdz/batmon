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
