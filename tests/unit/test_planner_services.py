from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.services import planner as planner_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


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
    payload = planner_services._extract_json_payload("```json\n{\"title\":\"长夜巡航\"}\n```")
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
        "(e.g. {\"scene\": \"incomplete example\"})."
    )
    payload = planner_services._extract_json_payload(raw)
    assert payload == {"chapters": [{"number": 1, "title": "序曲"}]}


def test_extract_json_payload_handles_markdown_fence_without_lang_tag() -> None:
    """Accept bare ``` fences (no ``json`` tag) that MiniMax sometimes emits."""
    raw = "```\n{\"title\": \"长夜巡航\", \"volume\": 5}\n```"
    payload = planner_services._extract_json_payload(raw)
    assert payload["volume"] == 5


def test_extract_json_payload_handles_multiple_fenced_blocks() -> None:
    """When the LLM emits multiple fenced blocks, pick the first balanced one."""
    raw = (
        "First attempt:\n"
        "```json\n{\"chapters\": [{\"number\": 1}]}\n```\n\n"
        "Alternative:\n"
        "```json\n{\"chapters\": [{\"number\": 2}]}\n```\n"
    )
    payload = planner_services._extract_json_payload(raw)
    # Balanced extraction picks up the first JSON object.
    assert payload == {"chapters": [{"number": 1}]}


def test_extract_json_payload_handles_nested_braces_in_strings() -> None:
    """Balanced extractor must respect string literals containing braces."""
    raw = (
        "```json\n"
        '{"outline": "vol 5 chapter 1: the trap uses a glyph like {X}",'
        '"count": 3}\n'
        "```"
    )
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
    assert all(int(m) >= 4 for m in matches), (
        f"planner _max_attempts must be >=4, found {matches}"
    )


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


def test_resolve_fallback_volume_title_cycles_phase_pool() -> None:
    first = planner_services._resolve_fallback_volume_title(
        "power_system_test", 0, 3, is_en=False
    )
    second = planner_services._resolve_fallback_volume_title(
        "power_system_test", 1, 6, is_en=False
    )
    assert first and second and first != second
    assert "第" not in first

    fallback = planner_services._resolve_fallback_volume_title(
        "unknown_phase", 0, 5, is_en=False
    )
    assert fallback == "第5卷"


def test_fallback_volume_plan_produces_distinct_titles_without_milestones() -> None:
    project = build_project()
    project.target_chapters = 1200
    project.target_word_count = 3_600_000
    project.genre = "action-progression"

    book_spec = planner_services._fallback_book_spec(project, "主角逆天改命。")
    world_spec = planner_services._fallback_world_spec(project, "主角逆天改命。", book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, "主角逆天改命。", book_spec, world_spec)

    volume_plan = planner_services._fallback_volume_plan(
        project, book_spec, cast_spec, world_spec, category_key="action-progression"
    )

    titles = [entry["volume_title"] for entry in volume_plan]
    assert len(titles) > 5
    assert all(title for title in titles)
    # No generic "第N卷" placeholder should remain when phase pools exist.
    assert not any(title == f"第{idx+1}卷" for idx, title in enumerate(titles))
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
    assert merged["protagonist"]["external_goal"] == fallback_book_spec["protagonist"]["external_goal"]
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
    assert zoe["metadata"]["role_evolution_normalized_label"] == "From information gatherer to active participant"


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
    assert denise["metadata"]["role_evolution_normalized_label"] == "From hidden observer to field coordinator"


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

    system_prompt, user_prompt = planner_services._book_spec_prompts(  # noqa: SLF001
        project,
        "A royal archivist discovers the crown has been deleting its own bloodline.",
        {},
    )

    assert "English-language commercial fiction planner" in system_prompt
    assert "Project title: Storm Ledger" in user_prompt
    assert "Target chapters: 24" in user_prompt
    assert "Write all planning artifacts in English." in user_prompt
    assert "长篇中文小说" not in system_prompt + user_prompt


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
    assert payload["protagonist"]["external_goal"] == fallback_book_spec["protagonist"]["external_goal"]


@pytest.mark.asyncio
async def test_generate_structured_artifact_uses_fallback_when_validator_rejects(
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

    payload, _ = await planner_services._generate_structured_artifact(
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

    assert payload == fallback_book_spec


@pytest.mark.asyncio
async def test_generate_novel_plan_creates_all_artifacts_and_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        assert slug == "my-story"
        return project

    artifact_counter = 0

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
    ):
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
    assert artifact_types[:6] == [
        ArtifactType.PREMISE,
        ArtifactType.BOOK_SPEC,
        ArtifactType.WORLD_SPEC,
        ArtifactType.CAST_SPEC,
        ArtifactType.VOLUME_PLAN,
        ArtifactType.PLAN_VALIDATION,
    ]
    assert ArtifactType.PROMOTIONAL_BRIEF in artifact_types
    assert ArtifactType.VOLUME_CHAPTER_OUTLINE in artifact_types
    assert ArtifactType.CHAPTER_OUTLINE_BATCH in artifact_types
    assert len(result.llm_run_ids) == 6
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 7


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
            "survival", "political_intrigue", "betrayal",
            "faction_war", "existential_threat", "internal_reckoning",
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
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

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
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

    assert cast_spec["antagonist"] is not None
    assert len(cast_spec["antagonist_forces"]) >= 1
    assert len(volume_plan) == 1
    assert len(outline["chapters"]) == 1


def test_assign_conflict_phases_distributes_correctly() -> None:
    assert planner_services._assign_conflict_phases(1) == ["survival"]
    assert planner_services._assign_conflict_phases(2) == ["survival", "existential_threat"]
    assert planner_services._assign_conflict_phases(3) == ["survival", "political_intrigue", "existential_threat"]
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
    assert planner_services._assign_conflict_phases(2, category_key=None) == ["survival", "existential_threat"]
    assert planner_services._assign_conflict_phases(5, category_key=None)[0] == "survival"


def test_resolve_phase_templates_from_category() -> None:
    """Category phase templates should contain formatted text."""
    tpl = planner_services._resolve_phase_templates(
        "individual_survival", category_key="action-progression", is_en=False,
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
