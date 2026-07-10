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
