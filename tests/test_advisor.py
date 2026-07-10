from batmon_web.advisor import compute_score

GOOD_HABITS = {"window_days": 30, "full_pct_of_ac": 5.0, "ac_share_pct": 60.0,
               "deep_discharges": 0, "overnight_sessions": 0,
               "cycles_30d": 8, "avg_temp_c": 30.0}
BAD_HABITS = {"window_days": 30, "full_pct_of_ac": 80.0, "ac_share_pct": 99.0,
              "deep_discharges": 6, "overnight_sessions": 20,
              "cycles_30d": 40, "avg_temp_c": 41.0}
HEALTH = [{"day": "2026-07-01", "cycle_count": 100, "max_capacity_pct": 100.0}]


def test_perfect_inputs_score_high():
    r = compute_score(GOOD_HABITS, HEALTH)
    assert r["score"] >= 90
    assert r["grade"] == "excellent"
    assert sum(c["max"] for c in r["components"]) == 100


def test_bad_inputs_score_low():
    r = compute_score(BAD_HABITS,
                      [{"day": "2026-07-01", "cycle_count": 500,
                        "max_capacity_pct": 82.0}])
    assert r["score"] <= 30
    assert r["grade"] == "poor"


def test_missing_data_renormalizes():
    habits = dict(GOOD_HABITS, full_pct_of_ac=None, avg_temp_c=None)
    r = compute_score(habits, [])
    # capacity (35) + full (25) + temp (20) components absent -> max sums to 20
    assert sum(c["max"] for c in r["components"]) == 20
    assert r["score"] == 100  # remaining components are perfect


def test_no_data_at_all():
    empty = {"window_days": 30, "full_pct_of_ac": None, "ac_share_pct": None,
             "deep_discharges": 0, "overnight_sessions": 0,
             "cycles_30d": None, "avg_temp_c": None}
    # deep_discharges / overnight are ints even on empty DBs, so those two
    # components always exist; score must not be None here
    r = compute_score(empty, [])
    assert r["score"] == 100


def test_every_component_has_why():
    r = compute_score(GOOD_HABITS, HEALTH)
    assert all(c["why"] for c in r["components"])

from batmon_web.advisor import recommendations

BASE_CTX = {
    "habits": {"window_days": 30, "full_pct_of_ac": 5.0, "ac_share_pct": 60.0,
               "deep_discharges": 0, "overnight_sessions": 0,
               "cycles_30d": 8, "avg_temp_c": 30.0},
    "top_apps": [{"app": "Safari", "attributed_wh": 2.0, "share_pct": 20.0}],
    "frequent_culprit": None,
    "avg_brightness_7d": 50.0,
    "charge_limit": {"holding": None},
}


def _ctx(**over):
    ctx = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in BASE_CTX.items()}
    for k, v in over.items():
        if isinstance(v, dict) and k in ctx and isinstance(ctx[k], dict):
            ctx[k].update(v)
        else:
            ctx[k] = v
    return ctx


def test_healthy_ctx_no_recommendations():
    assert recommendations(_ctx()) == []


def test_overnight_full_fires():
    recs = recommendations(_ctx(habits={"overnight_sessions": 8}))
    assert [r["id"] for r in recs] == ["overnight_full"]
    assert recs[0]["severity"] == "high"
    assert "8" in recs[0]["body"]


def test_overnight_silent_when_limit_holding():
    recs = recommendations(_ctx(habits={"overnight_sessions": 8},
                                charge_limit={"holding": True}))
    assert recs == []


def test_heavy_app_fires_with_name():
    recs = recommendations(_ctx(
        top_apps=[{"app": "Docker", "attributed_wh": 9.0, "share_pct": 55.0}]))
    assert recs[0]["id"] == "heavy_app"
    assert "Docker" in recs[0]["body"]


def test_sorted_high_first():
    recs = recommendations(_ctx(habits={"overnight_sessions": 8,
                                        "avg_temp_c": 37.0},
                                avg_brightness_7d=95.0))
    sevs = [r["severity"] for r in recs]
    assert sevs == sorted(sevs, key=("high", "medium", "low").index)


def test_none_fields_do_not_crash():
    recs = recommendations(_ctx(
        habits={"full_pct_of_ac": None, "ac_share_pct": None,
                "avg_temp_c": None},
        avg_brightness_7d=None, top_apps=[]))
    assert isinstance(recs, list)
