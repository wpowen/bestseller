from __future__ import annotations

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput

pytestmark = pytest.mark.unit


def test_chapter_outline_preserves_worldview_compliance_fields() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "灵田试约",
                    "goal": "主角拿到灵田试运营资格。",
                    "main_conflict": "长老会要求主角用三日账册证明规则。",
                    "hook_description": "账册背面出现旧制度印记。",
                    "world_rule_refs": ["trust_debt_accounting"],
                    "world_rule_landing": "信任债影响灵田产出。",
                    "world_state_deltas": [
                        {
                            "key": "trust_balance",
                            "delta": "+1",
                            "evidence": "主角用三日账册证明授权有效。",
                        }
                    ],
                    "world_asset_refs": ["spirit_field_account_book"],
                    "authority_claim_refs": ["elder_council_controls_field_rights"],
                    "world_scene_template_ref": "public-rule-audit",
                    "reveal_weight": 1,
                    "anti_copy_boundary_notes": ["不要使用退婚羞辱开局。"],
                    "location_refs": ["外门灵田"],
                    "faction_refs": ["宗门长老会"],
                    "key_reveals": ["信任债会改变灵田产出。"],
                }
            ],
        }
    )

    payload = batch.model_dump(mode="json", by_alias=True)
    chapter = payload["chapters"][0]

    assert chapter["world_rule_refs"] == ["trust_debt_accounting"]
    assert chapter["world_rule_landing"] == "信任债影响灵田产出。"
    assert chapter["world_state_deltas"] == [
        {
            "key": "trust_balance",
            "delta": "+1",
            "evidence": "主角用三日账册证明授权有效。",
        }
    ]
    assert chapter["world_asset_refs"] == ["spirit_field_account_book"]
    assert chapter["authority_claim_refs"] == ["elder_council_controls_field_rights"]
    assert chapter["world_scene_template_ref"] == "public-rule-audit"
    assert chapter["reveal_weight"] == 1
    assert chapter["anti_copy_boundary_notes"] == ["不要使用退婚羞辱开局。"]
    assert chapter["location_refs"] == ["外门灵田"]
    assert chapter["faction_refs"] == ["宗门长老会"]
    assert chapter["key_reveals"] == ["信任债会改变灵田产出。"]


def test_chapter_outline_normalizes_structured_reference_fields() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 1,
                    "goal": "主角验证第一条规则。",
                    "world_asset_refs": [
                        {
                            "asset_key": "toll-constitution-ledger",
                            "description": "收费规则第一次被观测层降权。",
                        }
                    ],
                    "authority_claim_refs": [
                        {
                            "claim_key": "register-has-final-word",
                            "description": "登记员派立场错位。",
                        }
                    ],
                    "location_refs": [{"name": "废弃收费站"}],
                    "key_reveals": [{"key": "第一条剧本规则可以被表演反制"}],
                }
            ],
        }
    )

    chapter = batch.model_dump(mode="json", by_alias=True)["chapters"][0]

    assert chapter["world_asset_refs"] == ["toll-constitution-ledger"]
    assert chapter["authority_claim_refs"] == ["register-has-final-word"]
    assert chapter["location_refs"] == ["废弃收费站"]
    assert chapter["key_reveals"] == ["第一条剧本规则可以被表演反制"]


def test_chapter_outline_clamps_reveal_weight() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 1,
                    "goal": "主角验证第一条规则。",
                    "reveal_weight": 8,
                },
                {
                    "chapter_number": 2,
                    "goal": "主角隐藏第二条线索。",
                    "reveal_weight": "7",
                },
            ],
        }
    )

    payload = batch.model_dump(mode="json", by_alias=True)

    assert payload["chapters"][0]["reveal_weight"] == 5
    assert payload["chapters"][1]["reveal_weight"] == 5
