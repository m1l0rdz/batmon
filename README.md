# batmon

> Know exactly what is draining your MacBook's battery - right now, and over the last month.

<p>
  <img src="https://img.shields.io/badge/macOS-000000?style=flat-square&logo=apple&logoColor=white" alt="macOS"/>
  <img src="https://img.shields.io/badge/Apple%20Silicon-333333?style=flat-square&logo=apple&logoColor=white" alt="Apple Silicon"/>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite"/>
  <img src="https://img.shields.io/badge/data-100%25%20local-00c853?style=flat-square" alt="100% local"/>
</p>

`batmon` is a native battery and power monitor for Apple Silicon Macs. It answers the
questions macOS never really does: which app is eating my battery this minute,
how much power the CPU/GPU/Neural Engine are pulling, how healthy the cells
actually are, and how long you have left. It runs entirely on your machine -
no cloud, no account, no telemetry - and gives you buttons to act on what it
finds.

> **Target:** MacBook Pro (M-series / Apple Silicon), macOS 26.x or newer.
> Apple Silicon only - there is no Intel build.

<p align="center">
  <img src="docs/menubar.png" alt="batmon menu bar app: live watts, charge %, and forecast in the title, with health, component power, top apps, and one-tap Keep-awake / Low Power Mode / Battery Settings in the dropdown" width="420"/>
</p>
<p align="center"><em>The native menu bar app: watts, charge, and forecast at a glance; the full readout one click away.</em></p>

---

## Why batmon

macOS tells you the battery percentage and, if you dig, a vague "using
significant energy" label. batmon turns that into real numbers you can trust
and act on: watts, watt-hours, milliamp-hours, cycle counts, per-app
attribution, and a forecast - sampled continuously and kept for months, all in
a single local SQLite file.

## Features

### See what is draining your battery

- **Live dashboard.** Real-time power draw in watts, current charge %, charge
  direction, power source, and a time-to-empty forecast (or time-to-full when
  charging). The Now view refreshes every 5 seconds.
- **Per-app energy attribution.** A ranked list of the apps burning the most
  energy, attributed from `powermetrics` process data. Look at the last hour on
  the dashboard, or open the Apps tab for 1h / 8h / 24h / 7d / 30d windows.
- **Component power breakdown & thermals.** Live milliwatt draw for the CPU, GPU, and
  Apple Neural Engine plus the total package (SoC) power, alongside the current
  thermal-pressure state and detailed temperatures for the SoC, SSD, and battery. DRAM power is sampled too.

### Act on it, without leaving the dashboard

- **Process taming.** Hover any app in the Top Apps table and pause it
  (SIGSTOP), resume it (SIGCONT), or kill it (SIGTERM) - stop a runaway battery
  hog in one click.
- **Keep-awake toggle.** One switch runs `caffeinate -d -i` for you, preventing
  idle display sleep and idle system sleep so your Mac stays up and your screen
  stays unlocked while you step away. Handy for keeping corporate VPNs (Cisco
  AnyConnect and friends) and remote sessions from dropping the moment you go
  idle. Lid-close, the power button, and manual sleep still work exactly as
  stock - batmon never overrides those.
- **Low Power Mode.** Toggle macOS Low Power Mode from the menu-bar plugin, and
  set an automatic threshold so the daemon flips it on for you once the battery
  drops below a level you choose (while on battery).
- **Battery Longevity (80% charge limit).** macOS can hold your charge at 80% to
  slow battery aging. batmon reports the limit, infers from your own charge
  history whether it is actually holding, and deep-links you straight to
  System Settings > Battery to flip it. Note: on Apple Silicon there is no
  accessible SMC key to set this limit programmatically, so batmon mirrors and
  verifies the native setting - it does not drive the charging hardware itself.

### Understand the long game

- **Battery health.** Maximum capacity %, cycle count, design vs full-charge
  capacity (mAh), per-cell voltage balance, lifetime temperature range, and
  battery operating age - plus a capacity-and-cycles trend chart over time.
- **Charging history.** Every charge and discharge session with start/end
  charge levels and energy moved, total time on battery vs AC, average charge
  power, and a discharge-depth histogram of how deep you typically run down.
- **Energy history.** Discharged vs charged watt-hours per hour or per day, with
  average display brightness overlaid, across 24h / 7d / 30d.

### Get warned when something is wrong

- **Anomaly detection & context** with a dedicated report page and push notifications
  (delivered by the native menu bar app via `rumps.notification`
  (`NSUserNotification`), since the root daemon cannot post to Notification
  Center). batmon provides root-cause analysis (e.g. "Caused by: Chrome") and actionable advice, flagging:
  - **Per-app energy spikes** - an app using more than 2x its trailing 7-day
    average (and more than 1.5 Wh today), once it has enough history.
  - **High thermal pressure** - sustained Heavy/Trapping thermal state.
  - **Rapid discharge** - a high average draw while on battery.
  - **Sleep drain (dark wakes)** - abnormal charge lost across a sleep gap, with the processes that woke or held the machine and the repeat offender.
  - **Weak charger** - "charging is bad": you are plugged into AC but the
    battery is still net-draining, so your adapter or cable cannot keep up.

### Connected devices and the menu bar

- **Bluetooth accessory battery levels.** See the charge of your AirPods, Magic
  Mouse, and keyboards right on the dashboard.
- **Radio warnings.** Get a dismissible hint if your Wi-Fi is on but disconnected,
  or Bluetooth is on with no devices connected, helping you save background power.
- **Native Menu-bar App.** Watts, charge %, and forecast live in your menu
  bar, with a sparkline power trend, top apps, battery health, one-tap
  Keep-awake / Low Power Mode / Open Battery Settings actions, and anomaly
  notifications. It is a completely standalone native Python app (`rumps`) that
  polls the API with **zero subprocess forks**, making it completely silent to
  EDR (Endpoint Detection and Response) tools. If the web service is down it
  gracefully degrades so the menu-bar readout never goes blank.

## Architecture

Three small processes share one local SQLite database (WAL mode). The daemon is
the only writer; everything else reads.

| Process | Runs as | Role |
| --- | --- | --- |
| **batmond** | root LaunchDaemon | The only database writer. Samples `ioreg` (battery, every 15s), `powermetrics` (a short burst per minute for component and per-app power), display brightness, and sleep assertions. Computes sessions, rollups, the discharge forecast, and anomalies. Accepts no network or socket input. |
| **batmon-web** | user LaunchAgent | FastAPI service on `127.0.0.1:8899`, serving the dashboard and JSON API. Opens the database **read-only**. Owns the optional `caffeinate` child for the Keep-awake toggle. |
| **Menu-bar app** (`ui/batmon_menu.py`) | user LaunchAgent | Native macOS menu bar app via `rumps`. Reads `/api/now`, gracefully handles API unavailability. Delivers native anomaly notifications (`NSUserNotification`). |

Data lives in `/usr/local/var/batmon/batmon.db`. Raw samples are kept 48 hours,
hourly rollups 90 days, and daily rollups indefinitely, so long-term trends stay
cheap to store.

## Installation

batmon installs with a single script. It needs Apple Silicon and a system
`python3` (the Xcode Command Line Tools interpreter is fine) - there is no
Homebrew or third-party charge-limit dependency for the core install. You are
asked for your password once so the installer can register the root daemon.

```bash
./install.sh
```

That's it. The daemon starts collecting immediately, and the native menu bar app will appear at the top of your screen. Open the dashboard:

**http://127.0.0.1:8899**

### Uninstall

```bash
./uninstall.sh            # remove batmon, keep your historical data
./uninstall.sh --purge    # remove batmon and delete the database
```

## Usage

Open **http://127.0.0.1:8899** and use the tabs:

- **Now** - live power, forecast, component breakdown, top apps (with
  pause/resume/kill), session, and connected devices.
- **History** - charge %, watts, and component power over 24h / 7d / 30d.
- **Apps** - per-app energy over 1h through 30d; toggle system processes on/off.
- **Energy** - discharged vs charged watt-hours with brightness overlay.
- **Health** - capacity, cycles, cell balance, lifetime temps, and trend.
- **Charging** - sessions, time on battery vs AC, and discharge-depth histogram.
- **Anomalies** - the full anomaly report.

Keep-awake and the Battery-limit status/Settings link live in the header;
Low Power Mode toggles from the native menu bar app.

## Development and testing

No root access is needed to hack on batmon. The daemon can run against recorded
fixtures captured on a real Apple Silicon Mac and write to a throwaway database,
so you can drive the whole stack with synthetic data.

```bash
# Set up a virtual environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Run the full test suite (113 pass, 1 skipped)
.venv/bin/pytest tests/ -v

# Dry-run the daemon: 480 ticks x 15s = ~2h of synthetic data, no root needed
.venv/bin/python -m batmond --dry-run --db /tmp/dev.db --ticks 480

# Serve the web UI against that dev database
BATMON_DB=/tmp/dev.db .venv/bin/uvicorn batmon_web.main:app --host 127.0.0.1 --port 8899
```

Parsers are tested against real `powermetrics` / `ioreg` fixtures checked into
the repo; rollup, session, forecast, and anomaly logic is covered by
synthetic-data unit tests (including sleep gaps, power-source flips, and DST),
and the API is tested with FastAPI's `TestClient` against a fixture-built
database.

## Privacy and non-goals

- **100% local.** No analytics, no tracking, no cloud, no account. Every sample
  stays in a SQLite file on your Mac.
- **No remote access.** The web service binds to `127.0.0.1` only. Its single
  state-changing endpoint set is local by design (Keep-awake, Low Power Mode,
  process actions, and opening Battery Settings).
- **Least privilege.** The root daemon opens no network sockets and never runs
  from your writable project folder - only from a root-owned directory. Its one
  local input is a command spool in a user-owned `0700` directory, restricted
  to a fixed whitelist (the Low Power Mode toggle and its auto-threshold); it
  runs no arbitrary input and executes fixed Apple binaries with fixed
  arguments.
- **Not in scope:** Intel Macs, `powerlog` import, websockets/streaming (the UI
  polls over HTTP), and any form of remote monitoring.
