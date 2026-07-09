from unittest.mock import patch
from batmond.parsers.radios import parse_radios

@patch("subprocess.run")
def test_parse_radios_both_warnings(mock_run):
    class FakeProc:
        def __init__(self, stdout):
            self.stdout = stdout

    def side_effect(args, **kwargs):
        if args[0].endswith("networksetup") and args[1] == "-getairportpower":
            return FakeProc("Wi-Fi Power (en0): On")
        elif args[0].endswith("ifconfig") and args[1] == "en0":
            return FakeProc("status: inactive")
        elif args[0].endswith("system_profiler"):
            return FakeProc("State: On\nNot Connected:")
        return FakeProc("")

    mock_run.side_effect = side_effect
    warnings = parse_radios()
    assert warnings == ["Wi-Fi is On but not connected", "Bluetooth is On but no devices connected"]

@patch("subprocess.run")
def test_parse_radios_no_warnings(mock_run):
    class FakeProc:
        def __init__(self, stdout):
            self.stdout = stdout

    def side_effect(args, **kwargs):
        if args[0].endswith("networksetup") and args[1] == "-getairportpower":
            return FakeProc("Wi-Fi Power (en0): Off")
        elif args[0].endswith("system_profiler"):
            return FakeProc("State: On\nConnected: Yes")
        return FakeProc("")

    mock_run.side_effect = side_effect
    warnings = parse_radios()
    assert warnings == []
