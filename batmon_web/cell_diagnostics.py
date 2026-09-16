"""Read-only diagnostic history. No SMC access or database migration here."""
from datetime import datetime, timedelta

from batmond.cell_diagnostics import RAW_AGGREGATES, JOIN

RANGES = {'24h': 1, '7d': 7, '30d': 30, '90d': 90, '1y': 365}
RESISTANCE = dict(status='unverified', keys=['BR00..BR14', 'B0R1..B0R3'],
                  note='Not collected: no same-machine reference confirms the meaning, units and scaling of these SMC keys. '
                       'A TI resistance-table description does not validate an Apple SMC mapping. '
                       'Dividing pack voltage by current does not measure internal resistance.')
FILTER_NOTE = ('Low-load subset: |current| / design capacity <= 0.05 C, not charging, '
               '20-80% charge and battery sensor 20-35 C. These are comparison filters, '
               'not health limits or proof of electrochemical rest. No universal failure threshold is applied.')


def _rows(cursor):
    keys = [c[0] for c in cursor.description]
    return [dict(zip(keys, row)) for row in cursor]


def _point(row):
    out = dict(row)
    for result, total, count in (
            ('spread_avg_mv','spread_sum','spread_count'),
            ('low_load_avg_mv','low_load_sum','low_load_count'),
            ('c_rate_avg','c_rate_sum','c_rate_count'),
            ('temp_avg_c','temp_sum','temp_count'),
            ('soc_avg_pct','soc_sum','soc_count')):
        out[result] = row[total] / row[count] if row[count] else None
    out['spread_max_mv'] = row['spread_max']
    out['temp_max_c'] = row['temp_max']
    return out


def history(conn, now_ts, range_name='24h'):
    result = dict(status='ok', range=range_name, points=[], resistance=RESISTANCE,
                  filter_note=FILTER_NOTE, resolution_sec=300 if range_name == '24h' else 3600,
                  note='Sample-weighted means and sampled maxima; short peaks between reads can be missed. '
                       'All power sources are combined. Compare charge, temperature and load before interpreting a change. '
                       'Missing telemetry is not zero. Raw 48h, hourly 90 days, daily summaries retained indefinitely.')
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='battery_diagnostics_samples'").fetchone()
    if not exists:
        result['status'] = 'awaiting_collector'
        return result
    start = now_ts - RANGES[range_name] * 86400
    if range_name == '24h':
        cell_sql = ','.join(f'AVG(json_extract(d.cells_json,\'$[{i}]\')) AS cell{i+1}_mv' for i in range(4))
        rows = _rows(conn.execute(f'SELECT (d.ts/300)*300 AS ts,{RAW_AGGREGATES},{cell_sql} '
                                 f'FROM {JOIN} WHERE d.ts>=? AND d.ts<=? GROUP BY (d.ts/300) ORDER BY ts',
                                 (start, now_ts)))
    elif range_name == '1y':
        today = datetime.fromtimestamp(now_ts).date()
        rows = _rows(conn.execute('SELECT * FROM battery_diagnostics_daily WHERE day>=? AND day<? ORDER BY day',
                                  ((today-timedelta(days=365)).isoformat(), today.isoformat())))
        for row in rows:
            row['ts'] = int(datetime.fromisoformat(row['day'] + 'T12:00:00').timestamp())
        result['resolution_sec'] = 25 * 3600  # local calendar days can include DST
        result['note'] += ' The 1y view shows completed local calendar days only.'
    else:
        hour = now_ts - now_ts % 3600
        # Reaggregate retained raw hours; this includes the current partial hour
        # and avoids a stale summary or double count when the daemon rolls it.
        earliest = conn.execute('SELECT MIN(ts) FROM battery_diagnostics_samples WHERE ts>=? AND ts<=?',
                                (start, now_ts)).fetchone()[0]
        boundary = earliest - earliest % 3600 if earliest is not None else hour + 3600
        # Only replace a complete raw hour. The first retained hour may be partial.
        if earliest is not None and earliest % 3600:
            boundary += 3600
        rows = _rows(conn.execute('SELECT hour AS ts,* FROM battery_diagnostics_hourly WHERE hour>=? AND hour<? ORDER BY hour',
                                  (start, min(boundary, hour + 3600))))
        rows += _rows(conn.execute(f'SELECT (d.ts/3600)*3600 AS ts,{RAW_AGGREGATES} FROM {JOIN} '
                                  'WHERE d.ts>=? AND d.ts<=? GROUP BY (d.ts/3600) ORDER BY ts',
                                  (max(start, boundary), now_ts)))
        # Fresh installs often start mid-hour with no summary yet.
        if earliest is not None:
            first_hour = earliest - earliest % 3600
            if first_hour >= start and not any(r['ts'] == first_hour for r in rows):
                rows += _rows(conn.execute(f'SELECT ? AS ts,{RAW_AGGREGATES} FROM {JOIN} '
                                          'WHERE d.ts>=? AND d.ts<? HAVING COUNT(*)>0',
                                          (first_hour, earliest, min(first_hour+3600,now_ts+1))))
        rows.sort(key=lambda r: r['ts'])
    result['points'] = [_point(r) for r in rows]
    result['sample_count'] = sum(r['sample_count'] for r in rows)
    result['cell_samples'] = sum(r['spread_count'] for r in rows)
    result['low_load_samples'] = sum(r['low_load_count'] for r in rows)
    if not rows:
        result['status'] = 'collecting'
    return result
