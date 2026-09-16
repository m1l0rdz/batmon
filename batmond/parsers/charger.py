"""Optional charger fields captured on Mac16,8. No undocumented reason decoding."""
import hashlib
import json
import math
import plistlib


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value) if low <= value <= high else None


def _text(value):
    return value.strip()[:120] if isinstance(value, str) else ''


def parse_charger(raw):
    data = plistlib.loads(raw)
    d = data[0] if isinstance(data, list) else data
    a = d.get('AdapterDetails')
    a = a if isinstance(a, dict) else {}
    t = d.get('PowerTelemetryData')
    t = t if isinstance(t, dict) else {}
    v = number(t.get('SystemVoltageIn'), 3000, 50000)
    i = number(t.get('SystemCurrentIn'), 0, 6000)
    p = number(t.get('SystemPowerIn'), 0, 300000)
    # Captured units: mV, mA, mW. Reject absent or inconsistent triples.
    # This is OS input telemetry, not a calibrated wall-meter measurement.
    input_w = None
    if v is not None and i is not None and p is not None:
        calculated = v * i / 1000000
        if abs(calculated - p / 1000) <= max(1, calculated * .1):
            input_w = p / 1000
    profiles = []
    menu = a.get('UsbHvcMenu', [])
    for item in menu if isinstance(menu, list) else []:
        if not isinstance(item, dict):
            continue
        mv = number(item.get('MaxVoltage'), 3000, 50000)
        ma = number(item.get('MaxCurrent'), 0, 6000)
        if mv is not None and ma is not None:
            profiles.append({'v':mv/1000, 'a':ma/1000})
    mv = number(a.get('AdapterVoltage'), 3000, 50000)
    ma = number(a.get('Current'), 0, 6000)
    descriptor = dict(description=_text(a.get('Name')) or _text(a.get('Description')) or 'Unidentified source',
                      manufacturer=_text(a.get('Manufacturer')),
                      capability_w=number(a.get('Watts'), 1, 300),
                      profile_v=mv/1000 if mv is not None else None,
                      profile_a=ma/1000 if ma is not None else None,
                      profiles=sorted(profiles,key=lambda x:(x['v'],x['a'])))
    serial = _text(a.get('SerialNumber')) or _text(a.get('SerialString'))
    if serial.lower() in ('0','unknown','n/a'):
        serial = ''
    identity = dict(descriptor, identifier=serial)
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20]
    return dict(descriptor, source_key=digest,
                identity_kind='reported_identifier' if serial else 'descriptor',
                input_w=input_w, input_v=v/1000 if input_w is not None else None,
                input_a=i/1000 if input_w is not None else None)
