"""Keep-awake toggle (design 5.5). Exactly `caffeinate -d -i`:
PreventUserIdleDisplaySleep + PreventUserIdleSystemSleep. Never -s
(would fight lid close on AC semantics) and never -u. State lives in
process memory; default OFF; dies with the web process (fail-safe)."""
import atexit
import subprocess

CAFFEINATE_ARGS = ["-d", "-i"]


class AwakeManager:
    def __init__(self, binary: str = "/usr/bin/caffeinate"):
        self._binary = binary
        self._proc = None
        atexit.register(self._kill)

    def is_on(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def set(self, on: bool) -> bool:
        if on and not self.is_on():
            self._proc = subprocess.Popen([self._binary] + CAFFEINATE_ARGS)
        elif not on:
            self._kill()
        return self.is_on()

    def _kill(self):
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
