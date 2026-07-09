import json
import os
import sqlite3
import tempfile
import time
from unittest.mock import patch

from batmond import db as db_mod
from batmond.ipc import process_commands
from batmon_web.api_cmd import spool_command

def test_ipc_spool_and_process():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batmon.db")
        ipc_dir = os.path.join(td, "ipc")
        os.makedirs(ipc_dir)

        conn = db_mod.open_rw(db_path)

        # Patch paths
        with patch("batmon_web.api_cmd.SPOOL_DIR", ipc_dir), \
             patch("batmond.ipc.SPOOL_DIR", ipc_dir), \
             patch("subprocess.run") as mock_run:
             
            # Test spool. Charge limiting is not an IPC command: it is a
            # read-only mirror of the native macOS 80% limit (batmon cannot set
            # it on Apple Silicon), so only LPM commands go through the spool.
            fname1 = spool_command("lpm", {"enabled": True})
            fname2 = spool_command("auto_lpm_threshold", {"pct": 30})

            # Verify files are there
            files = os.listdir(ipc_dir)
            assert len(files) == 2

            # Process commands
            process_commands(conn)

            # Files should be deleted
            assert len(os.listdir(ipc_dir)) == 0

            mock_run.assert_any_call(["/usr/bin/pmset", "-a", "lowpowermode", "1"], check=True, capture_output=True)
            assert db_mod.get_state(conn, "lpm") == "1"

            # auto_lpm_threshold
            assert db_mod.get_state(conn, "auto_lpm_threshold") == "30"

        conn.close()

def test_ipc_bad_command():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batmon.db")
        ipc_dir = os.path.join(td, "ipc")
        os.makedirs(ipc_dir)
        conn = db_mod.open_rw(db_path)

        with patch("batmond.ipc.SPOOL_DIR", ipc_dir), \
             patch("batmond.ipc.log.warning") as mock_log:
             
            bad_file = os.path.join(ipc_dir, "cmd_123_abc.json")
            with open(bad_file, "w") as f:
                json.dump({"cmd": "unknown_cmd", "args": {}}, f)
                
            process_commands(conn)
            
            assert not os.path.exists(bad_file)
            mock_log.assert_called_with("Unknown IPC command: %s", "unknown_cmd")

        conn.close()

def test_ipc_bad_json():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batmon.db")
        ipc_dir = os.path.join(td, "ipc")
        os.makedirs(ipc_dir)
        conn = db_mod.open_rw(db_path)

        with patch("batmond.ipc.SPOOL_DIR", ipc_dir), \
             patch("batmond.ipc.log.error") as mock_log:
             
            bad_file = os.path.join(ipc_dir, "cmd_123_abc.json")
            with open(bad_file, "w") as f:
                f.write("{bad json")
                
            process_commands(conn)
            
            assert not os.path.exists(bad_file)
            assert mock_log.called

        conn.close()
