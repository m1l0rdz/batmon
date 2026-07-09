"""batmond entry point. Production: launchd runs
`/usr/bin/python3 -m batmond` (PYTHONPATH=/usr/local/libexec/batmon).
Development/tests: --dry-run replays fixtures without root."""
import argparse
import logging
import logging.handlers
import os
import sys
import tempfile

from batmond import db as db_mod
from batmond.collector import Collector
from batmond.sources import FixtureSource, LiveSource

DEFAULT_DB = "/usr/local/var/batmon/batmon.db"
LOG_PATH = "/usr/local/var/batmon/batmond.log"


def _setup_logging(dry_run: bool):
    log = logging.getLogger("batmond")
    log.setLevel(logging.INFO)
    if dry_run:
        handler = logging.StreamHandler(sys.stderr)
    else:
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


def main(argv=None) -> str:
    ap = argparse.ArgumentParser(prog="batmond")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures", default="tests/fixtures")
    ap.add_argument("--ticks", type=int, default=480)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    _setup_logging(args.dry_run)
    if args.dry_run:
        db_path = args.db or os.path.join(tempfile.mkdtemp("batmon"),
                                          "batmon.db")
        conn = db_mod.open_rw(db_path)
        db_mod.ensure_readable(db_path)
        Collector(conn, FixtureSource(args.fixtures), db_path).run_dry(
            args.ticks)
        print(db_path)
        return db_path
    db_path = args.db or DEFAULT_DB
    conn = db_mod.open_rw(db_path)
    db_mod.ensure_readable(db_path)
    logging.getLogger("batmond").info(
        "batmond starting: db=%s python=%s", db_path, sys.version.split()[0])
    Collector(conn, LiveSource(), db_path).run_forever()
    return db_path


if __name__ == "__main__":
    main()
