import pytest
from batmond.parsers.pmset_log import parse_pmset_log

def test_parse_pmset_log():
    log_output = """
2023-10-18 10:10:10 +0200 Wake                Wake from Deep Idle [CDNVA] : due to SMC.OutboxNotEmpty smc.70070000 wifibt wlan/UserActivity Assertion
2023-10-18 10:15:00 +0200 Sleep               Entering Sleep state due to 'Software Sleep':TCPKeepAlive=active Using Batt (Charge:100%) 3000 secs
2023-10-18 11:11:11 +0200 DarkWake            DarkWake from Deep Idle [CDN] : due to RTC/Maintenance
2023-10-18 11:12:00 +0200 Sleep               Entering Sleep state due to Maintenance Sleep:Using BATT (Charge:99%)
"""
    
    # 2023-10-18 10:10:10 +0200 is 1697616610
    # 2023-10-18 10:15:00 +0200 is 1697616900
    # 2023-10-18 11:11:11 +0200 is 1697620271
    # 2023-10-18 11:12:00 +0200 is 1697620320

    t0 = 1697600000
    t1 = 1697700000

    results = parse_pmset_log(t0, t1, log_output)
    # Only DarkWake is a dark wake; the full "Wake" (user) event is ignored.
    assert len(results) == 1

    assert results[0]['ts'] == 1697620271
    assert results[0]['duration_sec'] == 49
    assert "RTC/Maintenance" in results[0]['reason']


def test_parse_pmset_log_ignores_user_wake():
    # A user full "Wake" (lid open / trackpad -> UserActivity) is not a dark wake.
    log_output = """
2026-07-09 00:11:15 +0500 Wake                Wake from Deep Idle [CDNVA] : due to smc.sysState.Wake(0x70070000) lid SMC.OutboxNotEmpty RTP.multi-touch/UserActivity Assertion Using BATT (Charge:64%)
2026-07-09 08:00:00 +0500 Sleep               Entering Sleep state due to 'Clamshell Sleep'
"""
    results = parse_pmset_log(1783537800, 1783538000, log_output)
    assert results == []

def test_parse_pmset_log_strips_power_status():
    log_output = """
2026-07-02 02:20:47 +0500 DarkWake            DarkWake from Deep Idle [CDN] : due to AOP.Outbox0_NotEmpty spu_queue_overflow_ep40/ Using BATT (Charge:66%) 45 secs
2026-07-02 02:21:32 +0500 Sleep               Entering Sleep state due to 'Maintenance Sleep' Using Batt (Charge:66%) 1545 secs
"""
    results = parse_pmset_log(1782900000, 1783000000, log_output)  # brackets 1782940847
    assert len(results) == 1
    assert results[0]['reason'] == "AOP.Outbox0_NotEmpty spu_queue_overflow_ep40/"


def test_parse_pmset_log_filter_t0_t1():
    log_output = """
2023-10-18 10:10:10 +0200 Wake                Wake from Deep Idle [CDNVA] : due to SMC.OutboxNotEmpty
2023-10-18 10:15:00 +0200 Sleep               Entering Sleep state due to 'Software Sleep'
"""
    # 2023-10-18 10:10:10 +0200 is 1697616610
    t0 = 1697616611 # after the wake
    t1 = 1697700000

    results = parse_pmset_log(t0, t1, log_output)
    assert len(results) == 0
