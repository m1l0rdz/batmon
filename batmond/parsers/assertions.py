"""assert_awake from `pmset -g assertions` system-wide summary."""
import re

_RE = re.compile(r"^\s*PreventUserIdleDisplaySleep\s+(\d+)", re.MULTILINE)


def parse_assert_awake(text: str) -> bool:
    m = _RE.search(text or "")
    return bool(m and int(m.group(1)) > 0)
