"""IPC Command Processor for batmond.
Scans the spool directory and executes pending commands.
"""
import glob
import json
import logging
import os
import subprocess
from typing import Any, Dict

from batmond import db as db_mod

SPOOL_DIR = "/usr/local/var/batmon/ipc"
log = logging.getLogger("batmond")

def process_commands(conn) -> None:
    """Scans the IPC spool directory and executes commands."""
    if not os.path.isdir(SPOOL_DIR):
        return

    pattern = os.path.join(SPOOL_DIR, "cmd_*.json")
    for filepath in sorted(glob.glob(pattern)):
        try:
            with open(filepath, "r") as f:
                payload = json.load(f)
            
            cmd = payload.get("cmd")
            args = payload.get("args", {})
            _dispatch(conn, cmd, args)
            
        except Exception as e:
            log.error("Failed to process IPC command %s: %s", filepath, e)
        finally:
            try:
                os.remove(filepath)
            except OSError:
                pass

def _dispatch(conn, cmd: str, args: Dict[str, Any]) -> None:
    if cmd == "lpm":
        enabled = args.get("enabled", False)
        val = "1" if enabled else "0"
        from batmond import lpm
        lpm.set_low_power_mode(enabled)
        db_mod.set_state(conn, "lpm", val)
        conn.commit()
    
    elif cmd == "auto_lpm_threshold":
        pct = args.get("pct")
        if pct is not None:
            db_mod.set_state(conn, "auto_lpm_threshold", str(pct))
            conn.commit()
            log.info("Set auto_lpm_threshold to %s", pct)
            
    else:
        log.warning("Unknown IPC command: %s", cmd)
