from batmond.parsers.sleep_culprits import parse_sleep_culprits

# Real-shape lines. Epochs (+0500):
#   2026-07-08 23:23:29 -> 1783535009  (Wake Requests)
#   2026-07-08 23:30:00 -> 1783535400  (MSTeams assertion)
#   2026-07-08 23:31:00 -> 1783535460  (caffeinate assertion)
LOG = """
2026-07-08 23:23:29 +0500 Wake Requests       \t[process=mDNSResponder request=Maintenance deltaSecs=7198 wakeAt=2026-07-09 01:23:27 info="upkeep wake"] [process=powerd request=UserWake deltaSecs=17720 wakeAt=2026-07-09 04:18:49 info="com.apple.alarm.user-invisible-com.apple.calaccessd.travelEngine.periodicRefreshTimer,793"]
2026-07-08 23:30:00 +0500 Assertions          \tPID 53261(MSTeams) Created PreventUserIdleSystemSleep "Microsoft Teams Call in progress" 00:11:48  id:0x0x1
2026-07-08 23:31:00 +0500 Assertions          \tPID 77437(caffeinate) Created PreventUserIdleDisplaySleep "caffeinate command-line tool" 00:00:00  id:0x0x2
"""

# Ranking scenario: MSTeams held twice (count 2), powerd is system with count
# 2, plus Music and Zoom once each -> 4 distinct culprits. Timestamps are all
# within [1783535000, 1783535500].
LOG_RANK = """
2026-07-08 23:24:00 +0500 Assertions          \tPID 1(MSTeams) Created PreventUserIdleSystemSleep "hold" 00:05:00  id:0x0x3
2026-07-08 23:24:10 +0500 Assertions          \tPID 1(MSTeams) Created PreventUserIdleSystemSleep "hold" 00:05:00  id:0x0x3b
2026-07-08 23:24:20 +0500 Assertions          \tPID 2(Music) Created NoIdleSleepAssertion "playing" 00:05:00  id:0x0x4
2026-07-08 23:24:30 +0500 Assertions          \tPID 3(Zoom) Created PreventUserIdleSystemSleep "call" 00:05:00  id:0x0x5
2026-07-08 23:24:40 +0500 Assertions          \tPID 357(powerd) Created PreventUserIdleSystemSleep "hold" 00:05:00  id:0x0x6
2026-07-08 23:24:50 +0500 Assertions          \tPID 357(powerd) Created PreventUserIdleSystemSleep "hold" 00:05:00  id:0x0x7
"""


def test_extracts_wakers_and_holders():
    res = parse_sleep_culprits(1783535000, 1783535500, LOG)
    by = {c["proc"]: c for c in res}
    # MSTeams held the machine (assertion)
    assert by["MSTeams"]["why"] == "kept-awake"
    # calaccessd (from info= bundle) preferred over the proxy "powerd"
    assert "calaccessd" in by
    assert by["calaccessd"]["why"] == "woke"
    # mDNSResponder scheduled a wake
    assert "mDNSResponder" in by


def test_excludes_caffeinate():
    res = parse_sleep_culprits(1783535000, 1783535500, LOG)
    assert all(c["proc"] != "caffeinate" for c in res)


def test_powerd_proxy_not_shown_when_info_has_app():
    # powerd is only a proxy here; the app (calaccessd) is the culprit.
    res = parse_sleep_culprits(1783535000, 1783535500, LOG)
    assert all(c["proc"] != "powerd" for c in res)


def test_window_filter_and_empty():
    # Window before all events -> nothing.
    assert parse_sleep_culprits(1783530000, 1783535008, LOG) == []
    assert parse_sleep_culprits(0, 10, "") == []


def test_returns_at_most_three():
    res = parse_sleep_culprits(1783535000, 1783535500, LOG)
    assert len(res) <= 3


def test_ranking_apps_before_system_and_caps_at_three():
    res = parse_sleep_culprits(1783535000, 1783535500, LOG_RANK)
    procs = [c["proc"] for c in res]
    assert len(res) == 3            # 4 distinct culprits -> [:3] truncation
    assert "powerd" not in procs    # system ranked last despite count 2
    assert procs[0] == "MSTeams"    # highest count first
    assert res[0]["n"] == 2
