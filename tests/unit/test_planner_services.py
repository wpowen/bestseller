from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.services import planner as planner_services
from bestseller.services.concept_lab import build_concept_lab_catalog
from bestseller.services.distilled_strategy_compiler import (
    DistilledStrategyCard,
    SelectedMechanism,
)
from bestseller.services.methodology_overlay import validate_ability_origin_contract
from bestseller.services.plan_fingerprint import scan_batch_for_duplicates
from bestseller.services.story_effect_skills import (
    STORY_EFFECT_SKILL_SELECTION_METADATA_KEY,
)
from bestseller.services.story_enhancers import StoryEnhancerSelection
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

    async def scalars(self, _stmt: object) -> _FakeExecuteResult:
        # Fresh project — no historical workflow steps or chapters.
        return _FakeExecuteResult()

    async def execute(self, _stmt: object) -> _FakeExecuteResult:
        # Fresh project — no rows. Used by helpers that query the
        # `chapters` table (e.g. existing-title dedup fetch).
        return _FakeExecuteResult()


def build_settings():
    settings = load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )
    settings.pipeline.enable_rolling_outline = False
    return settings


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        audience="web-serial",
        metadata_json={
            "premise": "沈砚，一名追查失踪星图的巡航员，被迫穿越封锁航道。"
        },
    )
    project.id = uuid4()
    return project


def lock_design_snapshot(
    project: ProjectModel,
    *,
    protagonist_name: str,
    reader_promise: str,
    core_story_engine: str,
    tone: str = "light",
    cost_style: str = "minimal",
    effect_skills: list[str] | None = None,
) -> None:
    project.metadata_json.update(
        {
            "book_design_snapshot_status": "locked",
            "book_design_snapshot": {
                "schema_version": "book-design-snapshot.v1",
                "snapshot_id": "source-bound-test",
                "source_hash": "a" * 64,
                "tone": tone,
                "protagonist": {"name": protagonist_name},
                "reader_promise": reader_promise,
                "core_story_engine": core_story_engine,
                "creation_intent": {
                    "tone_preference": tone,
                    "genre_intent": {"tone_preference": tone},
                    "story_enhancers": {
                        "cost_style": cost_style,
                        "effect_skills": effect_skills or [],
                    },
                },
            },
        }
    )


def test_planner_injects_no_guardrail_blocks_regardless_of_seed() -> None:
    """2026-08-01 product ruling: guardrail blocks were deleted from prompts.
    Whatever the creation seed says, ``_append_category_context`` must not
    append any anti-default-motif contract text — drift is caught by the
    output-side detectors instead."""
    project = build_project()
    project.metadata_json.update(
        {
            "premise": "模型生成的尸体账本故事不应关闭护栏",
            "creation_intent_contract": {"concept_seed": "修阵师让阵眼重新发光"},
        }
    )
    prompt = planner_services._append_category_context("BASE", project)
    assert "【事实源与自然因果契约】" not in prompt
    assert "【当下动机契约】" not in prompt

    project.metadata_json["creation_intent_contract"] = {
        "concept_seed": "守墓人必须在坟中找到两具尸体留下的线索"
    }
    prompt = planner_services._append_category_context("BASE", project)
    assert "【当下动机契约】" not in prompt
    assert "【事实源与自然因果契约】" not in prompt


def test_persisted_methodology_keeps_this_books_own_wording() -> None:
    """2026-08-02: the methodology sanitiser no longer censors motif words.

    It used to delete any field mentioning debt/death and substitute framework
    filler, so every book that tripped it received the same generic replacement.
    A methodology belongs to its own project; only genuinely empty fields are
    filled now.
    """
    project = build_project()
    project.metadata_json.update(
        {
            "writing_profile": {
                "concept_methodology": {
                    "mindset": "旧账簿唤醒了一具尸体",
                    "mechanism_types": ["行动反差", "欠条结算"],
                    "reader_promise_axis": "主角持续修复航道",
                    "shuangdian_cadence": ["发现断点"],
                    "design_axes": ["资源变化"],
                    "anti_patterns": ["开局解释过长"],
                    "market_signals": [],
                    "rationale": "本书自己的设计说明",
                    "source": "llm",
                }
            },
        }
    )

    block = planner_services._concept_methodology_block(project, language="zh-CN")

    assert "旧账簿唤醒了一具尸体" in block
    assert "欠条结算" in block


def test_minimal_cost_reaches_world_prompt_as_pacing_not_an_allowlist() -> None:
    """纯爽 arrives as one pacing line; the world vocabulary stays the book's own.

    The deleted apparatus rewrote 分账→利益分配 inside the assembled prompt and
    appended a lock confining every world rule to an approved pressure list.
    """
    project = build_project()
    project.metadata_json["story_enhancers"] = {"cost_style": "minimal"}

    premise = "巡航员利用价格同盟内部分账不均修复航线。"
    _, prompt = planner_services._world_spec_prompts(
        project,
        premise,
        planner_services._fallback_book_spec(project, premise),
    )

    assert "最终极简代价世界锁" not in prompt
    assert "因果白名单" not in prompt
    # The premise reaches the prompt verbatim — no substitution table.
    assert "分账不均" in prompt


def test_minimal_light_book_fallback_does_not_seed_stock_loss_trauma() -> None:
    project = build_project()
    project.metadata_json["story_enhancers"] = {"cost_style": "minimal"}
    project.metadata_json["writing_profile"] = {
        "style": {"tone_keywords": ["轻松", "幽默"]}
    }

    fallback = planner_services._fallback_book_spec(
        project,
        "陆沉守住废药园，把苏醒的灵药拿来养猪换钱。",
        category_key="action-progression",
    )
    protagonist = fallback["protagonist"]

    assert "失去了最重要的人或事物" not in protagonist["core_wound"]
    assert "轻视" in protagonist["core_wound"]
    assert "资源" in protagonist["core_wound"]
    assert "接受力量的代价" not in protagonist["internal_need"]
    assert "失去自己仍在意的人" not in fallback["stakes"]["personal"]

    world = planner_services._fallback_world_spec(
        project,
        "陆沉守住废药园，把苏醒的灵药拿来养猪换钱。",
        fallback,
        category_key="action-progression",
    )
    world_blob = json.dumps(world, ensure_ascii=False)

    assert "反噬代价规则" not in world_blob
    assert "不可逆牺牲" not in world_blob
    assert "操作容量规则" in world_blob
    assert "资源、时间、资格与暴露风险" in world_blob
    assert "长期被现有秩序排除在资源与机会之外" in world_blob


def test_planner_no_longer_vetoes_ordinary_story_material() -> None:
    """2026-08-02: the motif police were dismantled.

    Every payload below was a hard veto before. They are ordinary cultivation
    material — a shop's unpaid bill, a tier named 枯骨期, a backlash, owing an
    elder a favour, tending a garden for thirty days. The framework ordered
    costs elsewhere and then executed the book for writing them; two real books
    died in the foundation stage this way. None of these may block a book now.
    """
    project = build_project()
    project.metadata_json.update({"story_enhancers": {"cost_style": "minimal"}})

    for payload in (
        {"world_name": "旧账之城"},
        {"rule": "巡查拿走药渣抵账"},
        {"forbidden_zones": "百草堂同盟上游账房"},
        {"tiers": ["枯骨期"]},
        {"forbidden_zones": "邻派后山旧墓坑"},
        {"cost": "突破失败会废修为"},
        {"tier_progression": [{"breakthrough_cost": "晋升后强制停摆三天"}]},
        {"rule": "力量使用会反噬，主角短期失声"},
        {"rule": "封印残力让主角三个月无法调动灵气，需要休息养伤"},
        {"tier_progression": [{"breakthrough_cost": "晋升后欠长老一个人情"}]},
        {"rule": "照料期间不能离开废园半步"},
        {"rule": "连续照料三十日，中断就前功尽弃"},
        {"power_system": {"story_use": "灵药入菜延寿，境界按寿命增长"}},
    ):
        planner_services._validate_planner_creation_intent_payload(
            payload,
            project=project,
            logical_name="world_spec",
        )


def test_planner_motif_veto_is_a_no_op_that_cannot_raise() -> None:
    """The gate function must stay inert — no detector, no raise."""
    import inspect

    source = inspect.getsource(
        planner_services._validate_planner_creation_intent_payload
    )
    assert "raise" not in source
    assert "violations" not in source
    assert "anti_default_motif" not in source


def test_minimal_cost_world_prompt_carries_no_allowlist_lock() -> None:
    """纯爽 is a pacing preference, not a world-vocabulary allowlist.

    The deleted lock confined every world rule, location, faction asset and
    historical event to resources / windows / permits / inventory / orders,
    which is how a cultivation world came out reading like a logistics firm.
    """
    project = build_project()
    project.metadata_json["story_enhancers"] = {"cost_style": "minimal"}

    system, user = planner_services._world_spec_prompts(
        project,
        "少年守住废药园，把苏醒的灵药拿来换钱。",
        planner_services._fallback_book_spec(
            project,
            "少年守住废药园，把苏醒的灵药拿来换钱。",
        ),
    )

    assert "字段级硬契约" not in system
    assert "最终极简代价世界锁" not in user
    assert "可恢复的资源选择" not in user
    assert "每个输出字段都必须标明来自上述已确认事实" not in user
    # The structural tier contract survives — it defines fields, not content.
    assert "power_system 必须是结构化对象" in user


@pytest.mark.asyncio
async def test_structured_planner_accepts_story_material_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-pass artifact containing death/ledger words is now accepted."""
    project = build_project()
    project.metadata_json.update({"story_enhancers": {"cost_style": "minimal"}})
    calls: list[str] = []

    async def fake_complete_text(session: object, settings: object, request: object):
        calls.append(request.user_prompt)
        payload = {"world_name": "枯骨账本", "cost": "突破失败会爆体", "rules": []}
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps(payload, ensure_ascii=False),
                "llm_run_id": uuid4(),
                "finish_reason": "stop",
                "provider": "openai",
            },
        )()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    payload, _ = await planner_services._generate_structured_artifact(
        FakeSession(),
        build_settings(),
        project=project,
        logical_name="world_spec",
        system_prompt="system",
        user_prompt="user",
        fallback_payload={"world_name": "fallback", "rules": []},
        workflow_run_id=uuid4(),
    )

    assert len(calls) == 1
    assert payload["world_name"] == "枯骨账本"


def test_extract_json_payload_handles_wrapped_json() -> None:
    payload = planner_services._extract_json_payload('```json\n{"title":"长夜巡航"}\n```')
    assert payload["title"] == "长夜巡航"


def test_real_outline_prompt_compiler_records_snapshot_and_budget() -> None:
    project = build_project()
    settings = build_settings()
    system, user = planner_services._compile_volume_outline_prompt(
        project,
        settings,
        system_prompt="Return JSON only.",
        user_prompt=(
            "BookSpec summary:\n主角必须是林岑。\n" * 900
            + "请仅生成第1卷的 ChapterOutlineBatch JSON。"
            + "每章必须包含具体行动、阻力、代价和状态变化。"
        ),
    )

    report = project.metadata_json["planner_prompt_compiler_latest"]
    assert system == "Return JSON only."
    assert "请仅生成第1卷" in user
    assert report["required_complete"] is True
    assert report["source_snapshot_hash"] == project.metadata_json[
        "book_design_snapshot_hash"
    ]
    assert report["total_tokens"] <= report["usable_budget_tokens"]
    assert "rolling_outline.canonical_context" in report["truncated"]


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


def test_persist_qimao_opening_contract_skips_general_projects() -> None:
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

    assert contract is None
    assert "opening_quality_contract" not in (project.metadata_json or {})
    assert "qimao_opening_contract" not in (project.metadata_json or {})


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

    # De-hardcoded: fallback uses neutral role placeholders, not baked names,
    # so the same handful of names no longer recurs across unrelated books.
    assert cast_spec["protagonist"]["name"] == "主角"
    assert cast_spec["antagonist"]["name"] == "反派"
    assert cast_spec["supporting_cast"][0]["name"] == "盟友1"


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
    assert "【主角决策协议·每章必填】" in outline_prompt
    assert "obvious_safe_option" in outline_prompt
    assert "first_person_reasoning" in outline_prompt


def test_concept_lab_contract_enters_core_planner_prompts() -> None:
    project = build_project()
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]
    project.metadata_json = {"concept_lab": bundle.model_dump(mode="json")}
    premise = "一名仓库管理员在末世前夜收到来自未来的缺货清单。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    prompts = [
        planner_services._book_spec_prompts(project, premise, book_spec)[1],
        planner_services._world_spec_prompts(project, premise, book_spec)[1],
        planner_services._cast_spec_prompts(project, book_spec, world_spec)[1],
        planner_services._story_design_kernel_prompts(
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
        )[1],
        planner_services._volume_plan_prompts(project, book_spec, world_spec, cast_spec)[1],
        planner_services._volume_outline_prompts(
            project,
            book_spec,
            cast_spec,
            volume_plan,
            volume_plan[0],
        )[1],
    ]

    for prompt in prompts:
        assert "已选脑洞组合合同" in prompt
        assert bundle.reader_promise in prompt
        assert "per_chapter_contract" in prompt

    fallback_kernel = planner_services._fallback_story_design_kernel(
        project,
        premise,
        book_spec,
        world_spec,
        cast_spec,
    )
    assert fallback_kernel["reader_promise"] == bundle.reader_promise
    assert fallback_kernel["premise_contract"]["unique_hook"] == bundle.one_liner
    assert bundle.story_loop.opening_question in fallback_kernel["beat_schedule"][0][
        "hook_or_aftereffect"
    ]


def test_distilled_design_reference_blocks_are_prompt_budgeted() -> None:
    project = build_project()
    project.metadata_json = {
        "distilled_strategy_blocks": {
            "chapter_outline": "STRATEGY\n" + ("策略细节" * 1000),
        },
        "distilled_design_reference_blocks": {
            "chapter_outline": "DESIGN\n" + ("设计细节" * 1000),
        },
    }

    block = planner_services._distilled_design_reference_block(
        project, "chapter_outline"
    )

    assert "STRATEGY" in block
    assert "DESIGN" in block
    assert "trimmed for prompt budget" in block
    assert len(block) < 2700


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


def test_short_complete_outline_prompt_uses_compact_scene_contract() -> None:
    project = build_project()
    project.genre = "悬疑推理"
    project.sub_genre = "人性博弈"
    project.target_chapters = 20
    project.target_word_count = 44000
    premise = "战术分析师被卷入一场以生命为筹码的封闭规则博弈。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_entry = {
        "volume_number": 1,
        "volume_title": "深渊赛局",
        "chapter_count_target": 20,
        "start_chapter_number": 1,
        "end_chapter_number": 20,
        "volume_goal": "完成死亡游戏真相揭露并关闭当前故事。",
    }

    _, outline_prompt = planner_services._volume_outline_prompts(
        project,
        book_spec,
        cast_spec,
        [volume_entry],
        volume_entry,
    )

    assert "`chapters` 必须恰好输出 20 个章节对象" in outline_prompt
    assert "每章默认只需 2 个紧凑 scenes" in outline_prompt
    assert "每章至少 3 个 scenes" not in outline_prompt


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
            maturity_score=0.80,
            maturity_status="review",
            source_count=1,
            provenance_status="anonymous_aggregate",
            privacy_status="redacted",
            genre_profile_key="otherworld-cross-system",
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


def test_stash_distilled_strategy_card_removes_unsafe_stale_prompt_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        "premise": "少年接手废药园，发现杂草都是上古灵药。",
        "distilled_strategy_expected": True,
        "distilled_strategy_card": {"aggregate_key": "distillation-generic"},
        "distilled_strategy_blocks": {"volume_plan": "unrelated old plot"},
        "distilled_strategy_block": "unrelated old plot",
        "character_strategy": {"source": "distillation_character_intelligence"},
    }
    settings = build_settings()

    from bestseller.services import distilled_strategy_compiler

    monkeypatch.setattr(
        distilled_strategy_compiler,
        "compile_distilled_strategy_card",
        lambda **_: DistilledStrategyCard(
            aggregate_key="distillation-generic",
            maturity_score=0.5,
            maturity_status="review",
            source_count=0,
        ),
    )

    planner_services._stash_distilled_strategy_card(
        project,
        category_key="xuanhuan-power-fantasy",
        settings=settings,
    )

    assert project.metadata_json == {
        "premise": "少年接手废药园，发现杂草都是上古灵药。"
    }


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


def test_fallback_world_spec_tolerates_category_with_single_world_rule() -> None:
    project = build_project()
    project.genre = "都市"
    premise = "女主在直播间被全网误解后，靠证据和专业反击。"
    book_spec = planner_services._fallback_book_spec(
        project,
        premise,
        category_key="urban-contemporary",
    )

    world_spec = planner_services._fallback_world_spec(
        project,
        premise,
        book_spec,
        category_key="urban-contemporary",
    )

    rule_ids = {rule["rule_id"] for rule in world_spec["rules"]}
    assert len(world_spec["locations"]) >= 3
    assert len(world_spec["rules"]) >= 3
    assert world_spec["locations"][0]["name"] == "口碑回流规则"
    assert {"R001", "R002", "R003"}.issubset(rule_ids)


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


def test_planner_repairs_book_spec_default_protagonist_name_drift() -> None:
    project = build_project()
    project.language = "zh-CN"
    payload = {
        "title": "规则生存重启验证案卷",
        "protagonist": {
            "name": "沈照",
            "external_goal": "主角必须在六章内证明规则提示是免责剧本。",
        },
        "stakes": {"personal": "主角如果失败，同伴会被规则抹名。"},
    }

    repaired = planner_services._repair_protagonist_name_drift_for_planner(
        project,
        payload,
        protagonist_name="沈照",
        artifact_type="book_spec",
    )

    assert "主角" not in json.dumps(repaired, ensure_ascii=False)
    assert repaired["protagonist"]["external_goal"].startswith("沈照必须")
    assert repaired["_meta"]["name_drift_repair"][0]["replacement_count"] == 2


def test_planner_repairs_cast_spec_default_protagonist_name_drift() -> None:
    project = build_project()
    project.language = "zh-CN"
    payload = {
        "protagonist": {
            "name": "沈照",
            "role": "protagonist",
            "goal": "主角必须不断变强，获取足够力量。",
            "background": "主角曾在旧教学楼体系工作。",
        },
        "supporting_cast": [],
    }

    repaired = planner_services._repair_protagonist_name_drift_for_planner(
        project,
        payload,
        protagonist_name="沈照",
        artifact_type="cast_spec",
    )

    assert "主角" not in json.dumps(repaired, ensure_ascii=False)
    assert repaired["protagonist"]["goal"].startswith("沈照必须")
    assert repaired["_meta"]["name_drift_repair"][0]["artifact_type"] == "cast_spec"


def test_planner_repairs_rule_survival_book_goal_drift() -> None:
    project = build_project()
    project.language = "zh-CN"
    project.genre = "规则生存 / meta博弈"
    premise = "沈照必须用规则之间的矛盾破局，证明通关提示是免责剧本。"
    payload = {
        "protagonist": {
            "name": "沈照",
            "archetype": "求力者",
            "core_wound": "曾因力量不足失去重要的人。",
            "external_goal": "沈照必须不断变强，获取足够的力量来保护想保护的一切。",
            "internal_need": "沈照需要学会接受力量的代价，理解强大不等于正确。",
        }
    }

    repaired = planner_services._repair_rule_survival_goal_drift_for_planner(
        project,
        payload,
        premise=premise,
        artifact_type="book_spec",
    )

    protagonist = repaired["protagonist"]
    assert protagonist["archetype"] == "规则破局者"
    assert "免责剧本" in protagonist["external_goal"]
    assert "不断变强" not in json.dumps(repaired, ensure_ascii=False)
    assert repaired["_meta"]["rule_survival_goal_repair"][0]["artifact_type"] == "book_spec"


def test_planner_repairs_rule_survival_cast_goal_drift() -> None:
    project = build_project()
    project.language = "zh-CN"
    project.genre = "规则怪谈"
    premise = "沈照要拆穿旧教学楼规则提示里的免责剧本。"
    payload = {
        "protagonist": {
            "name": "沈照",
            "goal": "沈照必须不断变强，获取足够力量。",
            "golden_finger": None,
        },
        "supporting_cast": [],
    }

    repaired = planner_services._repair_rule_survival_goal_drift_for_planner(
        project,
        payload,
        premise=premise,
        artifact_type="cast_spec",
    )

    protagonist = repaired["protagonist"]
    assert "免责剧本" in protagonist["goal"]
    assert protagonist["golden_finger"] == "能捕捉规则改写前后的矛盾痕迹。"
    assert "不断变强" not in json.dumps(repaired, ensure_ascii=False)


def test_short_volume_outline_prompt_uses_compact_context() -> None:
    project = build_project()
    project.language = "zh-CN"
    project.genre = "规则生存 / meta博弈"
    project.target_chapters = 6
    project.metadata_json = {
        "story_design_kernel": {"long_field": "规则结构" * 2000},
        "emotion_driven_kernel": {"long_field": "情绪结构" * 2000},
        "public_emotion_kernel": {"long_field": "公众情绪" * 2000},
        "entry_system_kernel": {"long_field": "入口系统" * 2000},
    }

    _, user_prompt = planner_services._volume_outline_prompts(
        project,
        {"protagonist": {"name": "沈照", "external_goal": "拆穿免责剧本"}},
        {"protagonist": {"name": "沈照", "goal": "拆穿免责剧本"}},
        [{"volume_number": 1, "chapter_count_target": 6}],
        {"volume_number": 1, "chapter_count_target": 6, "chapter_range": "1-6"},
    )

    assert "【压缩核心执行上下文】" in user_prompt
    assert "每章默认只需 2 个紧凑 scenes" in user_prompt
    assert len(user_prompt) < 13000
    assert user_prompt.count("规则结构") < 100


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
    assert "并非表面原因" not in cast_spec["protagonist"]["secret"]
    assert "改写记录" in cast_spec["protagonist"]["secret"]
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


def test_chapter_outline_normalizes_structured_information_gap_mode() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "opening",
            "chapters": [
                {
                    "chapter_number": 1,
                    "chapter_title": "司命入职",
                    "goal": "沈照把神仙召进人力系统。",
                    "information_gap_mode": {
                        "gap_type": "partial_reveal",
                        "reason": "读者知道香火 KPI 异常，但沈照只看见入职表。",
                    },
                    "scenes": [],
                },
                {
                    "chapter_number": 2,
                    "chapter_title": "土地爷工牌",
                    "goal": "沈照发现土地爷刷不了门禁。",
                    "info_gap_mode": {"mode": "withhold", "note": "暂不暴露工牌失效来源。"},
                    "scenes": [],
                },
            ],
        }
    )

    assert batch.chapters[0].information_gap_mode == "partial_reveal"
    assert batch.chapters[1].information_gap_mode == "withhold"


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


def test_generated_outline_missing_title_soft_fills_from_goal_seed() -> None:
    """A missing title with a usable goal/conflict seed is soft-filled, not raised.

    2026-07-09 remediation intentionally reversed the old "no fallback
    synthesis" contract for this specific case: a pure-empty title used to
    kill the whole book even when the model left a perfectly usable goal
    sentence right next to it. See test_outline_title_soft_fill.py for the
    genuine hard-fail case (no seed of any kind available).
    """
    chapters = [{"chapter_number": 7, "goal": "苏砚追到镜铺后门。"}]
    planner_services._normalize_generated_outline_titles_or_fail(
        chapters,
        logical_name="volume_1_chapter_outline",
    )
    assert chapters[0]["title"]
    assert chapters[0]["_meta"]["title_soft_filled"] is True


def test_generated_outline_missing_title_fails_without_any_seed() -> None:
    with pytest.raises(
        planner_services.PlannerFallbackError, match="omitted concrete chapter titles"
    ):
        planner_services._normalize_generated_outline_titles_or_fail(
            [{"chapter_number": 7}],
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
                "goal": "建立沈青崖的调查角色，完善阴阳交界世界观体系。",
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
    assert "建立沈青崖" not in repaired["chapters"][0]["goal"]
    assert repaired["chapters"][0]["goal"].startswith("李宅血雨")
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
            "chapter_number": 1,
            "title": "井底回声",
            "goal": "沈青崖追查井底异响，必须在封门前找到血雨源头。",
            "main_conflict": "巡捕房误封现场，沈青崖必须避开阻拦读取井底痕迹。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    # 黄金三章批次校验复用下游 golden_three_solo_scene_chain 判定：
                    # 全 solo 场景链会被硬拦，这里给场景配第二位 cast 在场者。
                    "participants": ["沈青崖", "阿洛"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，阿洛在巷口望风，两人付出暴露行踪的代价。",
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


def _single_chapter_effect_outline(*, primary=None, secondary=None):
    selected = {
        key: value
        for key, value in (("primary", primary), ("secondary", secondary))
        if value
    }
    return [
        {
            "chapter_number": 1,
            "title": "井底回声",
            "goal": "沈青崖追查井底异响，必须在封门前找到血雨源头。",
            "main_conflict": "巡捕房误封现场，沈青崖必须避开阻拦读取井底痕迹。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "selected_effect_skills": selected,
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    "participants": ["沈青崖", "阿洛"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，阿洛在巷口望风，两人付出暴露行踪的代价。",
                        "emotion": "压力上升。",
                    },
                }
            ],
        }
    ]


def _effect_outline_cast_spec():
    return {
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


def test_volume_outline_rejects_unrouted_creation_effects() -> None:
    project = build_project()
    project.metadata_json = {
        "story_enhancers": {
            "effect_skills": ["comedy_engine", "hype_satisfaction_engine"]
        }
    }

    with pytest.raises(
        planner_services.StoryEnhancerCoverageError,
        match="STORY_ENHANCER_DISTRIBUTION_MISSING.*comedy_engine=0%.*hype_satisfaction_engine=0%",
    ) as exc_info:
        planner_services._validate_generated_volume_outline_or_raise(
            _single_chapter_effect_outline(),
            project=project,
            logical_name="volume_1_chapter_outline",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=1,
            cast_spec=_effect_outline_cast_spec(),
        )

    assert len(exc_info.value.directives) == 2
    assert any("comedy_engine" in item for item in exc_info.value.directives)
    assert any("hype_satisfaction_engine" in item for item in exc_info.value.directives)


def test_volume_outline_accepts_creation_effects_distributed_as_primary_secondary() -> None:
    project = build_project()
    project.metadata_json = {
        "story_enhancers": {
            "effect_skills": ["comedy_engine", "hype_satisfaction_engine"]
        }
    }

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        _single_chapter_effect_outline(
            primary="comedy_engine", secondary="hype_satisfaction_engine"
        ),
        project=project,
        logical_name="volume_1_chapter_outline",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=_effect_outline_cast_spec(),
    )

    selected = repaired["chapters"][0]["selected_effect_skills"]
    assert selected["primary"] == "comedy_engine"
    assert selected["secondary"] == "hype_satisfaction_engine"


def test_story_enhancer_route_patch_adds_structured_route_and_concrete_scene_beat() -> None:
    payload = {"chapters": _single_chapter_effect_outline()}
    repaired = planner_services._apply_story_enhancer_route_patches(
        payload,
        [
            {
                "chapter_number": 1,
                "scene_number": 1,
                "effect": "comedy_engine",
                "slot": "primary",
                "reason": "剑灵误把威胁说成老匠人的欠账，形成处境反差。",
                "growth_stage_fit": "主角初次学会借剑身传声。",
                "beat": "沈青崖试图借剑鸣示警，出口却变成催老匠人还三百年前的酒钱。",
            }
        ],
        selection=StoryEnhancerSelection(effect_skills=("comedy_engine",)),
    )

    chapter = repaired["chapters"][0]
    selected = chapter["selected_effect_skills"]
    assert selected["primary"] == "comedy_engine"
    assert selected["expected_contracts"]["comedy_engine"]["concrete_beat"].startswith(
        "沈青崖"
    )
    assert "催老匠人还三百年前的酒钱" in chapter["scenes"][0]["purpose"]["story"]


def test_story_enhancer_route_patch_replaces_unselected_occupied_slot() -> None:
    payload = {
        "chapters": _single_chapter_effect_outline(
            primary="tension_pressure_engine",
            secondary="suspense_reveal_engine",
        )
    }
    repaired = planner_services._apply_story_enhancer_route_patches(
        payload,
        [
            {
                "effect": "comedy_engine",
                "chapter_number": 1,
                "scene_number": 1,
                "beat": "主角顺着制度漏洞反问查验者，让僵持现场出现行动反差。",
            }
        ],
        selection=StoryEnhancerSelection(effect_skills=("comedy_engine",)),
    )

    selected = repaired["chapters"][0]["selected_effect_skills"]
    assert selected["primary"] == "tension_pressure_engine"
    assert selected["secondary"] == "comedy_engine"
    assert selected["expected_contracts"]["comedy_engine"]["concrete_beat"]


@pytest.mark.asyncio
async def test_story_enhancer_route_retries_only_residual_and_uses_safe_placement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json["story_enhancers"] = {
        "effect_skills": ["comedy_engine", "hype_satisfaction_engine"]
    }
    payload = {
        "chapters": [
            {
                "chapter_number": 1,
                "title": "一",
                "unrelated": {"keep": True},
                "selected_effect_skills": {"primary": "existing"},
                "scenes": [{"scene_number": 1, "purpose": {"story": "原场景"}}],
            },
            {
                "chapter_number": 2,
                "title": "二",
                "selected_effect_skills": {},
                "scenes": [{"scene_number": 1, "purpose": {"story": "第二场景"}}],
            },
        ],
        "preserve_me": "yes",
    }
    calls: list[list[str]] = []

    async def fake_complete_text(session: object, settings: object, request: object):
        effects = json.loads(request.metadata["missing_effects"] if isinstance(request.metadata["missing_effects"], str) else json.dumps(request.metadata["missing_effects"]))
        calls.append(effects)
        patches = (
            [
                {"effect": "comedy_engine", "chapter_number": 999, "slot": "bogus", "scene_number": 999, "beat": "主角误把敌人的威胁当成收费通知，当众讨价还价化解追兵。"},
                {"effect": "hype_satisfaction_engine", "chapter_number": 1, "slot": "primary", "beat": ""},
            ]
            if len(calls) == 1
            else [
                {"effect": "hype_satisfaction_engine", "chapter_number": 999, "slot": "bogus", "scene_number": 999, "beat": "主角在封锁线前公开亮出证据，迫使敌方首领当众撤令。"}
            ]
        )
        return type("CompletionStub", (), {"content": json.dumps({"patches": patches}, ensure_ascii=False), "llm_run_id": uuid4(), "provider": "openai"})()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)
    repaired, run_id = await planner_services._repair_story_enhancer_distribution(
        FakeSession(), build_settings(), project=project, workflow_run_id=uuid4(), logical_name="volume_1", payload=payload
    )
    assert calls == [["comedy_engine", "hype_satisfaction_engine"], ["hype_satisfaction_engine"]]
    assert run_id is not None
    assert repaired["preserve_me"] == "yes"
    assert repaired["chapters"][0]["unrelated"] == {"keep": True}
    assert repaired["chapters"][0]["selected_effect_skills"]["secondary"] == "comedy_engine"
    assert repaired["chapters"][1]["selected_effect_skills"]["primary"] == "hype_satisfaction_engine"


@pytest.mark.asyncio
async def test_story_enhancer_route_exhausted_residual_fails_closed_with_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json["story_enhancers"] = {"effect_skills": ["comedy_engine"]}
    payload = {"chapters": [{"chapter_number": 1, "selected_effect_skills": {"primary": "full", "secondary": "full2"}, "scenes": []}]}
    run_ids = [uuid4(), uuid4(), uuid4()]
    calls = 0

    async def fake_complete_text(session: object, settings: object, request: object):
        nonlocal calls
        calls += 1
        return type("CompletionStub", (), {"content": '{"patches": []}', "llm_run_id": run_ids[calls - 1], "provider": "openai"})()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)
    with pytest.raises(planner_services.StoryEnhancerCoverageError, match="llm_run_id="):
        await planner_services._repair_story_enhancer_distribution(
            FakeSession(), build_settings(), project=project, workflow_run_id=uuid4(), logical_name="volume_1", payload=payload
        )
    assert calls == 3


def test_volume_outline_requires_selected_brainhole_contract_when_selected() -> None:
    project = build_project()
    project.metadata_json = {
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": "brainhole_engine",
                "secondary": "comedy_engine",
            }
        }
    }
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
                    "participants": ["沈青崖", "阿洛"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，阿洛在巷口望风，两人付出暴露行踪的代价。",
                        "emotion": "压力上升。",
                    },
                },
            ],
        }
    ]

    with pytest.raises(
        planner_services.PlannerFallbackError,
        match="STORY_EFFECT_CONTRACT_MISSING",
    ):
        planner_services._validate_generated_volume_outline_or_raise(
            payload,
            project=project,
            logical_name="volume_1_chapter_outline_batch_1_5",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=1,
            cast_spec=cast_spec,
        )


def test_volume_outline_repairs_purpose_character_into_participants() -> None:
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
            "opening_situation": "李宅封门前，沈青崖按住井沿，井底传来断续敲击声。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "causal_contract": {
                "pressure": "封门队伍已经到巷口。",
                "protagonist_choice": "沈青崖决定先让阿洛望风，再下井取证。",
                "resistance": "巡捕房误封现场。",
                "cost_or_tradeoff": "他必须暴露自己还在追查旧案。",
                "gain_or_reveal": "他拿到铜钱旧印。",
                "state_change": "沈青崖从旁观旧案转为正式介入。",
                "next_reader_desire": "铜钱是谁故意丢进井里的？",
            },
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    "participants": ["沈青崖"],
                    "purpose": {
                        "story": "沈青崖让阿洛在井口望风，自己下井取出铜钱。",
                        "emotion": "压力上升。",
                    },
                },
            ],
        }
    ]

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        payload,
        project=project,
        logical_name="volume_1_chapter_outline_batch_1_5",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=cast_spec,
    )

    participants = repaired["chapters"][0]["scenes"][0]["participants"]
    assert participants == ["沈青崖", "阿洛"]


def test_volume_outline_preserves_selected_brainhole_contract_when_valid() -> None:
    project = build_project()
    project.metadata_json = {
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": "brainhole_engine",
                "secondary": "comedy_engine",
            }
        }
    }
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
    brainhole_contract = {
        "one_sentence_sell": "沈青崖把井底回声当成离职面谈，问出铜钱旧案。",
        "character_core_used": "沈青崖遇事先查证，不靠情绪判断。",
        "modern_system": "离职面谈与工单登记。",
        "contrast_mechanism": "古宅怨声被纳入现代流程，荒诞但能推进取证。",
        "visible_comedy": "阿洛在井口替怨声排队取号。",
        "serious_underbelly": "失踪者的声音被系统吞掉，暴露旧案未结。",
        "plot_consequence": "沈青崖拿到铜钱线索，下一章必须查登记源头。",
        "protagonist_decision": "先登记证据，再冒险下井。",
        "growth_stage_fit": "opening: observe/interview/recommend。",
        "risk_check": "不破坏角色人设，不让主角提前拥有制度权力。",
    }
    payload = [
        {
            "title": "井底回声",
            "goal": "沈青崖追查井底异响，必须在封门前找到血雨源头。",
            "main_conflict": "巡捕房误封现场，沈青崖必须避开阻拦读取井底痕迹。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "selected_effect_skills": {
                "primary": "brainhole_engine",
                "reason": "古宅怨声和现代工单形成反差。",
                "growth_stage_fit": "opening observe。",
                "expected_contracts": ["brainhole_contract"],
            },
            "brainhole_contract": brainhole_contract,
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    "participants": ["沈青崖", "阿洛"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，阿洛在巷口望风，两人付出暴露行踪的代价。",
                        "emotion": "压力上升。",
                    },
                },
            ],
        }
    ]

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        payload,
        project=project,
        logical_name="volume_1_chapter_outline_batch_1_5",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=cast_spec,
    )

    chapter = repaired["chapters"][0]
    assert chapter["selected_effect_skills"]["primary"] == "brainhole_engine"
    assert "secondary" not in chapter["selected_effect_skills"]
    assert chapter["brainhole_contract"] == brainhole_contract


def test_volume_outline_accepts_nested_brainhole_skill_selection() -> None:
    project = build_project()
    project.metadata_json = {
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": "brainhole_engine",
                "secondary": "comedy_engine",
            }
        }
    }
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
    brainhole_contract = {
        "one_sentence_sell": "沈青崖把井底回声当成离职面谈，问出铜钱旧案。",
        "character_core_used": "沈青崖遇事先查证，不靠情绪判断。",
        "modern_system": "离职面谈与工单登记。",
        "contrast_mechanism": "古宅怨声被纳入现代流程，荒诞但能推进取证。",
        "visible_comedy": "阿洛在井口替怨声排队取号。",
        "serious_underbelly": "失踪者的声音被系统吞掉，暴露旧案未结。",
        "plot_consequence": "沈青崖拿到铜钱线索，下一章必须查登记源头。",
        "protagonist_decision": "先登记证据，再冒险下井。",
        "growth_stage_fit": "opening: observe/interview/recommend。",
        "risk_check": "不破坏角色人设，不让主角提前拥有制度权力。",
    }
    payload = [
        {
            "title": "井底回声",
            "goal": "沈青崖追查井底异响，必须在封门前找到血雨源头。",
            "main_conflict": "巡捕房误封现场，沈青崖必须避开阻拦读取井底痕迹。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "selected_effect_skills": {
                "primary": {
                    "name": "brainhole_engine",
                    "reason": "古宅怨声和现代工单形成反差。",
                },
                "expected_contracts": ["brainhole_contract"],
            },
            "brainhole_contract": brainhole_contract,
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    "participants": ["沈青崖", "阿洛"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，付出暴露行踪的代价。",
                        "emotion": "压力上升。",
                    },
                },
            ],
        }
    ]

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        payload,
        project=project,
        logical_name="volume_1_chapter_outline_batch_1_5",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=cast_spec,
    )

    chapter = repaired["chapters"][0]
    assert chapter["selected_effect_skills"]["primary"] == "brainhole_engine"
    assert chapter["selected_effect_skills"]["reason"] == "古宅怨声和现代工单形成反差。"
    assert chapter["brainhole_contract"] == brainhole_contract


def test_volume_outline_normalizes_brainhole_selection_from_complete_contract() -> None:
    project = build_project()
    project.metadata_json = {
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": "brainhole_engine",
                "secondary": "comedy_engine",
            }
        }
    }
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
    brainhole_contract = {
        "one_sentence_sell": "沈青崖把井底回声当成离职面谈，问出铜钱旧案。",
        "character_core_used": "沈青崖遇事先查证，不靠情绪判断。",
        "modern_system": "离职面谈与工单登记。",
        "contrast_mechanism": "古宅怨声被纳入现代流程，荒诞但能推进取证。",
        "visible_comedy": "阿洛在井口替怨声排队取号。",
        "serious_underbelly": "失踪者的声音被系统吞掉，暴露旧案未结。",
        "plot_consequence": "沈青崖拿到铜钱线索，下一章必须查登记源头。",
        "protagonist_decision": "先登记证据，再冒险下井。",
        "growth_stage_fit": "opening: observe/interview/recommend。",
        "risk_check": "不破坏角色人设，不让主角提前拥有制度权力。",
    }
    payload = [
        {
            "title": "井底回声",
            "goal": "沈青崖追查井底异响，必须在封门前找到血雨源头。",
            "main_conflict": "巡捕房误封现场，沈青崖必须避开阻拦读取井底痕迹。",
            "hook_description": "井底浮出一枚刻着沈家旧印的铜钱。",
            "selected_effect_skills": {
                "primary": {
                    "name": "suspense_reveal_engine",
                    "reason": "章尾用旧印做悬疑钩子。",
                },
                "secondary": "relationship_chemistry_engine",
            },
            "brainhole_contract": brainhole_contract,
            "scenes": [
                {
                    "scene_number": 1,
                    "time_label": "李宅封门前",
                    "participants": ["沈青崖", "阿洛"],
                    "purpose": {
                        "story": "沈青崖撬开井盖，付出暴露行踪的代价。",
                        "emotion": "压力上升。",
                    },
                },
            ],
        }
    ]

    repaired = planner_services._validate_generated_volume_outline_or_raise(
        payload,
        project=project,
        logical_name="volume_1_chapter_outline_batch_1_5",
        volume_number=1,
        expected_count=1,
        chapter_number_offset=1,
        cast_spec=cast_spec,
    )

    chapter = repaired["chapters"][0]
    assert chapter["selected_effect_skills"]["primary"] == "brainhole_engine"
    assert chapter["selected_effect_skills"]["_normalized_from_selected_effect_skills"][
        "primary"
    ] == "suspense_reveal_engine"
    assert chapter["brainhole_contract"] == brainhole_contract


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
    monkeypatch.setattr(
        planner_services,
        "_repair_generated_volume_outline_contract_blocks",
        lambda *args, **kwargs: 0,
    )

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
    progress_events: list[tuple[str, dict[str, object]]] = []

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
        progress=lambda stage, payload: progress_events.append((stage, payload)),
    )

    assert llm_run_id is not None
    assert payload["chapters"][0]["hook_description"] != "具体事件是「尾钩」。"
    assert len(prompts) == 2
    assert "PLAN_CHAPTER_HOOK_GENERIC" in prompts[1]
    assert history[0]["status"] == "failed"
    assert history[-1]["status"] == "passed"
    assert [event[0] for event in progress_events] == [
        "planning_outline_attempt_started",
        "planning_outline_attempt_failed",
        "planning_outline_attempt_started",
        "planning_outline_attempt_completed",
    ]


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
    project.metadata_json = {
        "premise": "沈砚，一名追查失踪星图的巡航员，被迫穿越封锁航道。"
    }
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
async def test_generate_structured_artifact_never_merges_fallback_into_fail_closed_story_truth(
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
    assert "external_goal" not in payload["protagonist"]


def test_minimal_cost_cast_prompt_keeps_full_characterisation() -> None:
    """2026-08-02: 纯爽 no longer amputates the cast prompt.

    The deleted apparatus cut the personhood and villain-charisma sections out
    of the prompt and appended a contract forbidding childhood incidents,
    family history, hidden origins, prior failures and private wounds — for a
    book whose only crime was ticking a pacing preference. Characters need
    histories in every kind of story.
    """
    project = build_project()
    project.metadata_json["story_enhancers"] = {"cost_style": "minimal"}
    premise = "少年守住废药园，把苏醒的灵药拿来换钱。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)

    _system, prompt = planner_services._cast_spec_prompts(
        project,
        book_spec,
        world_spec,
    )

    assert "最终人物事实源契约" not in prompt
    assert "来源受限的人格合同" not in prompt

    # And the same prompt for a standard-cost book is now identical in shape.
    standard = build_project()
    standard.metadata_json["story_enhancers"] = {"cost_style": "standard"}
    _system2, standard_prompt = planner_services._cast_spec_prompts(
        standard,
        planner_services._fallback_book_spec(standard, premise),
        world_spec,
    )
    assert ("人格底层" in prompt) == ("人格底层" in standard_prompt)


def test_minimal_cast_is_compiled_only_from_approved_design() -> None:
    project = build_project()
    project.genre = "东方玄幻"
    project.sub_genre = "东方玄幻"
    project.metadata_json.update(
        {
            "story_enhancers": {"cost_style": "minimal"},
            "creation_intent_contract": {"audience_orientation": "male"},
        }
    )
    premise = "陆沉接手废药园，发现杂草是上古灵药，一边装废物，一边拿灵药喂猪。"
    lock_design_snapshot(
        project,
        protagonist_name="陆沉",
        reader_promise=premise,
        core_story_engine="每次灵药产出都会改变资源与下一轮外部试探",
        effect_skills=["comedy_engine", "hype_satisfaction_engine"],
    )
    book_spec = {
        "title": "我的废药园通神了",
        "tone": "轻松爽文",
        "logline": premise,
        "unique_hook": "灵药只认陆沉的浇水节奏与搭话。",
        "theme_statement": "把根扎稳，让所有人离不开自己。",
        "dramatic_question": "陆沉能否守住废园？",
        "protagonist": {
            "name": "陆沉",
            "archetype": "底层扮猪吃虎型经营主角",
            "outer_motivation": "盘活废园",
            "core_drive": "守住废园",
            "weakness": "根骨垫底，正面战力弱",
            "secret": "能按节奏唤醒灵药",
            "growth_curve": "从看园人到药市中间人",
        },
        "stakes": {
            "personal": "失去废园",
            "interpersonal": "能力暴露会被争抢",
        },
        "series_engine": {
            "core_serial_engine": "灵药换资源、地盘扩张、对手升级",
            "repeatable_story_unit": "每次出货都会引来更精明的试探",
        },
        "power_system": {
            "upgrade_engine": {"core_mechanism": "灵药苏醒反哺根骨"},
            "antagonist_ladder": [
                {"tier": "Tier-1 散户试探", "tactic": "低价收药并窥探废园"},
                {"tier": "Tier-2 药商压价", "tactic": "联合压价并争夺货源"},
            ],
        },
    }
    world_spec = {
        "world_premise": "陆沉是苍梧宗记名弟子和废药园看园人。",
        "power_system": {
            "protagonist_starting_tier": "枯草期",
            "hard_limits": "只认陆沉，不能外包",
        },
    }

    cast = planner_services._compile_source_bound_cast_spec(
        project,
        premise,
        book_spec,
        world_spec,
    )

    assert planner_services._source_bound_cast_enabled(project) is True
    assert cast["_meta"]["source_compiler"] == "approved-design-cast.v1"
    assert cast["protagonist"]["name"] == "陆沉"
    assert cast["protagonist"]["age"] is None
    assert cast["protagonist"]["family_imprint"]["family_secrets"] == []
    assert cast["protagonist"]["life_history"]["trauma"] == []
    assert cast["antagonist"] is None
    assert cast["supporting_cast"] == []
    assert [item["name"] for item in cast["antagonist_forces"]] == [
        "散户试探",
        "药商压价",
    ]
    assert cast["conflict_map"] == []
    from bestseller.services.bible_gate import (
        build_draft_from_materialization_content,
        validate_bible_completeness,
    )
    from bestseller.services.invariants import seed_invariants

    report = validate_bible_completeness(
        build_draft_from_materialization_content(
            book_spec_content=book_spec,
            world_spec_content=world_spec,
            cast_spec_content=cast,
        ),
        seed_invariants(
            project_id=project.id,
            language=project.language,
            words_per_chapter=None,
            genre=project.genre,
            sub_genre=project.sub_genre,
        ),
    )
    assert not {
        item.code for item in report.deficiencies
    }.intersection(planner_services._CAST_PERSONHOOD_REPAIR_CODES)
    serialized = json.dumps(cast, ensure_ascii=False)
    for polluted in (
        "尸体",
        "墓穴",
        "旧债",
        "血债",
        "账房",
        "账本",
        "童年",
        "父亲",
        "母亲",
        "家族秘密",
        "反噬",
        "寿元",
    ):
        assert polluted not in serialized

    emotion = planner_services._compile_source_bound_emotion_driven_kernel(
        project,
        premise,
        book_spec,
        world_spec,
        cast,
    )
    assert emotion["_meta"]["source_compiler"] == "approved-design-emotion.v1"
    assert emotion["antagonist_moral_contracts"] == []
    assert emotion["empathy_contracts"][0]["current_desire"] == "盘活废园"
    emotion_serialized = json.dumps(emotion, ensure_ascii=False)
    for polluted in (
        "尸体",
        "墓穴",
        "旧债",
        "血债",
        "账房",
        "账本",
        "童年",
        "父亲",
        "母亲",
        "崩塌伤口",
        "墓志铭",
        "反噬",
        "寿命",
        "寿元",
    ):
        assert polluted not in emotion_serialized


def test_source_bound_planning_spine_is_domain_neutral_and_snapshot_only() -> None:
    project = build_project()
    project.title = "回声航线"
    project.genre = "科幻"
    project.sub_genre = "太空冒险"
    project.target_chapters = 50
    project.target_word_count = 100000
    premise = "巡航员沈砚发现封锁航线会回应他的导航脉冲，每次修复都会让下一段航路主动改道。"
    engine = "每轮用导航脉冲确认一段航路、取得通行成果，并面对依据公开航迹升级的阻力。"
    project.metadata_json["story_enhancers"] = {
        "cost_style": "minimal",
        "effect_skills": ["hype_satisfaction_engine"],
    }
    lock_design_snapshot(
        project,
        protagonist_name="沈砚",
        reader_promise=premise,
        core_story_engine=engine,
        effect_skills=["hype_satisfaction_engine"],
    )

    book = planner_services._compile_source_bound_book_spec(project, premise)
    book = planner_services._ensure_book_spec_bible_fields(project, premise, book)
    world = planner_services._compile_source_bound_world_spec(project, premise, book)
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    emotion = planner_services._compile_source_bound_emotion_driven_kernel(
        project,
        premise,
        book,
        world,
        cast,
    )
    volumes = planner_services._compile_source_bound_volume_plan(
        project,
        premise,
        book,
        world,
        cast,
    )
    disclosure = planner_services._compile_source_bound_world_disclosure(
        project,
        volumes[0],
    )

    assert planner_services._source_bound_cast_enabled(project) is True
    assert book["_meta"]["source_compiler"] == "approved-design-book.v1"
    assert world["_meta"]["source_compiler"] == "approved-design-world.v1"
    assert cast["_meta"]["source_compiler"] == "approved-design-cast.v1"
    assert emotion["_meta"]["source_compiler"] == "approved-design-emotion.v1"
    assert volumes[0]["_meta"]["source_compiler"] == "approved-design-volume.v1"
    assert disclosure["_meta"]["source_compiler"] == "approved-design-world-disclosure.v1"
    assert book["naming_pool"] == ["沈砚"]
    assert world["history_key_events"] == []
    assert disclosure["new_locations"] == []
    assert disclosure["new_rules_revealed"] == []
    serialized = json.dumps(
        [book, world, cast, emotion, volumes, disclosure],
        ensure_ascii=False,
    )
    assert premise in serialized
    assert engine in serialized
    for unrelated_domain_seed in (
        "废药园",
        "灵药",
        "药畦",
        "猪圈",
        "异变猪",
        "浇水",
        "地契",
        "账本",
        "尸体",
        "旧灵脉",
        "封园事件",
        "老瞎子",
        "碎木牌",
        "童年",
        "父母",
    ):
        assert unrelated_domain_seed not in serialized


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
async def test_generate_structured_artifact_retries_truncated_fail_closed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fallback_book_spec = planner_services._fallback_book_spec(
        project,
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
    )
    calls: list[str] = []

    async def fake_complete_text(session: object, settings: object, request: object):
        calls.append(request.user_prompt)
        if len(calls) == 1:
            return type(
                "CompletionStub",
                (),
                {
                    "content": json.dumps({"title": "truncated"}, ensure_ascii=False),
                    "llm_run_id": uuid4(),
                    "finish_reason": "length",
                    "provider": "openai",
                },
            )()
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({"title": "Compact Book"}, ensure_ascii=False),
                "llm_run_id": uuid4(),
                "finish_reason": "stop",
                "provider": "openai",
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
    assert payload["title"] == "Compact Book"
    assert len(calls) == 2
    assert "STRICT RETRY AFTER TRUNCATED OUTPUT" in calls[1]


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
async def test_record_planner_failure_step_once_skips_existing_step() -> None:
    class ExistingStepSession(FakeSession):
        async def scalar(self, _stmt: object):
            return uuid4()

    session = ExistingStepSession()

    await planner_services._record_planner_failure_step_once(
        session,
        workflow_run_id=uuid4(),
        step_name="book_spec_quality_gate",
        step_order=4,
        error_message="book_spec_quality_gate failed: english_mechanism_leak",
    )

    assert session.added == []


@pytest.mark.asyncio
async def test_record_planner_failure_step_once_skips_existing_order() -> None:
    class ExistingOrderSession(FakeSession):
        async def scalar(self, _stmt: object):
            return uuid4()

    session = ExistingOrderSession()

    await planner_services._record_planner_failure_step_once(
        session,
        workflow_run_id=uuid4(),
        step_name="generate_story_design_kernel",
        step_order=13,
        error_message="story_design_kernel_gate failed: fallback_source_leak",
    )

    assert session.added == []


@pytest.mark.asyncio
async def test_record_planner_failure_step_once_records_missing_step() -> None:
    session = FakeSession()

    await planner_services._record_planner_failure_step_once(
        session,
        workflow_run_id=uuid4(),
        step_name="book_spec_quality_gate",
        step_order=4,
        error_message="book_spec_quality_gate failed: english_mechanism_leak",
    )

    workflow_steps = [item for item in session.added if isinstance(item, WorkflowStepRunModel)]
    assert len(workflow_steps) == 1
    assert workflow_steps[0].step_name == "book_spec_quality_gate"
    assert workflow_steps[0].status == "failed"
    assert workflow_steps[0].error_message == (
        "book_spec_quality_gate failed: english_mechanism_leak"
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
        if "chapter_outline" in logical_name and isinstance(fallback_payload, dict):
            payload = json.loads(json.dumps(fallback_payload, ensure_ascii=False))
            for chapter in payload.get("chapters", []):
                chapter_number = int(chapter.get("chapter_number") or 0)
                chapter["chapter_goal"] = (
                    f"沈砚在第{chapter_number}座航标熄灭前取回对应的航线记录。"
                )
                chapter["opening_situation"] = (
                    f"第{chapter_number}座航标突然熄灭，巡逻船正驶向沈砚的藏身处。"
                )
                chapter["main_conflict"] = (
                    f"港务官封锁第{chapter_number}号栈桥，沈砚必须改变取证路线。"
                )
                chapter["event_signature"] = (
                    f"航标-{chapter_number}-封锁-{chapter_number + 1}"
                )
                chapter["hook_description"] = (
                    f"记录背面出现只通往第{chapter_number + 1}座航标的新坐标。"
                )
            return payload, uuid4()
        return fallback_payload, uuid4()

    def fake_evaluate_worldview_compliance_gate(story_design_kernel, outline_payload):
        # The worldview-compliance gate (deterministic) is exercised by its own suite
        # (test_worldview_compliance_gate). This test only verifies the plan run
        # produces all artifacts + workflow records, so return a passing report rather
        # than evaluating it against the mock structured artifacts. The outer
        # _run_worldview_compliance_gate still runs (it records the workflow step).
        from bestseller.services.worldview_compliance_gate import WorldviewComplianceReport

        return WorldviewComplianceReport(
            passed=True,
            score=100,
            blocking_findings=(),
            warnings=(),
            worldview_snapshot={},
        )

    monkeypatch.setattr(planner_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(planner_services, "import_planning_artifact", fake_import_planning_artifact)
    monkeypatch.setattr(
        planner_services,
        "_generate_structured_artifact",
        fake_generate_structured_artifact,
    )
    monkeypatch.setattr(
        "bestseller.services.worldview_compliance_gate.evaluate_worldview_compliance_gate",
        fake_evaluate_worldview_compliance_gate,
    )

    session = FakeSession()
    settings = build_settings()
    settings.llm.mock = True
    result = await planner_services.generate_novel_plan(
        session,
        settings,
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
        < artifact_types.index(ArtifactType.PUBLIC_EMOTION_KERNEL)
        < artifact_types.index(ArtifactType.COMPLIANCE_BOUNDARY_KERNEL)
        < artifact_types.index(ArtifactType.STORY_DESIGN_KERNEL)
        < artifact_types.index(ArtifactType.EMOTION_DRIVEN_KERNEL)
        < artifact_types.index(ArtifactType.VOLUME_PLAN)
    )
    assert ArtifactType.PUBLIC_EMOTION_KERNEL in artifact_types
    assert ArtifactType.COMPLIANCE_BOUNDARY_KERNEL in artifact_types
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
    assert any(step.step_name == "generate_character_names" for step in workflow_steps)
    step_orders = [
        (step.workflow_run_id, step.step_order)
        for step in workflow_steps
        if step.workflow_run_id is not None
    ]
    assert len(step_orders) == len(set(step_orders))
    assert any(step.step_name == "prewrite_readiness_gate" for step in workflow_steps)
    assert any(step.step_name == "reverse_outline_gate" for step in workflow_steps)
    assert any(step.step_name == "worldview_progression_gate" for step in workflow_steps)
    assert any(step.step_name == "worldview_compliance_gate" for step in workflow_steps)
    assert "story_design_kernel" in project.metadata_json
    assert project.metadata_json["story_design_kernel"]["reverse_outline_status"] == "verified"
    assert "character_drama_map" in project.metadata_json
    assert "emotion_driven_kernel" in project.metadata_json
    assert "public_emotion_kernel" in project.metadata_json
    assert "compliance_boundary_kernel" in project.metadata_json
    assert "planning_kernel" in project.metadata_json
    assert project.metadata_json["reverse_outline_gate_report"]["passed"] is True
    assert "worldview_progression_gate_report" in project.metadata_json
    assert "worldview_compliance_gate_report" in project.metadata_json
    assert project.metadata_json["worldview_compliance_gate_report"]["passed"] is True
    assert project.metadata_json["planning_kernel"]["story_design"]["valid"] is True
    assert project.metadata_json["planning_kernel"]["emotion_driven"]["valid"] is True
    assert "prewrite_readiness_report" in project.metadata_json
    assert "Character Drama Engine" in prompts_by_logical_name["story_design_kernel"]
    assert "public_emotion_core" in prompts_by_logical_name["story_design_kernel"]
    assert "compliance_boundary" in prompts_by_logical_name["story_design_kernel"]
    assert "EmotionDrivenKernel" in prompts_by_logical_name["emotion_driven_kernel"]
    assert "public_emotion_core" in prompts_by_logical_name["emotion_driven_kernel"]
    assert "Story Design Kernel" in prompts_by_logical_name["volume_plan"]
    assert "emotion_driven_core" in prompts_by_logical_name["volume_plan"]
    assert "public_emotion_core" in prompts_by_logical_name["volume_plan"]
    assert "Character Drama Engine" in prompts_by_logical_name["volume_plan"]
    assert "world_state_targets" in prompts_by_logical_name["volume_plan"]
    assert "active_authority_claims" in prompts_by_logical_name["volume_plan"]
    assert "map_function" in prompts_by_logical_name["volume_plan"]
    assert "asset_risk_escalation" in prompts_by_logical_name["volume_plan"]
    assert "reveal_budget" in prompts_by_logical_name["volume_plan"]
    outline_prompt = next(
        prompt
        for logical_name, prompt in prompts_by_logical_name.items()
        if "chapter_outline" in logical_name
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
            "ip_anchor": {
                "quirks": ["整理袖口", "永远戴白手套"],
                "core_wound": "曾亲眼看见混乱航线吞掉家人",
            },
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

    monkeypatch.setattr(
        planner_services,
        "_synthesize_missing_cast_bible_fields",
        lambda _project, payload: payload,
    )

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


@pytest.mark.asyncio
async def test_source_bound_cast_skips_biography_invention_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json.update(
        {
            "story_enhancers": {"cost_style": "minimal"},
            "book_design_snapshot_status": "locked",
            "book_design_snapshot": {
                "snapshot_id": "approved-snapshot",
                "source_hash": "approved-source-hash",
                "protagonist": {"name": "余烬"},
            },
        }
    )
    source_bound_cast = {
        "protagonist": {
            "name": "余烬",
            "role": "protagonist",
            "goal": "利用当旬雷元完成炼器",
            "metadata": {"source_bound_minimal": True},
        },
        "antagonist": None,
        "supporting_cast": [],
        "antagonist_forces": [],
        "conflict_map": [],
    }

    async def unexpected_generate(*args: object, **kwargs: object):
        raise AssertionError("source-bound cast must not enter biography repair")

    monkeypatch.setattr(
        planner_services,
        "_generate_structured_artifact",
        unexpected_generate,
    )

    payload, llm_run_id = await planner_services._repair_cast_personhood_if_needed(
        session=FakeSession(),
        settings=build_settings(),
        project=project,
        book_spec_payload={},
        world_spec_payload={},
        cast_spec_payload=source_bound_cast,
        workflow_run_id=uuid4(),
    )

    assert payload == source_bound_cast
    assert llm_run_id is None


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


def _build_low_pressure_comedy_project() -> ProjectModel:
    """A 社畜摆烂·气运团宠·反向喜剧 that misroutes to the realist
    ``urban-contemporary`` category (reproduces 《福星甩不掉》)."""

    project = ProjectModel(
        slug="fuxing-test",
        title="福星甩不掉",
        genre="都市脑洞",
        target_word_count=80000,
        target_chapters=120,
        audience="番茄都市·年轻读者·轻松解压向",
        metadata_json={"prompt_pack_key": "shezhu-bailan-comedy"},
    )
    project.id = uuid4()
    project.sub_genre = "社畜摆烂·气运团宠·反向喜剧"
    return project


def test_strip_leading_subject_zh_removes_subject_and_modal() -> None:
    # Subject + modal stripped so the clause can embed after another subject.
    assert (
        planner_services._strip_leading_subject_zh(
            "主角要在现实规则中拿到位置", ["江不闲"]
        )
        == "在现实规则中拿到位置"
    )
    assert (
        planner_services._strip_leading_subject_zh("江不闲只想躲清静", ["江不闲"])
        == "只想躲清静"
    )
    # A clause that already starts with a verb is left untouched.
    assert (
        planner_services._strip_leading_subject_zh("追查被篡改的航线记录", ["沈砚"])
        == "追查被篡改的航线记录"
    )


def test_low_pressure_comedy_fallbacks_avoid_realist_subject_mash() -> None:
    """Regression for 《福星甩不掉》: the realist urban-contemporary goal/need
    templates jammed into theme/DQ frames produced ``真正的力量不是逃避主角进入
    行业…`` and ``主角能否在主角要在…的同时，仍然主角需要…``."""

    project = _build_low_pressure_comedy_project()
    premise = "被甩不掉的好运缠上的社畜，越想躲清静越被气运推着办成好事。"
    # The exact realist-misrouted payload that produced the garble.
    realist_payload = {
        "title": "福星甩不掉",
        "themes": ["主角进入行业或组织，拿到一个有限但可撬动的机会。"],
        "protagonist": {
            "name": "主角",
            "external_goal": "主角要在现实规则中拿到足以改变命运的位置。",
            "internal_need": "主角需要学会把人情、专业和底线同时纳入决策。",
        },
        "expected_character_count": 4,
        "naming_pool": ["江不闲"],
    }

    normalized = planner_services._ensure_book_spec_bible_fields(
        project, premise, realist_payload
    )
    theme = normalized["theme_statement"]
    dq = normalized["dramatic_question"]

    # No realist career-goal sentence mashed into the theme frame.
    assert "进入行业或组织" not in theme
    assert "主角" not in theme
    # No double-subject / double-在 mash, no literal 主角 placeholder.
    assert dq.endswith("？")
    assert "主角" not in dq
    assert "主角要在" not in dq
    assert "在在" not in dq


def test_build_protagonist_low_pressure_uses_comedy_goal_need() -> None:
    project = _build_low_pressure_comedy_project()
    book_spec = planner_services._fallback_book_spec(
        project, "社畜被好运缠上", category_key="urban-contemporary"
    )
    protagonist = book_spec["protagonist"]
    # Comedy framing replaces the realist career-ladder templates.
    assert "现实规则中拿到" not in protagonist["external_goal"]
    assert "人情、专业和底线" not in protagonist["internal_need"]


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
        "ABILITY_ORIGIN_CONTRACT_MISSING",
    }.issubset(planner_services._CAST_PERSONHOOD_REPAIR_CODES)


def test_cast_repair_adds_ability_origin_contract_for_power_protagonist() -> None:
    project = build_project()
    project.genre = "都市异能"
    project.sub_genre = "系统流"
    cast_spec = {
        "protagonist": {
            "name": "陆寻",
            "role": "protagonist",
            "gender": "male",
            "goal": "查清天盛集团如何利用底层人的愤怒牟利。",
            "fear": "害怕自己的反击会把无辜者也拖进代价里。",
            "flaw": "一旦被羞辱就容易用更激烈的方式回击。",
            "strength": "情绪能量吸收系统(吸收转化率60%)",
            "golden_finger": "情绪能量吸收系统,在受压迫或愤怒时积累能量并升级异能。",
            "differentiated_advantage": "受压迫越强,吸收速度越快。",
            "metadata": {},
        },
        "supporting_cast": [],
        "antagonist_forces": [],
        "conflict_map": [],
    }

    repaired = planner_services._synthesize_missing_cast_bible_fields(project, cast_spec)

    contract = (
        repaired["protagonist"]["metadata"]["methodology_overlay"]["ability_origin_contract"]
    )
    assert set(planner_services._ABILITY_ORIGIN_CONTRACT_FIELDS).issubset(contract)
    assert all(
        str(contract[field]).strip()
        for field in planner_services._ABILITY_ORIGIN_CONTRACT_FIELDS
    )

    findings = validate_ability_origin_contract(
        character_name="陆寻",
        role="protagonist",
        overlay=repaired["protagonist"]["metadata"]["methodology_overlay"],
        project_genre_text="都市异能 系统流",
    )
    assert findings == []


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


def test_character_decision_intelligence_enters_cast_and_story_design_prompts() -> None:
    project = build_project()
    premise = "一名送葬人每完成一次高危入殓，就会缩短自己的寿命。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

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

    policy = cast_spec["protagonist"]["decision_policy"]
    normalized_cast = planner_services.parse_cast_spec_input(cast_spec).model_dump(mode="json")
    assert policy["character_name"] == cast_spec["protagonist"]["name"]
    assert normalized_cast["protagonist"]["decision_policy"] == policy
    assert "decision_policy" in cast_prompt
    assert "正常人基线" in kernel_prompt
    assert "角色基线" in kernel_prompt
    assert "PROTAGONIST_PLOT_SERVING_STUPIDITY" in kernel_prompt
    assert policy["archetype"] in kernel_prompt
