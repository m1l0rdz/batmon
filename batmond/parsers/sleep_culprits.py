"""Attribute an abnormal sleep drain to processes, from `pmset -g log`.

Within the gap window [t0, t1]:
- "Wake Requests" lines: who SCHEDULED wakes (process=NAME, or the app named in
  info="...com.apple.<app>..."). why="woke".
- "Assertions" lines: who HELD the machine awake (PID n(NAME) with a
  Prevent*/NoIdle* assertion). why="kept-awake".

Not exact energy attribution - a ranked, deduped list of culprit processes.
Returns [] on any failure; never raises (daemon loop must survive).
"""
import re
import subprocess
from collections import Counter
from datetime import datetime

# batmon-web's own keep-awake child - never blame it.
_IGNORE = {"caffeinate"}
# real wakers/holders but not user-actionable apps; ranked last so an app wins.
_SYSTEM = {"powerd", "kernel_task", "kernelmanagerd", "kernel"}

_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\s[+-]\d{4})\s+"
    r"(Wake Requests|Assertions)\b(.*)$")
_WAKE_ITEM = re.compile(r"\[\*?process=([^\s\]]+)([^\]]*)\]")
_INFO = re.compile(r'info="([^"]*)"')
_BUNDLE = re.compile(r"[a-z]+\.[A-Za-z0-9]+\.([A-Za-z0-9]+)")
_ASSERT = re.compile(r"PID\s+\d+\(([^)]+)\)\s+\S+\s+(\S+)")


def _app_from_info(info):
    # Prefer the app bundle in info= over a proxy process (e.g. powerd waking
    # for com.apple.calaccessd...). Take the last bundle's third label.
    matches = _BUNDLE.findall(info or "")
    return matches[-1] if matches else None


def _add(counts, why, name, role):
    if not name or name in _IGNORE:
        return
    counts[name] += 1
    # "kept-awake" is the stronger signal; do not downgrade it to "woke".
    if why.get(name) != "kept-awake":
        why[name] = role


def parse_sleep_culprits(t0, t1, log_output=None):
    if log_output is None:
        try:
            log_output = subprocess.check_output(
                ["/usr/bin/pmset", "-g", "log"], text=True,
                stderr=subprocess.DEVNULL, timeout=20)
        except Exception:
            return []

    counts = Counter()
    why = {}
    for line in log_output.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        dt_str, kind, rest = m.groups()
        try:
            ts = int(datetime.strptime(
                dt_str, "%Y-%m-%d %H:%M:%S %z").timestamp())
        except ValueError:
            continue
        if not (t0 <= ts <= t1):
            continue
        if kind == "Wake Requests":
            for proc, tail in _WAKE_ITEM.findall(rest):
                info = _INFO.search(tail)
                name = _app_from_info(info.group(1) if info else "") or proc
                _add(counts, why, name, "woke")
        else:  # Assertions
            am = _ASSERT.search(rest)
            if am:
                name, atype = am.groups()
                if atype.startswith("Prevent") or "NoIdle" in atype:
                    _add(counts, why, name, "kept-awake")

    ranked = sorted(
        (p for p in counts if p not in _IGNORE),
        key=lambda p: (p in _SYSTEM, -counts[p], p))
    return [{"proc": p, "why": why[p], "n": counts[p]} for p in ranked[:3]]
