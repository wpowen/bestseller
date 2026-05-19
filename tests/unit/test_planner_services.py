from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.services.distilled_strategy_compiler import (
    DistilledStrategyCard,
    SelectedMechanism,
)
from bestseller.services import planner as planner_services
from bestseller.services.plan_fingerprint import scan_batch_for_duplicates
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class _FakeExecuteResult:
    """Minimal stand-in for SQLAlchemy ``Result`` returning no rows."""

    def all(self) -> list:
        return []

    def scalars(self) -> "_FakeExecuteResult":
        return self

    def first(self):
        return None


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is not None and "id" in table.c and getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, _stmt: object) -> None:
        # Fresh project — no written chapters to guard against.
        return None

    async def execute(self, _stmt: object) -> _FakeExecuteResult:
        # Fresh project — no rows. Used by helpers that query the
        # `chapters` table (e.g. existing-title dedup fetch).
        return _FakeExecuteResult()


def build_settings():
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def test_extract_json_payload_handles_wrapped_json() -> None:
    payload = planner_services._extract_json_payload('```json\n{"title":"长夜巡航"}\n```')
    assert payload["title"] == "长夜巡航"


def test_extract_json_payload_handles_prose_prefix_and_suffix() -> None:
    """MiniMax-M2.7 often prepends/appends explanatory prose around JSON.

    Observed failure mode (2026-04-21, romantasy-1776330993 volume_5_chapter_outline):
    2 retries exhausted because ``rfind("}")`` was fooled by a trailing
    prose example containing a ``}``.  The extractor must find the
    *balanced* closing brace for the first opening brace, not the last
    ``}`` in the entire text.
    """
    raw = (
        "Here is the chapter outline:\n\n"
        '{"chapters": [{"number": 1, "title": "序曲"}]}\n\n'
        "Note: please revise the last scene if needed "
        '(e.g. {"scene": "incomplete example"}).'
    )
    payload = planner_services._extract_json_payload(raw)
    assert payload == {"chapters": [{"number": 1, "title": "序曲"}]}


def test_extract_json_payload_handles_markdown_fence_without_lang_tag() -> None:
    """Accept bare ``` fences (no ``json`` tag) that MiniMax sometimes emits."""
    raw = '```\n{"title": "长夜巡航", "volume": 5}\n```'
    payload = planner_services._extract_json_payload(raw)
    assert payload["volume"] == 5


def test_extract_json_payload_handles_multiple_fenced_blocks() -> None:
    """When the LLM emits multiple fenced blocks, pick the first balanced one."""
    raw = (
        "First attempt:\n"
        '```json\n{"chapters": [{"number": 1}]}\n```\n\n'
        "Alternative:\n"
        '```json\n{"chapters": [{"number": 2}]}\n```\n'
    )
    payload = planner_services._extract_json_payload(raw)
    # Balanced extraction picks up the first JSON object.
    assert payload == {"chapters": [{"number": 1}]}


def test_extract_json_payload_handles_nested_braces_in_strings() -> None:
    """Balanced extractor must respect string literals containing braces."""
    raw = '```json\n{"outline": "vol 5 chapter 1: the trap uses a glyph like {X}","count": 3}\n```'
    payload = planner_services._extract_json_payload(raw)
    assert payload["count"] == 3
    assert "{X}" in payload["outline"]


def test_extract_json_payload_handles_leading_garbage_with_balanced_body() -> None:
    """Even without markdown fences, prose-before-JSON should be tolerated."""
    raw = (
        "我已经根据用户指令生成第5卷大纲如下（18 章）：\n"
        '{"chapters": [{"number": 211, "title": "结界裂痕"}, '
        '{"number": 212, "title": "旧伤"}]}'
    )
    payload = planner_services._extract_json_payload(raw)
    assert payload["chapters"][0]["number"] == 211


def test_extract_json_payload_raises_only_when_no_balanced_object_exists() -> None:
    """True parse-failure still raises — extractor doesn't silently pass bad input."""
    with pytest.raises(ValueError):
        planner_services._extract_json_payload("this is just prose, no json at all")


def test_extract_json_payload_repairs_minimax_duplicate_opener() -> None:
    """Root-cause regression: MiniMax-M2.7 occasionally emits doubled
    ``{`` before an object inside an array (observed 2026-04-21 on
    superhero-fiction-1776147970 volume_8_chapter_outline). Standard
    JSON parsers reject this; json-repair library handles it. The
    extractor must integrate that fallback so the heal pipeline no
    longer dies on structural MiniMax glitches.
    """
    raw = """```json
{
  "batch_name": "Volume 8",
  "volume": 8,
  "chapters": [
    {
      "chapter_number": 1,
      "title": "Dual Presence",
      "scenes": [
        {
          {
            "scene_number": 1,
            "story_task": "open scene"
          }
        }
      ]
    }
  ]
}
```"""
    payload = planner_services._extract_json_payload(raw)
    assert payload["volume"] == 8
    assert len(payload["chapters"]) == 1
    # The repaired payload preserves the scene content even though the
    # original had a malformed extra opener.
    scenes = payload["chapters"][0]["scenes"]
    assert len(scenes) >= 1

    # Walk down to find the actual scene_number regardless of whether
    # json-repair hoisted the inner object or preserved the outer wrapper.
    def _find_scene_number(node: object) -> int | None:
        if isinstance(node, dict):
            if "scene_number" in node:
                return node["scene_number"]
            for value in node.values():
                found = _find_scene_number(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _find_scene_number(item)
                if found is not None:
                    return found
        return None

    assert _find_scene_number(scenes) == 1


def test_extract_json_payload_repairs_trailing_commas() -> None:
    """Common LLM glitch: trailing comma in arrays/objects (json-repair handles)."""
    raw = '{"chapters": [{"number": 1,}, {"number": 2,}],}'
    payload = planner_services._extract_json_payload(raw)
    assert len(payload["chapters"]) == 2
    assert payload["chapters"][0]["number"] == 1


def test_planner_max_attempts_is_at_least_four() -> None:
    """Regression guard: retry budget must be >=4.

    Rationale — 2026-04-21 production failure (romantasy-1776330993):
    with only 2 attempts, a single pair of malformed MiniMax responses
    kills the entire heal job.  A 4-attempt budget lets transient
    formatting glitches self-heal instead of wedging the project.
    """
    import inspect

    src = inspect.getsource(
        planner_services._generate_structured_artifact  # type: ignore[attr-defined]
    )
    # Ensure the literal default is at least 4.
    import re

    matches = re.findall(r"_max_attempts\s*=\s*(\d+)", src)
    assert matches, "_max_attempts default not found in _generate_structured_artifact"
    assert all(int(m) >= 4 for m in matches), f"planner _max_attempts must be >=4, found {matches}"


def test_fallback_generators_create_complete_chain() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    assert book_spec["title"] == "长夜巡航"
    assert world_spec["rules"][0]["rule_id"] == "R001"
    assert cast_spec["protagonist"]["relationships"][0]["character"]
    assert len(volume_plan) >= 1
    assert len(outline_batch["chapters"]) == project.target_chapters
    assert len(outline_batch["chapters"][0]["scenes"]) == 3


def test_build_qimao_opening_contract_uses_plan_context() -> None:
    project = build_project()
    project.metadata_json = {
        "writing_profile": {
            "market": {
                "platform_target": "七猫小说",
                "opening_contract": "第一章从被迫选择和直接损失切入。",
            }
        }
    }
    premise = "被退婚的女主发现家族账本藏着一条会害死母亲的旧案。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    contract = planner_services.build_qimao_opening_contract(
        project,
        premise=premise,
        book_spec=book_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )

    assert contract["platform_target"] == "七猫小说"
    assert "直接损失" in contract["opening_incident"]
    assert "前600字" in contract["first_page_conflict"]
    assert contract["protagonist_immediate_goal"]
    assert "background_exposition" in contract["forbidden_opening_modes"]
    assert any("代入感较弱" in item for item in contract["rejection_causes_addressed"])


def test_persist_qimao_opening_contract_updates_project_metadata() -> None:
    project = build_project()
    project.metadata_json = {"platform_target": "七猫小说"}
    premise = "被退婚的女主发现家族账本藏着一条会害死母亲的旧案。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    contract = planner_services.persist_qimao_opening_contract(
        project,
        premise=premise,
        book_spec=book_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )

    assert contract is not None
    assert project.metadata_json["qimao_opening_contract"] == contract
    assert project.metadata_json["qimao_opening_contract_status"] == "planned"


def test_persist_qimao_opening_contract_applies_to_general_projects() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    contract = planner_services.persist_qimao_opening_contract(
        project,
        premise=premise,
        book_spec=book_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )

    assert contract is not None
    assert project.metadata_json["opening_quality_contract"] == contract
    assert project.metadata_json["opening_quality_contract_status"] == "planned"
    assert project.metadata_json["qimao_opening_contract"] == contract


def test_resolve_fallback_volume_title_cycles_phase_pool() -> None:
    first = planner_services._resolve_fallback_volume_title("power_system_test", 0, 3, is_en=False)
    second = planner_services._resolve_fallback_volume_title("power_system_test", 1, 6, is_en=False)
    assert first and second and first != second
    assert "第" not in first

    fallback = planner_services._resolve_fallback_volume_title("unknown_phase", 0, 5, is_en=False)
    assert fallback == "第5卷"


def test_fallback_volume_plan_produces_distinct_titles_without_milestones() -> None:
    project = build_project()
    project.target_chapters = 1200
    project.target_word_count = 3_600_000
    project.genre = "action-progression"

    book_spec = planner_services._fallback_book_spec(project, "主角逆天改命。")
    world_spec = planner_services._fallback_world_spec(project, "主角逆天改命。", book_spec)
    cast_spec = planner_services._fallback_cast_spec(
        project, "主角逆天改命。", book_spec, world_spec
    )

    volume_plan = planner_services._fallback_volume_plan(
        project, book_spec, cast_spec, world_spec, category_key="action-progression"
    )

    titles = [entry["volume_title"] for entry in volume_plan]
    assert len(titles) > 5
    assert all(title for title in titles)
    # No generic "第N卷" placeholder should remain when phase pools exist.
    assert not any(title == f"第{idx + 1}卷" for idx, title in enumerate(titles))
    # All titles should be unique across the plan.
    assert len(titles) == len(set(titles))


def test_fallback_cast_spec_uses_neutral_role_labels_when_names_are_missing() -> None:
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    project = build_project()
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    assert cast_spec["protagonist"]["name"] == "林逸"
    assert cast_spec["antagonist"]["name"] == "顾铭"
    assert cast_spec["supporting_cast"][0]["name"] == "沈远"


def test_story_package_seed_informs_fallback_specs(tmp_path: Path) -> None:
    package_path = tmp_path / "story_package.json"
    package_path.write_text(
        json.dumps(
            {
                "book": {
                    "synopsis": "末日前三天，灰楼开门。",
                    "tags": ["末日生存"],
                    "interaction_tags": ["势力扩张"],
                    "characters": [
                        {"name": "沈崇", "role": "反派", "title": "灰楼执钥人"},
                        {"name": "唐海", "role": "盟友", "title": "黑市搬运头子"},
                        {"name": "韩策", "role": "宿敌", "title": "安全区监察官"},
                    ],
                },
                "reader_desire_map": {
                    "core_fantasy": "主角靠规则优势一路滚雪球。",
                    "reward_promises": ["抢先囤货", "建立据点"],
                    "control_promises": ["掌控通路"],
                    "suspense_questions": ["谁在操纵灰楼"],
                },
                "story_bible": {
                    "premise": "末日前三天，灰楼开门。",
                    "side_threads": ["家族裂痕", "安全区权力斗争"],
                    "mainline_goal": "在秩序崩塌前抢到第一批核心资源。",
                },
                "route_graph": {
                    "mainline": "囤货 -> 建据点 -> 扩势力",
                    "hidden_routes": [{"reveal": "地下仓链并未断绝"}],
                    "milestones": [{"title": "灰楼开门"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    project = build_project()
    project.metadata_json = {"story_package_path": str(package_path)}
    premise = "一个普通人得到灰楼交易资格，必须在秩序崩塌前囤起第一座安全屋。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    assert book_spec["logline"] == "末日前三天，灰楼开门。"
    assert book_spec["series_engine"]["reader_promise"] == "主角靠规则优势一路滚雪球。"
    assert book_spec["series_engine"]["mainline_milestones"][0] == "灰楼开门"
    assert cast_spec["antagonist"]["name"] == "沈崇"
    assert cast_spec["supporting_cast"][0]["name"] == "唐海"
    assert volume_plan[0]["volume_title"] == "灰楼开门"
    assert any("地下仓链并未断绝" in item for item in volume_plan[0]["key_reveals"])


def test_distilled_design_reference_blocks_enter_planner_prompts() -> None:
    project = build_project()
    project.metadata_json = {
        "distilled_design_reference_blocks": {
            "architecture": "ARCH_DISTILLED_REFERENCE",
            "world": "WORLD_DISTILLED_REFERENCE",
            "cast": "CAST_DISTILLED_REFERENCE",
            "story_design": "KERNEL_DISTILLED_REFERENCE",
            "volume_plan": "VOLUME_DISTILLED_REFERENCE",
            "chapter_outline": "OUTLINE_DISTILLED_REFERENCE",
        },
        "distilled_strategy_blocks": {
            "architecture": "ARCH_STRATEGY_CARD",
            "world": "WORLD_STRATEGY_CARD",
            "cast": "CAST_STRATEGY_CARD",
            "story_design": "KERNEL_STRATEGY_CARD",
            "volume_plan": "VOLUME_STRATEGY_CARD",
            "chapter_outline": "OUTLINE_STRATEGY_CARD",
        },
    }
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    _, book_prompt = planner_services._book_spec_prompts(project, premise, book_spec)
    _, world_prompt = planner_services._world_spec_prompts(project, premise, book_spec)
    _, cast_prompt = planner_services._cast_spec_prompts(project, book_spec, world_spec)
    _, kernel_prompt = planner_services._story_design_kernel_prompts(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
        planner_services._fallback_story_design_kernel(
            project,
            premise,
            book_spec,
            world_spec,
            cast_spec,
        ),
    )
    _, volume_prompt = planner_services._volume_plan_prompts(
        project,
        book_spec,
        world_spec,
        cast_spec,
    )
    _, outline_prompt = planner_services._volume_outline_prompts(
        project,
        book_spec,
        cast_spec,
        volume_plan,
        volume_plan[0],
    )

    assert "ARCH_DISTILLED_REFERENCE" in book_prompt
    assert "ARCH_STRATEGY_CARD" in book_prompt
    assert "WORLD_DISTILLED_REFERENCE" in world_prompt
    assert "WORLD_STRATEGY_CARD" in world_prompt
    assert "CAST_DISTILLED_REFERENCE" in cast_prompt
    assert "CAST_STRATEGY_CARD" in cast_prompt
    assert "KERNEL_DISTILLED_REFERENCE" in kernel_prompt
    assert "KERNEL_STRATEGY_CARD" in kernel_prompt
    assert "VOLUME_DISTILLED_REFERENCE" in volume_prompt
    assert "VOLUME_STRATEGY_CARD" in volume_prompt
    assert "OUTLINE_DISTILLED_REFERENCE" in outline_prompt
    assert "OUTLINE_STRATEGY_CARD" in outline_prompt


def test_story_design_kernel_fallback_consumes_distilled_world_bindings() -> None:
    project = build_project()
    project.metadata_json = {
        "distilled_strategy_card": {
            "aggregate_key": "test-aggregate",
            "worldview_bindings": {
                "distilled_mechanism_bindings": [
                    {
                        "aggregate_key": "test-aggregate",
                        "mechanism_id": "dual-system-fusion-ladder",
                        "design_role": "world",
                        "source_confidence": 0.91,
                        "required_project_binding": "把双体系冲突改写成本书的航线规则仲裁。",
                        "state_variables": ["cross_system_understanding"],
                        "required_cost": "每次仲裁都会暴露主角的旧航线知识。",
                    }
                ],
                "state_variables": [
                    {
                        "key": "cross_system_understanding",
                        "variable_type": "knowledge",
                        "current_value": "主角只知道旧帝国航线规则。",
                        "desired_direction": "逐步理解边境新秩序。",
                        "change_triggers": ["破解航线记录", "公开解释规则冲突"],
                        "failure_mode": "世界观退化为背景说明。",
                        "source_mechanism_ids": ["dual-system-fusion-ladder"],
                    }
                ],
                "asset_ledger": [
                    {
                        "key": "hidden_route_archive",
                        "asset_type": "information",
                        "value": "证明帝国篡改边境航线。",
                        "cost": "使用档案会留下检索记录。",
                        "exposure_risk": "边境审计官会追踪异常访问。",
                        "attention_sources": ["帝国审计庭"],
                    }
                ],
                "authority_claims": [
                    {
                        "claimant": "帝国审计庭",
                        "target": "边境航线解释权",
                        "claim_basis": "帝国法令",
                        "legitimacy": "公开合法但隐瞒篡改。",
                        "conflict_with": ["边境导航员"],
                        "escalation_path": "从记录核查升级到航线封锁。",
                    }
                ],
                "scene_templates": [
                    {
                        "key": "route-audit-hearing",
                        "template_name": "航线审计听证",
                        "use_case": "公开展示规则冲突和权力压力。",
                        "required_change": ["cross_system_understanding"],
                    }
                ],
                "anti_copy_boundaries": ["不能照搬双修体系或宗门长老会。"],
            },
        }
    }
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    story_design = planner_services._fallback_story_design_kernel(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
    )
    _, kernel_prompt = planner_services._story_design_kernel_prompts(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
        story_design,
    )

    worldview = story_design["worldview_kernel"]
    assert worldview["state_variables"][0]["key"] == "cross_system_understanding"
    assert worldview["asset_ledger"][0]["key"] == "hidden_route_archive"
    assert worldview["authority_claims"][0]["claimant"] == "帝国审计庭"
    assert worldview["scene_templates"][0]["key"] == "route-audit-hearing"
    assert worldview["anti_copy_boundaries"] == ["不能照搬双修体系或宗门长老会。"]
    assert "state_variables" in kernel_prompt
    assert "anti_copy_boundaries" in kernel_prompt


def test_fallback_volume_plan_includes_worldview_progression_fields() -> None:
    project = build_project()
    project.metadata_json = {
        "distilled_strategy_card": {
            "aggregate_key": "test-aggregate",
            "worldview_bindings": {
                "state_variables": [
                    {
                        "key": "cross_system_understanding",
                        "variable_type": "knowledge",
                        "current_value": "只知道旧规则。",
                        "desired_direction": "逐步理解新秩序。",
                        "change_triggers": ["破解航线记录"],
                        "failure_mode": "世界观退化为背景说明。",
                    }
                ],
                "asset_ledger": [
                    {
                        "key": "hidden_route_archive",
                        "asset_type": "information",
                        "value": "证明航线被篡改。",
                        "cost": "使用档案会留下检索记录。",
                        "exposure_risk": "审计庭会追踪异常访问。",
                    }
                ],
                "authority_claims": [
                    {
                        "claimant": "帝国审计庭",
                        "target": "边境航线解释权",
                        "claim_basis": "帝国审计法",
                        "legitimacy": "公开合法但掩盖篡改。",
                        "escalation_path": "从核查升级到封港。",
                    }
                ],
                "scene_templates": [
                    {
                        "key": "route-audit-hearing",
                        "template_name": "航线审计听证",
                        "use_case": "公开展示规则冲突。",
                        "required_change": ["cross_system_understanding"],
                    }
                ],
            },
        }
    }
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    story_design = planner_services._fallback_story_design_kernel(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
    )
    project.metadata_json = {
        **(project.metadata_json or {}),
        "story_design_kernel": story_design,
    }

    volume_plan = planner_services._fallback_volume_plan(
        project,
        book_spec,
        cast_spec,
        world_spec,
    )

    first_volume = volume_plan[0]
    assert first_volume["world_state_targets"] == ["cross_system_understanding +1"]
    assert first_volume["active_authority_claims"] == ["边境航线解释权"]
    assert "map_function" in first_volume
    assert first_volume["world_asset_refs"] == ["hidden_route_archive"]
    assert "asset_risk_escalation" in first_volume
    assert first_volume["reveal_budget"] == 1


def test_emotion_driven_kernel_fallback_validates_and_enters_planner_prompts() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    story_design = planner_services._fallback_story_design_kernel(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
    )

    emotion_kernel = planner_services._fallback_emotion_driven_kernel(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
        story_design_kernel=story_design,
    )

    planner_services._validate_emotion_driven_kernel_payload(emotion_kernel)
    project.metadata_json = {
        "story_design_kernel": story_design,
        "emotion_driven_kernel": emotion_kernel,
    }

    _, volume_prompt = planner_services._volume_plan_prompts(
        project,
        book_spec,
        world_spec,
        cast_spec,
    )
    _, outline_prompt = planner_services._volume_outline_prompts(
        project,
        book_spec,
        cast_spec,
        volume_plan,
        volume_plan[0],
    )

    assert "emotion_driven_core" in volume_prompt
    assert "读者情绪合同" in volume_prompt
    assert "emotion_driven_core" in outline_prompt
    assert "读者情绪合同" in outline_prompt


def test_stash_distilled_design_reference_blocks_populates_project_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()

    from bestseller.services import distilled_design_reference

    def fake_render_blocks(**kwargs: object) -> dict[str, str]:
        assert kwargs["genre"] == project.genre
        return {
            "architecture": "ARCH_BLOCK",
            "world": "WORLD_BLOCK",
        }

    monkeypatch.setattr(
        distilled_design_reference,
        "render_all_distilled_design_reference_blocks",
        fake_render_blocks,
    )

    planner_services._stash_distilled_design_reference_blocks(
        project,
        category_key="science-fantasy",
        settings=settings,
    )

    assert project.metadata_json["distilled_design_reference_blocks"]["world"] == "WORLD_BLOCK"
    assert project.metadata_json["distilled_design_reference_block"] == "ARCH_BLOCK"


def test_stash_distilled_strategy_card_populates_project_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        "story_facets": {"unique_hook": "失效航图修复异界法则"},
        "premise": "导航员坠入异界。",
    }
    settings = build_settings()

    from bestseller.services import distilled_strategy_compiler

    def fake_compile(**kwargs: object) -> DistilledStrategyCard:
        assert kwargs["genre"] == project.genre
        assert kwargs["project_context"]["unique_hook"] == "失效航图修复异界法则"
        return DistilledStrategyCard(
            aggregate_key="otherworld-cross-system",
            maturity_score=0.42,
            maturity_status="review",
            source_count=1,
            selected_mechanisms=[
                SelectedMechanism(
                    mechanism_id="cross-system-rule-arbitrage",
                    source_confidence=0.86,
                    design_role="series_engine",
                    adaptation_instruction="转化为本项目因果链。",
                    required_project_specific_binding="绑定到失效航图。",
                    failure_mode="未绑定项目元素。",
                )
            ],
            required_state_variables=["cross_system_understanding"],
            required_change_vectors=["exploit_rule_gap"],
            anti_copy_boundaries=["exact-opening-chain"],
            transformation_requirements=["cross-system-rule-arbitrage: 绑定到失效航图。"],
            plan_consumption_checks=["Plan should track state variable."],
        )

    monkeypatch.setattr(
        distilled_strategy_compiler,
        "compile_distilled_strategy_card",
        fake_compile,
    )

    planner_services._stash_distilled_strategy_card(
        project,
        category_key="otherworld-cross-system",
        settings=settings,
    )

    assert project.metadata_json["distilled_strategy_card"]["aggregate_key"] == (
        "otherworld-cross-system"
    )
    assert project.metadata_json["character_strategy"]["source"] == (
        "distillation_character_intelligence"
    )
    assert "agency" in project.metadata_json["character_strategy"]["required_axes"]
    assert "architecture" in project.metadata_json["distilled_strategy_blocks"]
    assert "cross-system-rule-arbitrage" in project.metadata_json["distilled_strategy_block"]


def test_fallback_world_spec_uses_neutral_rule_scaffold() -> None:
    project = build_project()
    project.genre = "仙侠"
    premise = "一个被逐出宗门的弟子，在秘境中发现自己的谱牒被人篡改。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)

    rule_names = {rule["name"] for rule in world_spec["rules"]}
    assert "记录优先规则" not in rule_names
    assert "宗门谱牒规则" not in rule_names
    assert rule_names == {"核心秩序规则", "门槛通行规则", "禁区隔绝规则"}


def test_merge_planning_payload_preserves_fallback_nested_fields() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    fallback_book_spec = planner_services._fallback_book_spec(project, premise)

    merged = planner_services._merge_planning_payload(
        fallback_book_spec,
        {
            "title": "长夜巡航",
            "protagonist": {
                "name": "沈砚",
            },
        },
    )

    assert merged["title"] == "长夜巡航"
    assert merged["protagonist"]["name"] == "沈砚"
    assert (
        merged["protagonist"]["external_goal"] == fallback_book_spec["protagonist"]["external_goal"]
    )
    assert merged["stakes"]["personal"] == fallback_book_spec["stakes"]["personal"]


def test_fallback_cast_spec_tolerates_partial_or_malformed_inputs() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    cast_spec = planner_services._fallback_cast_spec(
        project,
        premise,
        {
            "protagonist": {
                "name": "沈砚",
            }
        },
        {
            "locations": {"broken": True},
            "factions": [],
            "power_system": {},
        },
    )

    assert cast_spec["protagonist"]["name"] == "沈砚"
    assert cast_spec["protagonist"]["goal"]
    assert cast_spec["protagonist"]["background"]
    assert cast_spec["antagonist"]["background"]


def test_fallback_chapter_outline_batch_tolerates_non_mapping_volume_items() -> None:
    project = build_project()

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        {},
        {
            "protagonist": {"name": "沈砚"},
            "antagonist": {"name": "祁镇"},
        },
        ["broken-volume-item"],  # type: ignore[list-item]
    )

    assert outline_batch["chapters"]
    assert outline_batch["chapters"][0]["volume_number"] == 1
    assert outline_batch["chapters"][0]["scenes"]


def test_fallback_chapter_outline_titles_do_not_cycle() -> None:
    """No chapter title may repeat across a 24-chapter book.

    Before the fix, ``_fallback_chapter_outline_batch`` indexed an 8-element
    hard-coded list by ``chapter_number % 8``, so chapters 2/10/18, 3/11/19,
    4/12/20 etc. got literally identical subtitles (封锁, 碰撞, 反咬, …).
    The fix replaces the cycle with a deterministic subtitle derived from
    the volume goal plus the chapter number, guaranteeing uniqueness.
    """
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    titles = [ch["title"] for ch in outline_batch["chapters"]]
    # Chapter 1 might be a genre-specific opener; chapters 2+ must be unique.
    non_empty = [t for t in titles[1:] if t]
    assert len(non_empty) == len(set(non_empty)), (
        f"Chapter titles must not repeat in a 24-chapter book; got {titles}"
    )


def test_fallback_chapter_outline_titles_are_concise_and_not_volume_goal_clips() -> None:
    project = build_project()
    project.target_chapters = 12
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = [
        {
            "volume_number": 1,
            "chapter_count_target": 12,
            "volume_goal": "沈渡需要在本卷内拿到一组足以改变局势的关键证据或盟友。",
        }
    ]

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    titles = [ch["title"] for ch in outline_batch["chapters"] if ch.get("title")]

    assert titles
    assert all("需要在本卷内" not in title for title in titles)
    assert all("·" not in title for title in titles)
    assert all(len(title) <= 8 for title in titles)
    banned_functional_tails = {
        "初现",
        "入局",
        "投石",
        "试探",
        "铺火",
        "露锋",
        "破冰",
        "起手",
        "掀幕",
        "落子",
        "追索",
        "摸底",
        "拆解",
        "寻隙",
        "探针",
        "回查",
        "溯源",
        "揭层",
        "织网",
        "破壁",
    }
    assert all(
        not any(title.endswith(tail) for tail in banned_functional_tails) for title in titles
    )
    hooks = [
        ch["hook_description"] for ch in outline_batch["chapters"] if ch.get("hook_description")
    ]
    assert all("尾钩" not in hook for hook in hooks)
    assert all("出现新的证据、时限或代价" not in hook for hook in hooks)
    assert all("围绕" not in hook for hook in hooks)


def test_chapter_outline_prefers_story_title_alias_over_functional_fallback() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "opening",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "浮标初现",
                    "chapter_title": "镜泣",
                    "goal": "苏砚确认铜镜异变与母亲旧案有关。",
                    "main_conflict": "苏砚必须在宿老封宅前读取铜镜残痕。",
                    "hook_description": "铜镜渗出血珠，映出大火夜的人影。",
                    "scenes": [],
                }
            ],
        }
    )

    assert batch.chapters[0].title == "镜泣"


def test_volume_outline_length_mismatch_fails_closed_instead_of_padding() -> None:
    with pytest.raises(planner_services.PlannerFallbackError, match="Refusing to pad or trim"):
        planner_services._require_complete_volume_outline(
            logical_name="volume_1_chapter_outline",
            volume_number=1,
            expected_count=3,
            chapters=[{"chapter_number": 1}, {"chapter_number": 2}],
        )


def test_generated_outline_uses_title_alias_without_fallback_synthesis() -> None:
    chapters = [{"chapter_number": 1, "chapter_title": "镜泣"}]

    planner_services._normalize_generated_outline_titles_or_fail(
        chapters,
        logical_name="volume_1_chapter_outline",
    )

    assert chapters[0]["title"] == "镜泣"


def test_generated_outline_missing_title_fails_without_fallback_synthesis() -> None:
    with pytest.raises(
        planner_services.PlannerFallbackError, match="omitted concrete chapter titles"
    ):
        planner_services._normalize_generated_outline_titles_or_fail(
            [{"chapter_number": 7, "goal": "苏砚追到镜铺后门。"}],
            logical_name="volume_1_chapter_outline",
        )


def test_generated_volume_outline_repairs_scene_contract_fields_before_validation() -> None:
    project = build_project()
    project.slug = "exorcist-detective-1778428166"
    cast_spec = {
        "protagonist": {
            "name": "沈青崖",
            "role": "protagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        "antagonist": {
            "name": "秦无咎",
            "role": "antagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        "supporting_cast": [
            {
                "name": "阿洛",
                "role": "supporting",
                "goal": "把走私账册送出港口",
                "value_to_story": "提供港口黑市线索和临场行动压力",
            }
        ],
    }
    payload = {
        "batch_name": "volume-1-outline",
        "chapters": [
            {
                "title": "血雨前夜",
                "goal": "沈青崖追查李宅血雨，必须在巡捕封锁前拿到第一条阴阳线索。",
                "main_conflict": "李宅血雨把案发现场变成阴阳交界，沈青崖必须抢在巡捕房误判前锁定邪术痕迹。",
                "hook_description": "沈青崖在封门前听见井底传来秦无咎的冷笑。",
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["沈青崖", "李夫人", "仵作"],
                        "purpose": {},
                    },
                    {
                        "scene_number": 2,
                        "time_label": "章节开场",
                        "participants": ["巡捕房巡捕"],
                        "purpose": {"story": "秦无咎把账册藏进义庄，逼沈青崖立刻改道。"},
                    },
                    {
                        "scene_number": 3,
                        "time_label": "李宅封门前",
                        "participants": ["沈青崖"],
                        "purpose": {
                            "story": "本章功能是完善秦无咎的反派线，并扩大后续悬念。",
                        },
                    },
                ],
            }
        ],
    }

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        payload,
        project=project,
        logical_name="volume_1_chapter_outline",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=cast_spec,
    )

    first_scene = repaired["chapters"][0]["scenes"][0]
    second_scene = repaired["chapters"][0]["scenes"][1]
    assert first_scene["participants"] == ["沈青崖"]
    assert first_scene["time_label"].startswith("第1章")
    assert first_scene["purpose"]["story"].startswith("第1章场景1让沈青崖")
    assert second_scene["participants"] == ["沈青崖", "秦无咎"]
    assert second_scene["time_label"].startswith("第1章")
    assert "本章功能" not in repaired["chapters"][0]["scenes"][2]["purpose"]["story"]


def test_generated_volume_outline_accepts_raw_chapter_list_from_llm() -> None:
    project = build_project()
    cast_spec = {
        "protagonist": {
            "name": "沈青崖",
            "role": "protagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        "supporting_cast": [
            {
                "name": "阿洛",
                "role": "supporting",
                "goal": "把走私账册送出港口",
                "value_to_story": "提供港口黑市线索和临场行动压力",
            }
        ],
    }
    payload = [
        {
            "title": "井底回声",
            "goal": "沈青崖追查井底异响，必须在封门前找到血雨源头。",
            "main_conflict": "巡捕房误封现场，沈青崖必须避开阻拦读取井底痕迹。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    "participants": ["沈青崖"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，发现血雨源头并付出暴露行踪的代价。",
                        "emotion": "压力上升。",
                    },
                },
            ],
        }
    ]

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        payload,
        project=project,
        logical_name="volume_1_chapter_outline",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=cast_spec,
    )

    assert repaired["batch_name"] == "volume-1-outline"
    assert repaired["chapters"][0]["chapter_number"] == 1


@pytest.mark.asyncio
async def test_volume_outline_repair_loop_regenerates_with_contract_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    volume_entry = volume_plan[0]
    fallback_payload = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        [volume_entry],
    )
    for chapter in fallback_payload["chapters"]:
        chapter["main_conflict"] = "沈砚必须在封港命令生效前拿到航线记录，并避开港务官的封锁。"

    prompts: list[str] = []

    async def fake_generate_structured_artifact(
        session: object,
        settings: object,
        **kwargs: object,
    ):
        prompts.append(str(kwargs["user_prompt"]))
        payload = json.loads(json.dumps(kwargs["fallback_payload"], ensure_ascii=False))
        if len(prompts) == 1:
            payload["chapters"][0]["hook_description"] = "具体事件是「尾钩」。"
        return payload, uuid4()

    monkeypatch.setattr(
        planner_services,
        "_generate_structured_artifact",
        fake_generate_structured_artifact,
    )
    settings = build_settings()
    settings.pipeline.chapter_outline_repair_attempts = 2

    payload, llm_run_id, history = await planner_services._generate_volume_outline_with_repair_loop(
        FakeSession(),
        settings,
        project=project,
        workflow_run_id=uuid4(),
        logical_name="volume_1_chapter_outline",
        book_spec=book_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
        volume_entry=volume_entry,
        fallback_payload=fallback_payload,
        volume_number=1,
        expected_count=len(fallback_payload["chapters"]),
        chapter_number_offset=1,
        revealed_ledger_block=None,
        base_constraints=[],
    )

    assert llm_run_id is not None
    assert payload["chapters"][0]["hook_description"] != "具体事件是「尾钩」。"
    assert len(prompts) == 2
    assert "PLAN_CHAPTER_HOOK_GENERIC" in prompts[1]
    assert history[0]["status"] == "failed"
    assert history[-1]["status"] == "passed"


def test_fallback_chapter_outline_avoids_critical_plan_fingerprints_with_long_hook_strategy() -> (
    None
):
    project = build_project()
    project.slug = "eastern-aesthetic-fantasy-1778332094"
    project.title = "器有魂"
    project.genre = "东方志怪"
    project.target_word_count = 220000
    project.target_chapters = 100
    project.metadata_json = {
        "writing_profile": {
            "market": {
                "chapter_hook_strategy": (
                    '【递进式悬念梯度】章末钩子按"谜题深化→威胁升级→利益/情感诱因"三层循环排布：'
                    "短回报钩子（次章解决）用于填充章节节奏；中回报钩子（3-5章）用于卷内悬念；"
                    '长回报钩子（10+章）用于主线伏笔。每五章设置一次"认知重塑"级钩子。'
                )
            }
        }
    }
    cast_spec = {
        "protagonist": {"name": "苏砚"},
        "antagonist": {"name": "厉青冥"},
        "supporting_cast": [],
        "antagonist_forces": [
            {
                "name": "铜镜器灵·残相",
                "character_ref": "铜镜器灵·残相",
                "role": "antagonist",
                "active_volumes": [1],
            }
        ],
    }
    volume_plan = [
        {
            "volume_number": 1,
            "chapter_count_target": 100,
            "volume_goal": "苏砚调查古宅镜泣事件，追查铭纹鼎与母亲旧案。",
            "conflict_phase": "survival",
            "primary_force_name": "铜镜器灵·残相",
        }
    ]

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        {"title": "器有魂"},
        cast_spec,
        volume_plan,
    )
    batch = ChapterOutlineBatchInput.model_validate(outline_batch)
    report = scan_batch_for_duplicates(list(batch.chapters), [])

    assert not report.critical_findings


def test_merge_volume_cast_expansion_keeps_existing_role_when_role_change_is_descriptive() -> None:
    cast_spec = {
        "protagonist": {"name": "Kade Mercer", "role": "protagonist"},
        "supporting_cast": [
            {
                "name": "Zoe Chen",
                "role": "ally",
                "metadata": {"existing": True},
            }
        ],
    }
    raw_role = (
        "From information gatherer to active participant—she cannot remain a detached "
        "investigator when Maya specifically targets her through convergence"
    )
    cast_expansion = {
        "character_evolutions": [
            {
                "name": "Zoe Chen",
                "changes": {
                    "role": raw_role,
                    "alliance_status": "Moves from tentative trust toward commitment.",
                },
            }
        ]
    }

    merged = planner_services._merge_volume_cast_expansion_into_cast_spec(
        cast_spec,
        cast_expansion,
    )

    zoe = merged["supporting_cast"][0]
    assert zoe["role"] == "ally"
    assert zoe["alliance_status"] == "Moves from tentative trust toward commitment."
    assert zoe["metadata"]["existing"] is True
    assert zoe["metadata"]["role_evolution"] == raw_role
    assert (
        zoe["metadata"]["role_evolution_normalized_label"]
        == "From information gatherer to active participant"
    )


def test_merge_volume_cast_expansion_normalizes_descriptive_role_for_new_character() -> None:
    cast_spec = {
        "protagonist": {"name": "Kade Mercer", "role": "protagonist"},
        "supporting_cast": [],
    }
    raw_role = (
        "From hidden observer to field coordinator—she can no longer stay outside "
        "the conflict once the breach starts choosing targets"
    )
    cast_expansion = {
        "new_characters": [
            {
                "name": "Denise Marlow",
                "role": raw_role,
                "goal": "Keep the remaining descendants alive.",
            }
        ]
    }

    merged = planner_services._merge_volume_cast_expansion_into_cast_spec(
        cast_spec,
        cast_expansion,
    )

    denise = merged["supporting_cast"][0]
    assert denise["role"] == "supporting"
    assert denise["goal"] == "Keep the remaining descendants alive."
    assert denise["metadata"]["role_evolution"] == raw_role
    assert (
        denise["metadata"]["role_evolution_normalized_label"]
        == "From hidden observer to field coordinator"
    )


def test_merge_volume_cast_expansion_normalizes_fuzzy_age_for_new_character() -> None:
    cast_spec = {
        "protagonist": {"name": "Kade Mercer", "role": "protagonist"},
        "supporting_cast": [],
    }
    cast_expansion = {
        "new_characters": [
            {
                "name": "Iris Vale",
                "role": "ally",
                "age": "late 40s",
            }
        ]
    }

    merged = planner_services._merge_volume_cast_expansion_into_cast_spec(
        cast_spec,
        cast_expansion,
    )

    iris = merged["supporting_cast"][0]
    assert iris["age"] == 48
    assert iris["metadata"]["age_note"] == "late 40s"
    assert iris["metadata"]["age_normalized"] == 48


def test_merge_volume_cast_expansion_moves_list_changes_into_metadata_notes() -> None:
    cast_spec = {
        "protagonist": {"name": "Kade Mercer", "role": "protagonist"},
        "supporting_cast": [{"name": "Zoe Chen", "role": "ally"}],
    }
    cast_expansion = {
        "character_evolutions": [
            {
                "name": "Zoe Chen",
                "changes": [
                    "Stops operating as a detached observer.",
                    "Commits to the breach team in the field.",
                ],
            }
        ]
    }

    merged = planner_services._merge_volume_cast_expansion_into_cast_spec(
        cast_spec,
        cast_expansion,
    )

    zoe = merged["supporting_cast"][0]
    assert zoe["metadata"]["evolution_notes"] == [
        "Stops operating as a detached observer.",
        "Commits to the breach team in the field.",
    ]
    assert "changes" not in zoe


def test_fallback_chapter_outline_scenes_have_no_chapter_number_prefix() -> None:
    """Scene titles / time_labels must not embed the chapter number.

    Historically these looked like ``f"第{chapter_number}章中段"`` and that
    prefix leaked into the rewrite-template fallback prose as
    ``"第13章中段，程彻重新被推回…"``. Keeping them generic guarantees no
    renderer can reconstruct a chapter-numbered meta sentence.
    """
    project = build_project()
    project.target_chapters = 6
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    import re

    prefix_re = re.compile(r"第\s*\d+\s*章")
    for chapter in outline_batch["chapters"]:
        for scene in chapter["scenes"]:
            assert not prefix_re.search(scene.get("title", "")), (
                f"scene title leaked chapter number: {scene['title']}"
            )
            assert not prefix_re.search(scene.get("time_label", "")), (
                f"scene time_label leaked chapter number: {scene['time_label']}"
            )


def test_fallback_volume_plan_does_not_create_zero_chapter_volumes_for_short_projects() -> None:
    project = build_project()
    project.target_chapters = 1
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    assert len(volume_plan) == 1
    assert volume_plan[0]["chapter_count_target"] == 1


def test_planner_prompts_switch_to_english_for_english_projects() -> None:
    project = ProjectModel(
        slug="storm-ledger",
        title="Storm Ledger",
        genre="Fantasy",
        sub_genre="Epic Fantasy",
        language="en-US",
        target_word_count=90000,
        target_chapters=24,
        audience="KU readers",
        metadata_json={
            "writing_profile": {
                "market": {
                    "platform_target": "Kindle Unlimited",
                    "content_mode": "English-language commercial fantasy serial",
                    "reader_promise": "Fast-moving fantasy with escalating political danger.",
                    "selling_points": ["storm magic", "buried dynasty", "betrayal"],
                    "trope_keywords": ["chosen family", "forbidden archive"],
                    "hook_keywords": ["sealed letter", "execution order"],
                    "opening_strategy": "Open with the order and the stolen key in the same scene.",
                    "chapter_hook_strategy": "End every chapter with a fresh threat or reveal.",
                    "payoff_rhythm": "Short payoff every chapter, major payoff every 5-7 chapters",
                },
                "style": {
                    "tone_keywords": ["taut", "ominous", "fast"],
                },
                "serialization": {
                    "opening_mandate": "Hook the reader in the first scene with concrete danger.",
                    "first_three_chapter_goal": "Lock in the central conflict, edge, and reversal.",
                    "scene_drive_rule": "Every scene must create a gain, a loss, or a sharper choice.",
                    "chapter_ending_rule": "Every chapter must end on a question, a threat, or a costly next move.",
                },
            }
        },
    )
    project.id = uuid4()

    system_prompt, user_prompt = planner_services._book_spec_prompts(
        project,
        "A royal archivist discovers the crown has been deleting its own bloodline.",
        {},
    )

    assert "English-language commercial fiction planner" in system_prompt
    assert "Project title: Storm Ledger" in user_prompt
    assert "Target chapters: 24" in user_prompt
    assert "Write all planning artifacts in English." in user_prompt
    assert "长篇中文小说" not in system_prompt + user_prompt


def test_next_volume_outline_prompt_builds_character_drama_from_current_cast() -> None:
    project = build_project()
    project.metadata_json = {}
    book_spec = {"title": "长夜巡航", "reader_promise": "每卷都有选择代价。"}
    cast_spec = {
        "protagonist": {
            "name": "沈砚",
            "role": "protagonist",
            "goal": "夺回被封存的航线记录",
            "fear": "再次害队友暴露",
            "flaw": "把所有风险都藏在自己手里",
            "moral_framework": {
                "core_values": ["守住同伴"],
                "lines_never_crossed": ["不伪造同伴意愿"],
            },
            "ip_anchor": {"quirks": ["紧张时反复校准旧罗盘"]},
        },
        "antagonist": {
            "name": "港务官",
            "role": "antagonist",
            "goal": "让篡改航线记录成为新秩序",
            "villain_charisma": {
                "philosophical_appeal": "牺牲少数边境船队，换取核心港区稳定",
                "protagonist_mirror": "同样重视秩序，却把秩序当作消音工具",
            },
        },
    }
    volume_plan = [
        {
            "volume_number": 1,
            "volume_title": "封港前夜",
            "chapter_count_target": 3,
            "volume_goal": "拿到第一份航线原始记录",
        },
        {
            "volume_number": 2,
            "volume_title": "暗航证词",
            "chapter_count_target": 3,
            "volume_goal": "逼证人公开港务官篡改航线的证据",
        },
    ]

    _, user_prompt = planner_services._volume_outline_prompts(
        project,
        book_spec,
        cast_spec,
        volume_plan,
        volume_plan[1],
    )

    assert "Character Drama Engine" in user_prompt
    assert "夺回被封存的航线记录" in user_prompt
    assert "不伪造同伴意愿" in user_prompt
    assert "INTJ" not in user_prompt


@pytest.mark.asyncio
async def test_generate_character_names_prompt_does_not_embed_fixed_example_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session: object, settings: object, request: object):
        captured["user_prompt"] = request.user_prompt
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({}, ensure_ascii=False),
                "llm_run_id": uuid4(),
            },
        )()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    await planner_services._generate_character_names(
        FakeSession(),
        build_settings(),
        genre="末日科幻",
        sub_genre="重生囤货",
        language="zh-CN",
        premise="主角重生回末日前三十天，提前囤货并抢占安全区通行权。",
        book_spec={},
    )

    prompt = captured["user_prompt"]
    assert "沈逸" not in prompt
    assert "裴云霄" not in prompt
    assert "林启" not in prompt
    assert "秦北" not in prompt


@pytest.mark.asyncio
async def test_generate_structured_artifact_merges_partial_llm_payload_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fallback_book_spec = planner_services._fallback_book_spec(
        project,
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
    )

    async def fake_complete_text(session: object, settings: object, request: object):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps(
                    {
                        "title": "Gemini Book",
                        "protagonist": {
                            "name": "沈砚",
                        },
                    },
                    ensure_ascii=False,
                ),
                "llm_run_id": uuid4(),
            },
        )()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    payload, llm_run_id = await planner_services._generate_structured_artifact(
        FakeSession(),
        build_settings(),
        project=project,
        logical_name="book_spec",
        system_prompt="system",
        user_prompt="user",
        fallback_payload=fallback_book_spec,
        workflow_run_id=uuid4(),
    )

    assert llm_run_id is not None
    assert payload["title"] == "Gemini Book"
    assert payload["protagonist"]["name"] == "沈砚"
    assert (
        payload["protagonist"]["external_goal"]
        == fallback_book_spec["protagonist"]["external_goal"]
    )


@pytest.mark.asyncio
async def test_generate_structured_artifact_can_disable_fallback_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fallback_payload = {
        "batch_name": "volume-1-outline",
        "chapters": [{"chapter_number": 1, "title": "fallback-title"}],
    }

    async def fake_complete_text(session: object, settings: object, request: object):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps(
                    {
                        "batch_name": "volume-1-outline",
                        "chapters": [{"chapter_number": 1}],
                    },
                    ensure_ascii=False,
                ),
                "llm_run_id": uuid4(),
            },
        )()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    payload, _ = await planner_services._generate_structured_artifact(
        FakeSession(),
        build_settings(),
        project=project,
        logical_name="volume_1_chapter_outline",
        system_prompt="system",
        user_prompt="user",
        fallback_payload=fallback_payload,
        workflow_run_id=uuid4(),
        merge_fallback=False,
    )

    assert payload["chapters"] == [{"chapter_number": 1}]


@pytest.mark.asyncio
async def test_generate_structured_artifact_fails_closed_when_validator_rejects_critical_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fallback_book_spec = planner_services._fallback_book_spec(
        project,
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
    )

    async def fake_complete_text(session: object, settings: object, request: object):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({"protagonist": []}, ensure_ascii=False),
                "llm_run_id": uuid4(),
            },
        )()

    def reject_non_mapping_protagonist(value: dict[str, object]) -> None:
        if not isinstance(value.get("protagonist"), dict):
            raise ValueError("invalid")

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    with pytest.raises(planner_services.PlannerFallbackError):
        await planner_services._generate_structured_artifact(
            FakeSession(),
            build_settings(),
            project=project,
            logical_name="book_spec",
            system_prompt="system",
            user_prompt="user",
            fallback_payload=fallback_book_spec,
            workflow_run_id=uuid4(),
            validator=reject_non_mapping_protagonist,
        )


@pytest.mark.asyncio
async def test_generate_novel_plan_creates_all_artifacts_and_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        assert slug == "my-story"
        return project

    artifact_counter = 0
    prompts_by_logical_name: dict[str, str] = {}

    async def fake_import_planning_artifact(session: object, project_slug: str, payload: object):
        nonlocal artifact_counter
        artifact_counter += 1
        return type(
            "ArtifactStub",
            (),
            {
                "id": uuid4(),
                "version_no": artifact_counter,
                "artifact_type": payload.artifact_type.value,
            },
        )()

    async def fake_generate_structured_artifact(
        session: object,
        settings: object,
        *,
        project: object,
        logical_name: str,
        system_prompt: str,
        user_prompt: str,
        fallback_payload: object,
        workflow_run_id,
        step_run_id=None,
        validator=None,
        **kwargs: object,
    ):
        prompts_by_logical_name[logical_name] = user_prompt
        if logical_name.endswith("_chapter_outline") and isinstance(fallback_payload, dict):
            payload = json.loads(json.dumps(fallback_payload, ensure_ascii=False))
            for chapter in payload.get("chapters", []):
                chapter["main_conflict"] = (
                    "沈砚必须在封港命令生效前拿到航线记录，并避开港务官的封锁。"
                )
            return payload, uuid4()
        return fallback_payload, uuid4()

    monkeypatch.setattr(planner_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(planner_services, "import_planning_artifact", fake_import_planning_artifact)
    monkeypatch.setattr(
        planner_services,
        "_generate_structured_artifact",
        fake_generate_structured_artifact,
    )

    session = FakeSession()
    result = await planner_services.generate_novel_plan(
        session,
        build_settings(),
        "my-story",
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
        requested_by="tester",
    )

    workflow_runs = [item for item in session.added if isinstance(item, WorkflowRunModel)]
    workflow_steps = [item for item in session.added if isinstance(item, WorkflowStepRunModel)]

    assert result.chapter_count == project.target_chapters
    assert result.volume_count >= 1
    artifact_types = [item.artifact_type for item in result.artifacts]
    assert artifact_types[:4] == [
        ArtifactType.PREMISE,
        ArtifactType.BOOK_SPEC,
        ArtifactType.WORLD_SPEC,
        ArtifactType.CAST_SPEC,
    ]
    assert ArtifactType.STORY_DESIGN_KERNEL in artifact_types
    assert (
        artifact_types.index(ArtifactType.CAST_SPEC)
        < artifact_types.index(ArtifactType.STORY_DESIGN_KERNEL)
        < artifact_types.index(ArtifactType.EMOTION_DRIVEN_KERNEL)
        < artifact_types.index(ArtifactType.VOLUME_PLAN)
    )
    assert ArtifactType.EMOTION_DRIVEN_KERNEL in artifact_types
    assert ArtifactType.VOLUME_PLAN in artifact_types
    assert ArtifactType.PLAN_VALIDATION in artifact_types
    assert artifact_types.index(ArtifactType.VOLUME_PLAN) < artifact_types.index(
        ArtifactType.PLAN_VALIDATION
    )
    assert ArtifactType.PREWRITE_READINESS in artifact_types
    assert artifact_types.index(ArtifactType.PLAN_VALIDATION) < artifact_types.index(
        ArtifactType.PREWRITE_READINESS
    )
    assert ArtifactType.PROMOTIONAL_BRIEF in artifact_types
    assert ArtifactType.VOLUME_CHAPTER_OUTLINE in artifact_types
    assert ArtifactType.CHAPTER_OUTLINE_BATCH in artifact_types
    assert len(result.llm_run_ids) >= 9
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) >= 7
    assert any(step.step_name == "prewrite_readiness_gate" for step in workflow_steps)
    assert any(step.step_name == "reverse_outline_gate" for step in workflow_steps)
    assert any(step.step_name == "worldview_progression_gate" for step in workflow_steps)
    assert any(step.step_name == "worldview_compliance_gate" for step in workflow_steps)
    assert "story_design_kernel" in project.metadata_json
    assert project.metadata_json["story_design_kernel"]["reverse_outline_status"] == "verified"
    assert "character_drama_map" in project.metadata_json
    assert "emotion_driven_kernel" in project.metadata_json
    assert "planning_kernel" in project.metadata_json
    assert project.metadata_json["reverse_outline_gate_report"]["passed"] is True
    assert "worldview_progression_gate_report" in project.metadata_json
    assert "worldview_compliance_gate_report" in project.metadata_json
    assert project.metadata_json["worldview_compliance_gate_report"]["passed"] is True
    assert project.metadata_json["planning_kernel"]["story_design"]["valid"] is True
    assert project.metadata_json["planning_kernel"]["emotion_driven"]["valid"] is True
    assert "prewrite_readiness_report" in project.metadata_json
    assert "Character Drama Engine" in prompts_by_logical_name["story_design_kernel"]
    assert "EmotionDrivenKernel" in prompts_by_logical_name["emotion_driven_kernel"]
    assert "Story Design Kernel" in prompts_by_logical_name["volume_plan"]
    assert "emotion_driven_core" in prompts_by_logical_name["volume_plan"]
    assert "Character Drama Engine" in prompts_by_logical_name["volume_plan"]
    assert "world_state_targets" in prompts_by_logical_name["volume_plan"]
    assert "active_authority_claims" in prompts_by_logical_name["volume_plan"]
    assert "map_function" in prompts_by_logical_name["volume_plan"]
    assert "asset_risk_escalation" in prompts_by_logical_name["volume_plan"]
    assert "reveal_budget" in prompts_by_logical_name["volume_plan"]
    outline_prompt = next(
        prompt
        for logical_name, prompt in prompts_by_logical_name.items()
        if logical_name.endswith("_chapter_outline")
    )
    assert "world_rule_refs" in outline_prompt
    assert "world_rule_landing" in outline_prompt
    assert "world_state_deltas" in outline_prompt
    assert "world_asset_refs" in outline_prompt
    assert "world_scene_template_ref" in outline_prompt


@pytest.mark.asyncio
async def test_repair_cast_personhood_regenerates_incomplete_character_bible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    thin_cast = {
        "protagonist": {"name": "沈砚", "goal": "找到账目证据"},
        "antagonist": {"name": "祁镇", "goal": "删光旧记录"},
        "supporting_cast": [],
        "conflict_map": [],
    }
    repaired_cast = {
        "protagonist": {
            "name": "沈砚",
            "goal": "找到账目证据",
            "ip_anchor": {
                "quirks": ["左手关节断裂", "洁癖", "口头禅：这不对劲"],
                "core_wound": "七岁目睹母亲被处决",
            },
            "psych_profile": {"mbti": "INTJ"},
            "life_history": {"education": "帝国导航学院"},
            "family_imprint": {"parenting_style": "父亲严苛"},
            "beliefs": {"ideology": "真相高于秩序"},
        },
        "antagonist": {
            "name": "祁镇",
            "goal": "删光旧记录",
            "ip_anchor": {"quirks": ["整理袖口", "永远戴白手套"]},
            "villain_charisma": {
                "noble_motivation": "维护航道秩序",
                "pain_origin": "曾因混乱失去家人",
                "personal_code": ["不亲手杀孩童"],
                "protagonist_mirror": "同样相信记录能决定命运",
            },
        },
        "supporting_cast": [],
        "conflict_map": [],
    }
    prompts: list[str] = []

    async def fake_generate_structured_artifact(
        session: object,
        settings: object,
        *,
        project: object,
        logical_name: str,
        system_prompt: str,
        user_prompt: str,
        fallback_payload: object,
        workflow_run_id,
        step_run_id=None,
        validator=None,
        **kwargs: object,
    ):
        assert logical_name == "cast_spec_personhood_repair"
        prompts.append(user_prompt)
        return repaired_cast, uuid4()

    monkeypatch.setattr(
        planner_services,
        "_generate_structured_artifact",
        fake_generate_structured_artifact,
    )

    payload, llm_run_id = await planner_services._repair_cast_personhood_if_needed(
        session=FakeSession(),
        settings=build_settings(),
        project=project,
        book_spec_payload={
            "title": "长夜巡航",
            "themes": ["真相"],
            "dramatic_question": "沈砚能否找回真相？",
        },
        world_spec_payload={"power_system": {"name": "导航印记", "tiers": ["学徒", "导航员"]}},
        cast_spec_payload=thin_cast,
        workflow_run_id=uuid4(),
    )

    assert llm_run_id is not None
    assert payload["protagonist"]["ip_anchor"]["quirks"][:3] == [
        "左手关节断裂",
        "洁癖",
        "口头禅：这不对劲",
    ]
    assert payload["protagonist"]["psych_profile"]["mbti"] == "INTJ"
    assert payload["antagonist"]["villain_charisma"]["noble_motivation"] == "维护航道秩序"
    assert payload["antagonist"]["ip_anchor"]["core_wound"]
    assert "Bible 回炉整改清单" in prompts[0]
    assert "CHARACTER_PERSONHOOD_INCOMPLETE" in prompts[0]


def test_fallback_volume_plan_has_different_obstacles_per_volume() -> None:
    """Each volume must have a unique obstacle, not the same antagonist template."""
    project = build_project()
    project.target_chapters = 54  # >50 chapters needed for multi-volume
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    obstacles = [v["volume_obstacle"] for v in volume_plan]
    # With multiple volumes, obstacles should be different
    assert len(volume_plan) >= 2
    assert len(set(obstacles)) == len(obstacles), (
        f"Volume obstacles must be unique; got {obstacles}"
    )


def test_fallback_book_spec_satisfies_project_level_bible_fields() -> None:
    project = build_project()
    project.target_chapters = 120
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    book_spec = planner_services._fallback_book_spec(project, premise)

    assert book_spec["theme_statement"]
    assert book_spec["dramatic_question"].endswith("？")
    assert book_spec["expected_character_count"] >= 12
    assert len(book_spec["naming_pool"]) >= book_spec["expected_character_count"] * 2


def test_ensure_book_spec_bible_fields_extends_thin_llm_payload() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    thin_payload = {
        "title": "长夜巡航",
        "themes": ["真相与代价"],
        "protagonist": {
            "name": "沈砚",
            "external_goal": "追查被篡改的航线记录",
            "internal_need": "重新学会信任同伴",
        },
        "expected_character_count": 4,
        "naming_pool": ["沈砚"],
    }

    normalized = planner_services._ensure_book_spec_bible_fields(
        project,
        premise,
        thin_payload,
    )

    assert normalized["theme_statement"].startswith("真正的力量")
    assert (
        normalized["dramatic_question"]
        == "沈砚能否在追查被篡改的航线记录的同时，仍然重新学会信任同伴？"
    )
    assert len(normalized["naming_pool"]) == 8
    assert "沈砚" in normalized["naming_pool"]


def test_synthesize_missing_cast_bible_fields_closes_character_gate_fields() -> None:
    project = build_project()
    cast_spec = {
        "protagonist": {
            "name": "沈砚",
            "role": "protagonist",
            "goal": "追查被篡改的航线记录",
            "fear": "再次害死搭档",
        },
        "antagonist": {
            "name": "程砚",
            "role": "antagonist",
            "goal": "封锁所有底层日志",
            "secret": "当年参与删改航线",
        },
        "supporting_cast": [
            {
                "name": "阿洛",
                "role": "supporting",
                "goal": "把走私账册送出港口",
                "value_to_story": "提供港口黑市线索和临场行动压力",
            }
        ],
    }

    repaired = planner_services._synthesize_missing_cast_bible_fields(
        project,
        cast_spec,
    )

    protagonist = repaired["protagonist"]
    antagonist = repaired["antagonist"]
    assert len(protagonist["ip_anchor"]["quirks"]) >= 3
    assert protagonist["ip_anchor"]["core_wound"]
    assert protagonist["psych_profile"]["mbti"]
    assert protagonist["life_history"]["formative_events"]
    assert protagonist["family_imprint"]["inherited_values"]
    assert protagonist["beliefs"]["ideology"]
    assert len(antagonist["ip_anchor"]["quirks"]) >= 2
    assert antagonist["villain_charisma"]["noble_motivation"]
    assert len(antagonist["villain_charisma"]["personal_code"]) >= 1
    assert protagonist["background"]
    assert antagonist["background"]

    supporting = repaired["supporting_cast"][0]
    assert supporting["ip_anchor"]["tag_memory"]
    assert supporting["ip_anchor"]["independent_life"]


def test_synthesize_missing_cast_bible_fields_separates_antagonist_motives() -> None:
    project = build_project()
    shared_motive = "复活上古邪神打破阴阳界限，借归墟会完成复仇。"
    cast_spec = {
        "protagonist": {
            "name": "沈青崖",
            "role": "protagonist",
            "goal": "查明灭门案真相",
            "fear": "血脉失控",
        },
        "antagonist": {
            "name": "清尘",
            "role": "antagonist",
            "goal": shared_motive,
            "background": shared_motive,
            "secret": shared_motive,
        },
        "supporting_cast": [
            {
                "name": "魏德曼",
                "role": "antagonist",
                "goal": shared_motive,
                "background": shared_motive,
                "secret": shared_motive,
            },
            {
                "name": "归墟会祭司",
                "role": "antagonist",
                "goal": shared_motive,
                "background": shared_motive,
                "secret": shared_motive,
            },
            {
                "name": "赵鹤鸣",
                "role": "antagonist",
                "goal": shared_motive,
                "background": shared_motive,
                "secret": shared_motive,
            },
            {
                "name": "沈天机",
                "role": "antagonist",
                "goal": shared_motive,
                "background": shared_motive,
                "secret": shared_motive,
            },
        ],
    }

    repaired = planner_services._synthesize_missing_cast_bible_fields(
        project,
        cast_spec,
    )

    goals = [
        repaired["antagonist"]["goal"],
        repaired["supporting_cast"][0]["goal"],
        repaired["supporting_cast"][1]["goal"],
        repaired["supporting_cast"][2]["goal"],
        repaired["supporting_cast"][3]["goal"],
    ]
    assert len(set(goals)) == 5
    assert repaired["antagonist"]["motive_axis"] != repaired["supporting_cast"][0]["motive_axis"]

    from bestseller.services.bible_gate import (  # noqa: PLC0415
        build_draft_from_materialization_content,
        validate_bible_completeness,
    )
    from bestseller.services.invariants import seed_invariants  # noqa: PLC0415

    draft = build_draft_from_materialization_content(
        book_spec_content={
            "title": "青崖诡事",
            "theme_statement": "复仇必须被真相约束。",
            "dramatic_question": "沈青崖能否查明真相而不被复仇吞没？",
            "expected_character_count": 4,
            "naming_pool": ["沈青崖", "清尘", "魏德曼", "归墟会祭司"] * 2,
        },
        world_spec_content={"power_system": {"name": "阴阳重瞳", "tiers": ["开眼", "照魂"]}},
        cast_spec_content=repaired,
    )
    report = validate_bible_completeness(
        draft,
        seed_invariants(
            project_id=project.id,
            language=getattr(project, "language", None),
            words_per_chapter=2200,
        ),
    )

    assert "ANTAGONIST_MOTIVE_OVERLAP" not in {d.code for d in report.deficiencies}


def test_cast_personhood_repair_codes_cover_l2_bible_character_gates() -> None:
    assert {
        "TAG_MEMORY_MISSING",
        "INDEPENDENT_LIFE_MISSING",
        "CHARACTER_CONTRAST_MISSING",
    }.issubset(planner_services._CAST_PERSONHOOD_REPAIR_CODES)


def test_fallback_volume_plan_carries_conflict_phase() -> None:
    """Each volume entry must include a conflict_phase and primary_force_name."""
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    for vol in volume_plan:
        assert "conflict_phase" in vol
        assert "primary_force_name" in vol
        assert vol["conflict_phase"] in (
            "survival",
            "political_intrigue",
            "betrayal",
            "faction_war",
            "existential_threat",
            "internal_reckoning",
        )


def test_fallback_chapter_outline_main_conflict_varies_across_volumes() -> None:
    """main_conflict in chapters of different volumes should differ."""
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(
        project, book_spec, cast_spec, volume_plan
    )

    chapters = outline["chapters"]
    # Group main_conflict by volume
    conflicts_by_volume: dict[int, set[str]] = {}
    for ch in chapters:
        vol = ch["volume_number"]
        conflicts_by_volume.setdefault(vol, set()).add(ch["main_conflict"])

    # Different volumes should produce different conflict texts
    all_vol_conflicts = [next(iter(s)) for s in conflicts_by_volume.values()]
    unique_count = len(set(all_vol_conflicts))
    assert unique_count >= min(2, len(conflicts_by_volume)), (
        f"Expected different conflict texts across volumes; got {all_vol_conflicts}"
    )


def test_fallback_cast_spec_includes_antagonist_forces() -> None:
    """The cast spec should include antagonist_forces for multi-force conflict."""
    project = build_project()
    project.target_chapters = 54  # >50 chapters needed for multi-volume/force
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    assert "antagonist_forces" in cast_spec
    forces = cast_spec["antagonist_forces"]
    assert len(forces) >= 2
    # Each force has required fields
    for force in forces:
        assert "name" in force
        assert "force_type" in force
        assert "active_volumes" in force
        assert len(force["active_volumes"]) >= 1


def test_fallback_cast_spec_backward_compat_single_chapter() -> None:
    """A single-chapter project should still work with antagonist_forces."""
    project = build_project()
    project.target_chapters = 1
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(
        project, book_spec, cast_spec, volume_plan
    )

    assert cast_spec["antagonist"] is not None
    assert len(cast_spec["antagonist_forces"]) >= 1
    assert len(volume_plan) == 1
    assert len(outline["chapters"]) == 1


def test_assign_conflict_phases_distributes_correctly() -> None:
    assert planner_services._assign_conflict_phases(1) == ["survival"]
    assert planner_services._assign_conflict_phases(2) == ["survival", "existential_threat"]
    assert planner_services._assign_conflict_phases(3) == [
        "survival",
        "political_intrigue",
        "existential_threat",
    ]
    phases_5 = planner_services._assign_conflict_phases(5)
    assert len(phases_5) == 5
    assert phases_5[0] == "survival"
    assert phases_5[-1] == "existential_threat"


def test_assign_conflict_phases_7_volumes_cycles_middle() -> None:
    """For 7+ volumes, middle phases should cycle instead of repeating last."""
    phases_7 = planner_services._assign_conflict_phases(7)
    assert len(phases_7) == 7
    assert phases_7[0] == "survival"
    assert phases_7[-1] == "internal_reckoning"
    # Middle should NOT just repeat internal_reckoning
    middle = phases_7[1:-1]
    assert "internal_reckoning" not in middle
    # Should cycle through the 4 middle phases
    assert len(set(middle)) >= 3  # at least 3 distinct phases in the middle

    phases_8 = planner_services._assign_conflict_phases(8)
    assert len(phases_8) == 8
    assert phases_8[0] == "survival"
    assert phases_8[-1] == "internal_reckoning"


def test_assign_conflict_phases_with_category_key() -> None:
    """Category-specific phases should replace legacy phases."""
    phases = planner_services._assign_conflict_phases(5, category_key="action-progression")
    assert len(phases) == 5
    assert phases[0] == "individual_survival"
    assert phases[-1] == "transcendence"

    # Different category yields different phases
    phases_rel = planner_services._assign_conflict_phases(5, category_key="relationship-driven")
    assert phases_rel[0] == "stranger"
    assert phases_rel != phases


def test_assign_conflict_phases_category_fewer_volumes() -> None:
    """When volume_count < pathway phases, should distribute correctly."""
    phases = planner_services._assign_conflict_phases(3, category_key="action-progression")
    assert len(phases) == 3
    assert phases[0] == "individual_survival"
    assert phases[-1] == "transcendence"


def test_assign_conflict_phases_unknown_category_falls_back() -> None:
    """Unknown category_key should fall back to legacy behavior."""
    phases = planner_services._assign_conflict_phases(3, category_key="nonexistent-xyz")
    assert phases == ["survival", "political_intrigue", "existential_threat"]


def test_assign_conflict_phases_none_category_preserves_legacy() -> None:
    """category_key=None should produce identical results to the old behavior."""
    assert planner_services._assign_conflict_phases(2, category_key=None) == [
        "survival",
        "existential_threat",
    ]
    assert planner_services._assign_conflict_phases(5, category_key=None)[0] == "survival"


def test_resolve_phase_templates_from_category() -> None:
    """Category phase templates should contain formatted text."""
    tpl = planner_services._resolve_phase_templates(
        "individual_survival",
        category_key="action-progression",
        is_en=False,
    )
    assert tpl["goal"]  # non-empty
    assert "{protagonist}" in tpl["goal"]  # still has placeholder


def test_resolve_phase_templates_legacy_fallback() -> None:
    """Without category, legacy templates should be returned."""
    tpl = planner_services._resolve_phase_templates("survival", category_key=None, is_en=False)
    assert tpl["goal"]
    assert "{protagonist}" in tpl["goal"]


def test_json_dump_helper_keeps_unicode() -> None:
    payload = {"title": "长夜巡航"}
    dumped = planner_services._json_dumps(payload)
    assert "长夜巡航" in dumped
    assert json.loads(dumped)["title"] == "长夜巡航"


# ---------------------------------------------------------------------------
# Fuzzy participant resolver — guards against LLM cast hallucinations
# ---------------------------------------------------------------------------


def _fixture_identity_index() -> dict[str, dict[str, object]]:
    """Match the real-world failure on female-no-cp-1776303225."""

    manifest = [
        {"name": "林鸢", "role": "protagonist"},
        {"name": "苏澄", "role": "ally"},
        {"name": "秦骁", "role": "rival"},
        {"name": "魏骁", "role": "antagonist"},
        {"name": "霍沉", "role": "antagonist"},
    ]
    return planner_services._outline_identity_index(manifest)


def test_fuzzy_resolve_aliases_unique_high_overlap_match() -> None:
    """The repro: 姜澄 (LLM typo) should resolve to 苏澄 (only neighbour)."""

    index = _fixture_identity_index()
    resolved = planner_services._outline_fuzzy_resolve_participant("姜澄", index)
    assert resolved is not None
    assert resolved["name"] == "苏澄"


def test_fuzzy_resolve_returns_none_when_ambiguous() -> None:
    """陆骁 sits between 秦骁 and 魏骁 — refuse to guess."""

    index = _fixture_identity_index()
    assert planner_services._outline_fuzzy_resolve_participant("陆骁", index) is None


def test_fuzzy_resolve_returns_none_for_genuinely_new_name() -> None:
    index = _fixture_identity_index()
    # 王五 shares no characters with any cast member.
    assert planner_services._outline_fuzzy_resolve_participant("王五", index) is None


def test_fuzzy_resolve_returns_none_on_length_mismatch() -> None:
    """Different lengths are always rejected — a 3-char name is not a typo of a 2-char one."""

    index = _fixture_identity_index()
    assert planner_services._outline_fuzzy_resolve_participant("林鸢儿", index) is None


def test_fuzzy_resolve_passes_exact_match_through() -> None:
    index = _fixture_identity_index()
    resolved = planner_services._outline_fuzzy_resolve_participant("林鸢", index)
    assert resolved is not None
    assert resolved["name"] == "林鸢"


def test_fuzzy_resolve_handles_empty_inputs() -> None:
    assert planner_services._outline_fuzzy_resolve_participant("", {}) is None
    index = _fixture_identity_index()
    assert planner_services._outline_fuzzy_resolve_participant("", index) is None
