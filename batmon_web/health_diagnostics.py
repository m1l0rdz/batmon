"""Observed capacity shifts and short-horizon, held-out trend validation.

These describe raw controller readings, not chemistry or service diagnosis.
"""
from datetime import date, timedelta
from statistics import median


def _fit(rows):
    origin = rows[0]['day'].toordinal()
    points = [(r['day'].toordinal() - origin, r['capacity']) for r in rows]
    slopes = [(by - ay) / (bx - ax)
              for i, (ax, ay) in enumerate(points)
              for bx, by in points[i + 1:] if bx > ax]
    slope = median(slopes)
    intercept = median(y - slope * x for x, y in points)
    return lambda day: intercept + slope * (day.toordinal() - origin)


def diagnostics(conn, today):
    rows = [dict(day=date.fromisoformat(d), capacity=c, cycles=cy, design=de)
            for d, c, cy, de in conn.execute(
                'SELECT day,max_capacity_pct,cycle_count,design_capacity_mah '
                'FROM battery_health_daily WHERE day <= ? ORDER BY day',
                (today.isoformat(),))]
    shifts = []
    for previous, current in zip(rows, rows[1:]):
        reasons = []
        change = None
        if previous['capacity'] is not None and current['capacity'] is not None:
            change = current['capacity'] - previous['capacity']
            if (current['day'] - previous['day']).days <= 3 and abs(change) >= 3:
                reasons.append('Raw capacity changed by at least 3 percentage points between nearby readings.')
        if (previous['cycles'] is not None and current['cycles'] is not None
                and current['cycles'] < previous['cycles']):
            reasons.append('Cycle counter decreased; a measurement or hardware change is possible.')
        if (previous['design'] and current['design']
                and abs(current['design'] / previous['design'] - 1) > .05):
            reasons.append('Reported design capacity changed by more than 5%.')
        if reasons:
            shifts.append(dict(day=current['day'].isoformat(), change_pp=change,
                               reason=' '.join(reasons)))

    # Never train across an observed measurement boundary. A new regime must
    # earn its own history; a jump does not prove replacement or recalibration.
    segment_start = date.fromisoformat(shifts[-1]['day']) if shifts else date.min
    valid = [r for r in rows if r['capacity'] is not None and r['day'] >= segment_start]
    errors, baseline_errors = [], []
    windows = 0
    # Disjoint test weeks, each trained only on the preceding 28 calendar days.
    for week in range(12):
        end = today + timedelta(days=1) - timedelta(days=7 * week)
        split = end - timedelta(days=7)
        train = [r for r in valid if split - timedelta(days=28) <= r['day'] < split]
        test = [r for r in valid if split <= r['day'] < end]
        recent = [r['capacity'] for r in train if r['day'] >= split - timedelta(days=7)]
        if len(train) < 21 or len(test) < 4 or len(recent) < 4:
            continue
        predict = _fit(train)
        baseline = median(recent)
        errors.extend(abs(predict(r['day']) - r['capacity']) for r in test)
        baseline_errors.extend(abs(baseline - r['capacity']) for r in test)
        windows += 1
    enough = windows >= 3
    mae = sum(errors) / len(errors) if enough else None
    base_mae = sum(baseline_errors) / len(baseline_errors) if enough else None
    backtest = dict(status=('ok' if mae <= base_mae else 'not_improved') if enough else 'insufficient_data',
                    windows=windows, mae_pp=mae, baseline_mae_pp=base_mae,
                    horizon_days=7, training_days=28,
                    note='Held-out 7-day predictions from the preceding 28 days, after the latest observed shift. '
                         'Needs 3 test weeks, 21 training and 4 test readings per window. '
                         'Error concerns raw readings, not battery lifetime; no years-ahead accuracy is implied.')
    cycles = []
    for days in (30, 60, 90):
        start = today - timedelta(days=days)
        observed = [r for r in rows if start <= r['day'] <= today and r['cycles'] is not None]
        delta = None
        # Both ends must be close to requested boundaries. Do not imply a full
        # 90-day count from a much shorter history or across a counter reset.
        if (len(observed) >= 2 and (observed[0]['day'] - start).days <= 3
                and (today - observed[-1]['day']).days <= 3
                and all(b['cycles'] >= a['cycles'] for a, b in zip(observed, observed[1:]))):
            delta = observed[-1]['cycles'] - observed[0]['cycles']
        cycles.append(dict(days=days, cycles_added=delta, observations=len(observed)))
    return dict(shifts=shifts, backtest=backtest, cycle_windows=cycles,
                note='A raw-reading shift is not a diagnosis of recalibration or battery replacement. '
                     'No shift detected does not prove that the measurement regime is unchanged.')
