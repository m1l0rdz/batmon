"""Durable charger sessions. Only adjacent observations contribute energy/time."""
import json

MAX_GAP = 45
DDL = '''
CREATE TABLE IF NOT EXISTS charger_sessions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 started INTEGER NOT NULL, last_ts INTEGER NOT NULL, ended INTEGER,
 end_reason TEXT, source_key TEXT NOT NULL, descriptor_json TEXT NOT NULL,
 soc_start REAL, soc_end REAL, metrics_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS charger_sessions_last ON charger_sessions(last_ts);
CREATE TABLE IF NOT EXISTS charger_readings(
 ts INTEGER PRIMARY KEY, session_id INTEGER, data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS charger_minutes(
 session_id INTEGER NOT NULL, minute INTEGER NOT NULL, data_json TEXT NOT NULL,
 PRIMARY KEY(session_id,minute)
);
CREATE INDEX IF NOT EXISTS charger_minutes_time ON charger_minutes(minute);
'''


def initial_metrics():
    return dict(observed_sec=0, battery_sec=0, input_sec=0, temp_sec=0,
                charge_sec=0, charge_power_sec=0, charge_ws=0,
                deficit_sec=0, hold_sec=0, battery_in_ws=0, battery_out_ws=0,
                input_ws=0, temp_csec=0, temp_max=None, strata={})


def _mean(a, b, key):
    x, y = a.get(key), b.get(key)
    return (x+y)/2 if x is not None and y is not None else None


def condition(d):
    """Conservative comparison bins, not thermal or hardware fault thresholds."""
    soc, temp, battery, power = (d.get(k) for k in ('soc','temp','watts','input_w'))
    if not d.get('charging') or d.get('policy_hold') or any(x is None for x in (soc,temp,battery,power)):
        return None
    if not (20 <= soc < 70 and 20 <= temp < 38 and battery > 0):
        return None
    load = power - battery
    if load < 0:
        return None
    return '{}:{}:{}'.format(int((soc-20)//10), int((temp-20)//2), int(load//5))


def accumulate(m, prev, cur, dt):
    m['observed_sec'] += dt
    watts, power, temp = (_mean(prev,cur,k) for k in ('watts','input_w','temp'))
    if watts is not None:
        m['battery_sec'] += dt
        # Integrate positive/negative endpoint contributions separately.
        m['battery_in_ws'] += (max(0,prev['watts'])+max(0,cur['watts']))/2*dt
        m['battery_out_ws'] += (max(0,-prev['watts'])+max(0,-cur['watts']))/2*dt
    if power is not None:
        m['input_sec'] += dt
        m['input_ws'] += power*dt
    if temp is not None:
        m['temp_sec'] += dt
        m['temp_csec'] += temp*dt
        m['temp_max'] = max(m['temp_max'] or temp,prev['temp'],cur['temp'])
    if prev['charging'] and cur['charging']:
        m['charge_sec'] += dt
        if watts is not None and prev['watts'] > 0 and cur['watts'] > 0:
            m['charge_power_sec'] += dt
            m['charge_ws'] += watts*dt
    if prev['policy_hold'] and cur['policy_hold']:
        m['hold_sec'] += dt
    elif watts is not None and prev['watts'] < -5 and cur['watts'] < -5:
        m['deficit_sec'] += dt
    c = condition(prev)
    if c is not None and c == condition(cur):
        bucket = m['strata'].setdefault(c,dict(sec=0,battery_ws=0,input_ws=0))
        bucket['sec'] += dt
        bucket['battery_ws'] += watts*dt
        bucket['input_ws'] += power*dt


def record(conn, sample, observation, policy):
    """Caller owns the transaction. Persistent last reading makes restarts idempotent."""
    row = conn.execute('SELECT value FROM state WHERE key=?',('charger_last',)).fetchone()
    prev = json.loads(row[0]) if row else None
    if prev and sample.ts <= prev['ts']:
        return
    level = policy.get('level')
    holding = (sample.on_ac and policy.get('holding') is True and level is not None
               and sample.soc_pct >= level-3 and not sample.is_charging)
    cur = dict(observation,ts=sample.ts,soc=sample.soc_pct,watts=sample.watts,
               temp=sample.temp_c,charging=bool(sample.is_charging),on_ac=bool(sample.on_ac),
               policy_hold=holding,policy_level=level,session_id=None)
    sid = prev.get('session_id') if prev else None
    dt = sample.ts-prev['ts'] if prev else 0
    reason = None
    if sid:
        if dt > MAX_GAP:
            reason = 'observation_gap'
        elif not sample.on_ac:
            reason = 'disconnected'
        elif prev.get('source_key') != cur.get('source_key'):
            reason = 'source_or_profile_changed'
        if reason:
            conn.execute('UPDATE charger_sessions SET ended=last_ts,end_reason=? WHERE id=?',(reason,sid))
            sid = None
    if sample.on_ac:
        if sid is None:
            descriptor = {k:v for k,v in observation.items() if k not in ('input_w','input_v','input_a')}
            sid = conn.execute('INSERT INTO charger_sessions(started,last_ts,source_key,descriptor_json,soc_start,soc_end,metrics_json) VALUES (?,?,?,?,?,?,?)',
                               (sample.ts,sample.ts,observation.get('source_key','unknown'),json.dumps(descriptor),sample.soc_pct,sample.soc_pct,json.dumps(initial_metrics()))).lastrowid
        elif 0 < dt <= MAX_GAP:
            row = conn.execute('SELECT metrics_json FROM charger_sessions WHERE id=?',(sid,)).fetchone()
            m = json.loads(row[0])
            accumulate(m,prev,cur,dt)
            conn.execute('UPDATE charger_sessions SET metrics_json=? WHERE id=?',(json.dumps(m),sid))
            minute = sample.ts//60*60
            row = conn.execute('SELECT data_json FROM charger_minutes WHERE session_id=? AND minute=?',(sid,minute)).fetchone()
            point = json.loads(row[0]) if row else initial_metrics()
            accumulate(point,prev,cur,dt)
            point.update(soc=sample.soc_pct,ts=sample.ts)
            conn.execute('INSERT OR REPLACE INTO charger_minutes VALUES (?,?,?)',(sid,minute,json.dumps(point)))
        conn.execute('UPDATE charger_sessions SET last_ts=?,soc_end=? WHERE id=?',(sample.ts,sample.soc_pct,sid))
        cur['session_id'] = sid
    conn.execute('INSERT OR REPLACE INTO charger_readings VALUES (?,?,?)',(sample.ts,sid,json.dumps(cur)))
    conn.execute('INSERT OR REPLACE INTO state(key,value) VALUES (?,?)',('charger_last',json.dumps(cur)))


def prune(conn, now_ts):
    conn.execute('DELETE FROM charger_readings WHERE ts < ?',(now_ts-48*3600,))
    conn.execute('DELETE FROM charger_minutes WHERE minute < ?',(now_ts-90*86400,))


def assessment(conn, now_ts):
    row = conn.execute('SELECT value FROM state WHERE key=?',('charger_last',)).fetchone()
    if not row:
        return None
    cur = json.loads(row[0])
    code, message = 'observing', 'Collecting a continuous charging window.'
    evidence = dict(observed_sec=0,battery_sec=0,deficit_sec=0)
    if now_ts-cur['ts'] > 90:
        code, message = 'stale', 'Readings are stale. Current source performance is unknown.'
    elif not cur['on_ac']:
        code, message = 'disconnected', 'Running on battery. Connect a source to begin a session.'
    elif cur.get('policy_hold'):
        code, message = 'policy_hold', 'Near the native charge limit; charging is paused. This does not indicate a weak adapter.'
    else:
        rows = conn.execute('SELECT data_json FROM charger_readings WHERE session_id=? AND ts>=? AND ts<=? ORDER BY ts',
                            (cur['session_id'],cur['ts']-600,cur['ts'])).fetchall()
        points = [json.loads(r[0]) for r in rows]
        m = initial_metrics()
        for a,b in zip(points,points[1:]):
            dt = b['ts']-a['ts']
            if 0 < dt <= MAX_GAP:
                accumulate(m,a,b,dt)
        evidence = {k:m[k] for k in ('observed_sec','battery_sec','deficit_sec')}
        if m['observed_sec'] >= 540 and m['battery_sec'] >= 540 and m['deficit_sec'] >= 540 and cur.get('watts') is not None and cur['watts'] < -5:
            code, message = 'battery_assisting', 'Battery supplied power alongside the connected source for at least 9 observed minutes. Reduce load or compare another charger/cable. A system policy may also cause discharge.'
        elif m['charge_power_sec'] >= 120 and cur['charging'] and cur.get('watts') is not None and cur['watts'] > 0:
            code, message = 'charging', 'Power reaches the battery under the observed load. This does not certify adapter safety or performance at higher load.'
        elif not cur['charging'] and cur.get('watts') is not None:
            code, message = 'paused', 'Connected, but not charging. Full charge, macOS policy or available power may explain this; no hardware diagnosis.'
    disconnects = conn.execute("SELECT COUNT(*) FROM charger_sessions WHERE end_reason='disconnected' AND ended>=? AND ended<=?", (now_ts-900,now_ts)).fetchone()[0]
    return dict(cur,recent_disconnects=disconnects,assessment=dict(code=code,message=message,**evidence))
