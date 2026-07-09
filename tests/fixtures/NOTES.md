# Fixture Inspection Notes

(a) NUL byte separation: Yes, powermetrics streams plist documents separated by NUL bytes (`\0`).
(b) exact key names:
  - tasks list: `tasks`
  - per-process fields:
    - pid: `pid`
    - name: `name`
    - energy impact: `energy_impact`
    - cputime_ms_per_s: `cputime_ms_per_s`
    - gputime_ms_per_s: MISSING (not output by powermetrics for tasks even with `--show-process-gpu`)
  - component power fields: `cpu_energy`, `gpu_energy`, `ane_energy`. No dram/combined seen.
  - thermal pressure: `thermal_pressure`
(c) per-process energy rows: `energy_impact` is now present. We added `--show-process-energy --show-process-gpu` to `scripts/capture_fixtures.sh`. (Will update `sources.py` when it exists).
(d) ioreg Capacity: `CurrentCapacity` is percent (e.g. 64), `AppleRawCurrentCapacity` (3602) and `AppleRawMaxCapacity` (5920) are present and in mAh.
(e) ioreg Temperature: `Temperature` (e.g. 3080) in hundredths of a degree C (30.8 C).
(f) brightness: `DisplayServicesBrightness` (e.g. 0.7083289623260498) is a float between 0 and 1.
