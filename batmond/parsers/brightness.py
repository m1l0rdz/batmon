"""Brightness from `corebrightnessdiag status-info` (NSDictionary text dump,
not strict plist - D8). Regex is FIXTURE-VERIFIED; adjust pattern to the
captured corebrightnessdiag.txt if it does not match. Never raises."""
from __future__ import annotations

import re
from typing import Optional

_RE = re.compile(r'(?:"?Brightness"?\s*=\s*"?|<key>DisplayServicesBrightness</key>\s*<real>)([0-9]*\.?[0-9]+)')


def parse_brightness(text: str) -> Optional[float]:
    if not text:
        return None
    m = _RE.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if 0.0 <= v <= 1.0:
        return v * 100.0
    if 1.0 < v <= 100.0:
        return v
    return None
