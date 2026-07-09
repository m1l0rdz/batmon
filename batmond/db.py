"""Schema and connection helpers. batmond is the ONLY writer (open_rw);
web/plugin/tests read via open_ro. Design doc section 6 + amendments D1-D4."""
import os
import sqlite3

SCHEMA_VERSION = 4

DDL = """
CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS battery_samples(
  ts             INTEGER PRIMARY KEY,
  soc_pct        REAL NOT NULL,
  current_ma     REAL,
  voltage_mv     REAL,
  watts          REAL,
  is_charging    INTEGER NOT NULL,
  on_ac          INTEGER NOT NULL,
  temp_c         REAL,
  brightness_pct REAL,
  assert_awake   INTEGER
);

CREATE TABLE IF NOT EXISTS app_energy(
  ts_minute      INTEGER NOT NULL,
  app            TEXT NOT NULL,
  pid_count      INTEGER NOT NULL,
  energy_impact  REAL NOT NULL,
  cpu_ms_per_s   REAL,
  gpu_ms_per_s   REAL,
  attributed_mwh REAL NOT NULL,
  PRIMARY KEY(ts_minute, app)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS component_power(
  ts_minute        INTEGER PRIMARY KEY,
  cpu_mw REAL, gpu_mw REAL, ane_mw REAL, dram_mw REAL,
  package_mw REAL, soc_temp_c REAL, ssd_temp_c REAL,
  thermal_pressure TEXT
);

CREATE TABLE IF NOT EXISTS rollup_hourly_battery(
  hour INTEGER PRIMARY KEY,
  soc_min REAL, soc_max REAL,
  wh_in REAL, wh_out REAL,
  avg_watts REAL,
  on_battery_sec INTEGER, on_ac_sec INTEGER,
  avg_brightness REAL,
  avg_cpu_mw REAL, avg_gpu_mw REAL, avg_ane_mw REAL, avg_package_mw REAL,
  avg_temp_c REAL, avg_soc_temp_c REAL, avg_ssd_temp_c REAL
);

CREATE TABLE IF NOT EXISTS rollup_hourly_apps(
  hour INTEGER NOT NULL,
  app TEXT NOT NULL,
  attributed_mwh REAL NOT NULL,
  avg_energy_impact REAL,
  PRIMARY KEY(hour, app)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS rollup_daily_battery(
  day TEXT PRIMARY KEY,
  soc_min REAL, soc_max REAL,
  wh_in REAL, wh_out REAL,
  avg_watts REAL,
  on_battery_sec INTEGER, on_ac_sec INTEGER,
  avg_brightness REAL,
  avg_cpu_mw REAL, avg_gpu_mw REAL, avg_ane_mw REAL, avg_package_mw REAL,
  avg_temp_c REAL, avg_soc_temp_c REAL, avg_ssd_temp_c REAL
);

CREATE TABLE IF NOT EXISTS rollup_daily_apps(
  day TEXT NOT NULL,
  app TEXT NOT NULL,
  attributed_mwh REAL NOT NULL,
  PRIMARY KEY(day, app)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK(kind IN ('battery','charging','full')),
  started INTEGER NOT NULL,
  ended INTEGER,
  soc_start REAL NOT NULL,
  soc_end REAL,
  wh REAL
);

CREATE TABLE IF NOT EXISTS battery_health_daily(
  day TEXT PRIMARY KEY,
  cycle_count INTEGER,
  max_capacity_pct REAL,
  design_capacity_mah REAL
);

CREATE TABLE IF NOT EXISTS anomalies(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  day TEXT NOT NULL,
  app TEXT NOT NULL,
  wh_today REAL NOT NULL,
  wh_baseline REAL NOT NULL,
  ratio REAL NOT NULL,
  detail TEXT,
  UNIQUE(day, app)
);

CREATE TABLE IF NOT EXISTS state(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dark_wakes(
  ts INTEGER,
  duration_sec INTEGER,
  reason TEXT,
  wh_drained REAL
);
"""


def open_rw(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.executescript(DDL)
    v_row = conn.execute("SELECT v FROM schema_version").fetchone()
    if v_row is None:
        conn.execute("INSERT INTO schema_version(v) VALUES (?)",
                     (SCHEMA_VERSION,))
        current_v = SCHEMA_VERSION
    else:
        current_v = v_row[0]

    if current_v < 2:
        # Vestigial kind column migration removed
        conn.execute("UPDATE schema_version SET v=2")
        
    if current_v < 3:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS dark_wakes(ts INTEGER, duration_sec INTEGER, reason TEXT, wh_drained REAL)"
        )
        conn.execute("UPDATE schema_version SET v=3")

    if current_v < 4:
        conn.execute("ALTER TABLE component_power ADD COLUMN soc_temp_c REAL")
        conn.execute("ALTER TABLE component_power ADD COLUMN ssd_temp_c REAL")
        conn.execute("ALTER TABLE rollup_hourly_battery ADD COLUMN avg_temp_c REAL")
        conn.execute("ALTER TABLE rollup_hourly_battery ADD COLUMN avg_soc_temp_c REAL")
        conn.execute("ALTER TABLE rollup_hourly_battery ADD COLUMN avg_ssd_temp_c REAL")
        conn.execute("ALTER TABLE rollup_daily_battery ADD COLUMN avg_temp_c REAL")
        conn.execute("ALTER TABLE rollup_daily_battery ADD COLUMN avg_soc_temp_c REAL")
        conn.execute("ALTER TABLE rollup_daily_battery ADD COLUMN avg_ssd_temp_c REAL")
        conn.execute("ALTER TABLE anomalies ADD COLUMN detail TEXT")
        conn.execute("UPDATE schema_version SET v=4")

    conn.commit()
    ensure_readable(path)
    ipc_dir = os.path.join(os.path.dirname(path), "ipc")
    os.makedirs(ipc_dir, exist_ok=True)
    return conn


def open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def ensure_readable(path: str) -> None:
    """WAL sidecars must be world-readable or readers cannot open the DB.
    Sidecars can appear after later writes, so callers re-run this
    periodically, not just at open."""
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        try:
            if os.path.exists(p):
                os.chmod(p, 0o644)
        except OSError:
            pass


def set_state(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)",
                 (key, value))


def get_state(conn, key: str):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None
