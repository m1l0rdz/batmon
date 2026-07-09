from pathlib import Path

from batmond.parsers.assertions import parse_assert_awake

FIXTURE = Path(__file__).parent / "fixtures" / "pmset_assertions.txt"


def test_fixture_returns_bool():
    assert parse_assert_awake(FIXTURE.read_text()) in (True, False)


def test_synthetic_on():
    text = "Assertion status system-wide:\n   PreventUserIdleDisplaySleep    1\n"
    assert parse_assert_awake(text) is True


def test_synthetic_off():
    text = "Assertion status system-wide:\n   PreventUserIdleDisplaySleep    0\n"
    assert parse_assert_awake(text) is False


def test_empty_false():
    assert parse_assert_awake("") is False
