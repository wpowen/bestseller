from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.identity_guard import CharacterIdentity
from bestseller.services.narrative_contracts import (
    build_identity_manifest,
    validate_chapter_plan_contract,
    validate_foundation_identity_contract,
    validate_scene_contract_pre_draft,
)


pytestmark = pytest.mark.unit


def test_foundation_identity_contract_blocks_missing_gender() -> None:
    report = validate_foundation_identity_contract(
        {
            "protagonist": {
                "name": "沈砚",
                "role": "protagonist",
            }
        }
    )

    assert report.blocks is True
    assert {
        "FOUNDATION_IDENTITY_GENDER_MISSING",
        "FOUNDATION_IDENTITY_PRONOUN_MISSING",
    }.issubset({violation.code for violation in report.violations})


def test_foundation_identity_contract_builds_locked_manifest() -> None:
    cast_spec = {
        "protagonist": {
            "name": "沈砚",
            "role": "protagonist",
            "gender": "male",
            "aliases": ["沈导航"],
        },
        "supporting_cast": [
            {
                "name": "苏晚",
                "role": "ally",
                "gender": "female",
            }
        ],
    }

    report = validate_foundation_identity_contract(cast_spec)
    manifest = build_identity_manifest(cast_spec)

    assert report.passed is True
    assert manifest[0]["name"] == "沈砚"
    assert manifest[0]["pronoun_set_zh"] == "他"
    assert manifest[1]["pronoun_set_en"] == "she/her"


def test_chapter_plan_contract_blocks_unknown_participant_and_missing_time() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "bad-plan",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "失准星图",
                    "main_conflict": "沈砚必须在封港前找到异常信号。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "participants": ["沈砚", "陌生人"],
                            "purpose": {"story": "发现封港命令。"},
                        }
                    ],
                }
            ],
        }
    )

    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=[{"name": "沈砚", "aliases": []}],
    )

    assert report.blocks is True
    assert {
        "PLAN_SCENE_TIME_MISSING",
        "PLAN_SCENE_UNKNOWN_PARTICIPANT",
    }.issubset({violation.code for violation in report.violations})


def test_chapter_plan_contract_blocks_placeholder_planning_text() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "placeholder-plan",
            "chapters": [
                {
                    "chapter_number": 12,
                    "title": "回声假章",
                    "chapter_goal": "推动本章剧情发展",
                    "main_conflict": "宁尘必须在生存压力代表的势力角力中找到位置。",
                    "hook_description": "回声假章尾声把「尾钩」转化为下一章必须处理的新压力。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "transition",
                            "time_label": "章节开场",
                            "participants": ["宁尘"],
                            "purpose": {
                                "story": "第12章第1场：具体事件是「开场」。",
                                "emotion": "压力上升。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=[{"name": "宁尘", "aliases": []}],
    )

    assert report.blocks is True
    assert {
        "PLAN_CHAPTER_GOAL_GENERIC",
        "PLAN_CHAPTER_CONFLICT_GENERIC",
        "PLAN_CHAPTER_HOOK_GENERIC",
        "PLAN_SCENE_TIME_GENERIC",
        "PLAN_SCENE_STORY_PURPOSE_GENERIC",
    }.issubset({violation.code for violation in report.violations})


def test_chapter_plan_contract_blocks_purpose_character_missing_from_participants() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "missing-purpose-character",
            "chapters": [
                {
                    "chapter_number": 70,
                    "title": "半决赛",
                    "main_conflict": "宁尘必须击败叶长青亲传弟子。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "duel",
                            "time_label": "大比半决赛开场",
                            "participants": ["宁尘"],
                            "purpose": {
                                "story": "孙乾开场使用叶长青亲传秘术。",
                                "emotion": "压力升级。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=[
            {"name": "宁尘", "aliases": []},
            {"name": "孙乾", "aliases": []},
        ],
    )

    assert report.blocks is True
    assert "PLAN_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS" in {
        violation.code for violation in report.violations
    }


def test_pre_draft_scene_contract_blocks_registry_mismatch() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        participants=["沈砚", "陌生人"],
        time_label="第一日夜",
        purpose={"story": "沈砚确认封港命令的真实来源。"},
    )
    registry = [
        CharacterIdentity(
            name="沈砚",
            gender="unknown",
            pronoun_set_zh="",
            pronoun_set_en="",
        )
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert report.blocks is True
    assert {
        "PREDRAFT_IDENTITY_GENDER_UNRESOLVED",
        "PREDRAFT_IDENTITY_PRONOUN_UNRESOLVED",
        "PREDRAFT_SCENE_UNKNOWN_PARTICIPANT",
    }.issubset({violation.code for violation in report.violations})


def test_pre_draft_scene_contract_blocks_purpose_character_missing_from_participants() -> None:
    scene = SimpleNamespace(
        scene_number=2,
        participants=["宁尘"],
        time_label="大比半决赛中段",
        purpose={"story": "孙乾剑势一变，逼宁尘暴露底牌。"},
    )
    registry = [
        CharacterIdentity(
            name="宁尘",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="孙乾",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert report.blocks is True
    assert "PREDRAFT_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS" in {
        violation.code for violation in report.violations
    }


def test_pre_draft_scene_contract_allows_case_subjects_and_dead_references() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        participants=["林渊", "苏婉宁", "孙九斤", "钱婆婆", "张家后人"],
        time_label="第十三章子夜",
        purpose={
            "story": "围绕“老张旧案”推进：追查张建军临死话和旧案，厘清他不是张家开门人。",
            "emotion": "保持悬疑压力。",
        },
    )
    registry = [
        CharacterIdentity(
            name="林渊",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="苏婉宁",
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
        ),
        CharacterIdentity(
            name="孙九斤",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="钱婆婆",
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
        ),
        CharacterIdentity(
            name="张家后人",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="张建军",
            aliases=("老张",),
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
            is_alive=False,
        ),
        CharacterIdentity(
            name="旧案证人",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert "PREDRAFT_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS" not in {
        violation.code for violation in report.violations
    }


def test_pre_draft_scene_contract_allows_historical_photo_reference() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        participants=["林渊", "苏婉宁", "孙九斤", "钱婆婆", "张家后人"],
        time_label="第十五章子夜",
        purpose={
            "story": "围绕“林正淳旧照”推进：释放林正淳曾替林渊还过第一笔债的局部信息。",
            "emotion": "保持悬疑压力。",
        },
    )
    registry = [
        CharacterIdentity(
            name="林渊",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="苏婉宁",
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
        ),
        CharacterIdentity(
            name="孙九斤",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="钱婆婆",
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
        ),
        CharacterIdentity(
            name="张家后人",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="林正淳",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert "PREDRAFT_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS" not in {
        violation.code for violation in report.violations
    }


def test_pre_draft_scene_contract_blocks_placeholder_scene_card() -> None:
    scene = SimpleNamespace(
        scene_number=3,
        participants=["宁尘"],
        time_label="章节结尾",
        purpose={"story": "尾场必须兑现变化，具体事件是「尾钩」。"},
    )
    registry = [
        CharacterIdentity(
            name="宁尘",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        )
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert report.blocks is True
    assert {
        "PREDRAFT_SCENE_TIME_GENERIC",
        "PREDRAFT_SCENE_STORY_PURPOSE_GENERIC",
    }.issubset({violation.code for violation in report.violations})
