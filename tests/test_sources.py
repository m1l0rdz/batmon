"""Live powermetrics invocation must match how fixtures were captured
(scripts/capture_fixtures.sh). Without --show-process-energy the tasks
sampler emits no energy_impact and every per-app attribution is 0."""
from batmond.sources import POWERMETRICS_CMD, FixtureSource


def test_powermetrics_cmd_has_process_energy_flags():
    assert "--show-process-energy" in POWERMETRICS_CMD
    assert "--show-process-gpu" in POWERMETRICS_CMD


def test_powermetrics_cmd_shape_unchanged():
    assert POWERMETRICS_CMD[0] == "/usr/bin/powermetrics"
    assert "--format" in POWERMETRICS_CMD
    assert POWERMETRICS_CMD[POWERMETRICS_CMD.index("--format") + 1] == "plist"


def test_fixture_source_temps():
    # It doesn't actually read from fixtures dir for this method but requires the dir for init
    import os
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    source = FixtureSource(os.path.join(tests_dir, "fixtures"))
    assert source.temps() == {"soc_temp_c": 45.7, "ssd_temp_c": 37.0}
