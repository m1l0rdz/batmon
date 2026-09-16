"""Read-only native charging policy. Keys verified on this Mac's archive."""
import plistlib


def parse_policy(payload):
    try:
        if 'policies' not in payload:
            return dict(holding=False, level=None)
        inner = plistlib.loads(payload['policies'])
        objects = inner['$objects']
        root = objects[inner['$top']['root'].data]
        for uid in root.get('NS.objects', []):
            policy = objects[uid.data]
            reason_uid = policy.get('reason')
            if reason_uid is None:
                continue
            reason = objects[reason_uid.data]
            if reason == 'manualChargeLimit' and not policy.get('terminated', False):
                raw = policy.get('soclimit')
                level = raw if type(raw) in (int, float) and 1 <= raw <= 100 else None
                return dict(holding=True, level=level)
        return dict(holding=False, level=None)
    except (KeyError, IndexError, AttributeError, TypeError, ValueError, plistlib.InvalidFileException):
        return dict(holding=None, level=None)


def read_policy():
    try:
        with open('/Library/Preferences/com.apple.powerd.charging.plist', 'rb') as handle:
            return parse_policy(plistlib.load(handle))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return dict(holding=None, level=None)
