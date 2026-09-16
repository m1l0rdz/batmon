"""Transparent habit heuristic, not a hardware diagnosis or lifetime model."""
from batmond.anomalies import SYSTEM_DAEMONS

CHARGING_SOURCE = 'https://support.apple.com/en-au/102338'
ENERGY_SOURCE = 'https://support.apple.com/en-ca/guide/activity-monitor/actmntr43697/mac'
CARE_SOURCE = 'https://www.apple.com/batteries/maximizing-performance/'


def _clamp01(x):
    return max(0.0, min(1.0, x))


def compute_score(habits, health_history):
    # health_history is retained for callers; wear is not a user habit.
    parts = []
    observed = habits.get('observed_h', 0)
    fp = habits.get('full_pct_of_ac')
    if fp is not None:
        parts.append(dict(name='High-charge exposure',
                          points=round(_clamp01(1 - fp / 50) * 60, 1), max=60,
                          why='%.1f%% of observed AC segment time held at >=95%% (endpoint estimate)' % fp))
    deep = habits.get('deep_discharges')
    if deep is not None and observed > 0:
        parts.append(dict(name='Low-charge episodes',
                          points=round(_clamp01(1 - deep / 5) * 40, 1), max=40,
                          why='%d observed episodes below 10%% in 30 days; re-armed at 20%%' % deep))
    available = sum(p['max'] for p in parts)
    result = dict(score=None, grade=None, components=parts,
                  available_weight=available, observed_h=observed,
                  score_kind='habits')
    if observed < 24 or not available:
        return result
    score = round(sum(p['points'] for p in parts) / available * 100)
    grade = 'excellent' if score >= 90 else 'good' if score >= 75 else 'fair' if score >= 50 else 'poor'
    return dict(result, score=score, grade=grade)


def recommendations(ctx):
    h = ctx.get('habits') or {}
    recs = []
    protecting = (ctx.get('charge_limit') or {}).get('holding') is True

    def add(key, severity, title, body, action, target, source):
        recs.append(dict(id=key, severity=severity, title=title, body=body,
                         action=action, target_tab=target, source_url=source))

    fp = h.get('full_pct_of_ac')
    if fp is not None and fp > 30:
        if protecting:
            add('charge_limit_recovery', 'low', 'A charge limit is enabled',
                'Historical high-charge exposure is %.1f%% of observed AC segment time. This is an endpoint estimate; your current charge-limit policy is enabled.' % fp,
                'Keep the limit if it suits your daily runtime; review the actual value in Battery settings.',
                'charging', CHARGING_SOURCE)
        else:
            add('parked_at_full', 'medium', 'Reduce prolonged high charge',
                'About %.1f%% of observed AC segment time was held at >=95%%. Nighttime charging alone is not a problem.' % fp,
                'Use Optimized Battery Charging or a charge limit in Battery settings when usually plugged in.',
                'charging', CHARGING_SOURCE)
    deep = h.get('deep_discharges') or 0
    if deep >= 3:
        add('deep_discharges', 'medium', 'Plan a charging reserve',
            '%d low-charge episodes were observed in 30 days. Sleep fragments below 10%% count as one episode until charge reaches 20%%.' % deep,
            'For a practical runtime buffer, connect power around 20% when convenient. No deliberate full discharge is needed.',
            'charging', CARE_SOURCE)
    if (h.get('ac_share_pct') or 0) > 95 and not protecting:
        add('mostly_docked', 'low', 'Mostly used on external power',
            'External power accounts for %.0f%% of recorded time. There is no need to cycle the battery just to use it.' % h['ac_share_pct'],
            'Review optimized charging and the charge limit in Battery settings.', 'charging', CHARGING_SOURCE)
    top = ctx.get('top_apps') or []
    if top and (top[0].get('share_pct') or 0) > 40:
        a = top[0]
        name = a['app']
        # Daemons are lowercase single tokens ending in "d"; "Discord" or
        # "Microsoft Word" are user apps.
        system = (name in SYSTEM_DAEMONS or name in {'WindowServer', 'SystemUIServer', 'kernel_task'}
                  or (name.islower() and ' ' not in name and name.endswith('d')))
        add('heavy_app', 'medium', 'Review the leading chip-energy estimate',
            '%s accounts for %.0f%% of the displayed chip-energy allocation (%.1f Wh, 24h, AC and battery combined). This is not measured whole-Mac energy.' % (a['app'], a['share_pct'], a['attributed_wh']),
            'Compare Activity Monitor and the current workload. Do not stop system services.' if system else
            'Save your work, reduce optional background tasks, then compare power under a similar workload.',
            'apps', ENERGY_SOURCE)
    culprit = ctx.get('frequent_culprit')
    if culprit:
        add('sleep_culprit', 'medium', 'Review activity during sleep gaps',
            '%s appeared in %d abnormal sleep-gap records. Co-occurrence is evidence to investigate, not proof of the energy cause.' % (culprit['proc'], culprit['n']),
            'Check background activity and connected peripherals before the next sleep interval.', 'anomalies', ENERGY_SOURCE)
    br = ctx.get('avg_brightness_7d')
    if br is not None and br > 80:
        add('high_brightness', 'low', 'Try a lower display brightness',
            'Mean recorded brightness is %.0f%% over 7 days. Brightness is a setting, not a measurement of display watts.' % br,
            'Lower brightness to a comfortable level and compare battery draw during the same task.', 'energy', CARE_SOURCE)
    recs.sort(key=lambda r: ('high', 'medium', 'low').index(r['severity']))
    return recs
