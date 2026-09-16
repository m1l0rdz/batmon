"""Read-only charger evidence and condition-matched session comparisons."""
import json
from collections import defaultdict

from batmond.chargers import assessment


def ratio(total, seconds):
    return total/seconds if seconds else None


def summarize(m):
    return dict(observed_sec=m['observed_sec'],power_observed_sec=m['battery_sec'],
                input_observed_sec=m['input_sec'],temp_observed_sec=m['temp_sec'],
                charge_sec=m['charge_sec'],hold_sec=m['hold_sec'],deficit_sec=m['deficit_sec'],
                battery_in_wh=m['battery_in_ws']/3600 if m['battery_sec'] else None,
                battery_out_wh=m['battery_out_ws']/3600 if m['battery_sec'] else None,
                avg_battery_w=ratio(m['battery_in_ws']-m['battery_out_ws'],m['battery_sec']),
                avg_charge_w=ratio(m['charge_ws'],m['charge_power_sec']),
                avg_input_w=ratio(m['input_ws'],m['input_sec']),
                avg_temp_c=ratio(m['temp_csec'],m['temp_sec']),max_temp_c=m['temp_max'],
                comparable_sec=sum(b['sec'] for b in m['strata'].values()))


def _session(row):
    sid,start,last,end,reason,key,desc,soc_start,soc_end,metrics = row
    m = json.loads(metrics)
    return dict(id=sid,started=start,last_ts=last,ended=end,end_reason=reason,
                source_key=key,descriptor=json.loads(desc),soc_start=soc_start,soc_end=soc_end,
                coverage_pct=100*m['observed_sec']/(last-start) if last>start else None,
                **summarize(m))


def session(conn, sid):
    row = conn.execute('SELECT * FROM charger_sessions WHERE id=?',(sid,)).fetchone()
    if row is None:
        return None
    result = _session(row)
    points = conn.execute('SELECT minute,data_json FROM charger_minutes WHERE session_id=? ORDER BY minute DESC LIMIT 1441',(sid,)).fetchall()
    result['points_truncated'] = len(points)>1440
    points = list(reversed(points[:1440]))
    result['points'] = [dict(minute=minute,soc=(m:=json.loads(raw))['soc'],**summarize(m)) for minute,raw in points]
    return result


def overview(conn, now_ts, days=30):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='charger_sessions'").fetchone()
    if not exists:
        return dict(current=None,sessions=[],patterns=[],available=False,days=days,total_sessions=0,truncated=False)
    rows = conn.execute('SELECT * FROM charger_sessions WHERE last_ts>=? AND started<=? ORDER BY started DESC LIMIT 501',
                        (now_ts-days*86400,now_ts)).fetchall()
    total = conn.execute('SELECT COUNT(*) FROM charger_sessions WHERE last_ts>=? AND started<=?',(now_ts-days*86400,now_ts)).fetchone()[0]
    sessions = [_session(r) for r in rows[:500]]
    groups = defaultdict(list)
    for s in sessions:
        groups[s['source_key']].append(s)
    patterns = []
    for key,items in groups.items():
        observed = sum(s['observed_sec'] for s in items)
        charge = sum(s['charge_sec'] for s in items)
        deficit = sum(s['deficit_sec'] for s in items)
        patterns.append(dict(source_key=key,descriptor=items[0]['descriptor'],
                             identity_kind=items[0]['descriptor'].get('identity_kind','descriptor'),
                             sessions=len(items),observed_sec=observed,charge_sec=charge,
                             deficit_sec=deficit,deficit_sessions=sum(s['deficit_sec']>=540 for s in items),
                             hold_sec=sum(s['hold_sec'] for s in items),
                             battery_in_wh=sum(s['battery_in_wh'] or 0 for s in items) if any(s['battery_in_wh'] is not None for s in items) else None,
                             power_observed_sec=sum(s['power_observed_sec'] for s in items)))
    return dict(current=assessment(conn,now_ts),sessions=sessions,patterns=patterns,
                available=True,days=days,total_sessions=total,truncated=total>500)


def compare(conn, a, b):
    base = dict(status='withheld',matched_sec=0,a_w=None,b_w=None,delta_w=None,
                a_id=a,b_id=b,strata=[])
    if a == b:
        return dict(base,reason='Choose two different sessions.')
    values = []
    for sid in (a,b):
        row = conn.execute('SELECT metrics_json FROM charger_sessions WHERE id=?',(sid,)).fetchone()
        if row is None:
            return dict(base,reason='A selected session is unavailable.')
        m = json.loads(row[0])
        values.append(m)
    ma,mb = values
    # Same context distribution on both sides; weight by overlapping duration.
    weight = aw = bw = 0
    buckets = []
    for key in sorted(ma['strata'].keys() & mb['strata'].keys()):
        x,y = ma['strata'][key],mb['strata'][key]
        seconds = min(x['sec'],y['sec'])
        weight += seconds
        ax,by = x['battery_ws']/x['sec'],y['battery_ws']/y['sec']
        aw += ax*seconds
        bw += by*seconds
        buckets.append(dict(condition=key,matched_sec=seconds,a_w=ax,b_w=by))
    base.update(matched_sec=weight,strata=buckets,
                a_eligible_sec=sum(s['sec'] for s in ma['strata'].values()),
                b_eligible_sec=sum(s['sec'] for s in mb['strata'].values()))
    if weight < 300:
        return dict(base,reason='Need at least 5 minutes of shared conditions: charging at 20-70% SOC, 20-38 C battery temperature, and the same estimated system-load band. Missing input power or temperature excludes an interval.')
    return dict(base,status='comparable',a_w=aw/weight,b_w=bw/weight,delta_w=(bw-aw)/weight,
                reason='Battery charging power, weighted to the same observed conditions. An observational comparison, not a controlled benchmark or a safety rating.')
