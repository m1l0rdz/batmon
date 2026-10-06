"""Detect that the Python install under a long-running process was replaced.

`brew upgrade` deletes the previous Cellar version, and a Command Line Tools
update rewrites /usr/bin/python3's framework. The running interpreter keeps
serving until it lazily imports a stdlib module (e.g. `_strptime` on the first
`datetime.strptime`), which then fails. Long-running batmon processes poll
this and exit cleanly so launchd KeepAlive restarts them on the new install.
"""
import os


def _stdlib_id():
    try:
        st = os.stat(os.__file__)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns)


_STARTUP_ID = _stdlib_id()


def interpreter_replaced() -> bool:
    return _stdlib_id() != _STARTUP_ID
