import re
import subprocess
from datetime import datetime

def parse_pmset_log(t0: int, t1: int, log_output: str = None) -> list[dict]:
    """
    Parses `pmset -g log` to extract DarkWake (background maintenance) events
    between t0 and t1. Full `Wake` events are USER wakes (lid open, key/trackpad
    -> "UserActivity"/"HID Activity") and are NOT dark wakes, so they are
    ignored. If log_output is None, runs the command.
    Returns [{'ts': int, 'duration_sec': int, 'reason': str}, ...]
    """
    if log_output is None:
        try:
            log_output = subprocess.check_output(["/usr/bin/pmset", "-g", "log"], text=True, stderr=subprocess.DEVNULL, timeout=20)
        except Exception:
            return []

    pattern = re.compile(
        r'^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\s[+-]\d{4})\s+(Wake|DarkWake|Sleep)\s+(?:.*due to\s+(.*?))?(?:$|\n)',
        re.MULTILINE
    )

    raw_events = []
    for match in pattern.finditer(log_output):
        dt_str, evt_type, reason = match.groups()
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S %z")
            ts = int(dt.timestamp())
        except ValueError:
            continue
        
        reason = reason.strip() if reason else ""
        # Drop the power/charge status pmset appends after the reason, e.g.
        # "... Using BATT (Charge:64%) 45 secs" -> keep only the wake cause.
        reason = re.split(r"\s+Using\s+", reason, maxsplit=1)[0].strip()
        raw_events.append((ts, evt_type, reason))

    raw_events.sort(key=lambda x: x[0])

    results = []
    for i, (ts, evt_type, reason) in enumerate(raw_events):
        if evt_type == "DarkWake" and t0 <= ts <= t1:
            next_ts = t1
            for j in range(i + 1, len(raw_events)):
                if raw_events[j][1] == "Sleep":
                    next_ts = min(raw_events[j][0], t1)
                    break
            
            duration = max(0, next_ts - ts)
            results.append({
                "ts": ts,
                "duration_sec": duration,
                "reason": reason
            })

    return results
