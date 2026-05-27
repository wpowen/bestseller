# Material Phase 6 Verify

- Implemented: `scripts/material_health_dashboard.py`.
- Generated report:
  - `.audit-reports/2026-05-27-material-phase-6-health.md`
- Verification:
  - `uv run python scripts/material_health_dashboard.py --slug exorcist-detective-1778051012 --output .audit-reports/2026-05-27-material-phase-6-health.md` -> report generated.
  - `uv run ruff check ...` -> passed for new dashboard script and services.

