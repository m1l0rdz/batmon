"""Parser for `powermetrics --format plist` burst output.

The burst is N concatenated XML plist documents separated by NUL bytes.
KEY_* constants below are FIXTURE-VERIFIED (Task 1 step 3, NOTES.md).
If a key is absent from the fixture, do not guess - fix the constant.
"""
import plistlib
from dataclasses import dataclass, field
from typing import Optional

# FIXTURE-VERIFY: confirm every name against tests/fixtures/NOTES.md
KEY_TASKS = "tasks"
KEY_PID = "pid"
KEY_NAME = "name"
KEY_ENERGY_IMPACT = "energy_impact"
KEY_CPU_MS = "cputime_ms_per_s"
KEY_GPU_MS = "gputime_ms_per_s"
KEY_PROCESSOR = "processor"
KEY_CPU_MW = "cpu_energy"
KEY_GPU_MW = "gpu_energy"
KEY_ANE_MW = "ane_energy"
KEY_DRAM_MW = "dram_power"
KEY_PACKAGE_MW = "combined_power"
KEY_THERMAL = "thermal_pressure"

_THERMAL_ORDER = ["Nominal", "Moderate", "Heavy", "Trapping", "Sleeping"]


@dataclass
class ProcRow:
    pid: int
    name: str
    energy_impact: float
    cpu_ms_per_s: float
    gpu_ms_per_s: float


@dataclass
class PMSample:
    procs: list[ProcRow] = field(default_factory=list)
    cpu_mw: Optional[float] = None
    gpu_mw: Optional[float] = None
    ane_mw: Optional[float] = None
    dram_mw: Optional[float] = None
    package_mw: Optional[float] = None
    thermal_pressure: Optional[str] = None


def split_stream(raw: bytes) -> list[bytes]:
    raw = raw.replace(b"\x00", b"")
    starts = []
    i = raw.find(b"<?xml")
    while i != -1:
        starts.append(i)
        i = raw.find(b"<?xml", i + 1)
    return [raw[s:e] for s, e in zip(starts, starts[1:] + [len(raw)])]


def _f(d, key):
    v = d.get(key)
    return float(v) if v is not None else None


def parse_sample(doc: bytes) -> PMSample:
    d = plistlib.loads(doc)
    procs = []
    for t in d.get(KEY_TASKS, []):
        procs.append(ProcRow(
            pid=int(t.get(KEY_PID, -1)),
            name=str(t.get(KEY_NAME, "unknown")),
            energy_impact=float(t.get(KEY_ENERGY_IMPACT, 0.0)),
            cpu_ms_per_s=float(t.get(KEY_CPU_MS, 0.0)),
            gpu_ms_per_s=float(t.get(KEY_GPU_MS, 0.0)),
        ))
    proc = d.get(KEY_PROCESSOR, {})
    s = PMSample(
        procs=procs,
        cpu_mw=_f(proc, KEY_CPU_MW),
        gpu_mw=_f(proc, KEY_GPU_MW),
        ane_mw=_f(proc, KEY_ANE_MW),
        dram_mw=_f(proc, KEY_DRAM_MW),
        package_mw=_f(proc, KEY_PACKAGE_MW),
        thermal_pressure=d.get(KEY_THERMAL),
    )
    if s.package_mw is None:
        parts = [v for v in (s.cpu_mw, s.gpu_mw, s.ane_mw, s.dram_mw)
                 if v is not None]
        s.package_mw = sum(parts) if parts else None
    return s


def parse_burst(raw: bytes) -> list[PMSample]:
    return [parse_sample(doc) for doc in split_stream(raw)]


def _avg(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def average_burst(samples: list[PMSample]) -> PMSample:
    if not samples:
        raise ValueError("empty burst")
    n = len(samples)
    acc = {}
    for s in samples:
        for p in s.procs:
            key = (p.pid, p.name)
            a = acc.setdefault(key, [0.0, 0.0, 0.0])
            a[0] += p.energy_impact
            a[1] += p.cpu_ms_per_s
            a[2] += p.gpu_ms_per_s
    procs = [ProcRow(pid, name, ei / n, cpu / n, gpu / n)
             for (pid, name), (ei, cpu, gpu) in acc.items()]
    pressures = [s.thermal_pressure for s in samples if s.thermal_pressure]
    worst = max(pressures, key=lambda p: _THERMAL_ORDER.index(p)
                if p in _THERMAL_ORDER else 0) if pressures else None
    return PMSample(
        procs=procs,
        cpu_mw=_avg(s.cpu_mw for s in samples),
        gpu_mw=_avg(s.gpu_mw for s in samples),
        ane_mw=_avg(s.ane_mw for s in samples),
        dram_mw=_avg(s.dram_mw for s in samples),
        package_mw=_avg(s.package_mw for s in samples),
        thermal_pressure=worst,
    )
