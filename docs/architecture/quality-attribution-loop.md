# Universal Quality Attribution Loop

This architecture replaces book-specific quality patches with a generic L0-L3 loop:

1. L0 reader panel reads chapter text and emits concrete reader feedback.
2. L1 causal attribution maps that feedback to the earliest upstream artifact layer.
3. L2 artifact health audit checks whether that artifact is specific enough to drive distinctive downstream output.
4. L3 repair planning sorts affected artifacts top-down and routes to existing repair surfaces.

The framework-level hard-coded surface is intentionally small: artifact topology, reader-panel roles, and repair priority. It does not encode a book's known bugs or genre-specific failure list.

## Entry Points

Service entry point:

```python
from bestseller.services.quality_attribution_loop import run_quality_attribution_loop
```

CLI entry points:

```bash
python -m bestseller.cli.quality_loop \
  --book-root output/exorcist-detective-1778051012 \
  --chapter-range 1-10 \
  --max-iterations 3

bestseller quality-loop \
  --book-root output/exorcist-detective-1778051012 \
  --chapter-range 1-10
```

Reports are written to:

```text
<book-root>/audits/quality-attribution-loop/
```

Files:

- `reader_feedback.jsonl`
- `attribution_report.jsonl`
- `artifact_health.jsonl`
- `repair_log.jsonl`

## Implementation Map

- L0: `src/bestseller/services/reader_panel_judge.py`
- L1: `src/bestseller/services/causal_attribution.py`
- L2: `src/bestseller/services/artifact_health_audit.py`
- L3: `src/bestseller/services/quality_attribution_loop.py`
- Stable topology: `src/bestseller/domain/artifact_topology.py`
- Stable panel roles: `src/bestseller/domain/reader_panel.py`

## Repair Boundary

The L3 implementation plans repair actions instead of directly rewriting story artifacts. That keeps the first MVP non-destructive while still enforcing the important ordering rule: upstream artifacts are selected before downstream chapter rewrite work. Material-like layers route through `plan_material_self_repair`; chapter text routes to the autonomous repair trigger metadata.
