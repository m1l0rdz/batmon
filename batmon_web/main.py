"""uvicorn entry: `uvicorn batmon_web.main:app --host 127.0.0.1 --port 8899`"""
import os

from batmon_web.app import create_app

app = create_app(os.environ.get("BATMON_DB",
                                "/usr/local/var/batmon/batmon.db"))
