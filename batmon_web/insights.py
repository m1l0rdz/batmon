"""Read-only, explicitly bounded analytics over existing observations.

Coverage is collection time, not uptime. App estimates and battery energy
have different measurement boundaries and are deliberately not reconciled.
"""
from datetime import datetime, timedelta
from statistics import median

from batmon_web import queries, system_health, health_diagnostics


def period_summary(conn, start, end):
    rows = conn.execute(
        'SELECT hour, wh_in, wh_out, on_battery_sec, on_ac_sec, avg_watts '
        'FROM rollup_hourly_battery WHERE hour >= ? AND hour < ? ORDER BY hour',
        (start, end)).fetchall()
    bat = ac = pure_seconds = pure_watt_seconds = 0.0
    energy_in = energy_out = None
    daily = {}
    day = datetime.fromtimestamp(start).date()
    last_day = datetime.fromtimestamp(end - 1).date()
    while day <= last_day:
        key = day.isoformat()
        daily[key] = dict(day=key, wh_in=None, wh_out=None,
                          observed_h=0.0, on_battery_h=0.0, on_ac_h=0.0)
        day += timedelta(days=1)
    for ts, wi, wo, bs, ads, avg_watts in rows:
        bs, ads = bs or 0, ads or 0
        if bs + ads <= 0:
            continue
        if avg_watts is None:
            wi = wo = None
        day = datetime.fromtimestamp(ts).date().isoformat()
        d = daily[day]
        bat += bs
        ac += ads
        if wi is not None:
            energy_in = (energy_in or 0) + wi
            d['wh_in'] = (d['wh_in'] or 0) + wi
        if wo is not None:
            energy_out = (energy_out or 0) + wo
            d['wh_out'] = (d['wh_out'] or 0) + wo
        d['observed_h'] += (bs + ads) / 3600
        d['on_battery_h'] += bs / 3600
        d['on_ac_h'] += ads / 3600
        if bs > 0 and ads == 0 and avg_watts is not None:
            pure_seconds += bs
            # Historical rollups do not retain power-sensor coverage. Wh may
            # integrate only part of the hour; dividing by all collected time
            # would falsely treat missing power as zero. Use observed sample
            # averages instead and disclose the approximate hourly weighting.
            pure_watt_seconds += max(0, -avg_watts) * bs
    observed = (bat + ac) / 3600
    return dict(start_ts=start, end_ts=end, has_data=observed > 0,
                wh_in=energy_in if observed else None,
                wh_out=energy_out if observed else None,
                on_battery_h=bat / 3600, on_ac_h=ac / 3600,
                observed_h=observed,
                coverage_pct=min(100.0, (bat + ac) / (end - start) * 100),
                pure_battery_h=pure_seconds / 3600,
                battery_only_watts=(pure_watt_seconds / pure_seconds
                                    if pure_seconds >= 7200 else None),
                power_method='Observed hourly power averages weighted by collected battery time. '
                             'Pure battery hours only; within-hour power-sensor coverage is not retained. '
                             'Energy totals cover available readings and may be incomplete.',
                daily=list(daily.values()))


def _change(current, previous):
    if current is None or previous is None or previous <= 0:
        return None
    return (current - previous) / previous * 100


def health_summary(conn, now_ts):
    today = datetime.fromtimestamp(now_ts).date()
    rows = [r for r in queries.health(conn)
            if r['max_capacity_pct'] is not None and r['day'] <= today.isoformat()]
    latest = queries.health_now(conn) or {}
    recent = [r['max_capacity_pct'] for r in rows
              if (today - timedelta(days=6)).isoformat() <= r['day']]
    prior = [r['max_capacity_pct'] for r in rows
             if (today - timedelta(days=13)).isoformat() <= r['day']
             < (today - timedelta(days=6)).isoformat()]
    weekly = {}
    for r in rows:
        day = datetime.strptime(r['day'], '%Y-%m-%d').date()
        monday = (day - timedelta(days=day.weekday())).isoformat()
        weekly.setdefault(monday, []).append(r['max_capacity_pct'])
    pred = queries.health_prediction(conn)
    status = pred['status']
    if status == 'insufficient_data':
        reason = 'A forecast needs 30 daily readings over 60 calendar days and 8 weeks.'
    elif status == 'unstable_trend':
        if abs(pred.get('slope_pct_per_day', 0)) > queries.MAX_PREDICTION_SLOPE_PCT_PER_DAY:
            reason = 'The observed slope is too steep to extrapolate safely. Calibration changes and wear can both affect raw capacity.'
        else:
            reason = 'Capacity readings vary too much for a reliable long-term extrapolation.'
    else:
        reason = 'Weekly median trend passes the quality gate. It is an observation, not a battery lifetime guarantee.'
    cur = median(recent) if len(recent) >= 4 else None
    prev = median(prior) if len(prior) >= 4 else None
    return dict(**system_health.read_health(now_ts),
                current_raw_pct=latest.get('max_capacity_pct'),
                median_7d_pct=cur, previous_7d_pct=prev,
                change_pp=cur - prev if cur is not None and prev is not None else None,
                points=len(rows), span_days=pred.get('span_days', 0),
                cycle_count=latest.get('cycle_count'),
                weekly=[dict(day=d, capacity_pct=median(v)) for d, v in weekly.items()],
                forecast_status=status, forecast_reason=reason,
                latest_ts=latest.get('ts'),
                recent_points=len(recent), previous_points=len(prior),
                diagnostics=health_diagnostics.diagnostics(conn, today))


def overview(conn, now_ts, days=7):
    end = now_ts - now_ts % 3600
    start = end - days * 86400
    current = period_summary(conn, start, end)
    previous = period_summary(conn, start - days * 86400, start)
    power_change = _change(current['battery_only_watts'], previous['battery_only_watts'])
    return dict(days=days, as_of_ts=end, current=current, previous=previous,
                comparison=dict(status='ok' if power_change is not None else 'insufficient_data',
                                power_change_pct=power_change,
                                energy_change_pct=_change(current['wh_out'], previous['wh_out']),
                                observed_hours_change_pct=_change(current['observed_h'], previous['observed_h'])),
                health=health_summary(conn, now_ts))


def _percentile(values, p):
    values = sorted(values)
    pos = (len(values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def runtime_estimate(conn, now_ts, reserve_pct=20):
    if reserve_pct not in (10, 20, 30):
        raise ValueError('reserve must be 10, 20 or 30 percent')
    out = dict(status='unavailable', reserve_pct=reserve_pct, minutes_low=None,
               minutes_median=None, minutes_high=None, window_minutes=0,
               sample_count=0, median_watts=None)
    last = queries.latest_sample(conn)
    if last is None:
        return out
    if now_ts - last['ts'] > 180:
        return dict(out, status='stale')
    if last['on_ac']:
        return dict(out, status='on_ac')
    if last['soc_pct'] <= reserve_pct:
        return dict(out, status='at_reserve')
    h = queries.health_now(conn) or {}
    remaining = h.get('raw_current_capacity_mah')
    if not remaining or not h.get('ts') or abs(h['ts'] - last['ts']) > 90:
        return out
    rows = conn.execute(
        'SELECT ts, current_ma, watts, on_ac FROM battery_samples '
        'WHERE ts >= ? AND ts <= ? ORDER BY ts DESC',
        (last['ts'] - 900, last['ts'])).fetchall()
    block = []
    prev_ts = last['ts']
    for ts, ma, watts, on_ac in rows:
        if prev_ts - ts > 90 or on_ac or ma is None or ma >= -10 or watts is None or watts >= 0:
            break
        block.append((ts, -ma, -watts))
        prev_ts = ts
    out['sample_count'] = len(block)
    out['window_minutes'] = (block[0][0] - block[-1][0]) / 60 if block else 0
    if len(block) < 12 or out['window_minutes'] < 5:
        return dict(out, status='warming_up')
    # Tie the reserve to displayed SoC to avoid mixing raw/current scales.
    available = remaining * (1 - reserve_pct / last['soc_pct'])
    currents = [r[1] for r in block]
    out.update(status='ok',
               minutes_low=round(available / _percentile(currents, .8) * 60),
               minutes_median=round(available / median(currents) * 60),
               minutes_high=round(available / _percentile(currents, .2) * 60),
               median_watts=median(r[2] for r in block))
    return out


def power_events(conn, start, end):
    """Observed state transitions only; gaps and restarts are not plug events."""
    rows = conn.execute(
        'SELECT kind,started,ended FROM sessions WHERE started < ? ORDER BY started,id',
        (end,)).fetchall()
    events = []
    for previous, current in zip(rows, rows[1:]):
        kind, ts, _ = current
        old_kind, _, old_end = previous
        if (start <= ts < end and old_end is not None and 0 <= ts - old_end <= 90
                and old_kind != kind):
            events.append(dict(ts=ts, kind=kind))
    return events


def report_apps(conn, start, end):
    """Same completed-hour boundary as the energy report."""
    return [dict(app=a, attributed_wh=m / 1000)
            for a, m in conn.execute(
                'SELECT app,SUM(attributed_mwh) FROM rollup_hourly_apps '
                f'WHERE hour >= ? AND hour < ? AND app NOT IN ({queries._SYS_PH}) '
                'GROUP BY app ORDER BY SUM(attributed_mwh) DESC LIMIT 10',
                (start, end) + queries.SYSTEM_APPS)]


def report_context(conn, start, end):
    counts = dict(conn.execute(
        'SELECT kind,COUNT(*) FROM sessions WHERE started>=? AND started<? GROUP BY kind',
        (start, end)))
    temp = conn.execute(
        'SELECT SUM(avg_temp_c*(on_battery_sec+on_ac_sec)), '
        'SUM(on_battery_sec+on_ac_sec) FROM rollup_hourly_battery '
        'WHERE hour>=? AND hour<? AND avg_temp_c IS NOT NULL', (start, end)).fetchone()
    return dict(sessions_battery=counts.get('battery', 0),
                sessions_charging=counts.get('charging', 0) + counts.get('full', 0),
                deep_discharges=queries.low_charge_episodes(conn, start, end),
                anomaly_count=conn.execute('SELECT COUNT(*) FROM anomalies WHERE ts>=? AND ts<?',
                                           (start, end)).fetchone()[0],
                avg_temp_c=temp[0] / temp[1] if temp[1] else None)
