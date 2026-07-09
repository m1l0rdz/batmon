from pathlib import Path

import pytest

from batmond.parsers.brightness import parse_brightness

FIXTURE = Path(__file__).parent / "fixtures" / "corebrightnessdiag.txt"


def test_fixture_parses_or_none():
    if not FIXTURE.exists():
        pytest.skip("brightness fixture not captured")
    v = parse_brightness(FIXTURE.read_text(errors="replace"))
    assert v is None or 0.0 <= v <= 100.0


def test_garbage_returns_none():
    assert parse_brightness("") is None
    assert parse_brightness("no brightness here") is None


def test_synthetic_value():
    assert parse_brightness('  "Brightness" = "0.5";') == 50.0
    assert parse_brightness("Brightness = 1;") == 100.0


def test_synthetic_plist():
    text = "<key>DisplayServicesBrightness</key>\n<real>0.76118874549865723</real>"
    assert parse_brightness(text) == pytest.approx(76.11887454986572)
