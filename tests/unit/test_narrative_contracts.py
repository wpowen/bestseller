from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.identity_guard import CharacterIdentity
from bestseller.services.narrative_contracts import (
    build_identity_manifest,
    repair_legacy_foundation_identity_locks,
    repair_legacy_scene_contract_model_pre_draft,
    repair_legacy_scene_contract_pre_draft,
    repair_missing_scene_participants_pre_draft,
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


def test_repair_legacy_foundation_identity_locks_uses_hints() -> None:
    repaired, count = repair_legacy_foundation_identity_locks(
        {
            "protagonist": {"name": "Rowan Ashford", "role": "protagonist"},
            "antagonist": {"name": "Victor Hale", "role": "antagonist"},
        },
        identity_hints=[
            {
                "name": "Rowan Ashford",
                "gender": "female",
                "pronoun_set_zh": "她",
                "pronoun_set_en": "she/her",
            }
        ],
    )

    assert count > 0
    assert repaired is not None
    report = validate_foundation_identity_contract(repaired)
    assert report.passed is True
    assert repaired["protagonist"]["gender"] == "female"
    assert repaired["antagonist"]["gender"] == "male"


def test_repair_legacy_foundation_identity_locks_uses_existing_pronouns_without_defaults() -> None:
    repaired, count = repair_legacy_foundation_identity_locks(
        {
            "supporting_cast": [
                {
                    "name": "镜渊之主",
                    "role": "antagonist",
                    "gender": "unknown",
                    "pronoun_set_zh": "它",
                    "pronoun_set_en": "it",
                },
                {"name": "Unresolved Witness", "role": "supporting"},
            ],
        },
        allow_unreliable_defaults=False,
    )

    assert count == 1
    assert repaired is not None
    assert repaired["supporting_cast"][0]["gender"] == "nonbinary"
    assert repaired["supporting_cast"][1].get("gender") is None


def test_repair_legacy_foundation_identity_locks_defaults_unknowns_to_nonbinary() -> None:
    repaired, _ = repair_legacy_foundation_identity_locks(
        {
            "protagonist": {"name": "QX-17", "role": "protagonist"},
            "supporting_cast": [{"name": "Archive Witness", "role": "supporting"}],
        }
    )

    assert repaired is not None
    report = validate_foundation_identity_contract(repaired)
    assert report.passed is True
    assert repaired["protagonist"]["gender"] == "nonbinary"
    assert repaired["protagonist"]["pronoun_set_en"] == "they/them"


def test_repair_legacy_foundation_identity_locks_removes_alias_colliding_with_name() -> None:
    repaired, count = repair_legacy_foundation_identity_locks(
        {
            "supporting_cast": [
                {
                    "name": "周建设",
                    "role": "social_antagonist",
                    "gender": "male",
                    "pronoun_set_zh": "他",
                    "pronoun_set_en": "he/him",
                },
                {
                    "name": "周拆迁",
                    "role": "obstacle_investigator",
                    "gender": "male",
                    "pronoun_set_zh": "他",
                    "pronoun_set_en": "he/him",
                    "name_variants": ["周建设", "拆迁办周主任"],
                },
            ]
        }
    )

    assert count == 1
    assert repaired is not None
    assert repaired["supporting_cast"][1]["name_variants"] == ["拆迁办周主任"]
    assert validate_foundation_identity_contract(repaired).passed is True


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
                    "opening_situation": "承接上一章尾钩，主角没有空档去长篇解释设定。",
                    "main_conflict": "宁尘必须在生存压力代表的势力角力中找到位置。",
                    "hook_description": "回声假章尾声把「尾钩」转化为下一章必须处理的新压力。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "transition",
                            "time_label": "章节开场",
                            "participants": ["宁尘"],
                            "purpose": {
                                "story": "推动本章局势前进，并换来新的代价或信息。",
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
        "PLAN_CHAPTER_OPENING_GENERIC",
        "PLAN_CHAPTER_CONFLICT_GENERIC",
        "PLAN_CHAPTER_HOOK_GENERIC",
        "PLAN_SCENE_TIME_GENERIC",
        "PLAN_SCENE_STORY_PURPOSE_GENERIC",
    }.issubset({violation.code for violation in report.violations})


def test_chapter_plan_contract_blocks_functional_phase_title() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "functional-title",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "浮标初现",
                    "chapter_goal": "苏砚读取铜镜残痕，确认旧案与青萝镇有关。",
                    "main_conflict": "苏砚必须在镇民封宅前拿到铜镜里的火场残相。",
                    "hook_description": "铜镜裂开后，碎片指向姜家祖坟。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "investigation",
                            "time_label": "青萝镇旧宅黄昏",
                            "participants": ["苏砚"],
                            "purpose": {
                                "story": "苏砚进入旧宅，以砚台引出铜镜血泪。",
                                "emotion": "警觉与旧痛同时上升。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=[{"name": "苏砚", "aliases": []}],
    )

    assert report.blocks is True
    assert "PLAN_CHAPTER_TITLE_FUNCTIONAL" in {violation.code for violation in report.violations}


def test_chapter_plan_contract_blocks_meta_story_design_language() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "meta-plan",
            "chapters": [
                {
                    "chapter_number": 12,
                    "title": "师徒",
                    "chapter_goal": "建立沈夜寒的导师角色，完善九域镇物的世界观体系。",
                    "main_conflict": "沈夜寒的信息有价值但目的不明，苏砚在信任与怀疑之间摇摆。",
                    "hook_description": "第12章尾钩：围绕「沈夜寒的信息有价值但目的不明」出现新的证据、时限或代价，迫使苏砚下一章立刻行动。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "time_label": "青岚峰石屋夜间",
                            "participants": ["苏砚", "沈夜寒"],
                            "purpose": {
                                "story": "通过沈夜寒讲述引入志怪监内部势力分裂的线索。",
                                "emotion": "信任与怀疑并存。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=[{"name": "苏砚", "aliases": []}, {"name": "沈夜寒", "aliases": []}],
    )
    codes = {violation.code for violation in report.violations}

    assert report.blocks is True
    assert {
        "PLAN_CHAPTER_GOAL_META",
        "PLAN_CHAPTER_HOOK_GENERIC",
        "PLAN_SCENE_STORY_PURPOSE_META",
    }.issubset(codes)


def test_chapter_plan_contract_blocks_fallback_instruction_leakage() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "fallback-leak",
            "chapters": [
                {
                    "chapter_number": 54,
                    "title": "镜铺",
                    "chapter_goal": "苏砚追到镜铺后门。章内必须落到这件可见事件：苏砚处理「镜匠失踪」，并获得可用结果或付出明确损失。",
                    "main_conflict": "镜铺掌柜拒绝承认后门血迹属于失踪镜匠。",
                    "hook_description": "苏砚撬开后门时，墙内传出镜匠还活着的敲击声。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "investigation",
                            "time_label": "青萝镇镜铺后巷夜雨",
                            "participants": ["苏砚"],
                            "purpose": {
                                "story": "第54章中段1围绕「镜匠失踪」制造新的代价或信息交换。（本章目标：苏砚追到镜铺后门。）",
                                "emotion": "紧张感上升。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=[{"name": "苏砚", "aliases": []}],
    )
    codes = {violation.code for violation in report.violations}

    assert report.blocks is True
    assert "PLAN_CHAPTER_GOAL_META" in codes
    assert "PLAN_SCENE_STORY_PURPOSE_META" in codes


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


def test_pre_draft_scene_contract_warns_on_methodology_gaps_in_warn_mode() -> None:
    scene = SimpleNamespace(
        scene_number=2,
        scene_type="confrontation",
        participants=["沈砚", "顾临"],
        time_label="第一日夜",
        purpose={"story": "沈砚逼顾临交出黑匣子缺页。"},
        metadata_json={},
    )
    registry = [
        CharacterIdentity(
            name="沈砚",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="顾临",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
        methodology_contract_mode="warn",
    )

    assert report.blocks is False
    assert "SCENE_METHODOLOGY_CONTRACT_MISSING" in {
        warning.code for warning in report.warnings
    }


def test_pre_draft_scene_contract_blocks_methodology_gaps_in_strict_mode() -> None:
    scene = SimpleNamespace(
        scene_number=2,
        scene_type="confrontation",
        participants=["沈砚", "顾临"],
        time_label="第一日夜",
        purpose={"story": "沈砚逼顾临交出黑匣子缺页。"},
        metadata_json={},
    )
    registry = [
        CharacterIdentity(
            name="沈砚",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="顾临",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
        methodology_contract_mode="strict",
    )

    assert report.blocks is True
    assert "SCENE_METHODOLOGY_CONTRACT_MISSING" in {
        violation.code for violation in report.violations
    }


def test_repair_missing_scene_participants_pre_draft_uses_identity_context() -> None:
    scene = SimpleNamespace(
        scene_number=4,
        participants=[],
        time_label="Chapter 365 aftermath",
        purpose={
            "story": "Maya reads the letter with Kade and recognizes their father's handwriting."
        },
        entry_state={"Kade Mercer": {"emotion": "tense"}},
        exit_state={},
    )
    registry = [
        CharacterIdentity(
            name="Kade Mercer",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="Maya",
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
        ),
        CharacterIdentity(
            name="Sam Blake",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
            is_alive=False,
        ),
    ]

    repaired = repair_missing_scene_participants_pre_draft(
        scene,
        identity_registry=registry,
    )
    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert repaired == 2
    assert scene.participants == ["Kade Mercer", "Maya"]
    assert "PREDRAFT_SCENE_PARTICIPANTS_MISSING" not in {
        violation.code for violation in report.violations
    }


def test_repair_missing_scene_participants_prefers_resolved_identity_aliases() -> None:
    scene = SimpleNamespace(
        scene_number=4,
        participants=[],
        time_label="Chapter 365 letter reveal",
        purpose={
            "story": (
                "Maya reads the letter with Kade and recognizes their father's "
                "handwriting before the powered community displacement."
            )
        },
        entry_state={},
        exit_state={},
    )
    registry = [
        CharacterIdentity(name="Father", role="supporting"),
        CharacterIdentity(name="Maya", role="supporting"),
        CharacterIdentity(name="Kade", role="supporting"),
        CharacterIdentity(name="Powered Community", role="supporting"),
        CharacterIdentity(
            name="Kade Mercer",
            aliases=("Kade",),
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
            role="protagonist",
        ),
        CharacterIdentity(
            name="Maya Mercer",
            aliases=("Maya",),
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
            role="family",
        ),
        CharacterIdentity(
            name="Alex Reed",
            aliases=("Father",),
            gender="nonbinary",
            pronoun_set_zh="ta",
            pronoun_set_en="they/them",
            role="family",
        ),
    ]

    repaired = repair_missing_scene_participants_pre_draft(
        scene,
        identity_registry=registry,
    )
    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert repaired == 3
    assert scene.participants == ["Alex Reed", "Maya Mercer", "Kade Mercer"]
    assert report.passed is True


def test_repair_missing_scene_participants_adds_named_purpose_character() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        participants=["林渊", "苏婉宁"],
        time_label="第36章子夜",
        purpose={"story": "围绕“三百年第一账”推进：三百年前林远山封镜的第一账露出轮廓。"},
        entry_state={},
        exit_state={},
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
            name="林远山",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    repaired = repair_missing_scene_participants_pre_draft(
        scene,
        identity_registry=registry,
    )
    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert repaired == 1
    assert scene.participants == ["林渊", "苏婉宁", "林远山"]
    assert report.passed is True


def test_repair_interactive_scene_adds_state_delta_character() -> None:
    scene = SimpleNamespace(
        scene_number=2,
        scene_type="confrontation",
        participants=["林鸢"],
        time_label="第508章中段",
        purpose={
            "story": "一位被信任的盟友做出林鸢一时难以理解的举动，引发新的代价。"
        },
        entry_state={"林鸢": {"emotion": "隐忍"}},
        exit_state={
            "林鸢": {"emotion": "震惊"},
            "霍沉": {"emotion": "愤怒", "arc_state": "示弱引诱"},
        },
    )
    registry = [
        CharacterIdentity(
            name="林鸢",
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
        ),
        CharacterIdentity(
            name="霍沉",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
        ),
    ]

    repaired = repair_missing_scene_participants_pre_draft(
        scene,
        identity_registry=registry,
    )
    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
    )

    assert repaired == 1
    assert scene.participants == ["林鸢", "霍沉"]
    assert report.passed is True


def test_repair_missing_scene_participants_skips_excluded_offstage_character() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        participants=["林渊", "苏婉宁"],
        time_label="第37章井口",
        purpose={"story": "围绕“井口铜钱”推进：孙九斤在井口铜钱中找到钱家旧誓。"},
        entry_state={},
        exit_state={},
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
    ]

    repaired = repair_missing_scene_participants_pre_draft(
        scene,
        identity_registry=registry,
        excluded_names={"孙九斤"},
    )
    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=registry,
        require_identity_registry=True,
        excluded_names={"孙九斤"},
    )

    assert repaired == 0
    assert scene.participants == ["林渊", "苏婉宁"]
    assert "PREDRAFT_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS" not in {
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


def test_pre_draft_scene_contract_allows_storyline_reference() -> None:
    scene = SimpleNamespace(
        scene_number=1,
        participants=["林渊", "苏婉宁", "孙九斤", "钱婆婆"],
        time_label="第32章夜间",
        purpose={
            "story": "围绕“老宅来信”推进：林家老宅寄来迟到三年的信，林正淳线推进。",
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


def test_repair_legacy_scene_contract_replaces_generic_hook_fields() -> None:
    scene = SimpleNamespace(
        scene_number=4,
        participants=["Rowan Ashford"],
        time_label="章节结尾",
        title="Closing Hook",
        scene_type="hook",
        purpose={
            "story": (
                "End every chapter with a sharper question. "
                "[chapter goal: The High Lord's ultimatum forces Rowan to choose.]"
            ),
            "emotion": "",
        },
    )

    repaired = repair_legacy_scene_contract_pre_draft(scene, chapter_number=343)
    report = validate_scene_contract_pre_draft(
        scene,
        identity_registry=[
            CharacterIdentity(
                name="Rowan Ashford",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            )
        ],
        require_identity_registry=True,
    )

    assert repaired == 2
    assert report.passed is True
    assert scene.time_label.startswith("Chapter 343 scene 4:")
    assert "End every chapter" not in scene.purpose["story"]


def test_repair_legacy_scene_contract_model_removes_template_labels() -> None:
    scene_contract = SimpleNamespace(
        chapter_number=365,
        scene_number=3,
        contract_summary=(
            "End each chapter with escalating threat, revealed secret, or forced choice. "
            "[chapter goal: A bystander is drawn into the conflict by accident.]"
        ),
        information_release="Closing Hook",
        tail_hook="Closing Hook",
    )

    repaired = repair_legacy_scene_contract_model_pre_draft(scene_contract)

    assert repaired == 3
    assert "Closing Hook" not in scene_contract.information_release
    assert "closing" not in scene_contract.information_release.lower()
    assert "bystander" in scene_contract.information_release
    assert scene_contract.contract_summary.startswith("Chapter 365 scene 3")
