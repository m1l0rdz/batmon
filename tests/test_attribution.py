import pytest

from batmond.attribution import attribute_minute, canonical_app
from batmond.parsers.powermetrics import ProcRow


def _p(name, impact, pid=1):
    return ProcRow(pid=pid, name=name, energy_impact=impact,
                   cpu_ms_per_s=10.0, gpu_ms_per_s=0.0)


def test_canonical_app_helper_mapping():
    assert canonical_app("Google Chrome Helper (Renderer)") == "Google Chrome"
    assert canonical_app("com.apple.WebKit.WebContent") == "Safari"
    assert canonical_app("SomeApp") == "SomeApp"


def test_proportional_split_conserves_energy():
    procs = [_p("A", 300.0, 1), _p("B", 100.0, 2)]
    out = attribute_minute(6000.0, procs)  # 6000 mW for 1 min = 100 mWh
    total = sum(a.attributed_mwh for a in out)
    assert total == pytest.approx(100.0)
    by_app = {a.app: a for a in out}
    assert by_app["A"].attributed_mwh == pytest.approx(75.0)
    assert by_app["B"].attributed_mwh == pytest.approx(25.0)


def test_helpers_grouped_into_one_app():
    procs = [_p("Google Chrome", 100.0, 1),
             _p("Google Chrome Helper (Renderer)", 100.0, 2),
             _p("Google Chrome Helper (GPU)", 100.0, 3)]
    out = attribute_minute(3000.0, procs)
    assert len(out) == 1
    a = out[0]
    assert a.app == "Google Chrome" and a.pid_count == 3
    assert a.energy_impact == pytest.approx(300.0)
    assert a.attributed_mwh == pytest.approx(50.0)  # all of 3000/60


def test_dead_tasks_renamed_but_still_in_denominator():
    """DEAD_TASKS is powermetrics' bucket for exited processes. It must
    stay in the attribution denominator (energy is real) but get a
    human-readable name the UI can filter."""
    assert canonical_app("DEAD_TASKS") == "(terminated)"
    procs = [_p("DEAD_TASKS", 100.0, 1), _p("A", 100.0, 2)]
    out = attribute_minute(6000.0, procs)   # 100 mWh total
    by_app = {a.app: a for a in out}
    assert "(terminated)" in by_app and "DEAD_TASKS" not in by_app
    assert by_app["A"].attributed_mwh == pytest.approx(50.0)
    assert by_app["(terminated)"].attributed_mwh == pytest.approx(50.0)


def test_zero_impact_or_no_power():
    procs = [_p("A", 0.0)]
    assert attribute_minute(5000.0, procs)[0].attributed_mwh == 0.0
    out = attribute_minute(None, [_p("A", 10.0)])
    assert out[0].attributed_mwh == 0.0 and out[0].energy_impact == 10.0
