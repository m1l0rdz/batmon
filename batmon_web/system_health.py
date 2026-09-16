"""Cached, read-only macOS battery assessment, separate from raw capacity.

The system_profiler JSON keys are verified on the target Mac. Optional
fields remain unavailable if the OS changes or the command times out.
"""
import json
import subprocess
import threading
import time

REFRESH_SEC = 600
RETRY_SEC = 60
_EMPTY = dict(macos_capacity_pct=None, macos_condition=None,
              macos_checked_ts=None)
_lock = threading.Lock()
_first_lock = threading.Lock()
_state = {}


def reset():
    with _lock:
        _state.update(result=dict(_EMPTY), next_ts=None, thread=None)


reset()


def _query():
    """One fixed read command, never browser input. None when it fails."""
    try:
        payload = json.loads(subprocess.check_output(
            ['/usr/sbin/system_profiler', 'SPPowerDataType', '-json'],
            text=True, timeout=5, stderr=subprocess.DEVNULL))
        result = dict(_EMPTY)
        for row in payload.get('SPPowerDataType', []):
            info = row.get('sppower_battery_health_info')
            if not isinstance(info, dict):
                continue
            raw = info.get('sppower_battery_health_maximum_capacity')
            if raw is not None:
                value = float(str(raw).rstrip('%'))
                if 0 <= value <= 100:
                    result['macos_capacity_pct'] = value
            result['macos_condition'] = info.get('sppower_battery_health')
            result['macos_checked_ts'] = int(time.time())
            break
        return result
    except (OSError, subprocess.SubprocessError, ValueError, TypeError,
            AttributeError):
        return None


def _refresh(now_ts):
    result = _query()
    with _lock:
        # A failed read keeps the last good assessment and retries sooner.
        if result is not None:
            _state['result'] = result
        _state['next_ts'] = now_ts + (REFRESH_SEC if result is not None else RETRY_SEC)
        _state['thread'] = None


def read_health(now_ts):
    """Return the last assessment without blocking requests on system_profiler.

    Only the first read in a process runs inline, so the dashboard has a
    value; later refreshes run in a background thread and serve the last
    result meanwhile.
    """
    with _lock:
        first = _state['next_ts'] is None and _state['thread'] is None
        due = _state['next_ts'] is not None and now_ts >= _state['next_ts']
        if due and _state['thread'] is None:
            _state['thread'] = threading.Thread(
                target=_refresh, args=(now_ts,), daemon=True)
            _state['thread'].start()
        if not first:
            return dict(_state['result'])
    with _first_lock:
        if _state['next_ts'] is None:
            _refresh(now_ts)
    with _lock:
        return dict(_state['result'])
