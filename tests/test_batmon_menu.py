import pytest
import json
import urllib.error
from unittest.mock import MagicMock, patch
from ui.batmon_menu import BatmonApp

@pytest.fixture
def mock_urlopen():
    with patch("urllib.request.urlopen") as mock:
        yield mock

@pytest.fixture
def mock_webbrowser():
    with patch("webbrowser.open") as mock:
        yield mock

def test_app_init(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({}).encode("utf-8")
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    app = BatmonApp()
    # Check menu items exist
    assert "Open dashboard" in app.menu
    assert "Open Battery Settings" in app.menu

def test_update_menu_success(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "component": {"package_mw": 12500},
        "sample": {"soc_pct": 85, "watts": 12.5}
    }).encode("utf-8")
    
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    
    app.update_menu(None)
    assert app.title == "12.5W ^ 85%"

def test_update_menu_not_charging(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "component": {"package_mw": 5000},
        "sample": {"soc_pct": 90, "watts": -5.0}
    }).encode("utf-8")
    
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    
    app.update_menu(None)
    assert app.title == "5.0W v 90%"

def test_update_menu_api_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
    app = BatmonApp()
    
    app.update_menu(None)
    assert app.title == "batmon: API error"

def test_open_dashboard(mock_urlopen, mock_webbrowser):
    mock_response = MagicMock()
    mock_response.read.return_value = b"{}"
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    app.open_dashboard(None)
    mock_webbrowser.assert_called_once_with("http://127.0.0.1:8899/")

def test_open_battery_settings(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"{}"
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    app.open_battery_settings(None)
    assert mock_urlopen.call_count == 3
    req = mock_urlopen.call_args_list[2][0][0]
    assert req.full_url == "http://127.0.0.1:8899/api/open_battery_settings"
    assert req.method == "POST"

def test_rebuild_menu_full(mock_urlopen):
    mock_response = MagicMock()
    data = {
        "component": {"package_mw": 12500, "cpu_mw": 500, "gpu_mw": 100},
        "sample": {"soc_pct": 85, "watts": 12.5, "temp_c": 35.5},
        "forecast": {"mode": "charging", "minutes": 125},
        "health": {"raw_current_capacity_mah": 5000, "max_capacity_pct": 98, "cycle_count": 12},
        "session": {"soc_now": 85, "soc_start": 45, "kind": "ac", "duration_sec": 3600},
        "top_apps": [{"app": "Safari", "attributed_wh": 1.5}, {"app": "Mail", "attributed_wh": 0.005}],
        "awake": True,
        "charge_limit": {"level": 80, "holding": True, "todays_peak_soc": 82},
        "lpm": "1"
    }
    mock_response.read.return_value = json.dumps(data).encode("utf-8")
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    
    app.update_menu(None)
    
    menu_keys = list(app.menu.keys())
    assert "full in 2h 5m" in menu_keys
    assert "5000 mAh - 35.5 C - health 98% - 12 cycles" in menu_keys
    assert "CPU 500 - GPU 100 - pkg 12500 mW" in menu_keys
    assert "On AC 1h 0m - +40%" in menu_keys
    assert "Top apps, last hour" in menu_keys
    assert "Safari - 1.50 Wh" in menu_keys
    assert "Mail - 5.0 mWh" in menu_keys
    
    assert app.menu["Keep awake"].state == True
    assert "Battery limit 80%: active (peak 82%)" in menu_keys
    assert app.menu["Low Power Mode"].state == True

def test_toggle_awake(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"{}"
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    sender = MagicMock()
    sender.state = False
    app.toggle_awake(sender)
    
    assert mock_urlopen.call_count == 5
    req = mock_urlopen.call_args_list[2][0][0]
    assert req.full_url == "http://127.0.0.1:8899/api/awake"
    assert req.method == "POST"
    assert json.loads(req.data.decode("utf-8")) == {"on": True}

def test_toggle_lpm(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"{}"
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    sender = MagicMock()
    sender.state = True
    app.toggle_lpm(sender)
    
    assert mock_urlopen.call_count == 5
    req = mock_urlopen.call_args_list[2][0][0]
    assert req.full_url == "http://127.0.0.1:8899/api/cmd"
    assert req.method == "POST"
    assert json.loads(req.data.decode("utf-8")) == {"cmd": "lpm", "args": {"enabled": False}}

@patch("rumps.notification")
def test_check_anomalies(mock_notification, mock_urlopen):
    mock_response_now = MagicMock()
    mock_response_now.read.return_value = b"{}"

    # The anomalies endpoint payload is mutable so the test can add a new
    # anomaly after startup and verify only that one notifies.
    payload = {"data": json.dumps([
        {"id": 1, "ts": 123, "day": "2026-07-09", "app": "Safari",
         "wh_today": 1.5, "wh_baseline": 0.5, "ratio": 3.0},
        {"id": 2, "ts": 124, "day": "2026-07-09",
         "app": "__SYSTEM_SLEEP_DRAIN__", "wh_today": 5.0,
         "wh_baseline": 0, "ratio": 0},
    ]).encode("utf-8")}

    def side_effect(req, *args, **kwargs):
        ctx = MagicMock()
        resp = MagicMock()
        if "anomalies" in req.full_url:
            resp.read.return_value = payload["data"]
        else:
            resp.read.return_value = b"{}"
        ctx.__enter__.return_value = resp
        return ctx

    mock_urlopen.side_effect = side_effect

    # First poll (during __init__) must SEED, not notify - otherwise every
    # pre-existing anomaly re-fires on each launchd (re)start.
    app = BatmonApp()
    assert mock_notification.call_count == 0
    assert app.last_anomaly_id == 2

    # A genuinely new anomaly appears: now it should notify, once per new row.
    payload["data"] = json.dumps([
        {"id": 3, "ts": 125, "day": "2026-07-09", "app": "Safari",
         "wh_today": 1.5, "wh_baseline": 0.5, "ratio": 3.0},
        {"id": 4, "ts": 126, "day": "2026-07-09",
         "app": "__SYSTEM_SLEEP_DRAIN__", "wh_today": 5.0,
         "wh_baseline": 0, "ratio": 0},
    ]).encode("utf-8")
    app.check_anomalies()
    assert mock_notification.call_count == 2
    mock_notification.assert_any_call(
        "batmon: Safari anomaly", "",
        "1.5 Wh today vs 0.5 Wh baseline (3.0x)")
    mock_notification.assert_any_call(
        "😴 Excessive Sleep Drain", "", "Battery dropped 5.0% while asleep")
    assert app.last_anomaly_id == 4

def test_rebuild_menu_score(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"score": {"score": 94, "grade": "excellent"}}).encode("utf-8")
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    app.update_menu(None)
    assert "Score 94/100 (excellent)" in app.menu.keys()

def test_rebuild_menu_no_score(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"{}"
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_ctx
    
    app = BatmonApp()
    app.update_menu(None)
    # Just check it doesn't crash and adds no score line
    assert not any("Score" in k for k in app.menu.keys())
