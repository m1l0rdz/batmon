"""uvicorn entry: `uvicorn batmon_web.main:app --host 127.0.0.1 --port 8899`"""
import os
import signal
import sys
import threading
import time
from contextlib import asynccontextmanager

from batmon_web.app import create_app
from batmond.stdlib_guard import interpreter_replaced


def _exit_when_interpreter_replaced():
    # SIGTERM, not os._exit: uvicorn shuts down gracefully and atexit stops
    # the caffeinate child. launchd KeepAlive restarts us on the new Python.
    while not interpreter_replaced():
        time.sleep(60)
    print("Python install changed; exiting for launchd restart",
          file=sys.stderr, flush=True)
    os.kill(os.getpid(), signal.SIGTERM)


@asynccontextmanager
async def _lifespan(_app):
    threading.Thread(target=_exit_when_interpreter_replaced,
                     daemon=True).start()
    yield


app = create_app(os.environ.get("BATMON_DB",
                                "/usr/local/var/batmon/batmon.db"),
                 lifespan=_lifespan)
