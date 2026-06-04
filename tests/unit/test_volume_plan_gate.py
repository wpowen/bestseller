from __future__ import annotations

from bestseller.services.prewrite_quality_profile import evaluate_volume_plan_quality


def _vol(phase: str, force: str, *, climax: bool = True) -> dict:
    entry = {"conflict_phase": phase, "primary_force_name": force, "volume_goal": "推进主线"}
    if climax:
        entry["volume_climax"] = "高潮：代价全面兑现。"
    return entry


class TestVolumePlanGate:
    def test_single_volume_20ch_passes(self) -> None:
        # 20ch maps to a single volume (compute_linear_hierarchy(20) -> 1 vol);
        # one conflict_phase / primary_force is structurally correct, not "thin".
        plan = {"volumes": [_vol("survival", "生存压力")]}
        report = evaluate_volume_plan_quality(plan, target_chapters=20)
        assert report.passed, [f.code for f in report.blocking_findings]

    def test_multi_volume_collapsed_to_one_phase_blocked(self) -> None:
        plan = {"volumes": [_vol("survival", "生存压力"), _vol("survival", "生存压力")]}
        report = evaluate_volume_plan_quality(plan, target_chapters=20)
        assert not report.passed
        assert "volume_plan_thin" in [f.code for f in report.blocking_findings]

    def test_multi_volume_with_variety_passes(self) -> None:
        plan = {
            "volumes": [
                _vol("survival", "生存压力"),
                _vol("political_intrigue", "财阀董事会"),
            ]
        }
        report = evaluate_volume_plan_quality(plan, target_chapters=20)
        assert report.passed, [f.code for f in report.blocking_findings]

    def test_single_volume_without_climax_marker_flagged(self) -> None:
        plan = {"volumes": [_vol("survival", "生存压力", climax=False)]}
        report = evaluate_volume_plan_quality(plan, target_chapters=20)
        assert not report.passed
        assert "volume_plan_thin" in [f.code for f in report.blocking_findings]

    def test_short_book_under_20ch_not_required_to_be_multi(self) -> None:
        plan = {"volumes": [_vol("survival", "生存压力")]}
        report = evaluate_volume_plan_quality(plan, target_chapters=12)
        assert report.passed, [f.code for f in report.blocking_findings]
