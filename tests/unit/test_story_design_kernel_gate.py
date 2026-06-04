from __future__ import annotations

import json

from bestseller.services.prewrite_quality_profile import (
    evaluate_story_design_kernel_quality,
    has_kernel_leak,
    sanitize_distilled_leak,
)


def _full_coverage_kernel(target_chapters: int) -> dict:
    """A minimal but gate-valid kernel: clean text + beats covering all chapters."""
    return {
        "reader_promise": "每章产生可见状态变化。",
        "beat_schedule": [
            {"chapter_range": "1-5", "duty": "建立承诺与第一轮兑现。"},
            {"chapter_range": "6-10", "duty": "压力升级与代价显形。"},
            {"chapter_range": "11-15", "duty": "中段转折与反噬。"},
            {"chapter_range": f"16-{target_chapters}", "duty": "高潮与闭环。"},
        ],
    }


class TestHasKernelLeak:
    def test_off_genre_tokens_flagged(self) -> None:
        assert has_kernel_leak("siege_under_pressure now")
        assert has_kernel_leak("the alliance depleted its forces")
        assert has_kernel_leak("advisor council convened")

    def test_fallback_tokens_flagged(self) -> None:
        assert has_kernel_leak("fallback_progress")
        assert has_kernel_leak("complete-extraction-failure")
        assert has_kernel_leak("zero-confidence source")

    def test_clean_text_not_flagged(self) -> None:
        assert not has_kernel_leak("外卖员纪渊在站点被误读为暗子。")
        assert not has_kernel_leak("")


class TestSanitizeDistilledLeak:
    def test_drops_polluted_leaves_keeps_clean(self) -> None:
        payload = {
            "state_variables": ["fallback_progress", "威望误涨值"],
            "worldview": {
                "summary": "siege_under_pressure",
                "real": "供货链反噬倒计时",
            },
            "notes": "line one\nadvisor council\nline three",
        }
        cleaned = sanitize_distilled_leak(payload)
        dumped = json.dumps(cleaned, ensure_ascii=False)
        # No blocked token survives anywhere
        assert not has_kernel_leak(dumped)
        # Clean content is preserved
        assert "威望误涨值" in cleaned["state_variables"]
        assert cleaned["worldview"]["real"] == "供货链反噬倒计时"
        assert "line one" in cleaned["notes"] and "line three" in cleaned["notes"]
        # The fully-polluted nested key is dropped, not left empty
        assert "summary" not in cleaned["worldview"]

    def test_idempotent_on_clean_payload(self) -> None:
        payload = {"a": ["b", "c"], "d": {"e": "f"}}
        assert sanitize_distilled_leak(payload) == payload


class TestStoryDesignKernelGate:
    def test_clean_full_coverage_kernel_passes(self) -> None:
        report = evaluate_story_design_kernel_quality(
            _full_coverage_kernel(20), target_chapters=20
        )
        assert report.passed, [f.code for f in report.blocking_findings]

    def test_opening_only_beats_blocked(self) -> None:
        kernel = {"reader_promise": "ok", "beat_schedule": [{"chapter_range": "1-3"}]}
        report = evaluate_story_design_kernel_quality(kernel, target_chapters=20)
        assert not report.passed
        assert "beat_schedule_incomplete" in [f.code for f in report.blocking_findings]

    def test_leak_tokens_blocked(self) -> None:
        kernel = _full_coverage_kernel(20)
        kernel["state_variables"] = ["fallback_progress"]
        report = evaluate_story_design_kernel_quality(kernel, target_chapters=20)
        assert not report.passed
        assert "fallback_source_leak" in [f.code for f in report.blocking_findings]

    def test_sanitized_kernel_passes(self) -> None:
        kernel = _full_coverage_kernel(20)
        kernel["state_variables"] = ["fallback_progress", "威望误涨值"]
        sanitized = sanitize_distilled_leak(kernel)
        report = evaluate_story_design_kernel_quality(sanitized, target_chapters=20)
        assert report.passed, [f.code for f in report.blocking_findings]
