from bestseller.services.entry_feedback import (
    build_entry_blueprint_review_row,
    entry_feedback_passes_privacy_gate,
    sanitize_entry_feedback_text,
)


def test_successful_entry_pattern_becomes_anonymized_review_row() -> None:
    row = build_entry_blueprint_review_row(
        {
            "entry_type": "artifact",
            "mechanism_summary": "核心资产每次解决问题后都会提高维护成本和势力关注。",
            "state_variables": ["asset_control", "faction_attention"],
            "required_cost_patterns": ["maintenance_cost"],
            "reader_rewards": ["earned_solution"],
            "genre": "玄幻",
            "sub_genre": "修仙",
            "confidence": 0.86,
            "entry_name": "青衡玉册",
            "project_title": "玄门账本",
        }
    )

    assert row is not None
    assert row["dimension"] == "entry_blueprints"
    assert row["status"] == "review"
    assert "青衡玉册" not in str(row)
    assert "玄门账本" not in str(row)
    assert row["content_json"]["state_variables"] == ["asset_control", "faction_attention"]


def test_feedback_privacy_gate_rejects_source_specific_text() -> None:
    row = {
        "name": "青衡玉册机制",
        "narrative_summary": "复用青衡玉册在玄门账本第12章的获取桥段。",
        "confidence": 0.9,
        "content_json": {},
    }

    passed, reason = entry_feedback_passes_privacy_gate(row)

    assert passed is False
    assert reason == "source_specific"


def test_low_confidence_feedback_is_rejected() -> None:
    row = build_entry_blueprint_review_row(
        {
            "entry_type": "artifact",
            "mechanism_summary": "机制还不稳定。",
            "confidence": 0.4,
        }
    )

    assert row is None


def test_sanitize_entry_feedback_text_removes_named_entities() -> None:
    text = sanitize_entry_feedback_text(
        "青衡玉册帮助宁尘解决玄门账本主线。",
        blocked_terms=("青衡玉册", "宁尘", "玄门账本"),
    )

    assert "青衡玉册" not in text
    assert "宁尘" not in text
    assert "玄门账本" not in text
    assert "[REDACTED]" in text
