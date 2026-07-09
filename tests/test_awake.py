import os
import stat
import time

import pytest

from batmon_web.awake import AwakeManager

STUB = """#!/bin/bash
# caffeinate stub: record args, sleep until TERM
echo "$@" > "$0.args"
trap 'exit 0' TERM
while true; do sleep 1; done
"""


def _stub(tmp_path):
    p = tmp_path / "caffeinate_stub"
    p.write_text(STUB)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_toggle_lifecycle(tmp_path):
    m = AwakeManager(binary=_stub(tmp_path))
    assert m.is_on() is False
    assert m.set(True) is True
    pid = m._proc.pid
    # Poll up to 3s for the stub to write its args file (timing-safe)
    args_file = tmp_path / "caffeinate_stub.args"
    deadline = time.monotonic() + 3.0
    while not args_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert args_file.read_text().strip() == "-d -i"
    assert m.set(True) is True          # idempotent, same child
    assert m._proc.pid == pid
    assert m.set(False) is False
    time.sleep(1.0)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                 # child really gone


def test_dead_child_detected(tmp_path):
    m = AwakeManager(binary=_stub(tmp_path))
    m.set(True)
    m._proc.terminate()
    m._proc.wait()
    assert m.is_on() is False
