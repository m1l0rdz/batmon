# AGENTS.md

Agent instructions for this repository live in [CLAUDE.md](CLAUDE.md).
Read it first - everything there (invariants, autonomy rule, coding
principles, typography) is binding for ANY coding agent, not only Claude.

Quick orientation:

- Project: batmon - macOS battery monitor for Apple Silicon (Python +
  FastAPI + SQLite + Native Menu Bar).
- **Status: FULLY IMPLEMENTED.** All 18 plan tasks (0-17) are complete.
  95 tests pass, 1 skipped (optional charging fixture not captured).
- WHAT was built: [2026-07-07-batmon-design.md](2026-07-07-batmon-design.md)
  (approved design).
- HOW it was built: [docs/superpowers/plans/2026-07-07-batmon-implementation.md](docs/superpowers/plans/2026-07-07-batmon-implementation.md)
  (task-by-task plan with tests and code). Its "Design amendments" table
  (D1-D12) overrides the design doc where they conflict.
- Hard rules that must never be violated: CLAUDE.md section
  "Invariants (do not violate)".
- Self-learning system (gitignored, internal): read `constraints.md` and
  `lessons.md` at the repo root before non-trivial work, and append to them
  after resolving a non-obvious bug. See CLAUDE.md "Self-learning system".
