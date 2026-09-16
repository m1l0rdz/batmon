"""Validated cell history and sample-weighted summaries. Daemon-only writes.

Low-load selection is an observational filter, not a relaxed-OCV test or a
manufacturer fault threshold. Unverified resistance keys are not collected.
"""
import json
import math

# Sums and counts preserve weighting through daily rollups and missing sensors.
AGGREGATES = {
    'sample_count': 'COUNT(*)',
    'spread_sum': 'SUM(d.spread_mv)',
    'spread_count': 'COUNT(d.spread_mv)',
    'spread_max': 'MAX(d.spread_mv)',
    'low_load_sum': 'SUM(d.low_load_spread_mv)',
    'low_load_count': 'COUNT(d.low_load_spread_mv)',
    'c_rate_sum': 'SUM(d.design_c_rate)',
    'c_rate_count': 'COUNT(d.design_c_rate)',
    'c_rate_max': 'MAX(d.design_c_rate)',
    'temp_sum': 'SUM(s.temp_c)',
    'temp_count': 'COUNT(s.temp_c)',
    'temp_max': 'MAX(s.temp_c)',
    'soc_sum': 'SUM(s.soc_pct)',
    'soc_count': 'COUNT(s.soc_pct)',
    'charging_count': 'SUM(s.is_charging)',
}
COLUMNS = ','.join(AGGREGATES)
RAW_AGGREGATES = ','.join(f'{expression} AS {name}' for name, expression in AGGREGATES.items())
SUMMARY_AGGREGATES = ','.join(
    f'{"MAX" if name.endswith("_max") else "SUM"}({name}) AS {name}'
    for name in AGGREGATES)
JOIN = 'battery_diagnostics_samples d JOIN battery_samples s ON s.ts=d.ts'
_FIELDS = ','.join(f'{name} {"INTEGER" if name.endswith("count") else "REAL"}' for name in AGGREGATES)
DDL = f'''
CREATE TABLE IF NOT EXISTS battery_diagnostics_samples(
    ts INTEGER PRIMARY KEY, cells_json TEXT, spread_mv REAL,
    design_c_rate REAL, low_load_spread_mv REAL
);
CREATE TABLE IF NOT EXISTS battery_diagnostics_hourly(hour INTEGER PRIMARY KEY,{_FIELDS});
CREATE TABLE IF NOT EXISTS battery_diagnostics_daily(day TEXT PRIMARY KEY,{_FIELDS});
'''


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_cells(cells, pack_mv):
    """Reject incomplete packs and nonsensical values, not diagnose imbalance.

5% pack-sum tolerance only guards gross mismatches between asynchronous
SMC/ioreg reads. It is not measurement accuracy or a health threshold.
    """
    if not isinstance(cells, (tuple, list)) or not 2 <= len(cells) <= 4:
        return None
    if not all(_finite(v) and 1000 <= v <= 5000 for v in cells):
        return None
    if not _finite(pack_mv) or pack_mv <= 0 or abs(sum(cells) - pack_mv) > .05 * pack_mv:
        return None
    return tuple(cells)


def record(conn, sample):
    cells = valid_cells(sample.cell_voltage_mv, sample.voltage_mv)
    spread = max(cells) - min(cells) if cells else None
    current, capacity = sample.current_ma, sample.design_capacity_mah
    c_rate = (abs(current) / capacity if _finite(current) and _finite(capacity)
              and capacity > 0 else None)
    low_load = (c_rate is not None and c_rate <= .05 and not sample.is_charging
                and _finite(sample.soc_pct) and 20 <= sample.soc_pct <= 80
                and _finite(sample.temp_c) and 20 <= sample.temp_c <= 35)
    conn.execute('INSERT OR REPLACE INTO battery_diagnostics_samples '
                 '(ts,cells_json,spread_mv,design_c_rate,low_load_spread_mv) VALUES (?,?,?,?,?)',
                 (sample.ts, json.dumps(cells) if cells else None, spread, c_rate,
                  spread if low_load else None))


def roll_hour(conn, hour):
    conn.execute(f'INSERT OR REPLACE INTO battery_diagnostics_hourly(hour,{COLUMNS}) '
                 f'SELECT ?,{RAW_AGGREGATES} FROM {JOIN} WHERE d.ts>=? AND d.ts<? '
                 'HAVING COUNT(*)>0', (hour, hour, hour + 3600))


def roll_day(conn, day, hours):
    placeholders = ','.join('?' for _ in hours)
    conn.execute(f'INSERT OR REPLACE INTO battery_diagnostics_daily(day,{COLUMNS}) '
                 f'SELECT ?,{SUMMARY_AGGREGATES} FROM battery_diagnostics_hourly '
                 f'WHERE hour IN ({placeholders}) HAVING COUNT(*)>0 '
                 'AND SUM(sample_count)>=COALESCE((SELECT sample_count FROM '
                 'battery_diagnostics_daily WHERE day=?),0)', [day] + hours + [day])
