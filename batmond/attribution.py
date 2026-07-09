"""Attribution v1 (design 5.1): distribute measured package power across
processes proportionally to energy-impact share within the same minute.
Raw score AND attributed mWh both stored; UI labels numbers as attributed."""
from dataclasses import dataclass

# Helper -> app mapping (design: hardcoded, extendable). Extend here.
# DEAD_TASKS is powermetrics' bucket for processes that exited during the
# sample window: real energy, so it stays in the denominator, but under a
# name the UI can recognize and filter.
HELPER_MAP = {
    "DEAD_TASKS": "(terminated)",
    "Google Chrome Helper": "Google Chrome",
    "Google Chrome Helper (Renderer)": "Google Chrome",
    "Google Chrome Helper (GPU)": "Google Chrome",
    "Google Chrome Helper (Plugin)": "Google Chrome",
    "com.apple.WebKit.WebContent": "Safari",
    "com.apple.WebKit.GPU": "Safari",
    "com.apple.WebKit.Networking": "Safari",
}


def canonical_app(process_name: str) -> str:
    return HELPER_MAP.get(process_name, process_name)


@dataclass
class AppMinute:
    app: str
    pid_count: int
    energy_impact: float
    cpu_ms_per_s: float
    gpu_ms_per_s: float
    attributed_mwh: float


def attribute_minute(package_mw, procs) -> list:
    groups = {}
    for p in procs:
        app = canonical_app(p.name)
        g = groups.get(app)
        if g is None:
            groups[app] = g = AppMinute(app, 0, 0.0, 0.0, 0.0, 0.0)
        g.pid_count += 1
        g.energy_impact += p.energy_impact
        g.cpu_ms_per_s += p.cpu_ms_per_s
        g.gpu_ms_per_s += p.gpu_ms_per_s
    total = sum(g.energy_impact for g in groups.values())
    if package_mw and total > 0:
        mwh_minute = package_mw / 60.0  # mW sustained for 1 min -> mWh
        for g in groups.values():
            g.attributed_mwh = mwh_minute * (g.energy_impact / total)
    return sorted(groups.values(), key=lambda g: -g.energy_impact)
