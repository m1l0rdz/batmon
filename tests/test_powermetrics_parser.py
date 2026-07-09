import plistlib
from pathlib import Path

import pytest

from batmond.parsers import powermetrics as pm

FIXTURE = Path(__file__).parent / "fixtures" / "powermetrics_burst.plist"


@pytest.fixture(scope="module")
def raw():
    return FIXTURE.read_bytes()


def test_split_stream_yields_five_documents(raw):
    docs = pm.split_stream(raw)
    assert len(docs) == 5
    for d in docs:
        assert d.lstrip().startswith(b"<?xml")


def test_parse_burst_shapes(raw):
    samples = pm.parse_burst(raw)
    assert len(samples) == 5
    s = samples[0]
    assert len(s.procs) > 5
    p = s.procs[0]
    assert isinstance(p.pid, int) and isinstance(p.name, str) and p.name
    assert p.energy_impact >= 0.0
    assert s.package_mw is not None and 0 < s.package_mw < 200_000
    assert s.cpu_mw is None or 0 <= s.cpu_mw < 200_000


def test_average_burst(raw):
    samples = pm.parse_burst(raw)
    avg = pm.average_burst(samples)
    assert avg.package_mw == pytest.approx(
        sum(s.package_mw for s in samples) / 5)
    names = {p.name for s in samples for p in s.procs}
    assert {p.name for p in avg.procs} == names


def test_average_burst_empty_raises():
    with pytest.raises(ValueError):
        pm.average_burst([])


def test_missing_package_mw_fallback():
    doc = plistlib.dumps({
        "processor": {
            "cpu_energy": 10.0,
            "gpu_energy": 20.0,
            "ane_energy": 5.0,
            "dram_power": 15.0
        }
    })
    sample = pm.parse_sample(doc)
    assert sample.package_mw == 50.0


def test_unknown_thermal_pressure():
    doc1 = plistlib.dumps({"thermal_pressure": "UnknownState"})
    doc2 = plistlib.dumps({"thermal_pressure": "Heavy"})
    
    samples = [pm.parse_sample(doc1), pm.parse_sample(doc2)]
    avg = pm.average_burst(samples)
    
    assert samples[0].thermal_pressure == "UnknownState"
    # "Heavy" should be picked over "UnknownState" because UnknownState gets index 0
    assert avg.thermal_pressure == "Heavy"
