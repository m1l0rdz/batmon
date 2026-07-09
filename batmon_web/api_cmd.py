"""IPC command spooler for batmon-web.
Writes command JSON files to the spool directory for batmond to execute.
"""
import json
import os
import secrets
import time
from typing import Any, Dict

SPOOL_DIR = "/usr/local/var/batmon/ipc"

def spool_command(cmd: str, args: Dict[str, Any]) -> str:
    """Spools a command to be executed by batmond."""
    payload = {
        "cmd": cmd,
        "args": args,
        "ts": int(time.time())
    }
    
    rand_id = secrets.token_hex(4)
    filename = f"cmd_{payload['ts']}_{rand_id}.json"
    filepath = os.path.join(SPOOL_DIR, filename)
    
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f)
    os.rename(tmp_path, filepath)
    
    return filename
