# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Fully implemented. All 18 plan tasks (0-17) are complete. 95 tests pass,
1 skipped (optional ioreg_battery_charging.plist not captured as
discharging-while-plugged is hard to force). v1.1 dark-wakes behavior was
corrected 2026-07-09 (see `lessons.md`).

Two documents govern the implementation:

1. WHAT was built: approved design
   [2026-07-07-batmon-design.md](2026-07-07-batmon-design.md).
2. HOW it was built: implementation plan
   [docs/superpowers/plans/2026-07-07-batmon-implementation.md](docs/superpowers/plans/2026-07-07-batmon-implementation.md)
   - 18 tasks (0-17) with exact file paths, tests, code, and commit steps.
   Its "Design amendments" table (D1-D12) resolves design-doc ambiguities
   and OVERRIDES the design doc where they conflict (session kinds, ioreg
   mAh fields, rollup component columns, anomalies UNIQUE constraint,
   plugin is API-only, no venv under /usr/local/libexec, etc.).

Milestone mapping (design section 10):
tasks 0-4 = parsers + fixtures, 5-9 = batmond core, 10-11 = forecast +
anomalies, 12-14 = web API + dashboard, 15 = SwiftBar plugin,
16-17 = installers + E2E smoke.

Fixtures captured on the target machine (MacBook Pro M4 Pro, macOS 26.5)
and checked in under tests/fixtures/. KEY_* constants in parsers are
FIXTURE-VERIFIED - see tests/fixtures/NOTES.md.

## What this is

batmon: a macOS battery monitor for Apple Silicon (target: MacBook Pro M4 Pro,
macOS 26.5). Shows per-app energy attribution, component power breakdown,
charging patterns, battery health trend, discharge forecast, and anomaly alerts.

Stack: Python + FastAPI + SQLite; Native Python menu bar app (rumps); vanilla JS +
vendored Chart.js dashboard (no CDN).

## Architecture (three processes, one SQLite file)

- **batmond** - root LaunchDaemon, the ONLY database writer. Samples
  `powermetrics` (5s burst per minute), `ioreg` AppleSmartBattery (15s),
  display brightness, and `pmset -g assertions`. Does rollups, sessions,
  forecast, and anomaly detection internally.
- **batmon-web** - user LaunchAgent, FastAPI on 127.0.0.1:8899, opens the DB
  read-only (`file:...?mode=ro`). Owns the optional `caffeinate` child for the
  keep-awake toggle (not a DB participant for that).
- **Menu bar app** (`ui/batmon_menu.py`) - reads `/api/now`. Delivers anomaly
  notifications natively via `rumps.notification`, keeping track of anomalies in memory.

Database: SQLite WAL at `/usr/local/var/batmon/batmon.db`. Schema is in design
doc section 6. Raw tables keep 48h; hourly rollups 90 days; daily rollups forever.

## Commands

```bash
# setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# tests (full suite)
.venv/bin/pytest tests/ -v

# dry-run daemon: 480 ticks x 15s = 2h synthetic data, no root needed
.venv/bin/python -m batmond --dry-run --db /tmp/dev.db --ticks 480

# capture parser fixtures (target machine only, sudo once)
bash scripts/capture_fixtures.sh

# run web against a dev DB
BATMON_DB=/tmp/dev.db .venv/bin/uvicorn batmon_web.main:app --host 127.0.0.1 --port 8899
```

Files appear as plan tasks land; these are the plan's canonical command forms.

## Development and testing

- No root needed for development: `batmond --dry-run` reads recorded fixture
  files instead of executing powermetrics/ioreg and writes to a temp DB.
- Parsers are tested against real fixtures captured on the target machine and
  checked into the repo. Never invent powermetrics/ioreg output shapes - use
  the fixtures.
- Rollup/session/forecast/anomaly logic: synthetic-data unit tests, including
  sleep gaps, power source flips, and DST.
- API tests: pytest + FastAPI TestClient against a fixture-built DB.
- End-to-end smoke: `batmond --dry-run` over fixtures -> temp DB -> API queries.

## Invariants (do not violate)

- The root daemon is the only DB writer; web and plugin are read-only clients.
- The root daemon opens no network sockets. Its only input is the command
  spool at `/usr/local/var/batmon/ipc/` (user-owned, `0700`), restricted to a
  fixed whitelist (`lpm`, `auto_lpm_threshold`) that runs fixed Apple binaries
  with fixed arguments - keep it that way; never widen it to arbitrary input.
  The daemon must run only from root-owned `/usr/local/libexec/batmon/`, never
  from the user-writable project dir (privilege escalation otherwise).
  install.sh copies code there.
- The daemon chmods `batmon.db`, `-wal`, and `-shm` to 0644 after opening;
  readers fail without this.
- Web binds 127.0.0.1 only. There are exactly 4 state-changing endpoints (POST /api/awake, POST /api/open_battery_settings, POST /api/apps/action, POST /api/cmd) + IPC.
- Keep-awake uses `caffeinate -d -i` exactly - never `-s` or `-u` (lid close,
  power button, and manual sleep must keep working stock).
- An unhandled exception in one collection loop iteration must never kill the
  daemon: log and continue.
- Timestamps: `ts`/`hour` are UTC epoch seconds; `day` buckets use the local
  calendar day.
- No Intel support, no powerlog import, no websockets (HTTP polling only),
  no remote access - see design doc section 3 for the full non-goals list.
- **Strictly internal**: Internal documentation, planning documents (e.g. `docs/superpowers`), and development requirements (`requirements-dev.txt`) must NEVER be tracked in GitHub. Always add them to `.gitignore`.

## Self-learning system (internal, gitignored)

Two files at the repo root accumulate hard-won knowledge across sessions.
They are gitignored (internal, like `docs/superpowers`):

- **`constraints.md`** - distilled hard rules ("never do X" / "always do Y")
  drawn from the invariants above and from bugs actually hit. READ it before
  editing code; it is binding, same weight as the invariants.
- **`lessons.md`** - chronological log of non-obvious bugs/decisions:
  symptom -> root cause -> fix -> rule.

Workflow: read both at the start of non-trivial work. After resolving a
non-obvious bug or making a non-obvious decision, APPEND a `lessons.md` entry
and, if it generalizes, mirror the rule into `constraints.md`.

## Autonomy rule

Do everything you can yourself without asking permission for reversible
local work. Block only on:

- destructive operations (`rm -rf`, force-push, drop table, send to
  external party)
- actions with multi-stakeholder side effects (Slack post, Jira create,
  email send)
- credential or secret operations
- decisions the user has not yet expressed a default preference for

Default to action on reversible work. Free the user's time.

## Karpathy coding principles

Source: `github.com/multica-ai/andrej-karpathy-skills`. Equal weight to
the rules above; this is the section the operating contract calls "the
karpathy principles already in effect". Bias toward caution over speed;
for trivial tasks use judgment. Applies to every code/config edit.

1. **Think before coding.** State assumptions explicitly; if uncertain,
   ask. If multiple interpretations exist, present them - do not pick
   silently. Surface a simpler approach when one exists. If something is
   unclear, stop and name it. (Reinforces the push-back rule.)
2. **Simplicity first.** Minimum code that solves the problem, nothing
   speculative. No unrequested features, no abstractions for single-use
   code, no "flexibility/configurability" that was not asked for, no
   error handling for impossible scenarios. If 200 lines could be 50,
   rewrite. Test: "would a senior engineer call this overcomplicated?"
3. **Surgical changes.** Touch only what the request requires. Do not
   "improve" adjacent code, comments, or formatting; do not refactor
   what is not broken; match existing style. Remove only the
   imports/vars/functions YOUR change orphaned - flag pre-existing dead
   code, do not delete it. Every changed line must trace to the request.
4. **Goal-driven execution.** Turn each task into a verifiable goal with
   an explicit success check ("add validation" -> "write tests for
   invalid inputs, then make them pass"). For multi-step work, state a
   brief plan with a per-step verify, then loop until verified. Strong
   success criteria feed the [C] Check phase of FORDEC.

## FORDEC Analytical Decision Protocol

When the request is requirements analysis, Change Request evaluation,
feature design, or conflict resolution, apply the FORDEC framework.
(`.opencode/rules/fordec-protocol.mdc` is not present in this repo;
the phase summary below is the authoritative copy.)

Phase summary:

- **[F] Facts** - gather from the KB (graph, docs, code), NFRs. Cite sources (file + line range or doc URL).
- **[O] Options** - 2-3 viable architectural / business-logic / process variants, each one-line summarized first.
- **[R] Risks & Benefits** - cross-layer impact (backend, frontend, API contracts, data models) per option; trade-offs; NFR conflicts.
- **[D] Decision** - data-driven recommendation with rationale tied to [F] + [R].
- **[E] Execution** - Jira tasks (or `docs/tasks/*.md` stubs), User Stories, Acceptance Criteria.
- **[C] Check** - success criteria mapped back to business requirements + BABOK quality characteristics.

## Typography (strict)

- Hyphen `-` only. NO em dash, NO en dash.
- Straight quotes only. NO smart quotes.