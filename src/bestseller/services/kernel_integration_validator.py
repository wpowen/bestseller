from __future__ import annotations

from pathlib import Path

from bestseller.domain.gate_verdict import GateFinding, GateVerdict

EXPECTED_KERNEL_FILES: tuple[str, ...] = (
    "geography-kernel.json",
    "cultural-texture-module.json",
    "ensemble-arc-kernel.json",
    "mystery-anchor-kernel.json",
    "ethical-dilemma-kernel.json",
    "lineage-kernel.json",
    "crowd-scene.json",
    "zeitgeist-contract.json",
    "meta-layer-contract.json",
)


def validate_kernel_file_integration(
    story_bible_dir: str | Path,
    *,
    min_bytes: int = 3000,
) -> GateVerdict:
    kernels_dir = Path(story_bible_dir) / "kernels"
    findings: list[GateFinding] = []
    for filename in EXPECTED_KERNEL_FILES:
        path = kernels_dir / filename
        if not path.exists():
            findings.append(
                GateFinding(
                    code="kernel_file_missing",
                    severity="critical",
                    message=f"kernel file missing: {filename}",
                    path=str(path),
                    repair_action="materialize kernel file before prompt composition",
                )
            )
            continue
        if path.stat().st_size < min_bytes:
            findings.append(
                GateFinding(
                    code="kernel_file_too_small",
                    severity="critical",
                    message=f"kernel file appears placeholder-sized: {filename}",
                    path=str(path),
                    repair_action="replace placeholder with durable kernel content",
                )
            )
    coverage = (len(EXPECTED_KERNEL_FILES) - len(findings)) / len(EXPECTED_KERNEL_FILES)
    return GateVerdict(
        gate_name="kernel_file_integration",
        verdict="blocked" if findings else "pass",
        coverage=coverage,
        findings=tuple(findings),
        metrics={"expected_kernel_count": len(EXPECTED_KERNEL_FILES)},
    )
