"""End-to-end plumbing: hype blocks → scene prompt.

Verifies the integration seam from the plan §Phase 1-2 "wire hype engine
into the live pipeline" task:

* ``SceneWriterContextPacket`` carries the new hype fields with safe
  ``None`` defaults so legacy code paths don't have to be updated.
* ``build_scene_draft_prompts`` renders the pre-rendered hype blocks into
  the user prompt in both Chinese and English language branches.
* When the blocks are ``None`` (legacy project with empty HypeScheme),
  the prompt is unchanged — no stray markers leak through.

These tests stand between the ``test_hype_engine_prompt`` unit tests
(which cover the rendering helpers in isolation) and the scene-pipeline
integration path in ``pipelines.py`` (covered by the scene pipeline
tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.services.drafts import _maybe_write_scene_prompt_trace, build_scene_draft_prompts


def _minimal_packet_kwargs() -> dict:
    return {
        "project_id": uuid4(),
        "project_slug": "test-project",
        "chapter_id": uuid4(),
        "scene_id": uuid4(),
        "chapter_number": 1,
        "scene_number": 1,
        "query_text": "q",
    }


pytestmark = pytest.mark.unit


def _sample_project(*, language: str = "zh-CN") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        title="诡豪试炼",
        slug="gui-hao-trial",
        language=language,
        genre="玄幻",
        sub_genre=None,
        status="drafting",
        metadata_json={},
    )


def _sample_chapter() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        chapter_goal="亮出冥符翻盘",
        title="第一章·冥符出世",
        status="drafting",
        production_state="pending",
        target_word_count=2200,
        current_word_count=0,
        metadata_json={},
    )


def _sample_scene() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        scene_number=1,
        title="当众羞辱",
        participants=["主角", "仇家"],
        purpose={"story": "抛出冥符底牌", "emotion": "压迫感"},
        time_label="夜",
        entry_state={"status": "被羞辱"},
        exit_state={"status": "冥符显形"},
        scene_type="hook",
        target_word_count=1200,
        status="planned",
        metadata_json={},
    )


def _sample_style_guide() -> SimpleNamespace:
    return SimpleNamespace(
        pov_type="third-limited",
        tone_keywords=["紧张", "压迫"],
    )


# ---------------------------------------------------------------------------
# SceneWriterContextPacket defaults.
# ---------------------------------------------------------------------------


class TestSceneContextPacketHypeDefaults:
    def test_new_packet_has_none_hype_fields(self) -> None:
        packet = SceneWriterContextPacket(**_minimal_packet_kwargs())
        assert packet.reader_contract_block is None
        assert packet.hype_constraints_block is None
        assert packet.assigned_hype_type is None
        assert packet.assigned_hype_recipe_key is None
        assert packet.assigned_hype_intensity is None
        assert packet.ranking_capability_profile_block is None
        assert packet.progression_context_block is None
        assert packet.decision_policy_block is None
        assert packet.rule_system_context_block is None
        assert packet.faction_ecology_context_block is None
        assert packet.relationship_agency_context_block is None

    def test_packet_accepts_populated_hype_fields(self) -> None:
        packet = SceneWriterContextPacket(
            **_minimal_packet_kwargs(),
            reader_contract_block="【读者契约】...",
            hype_constraints_block="【本章爽点约束】...",
            assigned_hype_type="face_slap",
            assigned_hype_recipe_key="冥符拍脸",
            assigned_hype_intensity=8.5,
        )
        assert packet.reader_contract_block == "【读者契约】..."
        assert packet.hype_constraints_block == "【本章爽点约束】..."
        assert packet.assigned_hype_type == "face_slap"
        assert packet.assigned_hype_recipe_key == "冥符拍脸"
        assert packet.assigned_hype_intensity == 8.5

    def test_packet_accepts_premium_engine_fields(self) -> None:
        packet = SceneWriterContextPacket(
            **_minimal_packet_kwargs(),
            ranking_capability_profile_block="【榜单级能力 Profile】固定入口和可解规则。",
            progression_context_block="【进阶体系约束】不得无因升级。",
            decision_policy_block="【主角决策策略】不得为虚荣冒险。",
            rule_system_context_block="【规则系统约束】规则必须有代价。",
            faction_ecology_context_block="【阵营生态与反应压力约束】势力必须反应。",
            relationship_agency_context_block="【关系张力与主角能动性约束】关系戏必须推进。",
        )
        assert (
            packet.ranking_capability_profile_block
            == "【榜单级能力 Profile】固定入口和可解规则。"
        )
        assert packet.progression_context_block == "【进阶体系约束】不得无因升级。"
        assert packet.decision_policy_block == "【主角决策策略】不得为虚荣冒险。"
        assert packet.rule_system_context_block == "【规则系统约束】规则必须有代价。"
        assert packet.faction_ecology_context_block == "【阵营生态与反应压力约束】势力必须反应。"
        assert (
            packet.relationship_agency_context_block
            == "【关系张力与主角能动性约束】关系戏必须推进。"
        )


class TestScenePromptTrace:
    def test_scene_prompt_trace_is_disabled_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("BESTSELLER_TRACE_SCENE_PROMPTS", raising=False)
        settings = SimpleNamespace(
            output=SimpleNamespace(base_dir=str(tmp_path)),
            generation=SimpleNamespace(context_budget_tokens=6000),
        )
        packet = SceneWriterContextPacket(**_minimal_packet_kwargs())

        path = _maybe_write_scene_prompt_trace(
            settings,
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            packet,
            system_prompt="system",
            user_prompt="user",
            workflow_run_id=None,
            step_run_id=None,
            model_tier="standard",
        )

        assert path is None
        assert not list(tmp_path.rglob("*.json"))

    def test_scene_prompt_trace_records_blocks_and_prompts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("BESTSELLER_TRACE_SCENE_PROMPTS", "full")
        settings = SimpleNamespace(
            output=SimpleNamespace(base_dir=str(tmp_path)),
            generation=SimpleNamespace(context_budget_tokens=6000),
        )
        ranking_block = "【榜单级能力 Profile】必须把能力落实为行动。"
        packet = SceneWriterContextPacket(
            **_minimal_packet_kwargs(),
            ranking_capability_profile_block=ranking_block,
        )

        path = _maybe_write_scene_prompt_trace(
            settings,
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            packet,
            system_prompt="system prompt",
            user_prompt=f"before\n{ranking_block}\nafter",
            workflow_run_id=None,
            step_run_id=None,
            model_tier="standard",
        )

        assert path is not None
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        assert payload["mode"] == "full"
        assert payload["project"]["slug"] == "gui-hao-trial"
        block = payload["context_blocks"]["ranking_capability_profile_block"]
        assert block["present"] is True
        assert block["included_in_user_prompt"] is True
        assert payload["prompts"]["user"].endswith("after")

    def test_scene_prompt_trace_can_use_rewrite_prefix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("BESTSELLER_TRACE_SCENE_PROMPTS", "summary")
        settings = SimpleNamespace(
            output=SimpleNamespace(base_dir=str(tmp_path)),
            generation=SimpleNamespace(context_budget_tokens=6000),
        )
        packet = SceneWriterContextPacket(**_minimal_packet_kwargs())

        path = _maybe_write_scene_prompt_trace(
            settings,
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            packet,
            system_prompt="system prompt",
            user_prompt="user prompt",
            workflow_run_id=None,
            step_run_id=None,
            model_tier="editor",
            trace_kind="rewrite",
        )

        assert path is not None
        assert Path(path).name.startswith("rewrite-prompt-")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        assert payload["trace_kind"] == "rewrite"


# ---------------------------------------------------------------------------
# build_scene_draft_prompts — hype blocks land in user_prompt.
# ---------------------------------------------------------------------------


class TestSceneDraftPromptsHypeBlocks:
    def test_none_blocks_leave_prompt_unchanged(self) -> None:
        """Legacy projects: no hype fields → no stray markers in prompt."""

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=None,
            hype_constraints_block=None,
        )
        assert "【读者契约】" not in user_prompt
        assert "【本章爽点约束】" not in user_prompt

    def test_empty_string_blocks_leave_prompt_unchanged(self) -> None:
        """Empty strings (also produced by no-op path) must not emit markers."""

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block="",
            hype_constraints_block="",
        )
        assert "【读者契约】" not in user_prompt
        assert "【本章爽点约束】" not in user_prompt

    def test_populated_blocks_land_in_user_prompt_zh(self) -> None:
        reader_block = (
            "【读者契约】(卖点: 诡异复苏 / 阴阳万亿资产)\n"
            "本书承诺: 第一章就要亮出冥符阴兵才是万亿资产的世界规则。"
        )
        hype_block = (
            "【本章爽点约束】\n"
            "- 爽点类型: face_slap (强度目标 8.5/10)\n"
            "- 推荐配方: 冥符拍脸-当众羞辱反转\n"
            "- 爽点 ≠ 章末悬念"
        )
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=reader_block,
            hype_constraints_block=hype_block,
        )
        assert "【读者契约】" in user_prompt
        assert "诡异复苏" in user_prompt
        assert "【本章爽点约束】" in user_prompt
        assert "face_slap" in user_prompt
        assert "冥符拍脸-当众羞辱反转" in user_prompt
        assert "爽点 ≠ 章末悬念" in user_prompt

    def test_fanqie_market_craft_profile_from_project_meta_lands_in_prompt(self) -> None:
        project = _sample_project()
        project.metadata_json = {
            "fanqie_craft_profile": {
                "category": "都市脑洞",
                "confidence": "medium",
                "allowed_style_principles": ["高压开场", "短句推进"],
                "disallowed_copy_targets": ["禁止复刻具体作者文风"],
                "hook_rules": ["开篇先给可见危机"],
                "pacing_rules": ["每章保留一个行动反馈"],
                "structure_rules": ["压迫-行动-回报-升级"],
                "sentence_style": "短句优先，少解释。",
            },
        }

        _, user_prompt = build_scene_draft_prompts(
            project,
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
        )

        assert "番茄榜单匿名工艺卡" in user_prompt
        assert "都市脑洞" in user_prompt
        assert "禁止复刻具体作者文风" in user_prompt
        assert "开篇先给可见危机" in user_prompt
        assert "压迫-行动-回报-升级" in user_prompt

    def test_qimao_opening_contract_lands_in_opening_prompt_zh(self) -> None:
        project = _sample_project()
        project.metadata_json = {
            "writing_profile": {"market": {"platform_target": "七猫小说"}},
            "editor_rejection_reasons": "文笔还有待提升，代入感较弱，开篇切入点普通。",
            "qimao_opening_contract": {
                "opening_incident": "第一章从被迫选择和直接损失切入。",
                "first_page_conflict": "前600字内被逼交出冥符，否则母亲旧案证据被毁。",
                "protagonist_immediate_goal": "先保住冥符并确认谁在灭口。",
                "visible_loss_if_fail": "失败会失去唯一翻案证据。",
                "protagonist_edge": "主角能从冥符纹路看出隐藏漏洞。",
                "edge_limit": "冥符只能救第一轮，不能直接推翻主谋。",
                "chapter_1_small_turn": "主角当众反制逼迫者。",
                "chapter_2_reveal": "逼迫者背后另有主谋。",
                "chapter_3_payoff": "拿到第一个筹码并打开下一轮钩子。",
                "first_10000_loop": "触发冲突 -> 主角行动 -> 收益/代价 -> 新钩子",
                "forbidden_opening_modes": ["background_exposition", "normal_day", "scenery_first"],
            },
        }

        _, user_prompt = build_scene_draft_prompts(
            project,
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
        )

        assert "【七猫签约门槛】" in user_prompt
        assert "【七猫再生成合同】" in user_prompt
        assert "【opening_quality_contract｜商业签约开篇合同】" in user_prompt
        assert "本章不是自由发挥" in user_prompt
        assert "前100字聚焦主角" in user_prompt
        assert "黄金三章任务" in user_prompt
        assert "第一章从被迫选择和直接损失切入" in user_prompt
        assert "文笔还有待提升" in user_prompt

    def test_story_principle_event_unit_contract_lands_in_scene_prompt_zh(self) -> None:
        chapter = _sample_chapter()
        chapter.metadata_json = {
            "chapter_event_role": "method_search",
            "information_gap_mode": "reader_knows_less",
            "event_cycle_contract": {
                "event_unit_id": "v1-event-2",
                "reader_desire": "读者想看主角如何不牺牲盟友也修复账册。",
                "event_pressure": "长老会限时公开审计。",
                "solution_method": "把私下解释改成公开信任债测试。",
                "handoff_to_next": "测试需要盟友公开担保。",
            },
        }

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            chapter,
            _sample_scene(),
            _sample_style_guide(),
        )

        assert "写作原理执行约束：事件单元合同" in user_prompt
        assert "不是每场/每章都复刻完整六步" in user_prompt
        assert "本章事件角色：method_search" in user_prompt
        assert "信息差模式：reader_knows_less" in user_prompt
        assert "solution_method: 把私下解释改成公开信任债测试。" in user_prompt
        assert "本场景贡献：先证明旧方法为什么失效。" in user_prompt

    def test_populated_blocks_land_in_user_prompt_en(self) -> None:
        reader_block = (
            "[READER CONTRACT] selling points: ghost wealth, supernatural capitalism\n"
            "The first chapter must show that money is no longer currency."
        )
        hype_block = (
            "[CHAPTER HYPE CONSTRAINTS]\n"
            "- Assigned hype type: face_slap (intensity 8.5/10)\n"
            "- Recipe: ghost-talisman face-slap\n"
            "- Hype is NOT the cliffhanger."
        )
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(language="en"),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=reader_block,
            hype_constraints_block=hype_block,
        )
        assert "READER CONTRACT" in user_prompt
        assert "CHAPTER HYPE CONSTRAINTS" in user_prompt
        assert "face_slap" in user_prompt
        assert "Hype is NOT the cliffhanger." in user_prompt

    def test_block_order_reader_contract_precedes_hype(self) -> None:
        """Plan-documented order: reader contract, then hype constraints."""

        reader_block = "【读者契约】CONTRACT_MARKER"
        hype_block = "【本章爽点约束】HYPE_MARKER"
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=reader_block,
            hype_constraints_block=hype_block,
        )
        contract_at = user_prompt.index("CONTRACT_MARKER")
        hype_at = user_prompt.index("HYPE_MARKER")
        assert contract_at < hype_at

    def test_only_hype_block_no_reader_contract_still_renders(self) -> None:
        """Chapter 12 (head=10, tail=5) omits reader contract but keeps hype."""

        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            reader_contract_block=None,
            hype_constraints_block="【本章爽点约束】hype-only",
        )
        assert "【读者契约】" not in user_prompt
        assert "【本章爽点约束】" in user_prompt
        assert "hype-only" in user_prompt

    def test_premium_engine_blocks_land_in_user_prompt_zh(self) -> None:
        ranking_block = "【榜单级能力 Profile】RANKING_MARKER: 固定入口、可解规则、单元案推动主线。"
        progression_block = "【进阶体系约束】PROGRESSION_MARKER: 当前炼气十层, 不得无因突破筑基。"
        decision_block = (
            "【主角决策策略】DECISION_MARKER: 不可为虚荣公开决斗, 优先避险夺取稀缺资源。"
        )
        rule_block = "【规则系统约束】RULE_MARKER: 每条民俗规则必须有可见效果、破局路径和反噬。"
        faction_block = "【阵营生态与反应压力约束】FACTION_MARKER: 势力反应必须差异化。"
        relationship_block = (
            "【关系张力与主角能动性约束】RELATIONSHIP_MARKER: "
            "关系戏必须改变信任/权力/误会/承诺。"
        )
        entry_system_block = "【词条体系约束】ENTRY_SYSTEM_MARKER: 法宝升级必须支付代价。"
        entry_registry_block = "【词条注册表】ENTRY_REGISTRY_MARKER: artifact-core 仍可用。"
        entry_state_block = "【词条状态账本】ENTRY_STATE_MARKER: artifact-core state=owned。"
        _, user_prompt = build_scene_draft_prompts(
            _sample_project(),
            _sample_chapter(),
            _sample_scene(),
            _sample_style_guide(),
            ranking_capability_profile_block=ranking_block,
            progression_context_block=progression_block,
            decision_policy_block=decision_block,
            rule_system_context_block=rule_block,
            faction_ecology_context_block=faction_block,
            relationship_agency_context_block=relationship_block,
            entry_system_context_block=entry_system_block,
            entry_registry_context_block=entry_registry_block,
            entry_state_ledger_block=entry_state_block,
        )
        assert "【榜单级能力 Profile】" in user_prompt
        assert "固定入口、可解规则、单元案推动主线" in user_prompt
        assert "【进阶体系约束】" in user_prompt
        assert "不得无因突破筑基" in user_prompt
        assert "【主角决策策略】" in user_prompt
        assert "不可为虚荣公开决斗" in user_prompt
        assert "【规则系统约束】" in user_prompt
        assert "每条民俗规则必须有可见效果" in user_prompt
        assert "【阵营生态与反应压力约束】" in user_prompt
        assert "势力反应必须差异化" in user_prompt
        assert "【关系张力与主角能动性约束】" in user_prompt
        assert "关系戏必须改变信任/权力/误会/承诺" in user_prompt
        assert "【词条体系约束】" in user_prompt
        assert "【词条注册表】" in user_prompt
        assert "【词条状态账本】" in user_prompt
        assert user_prompt.index("RANKING_MARKER") < user_prompt.index("PROGRESSION_MARKER")
        assert user_prompt.index("PROGRESSION_MARKER") < user_prompt.index("DECISION_MARKER")
        assert user_prompt.index("DECISION_MARKER") < user_prompt.index("RULE_MARKER")
        assert user_prompt.index("RULE_MARKER") < user_prompt.index("FACTION_MARKER")
        assert user_prompt.index("FACTION_MARKER") < user_prompt.index("RELATIONSHIP_MARKER")
        assert user_prompt.index("RELATIONSHIP_MARKER") < user_prompt.index("ENTRY_SYSTEM_MARKER")
        assert user_prompt.index("ENTRY_SYSTEM_MARKER") < user_prompt.index("ENTRY_REGISTRY_MARKER")
        assert user_prompt.index("ENTRY_REGISTRY_MARKER") < user_prompt.index("ENTRY_STATE_MARKER")
