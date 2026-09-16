"""Read-only recent evidence from retained raw telemetry.

Battery intervals are linear endpoint estimates, never extrapolated across
source changes or gaps over 90 seconds. Power is signed at the sensor: negative
means discharge. average_watts here describes battery discharge draw; AC power
is not adapter input or total machine power.

Collector._powermetrics_step rounds its tick timestamp down to a minute and
stores a 5-second burst extrapolated to one minute. Exact burst boundaries are
not persisted. Source assignment uses a continuously observed 65-second minute
envelope as an estimate, not verified simultaneous measurement. Collection
latency can shift the burst. No chip/battery residual is supported.
"""
from bisect import bisect_right
from collections import defaultdict
import math


RETENTION_HOURS = 48
MAX_GAP = 90
FIELDS = ('watts', 'brightness_pct', 'soc_pct', 'temp_c')


def _finite(value):
    return value is not None and math.isfinite(value)


def _segments(conn, start, end):
    rows = conn.execute(
        'SELECT ts,on_ac,is_charging,watts,brightness_pct,soc_pct,temp_c '
        'FROM battery_samples WHERE ts>=? AND ts<=? ORDER BY ts',
        (start - MAX_GAP, end + MAX_GAP)).fetchall()
    result = []
    for a, b in zip(rows, rows[1:]):
        if not 0 < b[0] - a[0] <= MAX_GAP or a[1] != b[1]:
            continue
        segment = {'start': a[0], 'end': b[0], 'on_ac': bool(a[1]),
                   'charging': bool(a[2] and b[2])}
        for index, name in enumerate(FIELDS, 3):
            segment[name] = (a[index], b[index])
        clipped = _clip(segment, start, end)
        if clipped:
            result.append(clipped)
    return result


def _clip(segment, start, end):
    lo, hi = max(start, segment['start']), min(end, segment['end'])
    if hi <= lo:
        return None
    result = dict(segment, start=lo, end=hi)
    duration = segment['end'] - segment['start']
    for name in FIELDS:
        a, b = segment[name]
        if _finite(a) and _finite(b):
            result[name] = (a + (b - a) * (lo - segment['start']) / duration,
                            a + (b - a) * (hi - segment['start']) / duration)
        else:
            result[name] = (None, None)
    return result


def _positive_average(a, b):
    """Mean positive part of a linear signal, including a zero crossing."""
    if a >= 0 and b >= 0:
        return (a + b) / 2
    if a <= 0 and b <= 0:
        return 0.0
    positive = max(a, b)
    return positive * positive / (2 * abs(b - a))


def _stats(segments):
    observed = energy = brightness = out = incoming = brightness_sum = 0.0
    for segment in segments:
        duration = segment['end'] - segment['start']
        observed += duration
        a, b = segment['watts']
        if a is not None and b is not None:
            energy += duration
            out += _positive_average(-a, -b) * duration
            incoming += _positive_average(a, b) * duration
        a, b = segment['brightness_pct']
        if a is not None and b is not None:
            brightness += duration
            brightness_sum += (a + b) / 2 * duration
    return {'observed_h': observed / 3600,
            'energy_observed_h': energy / 3600,
            'brightness_observed_h': brightness / 3600,
            'wh_out': out / 3600 if energy else None,
            'wh_in': incoming / 3600 if energy else None,
            'average_watts': out / energy if energy else None,
            'avg_brightness': brightness_sum / brightness if brightness else None}


def _exposure(segments):
    soc = temp = charging_temp = charging_sum = 0.0
    above = {80: 0.0, 90: 0.0, 95: 0.0}
    for segment in segments:
        duration = segment['end'] - segment['start']
        a, b = segment['soc_pct']
        if a is not None and b is not None:
            soc += duration
            for threshold in above:
                if min(a, b) >= threshold:
                    above[threshold] += duration
        a, b = segment['temp_c']
        if a is not None and b is not None:
            temp += duration
            if segment['charging'] and segment['on_ac']:
                charging_temp += duration
                charging_sum += (a + b) / 2 * duration
    return {'soc_observed_h': soc / 3600,
            **{f'above_{k}_h': v / 3600 if soc else None for k, v in above.items()},
            'temp_observed_h': temp / 3600,
            'charging_temp_observed_h': charging_temp / 3600,
            'avg_charging_temp_c': charging_sum / charging_temp if charging_temp else None}


def _source_runs(segments):
    runs = []
    for segment in segments:
        if (runs and runs[-1]['end'] == segment['start']
                and runs[-1]['on_ac'] == segment['on_ac']):
            runs[-1]['end'] = segment['end']
        else:
            runs.append({key: segment[key] for key in ('start', 'end', 'on_ac')})
    return runs


def _rank(totals):
    total = sum(totals.values())
    return [{'app': app, 'attributed_wh': wh,
             'share_pct': wh / total * 100 if total > 0 else None}
            for app, wh in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))]


def recent_observations(conn, now_ts, hours=24):
    """Return coverage-aware raw observations, within the 48-hour retention."""
    if not _finite(hours) or not 0 < hours <= RETENTION_HOURS:
        raise ValueError('hours must be greater than zero and at most 48')
    if not _finite(now_ts):
        raise ValueError('now_ts must be finite')
    start, end = now_ts - hours * 3600, now_ts
    segments = _segments(conn, start, end)
    runs = _source_runs(segments)
    run_starts = [run['start'] for run in runs]
    segment_ends = [segment['end'] for segment in segments]
    components = conn.execute(
        'SELECT ts_minute,package_mw FROM component_power '
        'WHERE ts_minute>=? AND ts_minute+60<=? ORDER BY ts_minute',
        (start, end)).fetchall()
    sources = {}
    packages = {}
    matched_seconds = battery_energy = chip_energy = 0.0
    last_matched_end = start
    for minute, package in components:
        if not _finite(package) or package < 0:
            continue
        packages[minute] = package
        run_index = bisect_right(run_starts, minute) - 1
        if run_index < 0 or runs[run_index]['end'] < minute + 65:
            continue
        source = 'ac' if runs[run_index]['on_ac'] else 'battery'
        sources[minute] = source
        if source != 'battery' or minute < last_matched_end:
            continue
        index = bisect_right(segment_ends, minute)
        matched = []
        while index < len(segments) and segments[index]['start'] < minute + 60:
            matched.append(_clip(segments[index], minute, minute + 60))
            index += 1
        stats = _stats(matched)
        if not math.isclose(stats['energy_observed_h'] * 3600, 60):
            continue
        matched_seconds += 60
        battery_energy += stats['wh_out'] * 3600
        chip_energy += package / 1000 * 60
        last_matched_end = minute + 60
    totals = {key: defaultdict(float) for key in ('all', 'battery', 'ac')}
    for minute, app, mwh in conn.execute(
            'SELECT ts_minute,app,attributed_mwh FROM app_energy '
            'WHERE ts_minute>=? AND ts_minute+60<=?', (start, end)):
        if minute not in packages or not _finite(mwh) or mwh < 0:
            continue
        totals['all'][app] += mwh / 1000
        if minute in sources:
            totals[sources[minute]][app] += mwh / 1000
    battery = _stats([s for s in segments if not s['on_ac']])
    ac = _stats([s for s in segments if s['on_ac']])
    return {'start_ts': start, 'end_ts': end, 'retention_hours': RETENTION_HOURS,
            'observed_h': battery['observed_h'] + ac['observed_h'],
            'battery': battery, 'ac': ac, 'exposure': _exposure(segments),
            'matched': {
                'status': 'estimate' if matched_seconds else 'unavailable',
                'observed_h': matched_seconds / 3600,
                'battery_watts': battery_energy / matched_seconds if matched_seconds else None,
                'chip_watts': chip_energy / matched_seconds if matched_seconds else None,
                'unattributed_watts': None,
                'note': 'Minute-window estimate only. Battery readings are about 15 seconds apart; '
                        'chip power is a 5-second burst per minute. Exact burst time is not saved '
                        'and collection latency can shift it. Source alignment uses a continuously '
                        'observed 65-second envelope. These are different measurements, not an '
                        'energy balance; their difference cannot identify display or device watts.'},
            'apps': {key: _rank(value) for key, value in totals.items()},
            'apps_note': 'Attributed sample estimates extrapolated from a 5-second burst to one minute. '
                         'Only complete minute buckets are included; source groups require a '
                         'continuous 65-second same-source envelope. Exact burst timing is not saved, '
                         'so source alignment remains an estimate. Unknown source remains in All.'}


def experiment(conn, start, split, end):
    """Compare two observed phases; comparable never means causal."""
    if (not all(_finite(value) for value in (start, split, end))
            or split - start < 300 or end - split < 300
            or end - start > RETENTION_HOURS * 3600):
        raise ValueError('Two ordered phases of at least 300 seconds are required, within 48 hours')
    segments = _segments(conn, start, end)
    phases = []
    reasons = []
    for label, lo, hi in (('Before', start, split), ('After', split, end)):
        parts = [part for segment in segments if (part := _clip(segment, lo, hi))]
        stats = _stats(parts)
        continuous = math.isclose(stats['observed_h'] * 3600, hi - lo, abs_tol=1e-6)
        battery_only = bool(parts) and all(not part['on_ac'] for part in parts)
        stats.update(start_ts=lo, end_ts=hi, continuous=continuous, battery_only=battery_only)
        phases.append(stats)
        if not continuous:
            reasons.append(f'{label}: continuous observation is required; gaps or source changes were found.')
        if not battery_only:
            reasons.append(f'{label}: the whole phase must be on battery.')
        if not math.isclose(stats['energy_observed_h'] * 3600, hi - lo, abs_tol=1e-6):
            reasons.append(f'{label}: power readings are missing.')
        if not math.isclose(stats['brightness_observed_h'] * 3600, hi - lo, abs_tol=1e-6):
            reasons.append(f'{label}: brightness readings are missing.')
    before, after = phases
    if (before['avg_brightness'] is not None and after['avg_brightness'] is not None
            and abs(before['avg_brightness'] - after['avg_brightness']) > 5):
        reasons.append('Average brightness differs by more than 5 percentage points.')
    if before['average_watts'] is not None and before['average_watts'] <= 0:
        reasons.append('Before: positive battery discharge power is required for a percentage comparison.')
    comparable = not reasons
    return {'status': 'comparable' if comparable else 'insufficient_data',
            'reasons': reasons, 'before': before, 'after': after,
            'power_change_pct': ((after['average_watts'] / before['average_watts'] - 1) * 100
                                 if comparable else None),
            'note': 'Observed before/after comparison only, not evidence of causality. '
                    'Keep workload and other conditions consistent yourself.'}
