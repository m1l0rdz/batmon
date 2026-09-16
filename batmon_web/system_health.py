"""Cached, read-only macOS battery assessment, separate from raw capacity.

The system_profiler JSON keys are verified on the target Mac. Optional
fields remain unavailable if the OS changes or the command times out.
"""
import json
import subprocess
import time
from functools import lru_cache


@lru_cache(maxsize=1)
def read_health(cache_bucket):
    # The bucket bounds caching to ten minutes. The fixed read command never
    # accepts browser input and does not participate in database writes.
    result = dict(macos_capacity_pct=None, macos_condition=None,
                  macos_checked_ts=None)
    try:
        payload = json.loads(subprocess.check_output(
            ['/usr/sbin/system_profiler', 'SPPowerDataType', '-json'],
            text=True, timeout=5, stderr=subprocess.DEVNULL))
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
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        pass
    return result
