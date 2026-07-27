"""Regression tests for the 2026-06-12 framework-quality fixes (R4/R5/R16).

Pins three deterministic, genre-free behaviors in ``planner``:

* **R5 — cross-batch consumed-event ledger**: progressive outline batches
  accumulate a deterministic ledger of consumed events (chapter + goal +
  hook), inject it into the next batch's constraints, and reject new
  chapters whose goal is a near-rewrite of a consumed event via the
  existing repair-directive retry path (fail closed on the final attempt).
* **R4 — premise roster passthrough**: explicit character names in the
  premise (marker sections or high-frequency name-shaped tokens) survive
  the LLM name pool as ``locked_names`` and reach the cast-spec prompt as
  a do-not-rename instruction.
* **R16 — clipped-fragment title guard**: the deterministic title dedupe
  never ships a goal-text fragment (「霍听澜把一份」-style) as a chapter
  title — it prefers a 2-4 char noun phrase from the chapter's own
  hook/goal and falls back to a numbered 「原标题·二」 suffix.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType, ProjectStatus
from bestseller.infra.db.models import ProjectModel
import bestseller.services.planner as planner_services
from bestseller.services.planner import (
    OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT,
    OutlineEventDuplicationError,
    _cast_spec_prompts,
    _compact_outline_repair_baseline,
    _extract_concrete_title_phrase,
    _extract_premise_locked_names,
    _is_clipped_title_fragment,
    _merge_locked_names_into_pool,
    _numbered_title_fallback,
    _outline_batch_constraints,
    _outline_consumed_event_entries,
    _outline_duplicate_event_findings,
    _outline_event_ledger_lines,
    _planning_artifact_reuse_allowed,
    _persist_creation_protagonist_choice,
    _prefer_outline_repair_baseline,
    _previous_outline_batch_constraints,
    _outline_repair_directives_from_error,
    _repair_clipped_dedup_titles,
)
from bestseller.settings import load_settings


def test_commercial_repair_round_uses_previous_batch_as_edit_baseline() -> None:
    previous = {
        "chapters": [
            {"chapter_number": 1, "title": "留痕", "chapter_goal": "保住证据"},
            {"chapter_number": 2, "title": "验印", "chapter_goal": "查清印记"},
            {"chapter_number": 4, "title": "越界", "chapter_goal": "不应进入本批"},
        ]
    }

    constraints = _previous_outline_batch_constraints(
        previous,
        chapter_start=1,
        chapter_end=2,
        language="zh-CN",
    )

    assert len(constraints) == 1
    assert "定点修改" in constraints[0]
    assert "保住证据" in constraints[0]
    assert "查清印记" in constraints[0]
    assert "不应进入本批" not in constraints[0]


def test_commercial_first_round_has_no_synthetic_edit_baseline() -> None:
    assert (
        _previous_outline_batch_constraints(
            None,
            chapter_start=1,
            chapter_end=3,
            language="zh-CN",
        )
        == []
    )


def test_commercial_repair_baseline_is_compact_but_keeps_scene_causality() -> None:
    long_text = "具体因果动作" * 200
    previous = {
        "chapters": [
            {
                "chapter_number": number,
                "title": f"第{number}章",
                "chapter_goal": long_text,
                "opening_situation": long_text,
                "main_conflict": long_text,
                "causal_contract": {"state_change": long_text},
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["姬衡", "萧崇"],
                        "purpose": long_text,
                        "entry_state": {"summary": long_text},
                        "exit_state": {"summary": long_text},
                    }
                ],
                "large_unused_contract": {"blob": long_text * 10},
            }
            for number in (1, 2, 3)
        ]
    }

    constraint = _previous_outline_batch_constraints(
        previous,
        chapter_start=1,
        chapter_end=3,
        language="zh-CN",
    )[0]

    assert len(constraint) < 8_000
    assert '"causal_contract"' in constraint
    assert '"scenes"' in constraint
    assert "large_unused_contract" not in constraint


def test_commercial_repair_baseline_keeps_production_field_names() -> None:
    previous = {
        "chapters": [
            {
                "chapter_number": 2,
                "title": "错视",
                "goal": "保住赵翰",
                "tail_hook": "暗哨落笔",
                "world_rule_landing": "单段啼音无效",
                "causal_contract": {
                    "pressure": "调令将下",
                    "resistance": "暗哨盯住赵翰",
                    "visible_action_or_reaction": "姬衡只观察，不主动挡险",
                },
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["姬衡", "赵翰"],
                        "entry_state": {"summary": "赵翰今日不上值"},
                        "exit_state": {"summary": "暗哨在赵翰名后多记一档"},
                        "key_dialogue_beats": ["乳母说明赵翰告假"],
                    }
                ],
            }
        ]
    }

    constraint = _previous_outline_batch_constraints(
        previous,
        chapter_start=2,
        chapter_end=2,
        language="zh-CN",
    )[0]

    assert "保住赵翰" in constraint
    assert "单段啼音无效" in constraint
    assert "调令将下" in constraint
    assert "姬衡只观察，不主动挡险" in constraint
    assert "赵翰今日不上值" in constraint


def test_compact_outline_repair_baseline_round_trips_to_next_heal() -> None:
    baseline = _compact_outline_repair_baseline(
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "烫舌",
                    "goal": "熬过第一次喂养",
                    "causal_contract": {"pressure": "暗哨在门外落笔"},
                    "scenes": [
                        {
                            "scene_number": 1,
                            "participants": ["姬衡", "萧崇"],
                            "entry_state": {"summary": "米糊送到唇边"},
                            "exit_state": {"summary": "萧崇把军议推迟一刻"},
                        }
                    ],
                }
            ]
        },
        language="zh-CN",
    )

    assert baseline is not None
    assert baseline["chapters"][0]["goal"] == "熬过第一次喂养"
    assert baseline["chapters"][0]["causal_contract"]["pressure"] == "暗哨在门外落笔"


def test_outer_heal_keeps_best_scoring_failed_outline_and_matching_report() -> None:
    best_payload, best_report = _prefer_outline_repair_baseline(
        best_payload=None,
        best_report=None,
        candidate_payload={"chapters": [{"chapter_number": 1, "goal": "第一版"}]},
        candidate_report={"overall_score": 0.68, "repair_directives": ["修第一版"]},
    )

    retained_payload, retained_report = _prefer_outline_repair_baseline(
        best_payload=best_payload,
        best_report=best_report,
        candidate_payload={"chapters": [{"chapter_number": 1, "goal": "退化版"}]},
        candidate_report={"overall_score": 0.58, "repair_directives": ["修退化版"]},
    )

    assert retained_payload == best_payload
    assert retained_report == best_report
    assert retained_payload["chapters"][0]["goal"] == "第一版"
    assert retained_report["repair_directives"] == ["修第一版"]


def test_outer_heal_promotes_a_later_higher_scoring_failed_outline() -> None:
    payload, report = _prefer_outline_repair_baseline(
        best_payload={"chapters": [{"chapter_number": 1, "goal": "旧版"}]},
        best_report={"overall_score": 0.48, "repair_directives": ["修旧版"]},
        candidate_payload={"chapters": [{"chapter_number": 1, "goal": "改良版"}]},
        candidate_report={"overall_score": 0.68, "repair_directives": ["修改良版"]},
    )

    assert payload["chapters"][0]["goal"] == "改良版"
    assert report["repair_directives"] == ["修改良版"]


def test_llm_creation_protagonist_is_persisted_before_snapshot_creation() -> None:
    project = build_project()

    chosen = _persist_creation_protagonist_choice(project, "纪赊")

    assert chosen == "纪赊"
    assert project.metadata_json["creation_protagonist_name"] == "纪赊"
    assert (
        project.metadata_json["creation_protagonist_source"]
        == "llm_premise_identity_resolution"
    )


def test_original_premise_identity_overrides_conflicting_generated_name() -> None:
    project = build_project()
    project.metadata_json = {
        "premise": "矿场贱民纪赊在血契账册上发现了自己的名字。",
    }

    chosen = _persist_creation_protagonist_choice(project, "陆沉")

    assert chosen == "纪赊"
    assert project.metadata_json["creation_protagonist_name"] == "纪赊"
    assert project.metadata_json["creation_protagonist_source"] == "original_premise"


def test_outline_judge_brief_uses_snapshot_identity_not_stale_story_spine() -> None:
    project = build_project()
    project.metadata_json = {
        "premise": "姬衡以婴儿之身在仇敌膝上求生。",
        "creation_protagonist_name": "姬衡",
        "story_spine": {"who": "李玄，废弃旧稿中的主角"},
        "book_design_snapshot": {"protagonist": {"name": "姬衡"}},
    }

    brief = planner_services._outline_judge_project_brief(
        project,
        metadata=project.metadata_json,
        semantic_candidates=[],
    )

    assert brief["canonical_protagonist"] == "姬衡"
    assert brief["premise"].startswith("姬衡")
    assert "metadata" not in brief
    assert "李玄" not in json.dumps(brief, ensure_ascii=False)


def test_infant_embodiment_contract_reaches_outline_and_judge() -> None:
    project = build_project()
    project.language = "zh-CN"
    project.target_chapters = 50
    project.metadata_json = {
        "premise": "姬衡以三个月大的婴儿之身在仇敌膝上求生。",
        "concept_contract": {
            "champion_id": "concept-infant",
            "hook_card": {
                "one_liner": "废太子重生为仇人怀里的婴儿。",
                "protagonist": "姬衡，三个月大的婴儿身体。",
                "story_motion": "成年人自主博弈，姬衡被动听见信息。",
            },
            "story_spine": {
                "who": "姬衡，成年记忆困在婴儿身体里。",
                "unit_engine_ref": "议事—泄露—外溢",
            },
            "seriality_proof": {
                "repeatable_story_unit": "成人议事推动外部事件。",
            },
        },
    }
    volume_entry = {
        "volume_number": 1,
        "chapter_count_target": 9,
        "chapter_range": "1-9",
    }
    _, prompt = planner_services._volume_outline_prompts(
        project,
        {"protagonist": {"name": "姬衡"}},
        {"protagonist": {"name": "姬衡"}},
        [volume_entry],
        volume_entry,
    )
    brief = planner_services._outline_judge_project_brief(
        project,
        metadata=project.metadata_json,
        semantic_candidates=[],
    )

    assert "身体能力权威合同·婴儿主角" in prompt
    assert "严禁把精确啼哭节奏" in prompt
    assert "身体能力权威合同·婴儿主角" in brief[
        "protagonist_embodiment_authority"
    ]
    assert brief["approved_concept_authority"]["champion_id"] == "concept-infant"


def test_needs_replan_never_reuses_structurally_approved_outline_candidate() -> None:
    project = build_project()
    project.status = ProjectStatus.NEEDS_REPLAN.value
    project.metadata_json = {
        "planning_status": "needs_replan",
        "outline_semantic_gate_status": "needs_replan",
    }

    assert not _planning_artifact_reuse_allowed(
        project,
        artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
    )
    assert _planning_artifact_reuse_allowed(
        project,
        artifact_type=ArtifactType.CAST_SPEC,
    )


def test_semantically_released_outline_can_still_reuse_cache() -> None:
    project = build_project()
    project.status = ProjectStatus.WRITING.value
    project.metadata_json = {
        "planning_status": "writing",
        "outline_semantic_gate_status": "approved",
    }

    assert _planning_artifact_reuse_allowed(
        project,
        artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
    )


def build_settings():
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


def build_project(language: str = "zh-CN") -> ProjectModel:
    project = ProjectModel(
        slug="ledger-story",
        title="长夜巡航",
        genre="都市悬疑",
        language=language,
        target_word_count=80000,
        target_chapters=50,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


class FakeSession:
    """No-op stand-in; the helpers under test never touch the DB."""


# ---------------------------------------------------------------------------
# R5 — consumed-event ledger
# ---------------------------------------------------------------------------


class TestConsumedEventLedger:
    def test_entries_compress_chapter_goal_and_hook(self) -> None:
        chapters = [
            {
                "chapter_number": 3,
                "chapter_goal": "赵小磊在太平间签字确认尸源，发现编号被人调换",
                "hook_description": "签字单上的名字是他自己",
            },
            {"chapter_number": 4},  # no goal/hook → no entry
        ]
        entries = _outline_consumed_event_entries(chapters)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["chapter_number"] == 3
        assert entry["goal"].startswith("赵小磊在太平间签字")
        assert entry["line"].startswith("ch3 | ")
        assert "签字单上的名字是他自己" in entry["line"]

    def test_ledger_lines_keep_head_and_tail_when_over_cap(self) -> None:
        entries = [
            {"chapter_number": i, "goal": f"事件{i}", "hook": "", "line": f"ch{i} | 事件{i}"}
            for i in range(1, 201)
        ]
        lines = _outline_event_ledger_lines(entries)
        assert len(lines) == planner_services._OUTLINE_EVENT_LEDGER_MAX_PROMPT_LINES
        assert lines[0] == "ch1 | 事件1"
        assert lines[-1] == "ch200 | 事件200"

    def test_batch_constraints_inject_ledger_with_hard_ban(self) -> None:
        project = build_project()
        ledger = ["ch3 | 赵小磊签字确认尸源 | 名字是他自己"]
        constraints = _outline_batch_constraints(
            project,
            volume_number=1,
            chapter_start=11,
            chapter_end=20,
            count=10,
            previous_exit_state=None,
            consumed_event_ledger=ledger,
        )
        joined = "\n".join(constraints)
        assert "已消耗事件台账" in joined
        assert "禁止重写" in joined
        assert "ch3 | 赵小磊签字确认尸源" in joined

    def test_batch_constraints_without_ledger_unchanged(self) -> None:
        project = build_project()
        constraints = _outline_batch_constraints(
            project,
            volume_number=1,
            chapter_start=1,
            chapter_end=10,
            count=10,
            previous_exit_state=None,
        )
        assert "已消耗事件台账" not in "\n".join(constraints)

    def test_duplicate_findings_flag_near_rewrite_only(self) -> None:
        entries = _outline_consumed_event_entries(
            [
                {
                    "chapter_number": 3,
                    "chapter_goal": "赵小磊在医院太平间签字确认尸源，发现编号被人调换",
                }
            ]
        )
        new_chapters = [
            {
                "chapter_number": 12,
                "chapter_goal": "赵小磊在医院太平间签字确认尸源，发现编号又被人调换",
            },
            {
                "chapter_number": 13,
                "chapter_goal": "霍听澜约陈屿在码头交换一份旧案卷宗",
            },
        ]
        findings = _outline_duplicate_event_findings(
            new_chapters, entries, threshold=OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT
        )
        assert [f["chapter_number"] for f in findings] == [12]
        assert findings[0]["ledger_chapter_number"] == 3
        assert findings[0]["similarity"] >= OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT

    def test_duplicate_threshold_is_configurable(self) -> None:
        entries = _outline_consumed_event_entries(
            [{"chapter_number": 3, "chapter_goal": "赵小磊签字确认尸源编号被调换"}]
        )
        chapters = [{"chapter_number": 12, "chapter_goal": "赵小磊签字确认尸源编号又被调换"}]
        assert _outline_duplicate_event_findings(chapters, entries, threshold=0.99) == []
        assert _outline_duplicate_event_findings(chapters, entries, threshold=0.5) != []

    def test_duplication_error_yields_per_chapter_directives(self) -> None:
        error = OutlineEventDuplicationError(
            "dup",
            findings=[
                {
                    "chapter_number": 12,
                    "goal": "赵小磊签字确认尸源",
                    "ledger_chapter_number": 3,
                    "ledger_goal": "赵小磊在太平间签字确认尸源",
                    "similarity": 0.88,
                }
            ],
        )
        directives = _outline_repair_directives_from_error(error, language="zh-CN")
        joined = "\n".join(directives)
        assert "第12章" in joined
        assert "第3章" in joined
        assert "禁止生成其变体" in joined
        # Targeted repair, not a whole-batch rewrite.
        assert "不要重写整批" in joined

    @pytest.mark.asyncio
    async def test_repair_loop_retries_on_event_duplication_then_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = build_project()
        settings = build_settings()
        attempts: list[list[str]] = []
        dup_payload = {
            "chapters": [
                {
                    "chapter_number": 11,
                    "title": "签字疑云",
                    "chapter_goal": "赵小磊在医院太平间签字确认尸源，发现编号被人调换",
                }
            ]
        }
        fresh_payload = {
            "chapters": [
                {
                    "chapter_number": 11,
                    "title": "码头交易",
                    "chapter_goal": "霍听澜约陈屿在码头交换一份旧案卷宗",
                }
            ]
        }

        async def fake_fetch_existing_titles(session, project_id, **kwargs):
            return []

        def fake_prompts(*args, **kwargs):
            attempts.append(list(kwargs.get("extra_constraints") or []))
            return "system", "user"

        async def fake_generate(*args, **kwargs):
            payload = dup_payload if len(attempts) == 1 else fresh_payload
            return json.loads(json.dumps(payload)), uuid4()

        def fake_validate(raw_payload, **kwargs):
            return raw_payload

        monkeypatch.setattr(
            planner_services, "_fetch_existing_chapter_titles", fake_fetch_existing_titles
        )
        monkeypatch.setattr(planner_services, "_volume_outline_prompts", fake_prompts)
        monkeypatch.setattr(
            planner_services,
            "_compile_volume_outline_prompt",
            lambda *a, system_prompt, user_prompt, **k: (system_prompt, user_prompt),
        )
        monkeypatch.setattr(planner_services, "_generate_structured_artifact", fake_generate)
        monkeypatch.setattr(
            planner_services, "_validate_generated_volume_outline_or_raise", fake_validate
        )
        monkeypatch.setattr(
            planner_services, "attach_planner_methodology", lambda prompt, **kwargs: prompt
        )

        consumed = _outline_consumed_event_entries(
            [
                {
                    "chapter_number": 3,
                    "chapter_goal": "赵小磊在医院太平间签字确认尸源，发现编号又被人调换",
                }
            ]
        )
        repair_loop = planner_services._generate_volume_outline_with_repair_loop
        payload, _run_id, history = await repair_loop(
            FakeSession(),
            settings,
            project=project,
            workflow_run_id=uuid4(),
            logical_name="volume_1_chapter_outline_batch_11_20",
            book_spec={},
            cast_spec={},
            volume_plan=[],
            volume_entry={},
            fallback_payload={"chapters": []},
            volume_number=1,
            expected_count=1,
            chapter_number_offset=11,
            revealed_ledger_block=None,
            base_constraints=[],
            consumed_event_entries=consumed,
        )
        # Second attempt produced a fresh event and was accepted.
        assert payload["chapters"][0]["chapter_goal"].startswith("霍听澜约陈屿")
        assert len(attempts) == 2
        # The retry prompt carried targeted event-dedup repair directives.
        retry_constraints = "\n".join(attempts[1])
        assert "第11章" in retry_constraints
        assert "禁止生成其变体" in retry_constraints
        assert any(item.get("status") == "failed" for item in history)

    @pytest.mark.asyncio
    async def test_repair_loop_forwards_residual_duplicate_to_llm_on_final_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = build_project()
        settings = build_settings()
        settings.pipeline.chapter_outline_repair_attempts = 1
        dup_payload = {
            "chapters": [
                {
                    "chapter_number": 11,
                    "title": "签字疑云",
                    "chapter_goal": "赵小磊在医院太平间签字确认尸源，发现编号被人调换",
                }
            ]
        }

        async def fake_fetch_existing_titles(session, project_id, **kwargs):
            return []

        async def fake_generate(*args, **kwargs):
            return json.loads(json.dumps(dup_payload)), uuid4()

        monkeypatch.setattr(
            planner_services, "_fetch_existing_chapter_titles", fake_fetch_existing_titles
        )
        monkeypatch.setattr(
            planner_services, "_volume_outline_prompts", lambda *a, **k: ("system", "user")
        )
        monkeypatch.setattr(
            planner_services,
            "_compile_volume_outline_prompt",
            lambda *a, system_prompt, user_prompt, **k: (system_prompt, user_prompt),
        )
        monkeypatch.setattr(planner_services, "_generate_structured_artifact", fake_generate)
        monkeypatch.setattr(
            planner_services,
            "_validate_generated_volume_outline_or_raise",
            lambda raw_payload, **kwargs: raw_payload,
        )
        monkeypatch.setattr(
            planner_services, "attach_planner_methodology", lambda prompt, **kwargs: prompt
        )

        consumed = _outline_consumed_event_entries(
            [
                {
                    "chapter_number": 3,
                    "chapter_goal": "赵小磊在医院太平间签字确认尸源，发现编号又被人调换",
                }
            ]
        )
        repair_loop = planner_services._generate_volume_outline_with_repair_loop
        payload, _run_id, _history = await repair_loop(
            FakeSession(),
            settings,
            project=project,
            workflow_run_id=uuid4(),
            logical_name="volume_1_chapter_outline_batch_11_20",
            book_spec={},
            cast_spec={},
            volume_plan=[],
            volume_entry={},
            fallback_payload={"chapters": []},
            volume_number=1,
            expected_count=1,
            chapter_number_offset=11,
            revealed_ledger_block=None,
            base_constraints=[],
            consumed_event_entries=consumed,
        )

        candidates = payload["_meta"]["cross_batch_event_similarity_candidates"]
        assert candidates[0]["chapter_number"] == 11
        assert candidates[0]["ledger_chapter_number"] == 3


# ---------------------------------------------------------------------------
# R4 — premise roster passthrough
# ---------------------------------------------------------------------------


class TestPremiseRosterPassthrough:
    def test_marker_roster_extracted_in_order(self) -> None:
        premise = (
            "都市悬疑长篇。人物名册（正文必须沿用以下姓名）：赵小磊（主角）、霍听澜（对手）、陈屿。\n\n"
            "故事从一次太平间签字事故开始。"
        )
        names = _extract_premise_locked_names(premise)
        assert names[:3] == ["赵小磊", "霍听澜", "陈屿"]
        # Role labels / structural words never leak in as "names".
        assert "主角" not in names
        assert "正文" not in names
        assert "姓名" not in names

    def test_frequency_path_catches_unmarked_recurring_name(self) -> None:
        premise = (
            "林晚秋在末日废墟里救下一个孩子。林晚秋的住处被烧毁。"
            "林晚秋决定向北方走。林晚秋不再回头。"
        )
        assert "林晚秋" in _extract_premise_locked_names(premise)

    def test_frequency_path_ignores_common_words_and_substrings(self) -> None:
        premise = (
            "林晚秋发现了问题。林晚秋解决了问题。林晚秋忘掉了问题。"
            "这个故事讲的是这个故事里的这个故事。"
        )
        names = _extract_premise_locked_names(premise)
        assert names == ["林晚秋"]  # not 问题/故事/这个, not 林晚/晚秋

    def test_empty_premise_returns_no_names(self) -> None:
        assert _extract_premise_locked_names("") == []
        assert _extract_premise_locked_names("   ") == []

    def test_merge_locked_names_is_immutable_and_dedups(self) -> None:
        pool = {
            "protagonist": {"name": "赵小磊"},
            "allies": [{"name": "陈屿"}],
            "antagonists": [{"name": "霍听澜"}],
        }
        merged = _merge_locked_names_into_pool(pool, ["赵小磊", "霍听澜", "陈屿", "沈茉"])
        assert merged["locked_names"] == ["赵小磊", "霍听澜", "陈屿", "沈茉"]
        ally_names = [a["name"] for a in merged["allies"]]
        # Only the genuinely new name is appended, marked locked.
        assert ally_names == ["陈屿", "沈茉"]
        assert merged["allies"][-1]["locked"] is True
        # Original pool untouched.
        assert "locked_names" not in pool
        assert len(pool["allies"]) == 1

    def test_merge_noop_without_locked_names(self) -> None:
        pool = {"protagonist": {"name": "赵小磊"}}
        assert _merge_locked_names_into_pool(pool, []) is pool

    def test_cast_spec_prompt_injects_do_not_rename_block(self) -> None:
        project = build_project()
        _, user_prompt = _cast_spec_prompts(
            project, {}, {}, locked_names=["赵小磊", "霍听澜", "陈屿"]
        )
        assert "既定人物名册" in user_prompt
        assert "赵小磊、霍听澜、陈屿" in user_prompt
        assert "禁止改名" in user_prompt
        assert "只允许在此名册之外补充新角色名" in user_prompt

    def test_cast_spec_prompt_without_locked_names_has_no_block(self) -> None:
        project = build_project()
        _, user_prompt = _cast_spec_prompts(project, {}, {})
        assert "既定人物名册" not in user_prompt

    @pytest.mark.asyncio
    async def test_generate_character_names_carries_premise_roster_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_complete_text(session: object, settings: object, request: object):
            return type(
                "CompletionStub",
                (),
                {
                    "content": json.dumps(
                        {
                            "protagonist": {"name": "顾远舟"},
                            "allies": [{"name": "白知微"}],
                            "antagonists": [{"name": "纪声"}],
                        },
                        ensure_ascii=False,
                    ),
                    "llm_run_id": uuid4(),
                },
            )()

        monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)
        premise = (
            "人物名册（正文必须沿用以下姓名）：赵小磊、霍听澜、陈屿。\n\n"
            "一桩太平间签字事故牵出连环顶替案。"
        )
        pool = await planner_services._generate_character_names(
            FakeSession(),
            build_settings(),
            genre="都市悬疑",
            sub_genre="刑侦",
            language="zh-CN",
            premise=premise,
            book_spec={},
        )
        assert pool["locked_names"] == ["赵小磊", "霍听澜", "陈屿"]
        ally_names = [a["name"] for a in pool["allies"]]
        # The LLM pool keeps its own names AND the roster joins it, locked.
        assert "白知微" in ally_names
        for name in ("赵小磊", "霍听澜", "陈屿"):
            assert name in ally_names


# ---------------------------------------------------------------------------
# R16 — clipped-fragment title guard
# ---------------------------------------------------------------------------


class TestClippedTitleFragmentGuard:
    @pytest.mark.parametrize(
        "fragment",
        ["霍听澜把一份", "试图让陈屿的", "的软肋", "在最深", "已经知道了", ""],
    )
    def test_detects_clipped_fragments(self, fragment: str) -> None:
        assert _is_clipped_title_fragment(fragment) is True

    @pytest.mark.parametrize(
        "title",
        ["青鸾令", "断指", "后视镜", "太平间名单", "后视镜·二", "Cipher Crossing"],
    )
    def test_accepts_noun_phrases_and_latin(self, title: str) -> None:
        assert _is_clipped_title_fragment(title) is False

    def test_extract_prefers_bracketed_noun_from_hook(self) -> None:
        chapter = {
            "chapter_number": 7,
            "hook_description": "他在卷宗里发现了「青鸾令」的拓印。",
            "chapter_goal": "霍听澜把一份伪造的笔录压进卷宗",
        }
        assert _extract_concrete_title_phrase(chapter, []) == "青鸾令"

    def test_extract_never_returns_fragment_or_used_title(self) -> None:
        chapter = {
            "chapter_number": 7,
            "hook_description": "他在卷宗里发现了「青鸾令」的拓印。",
            "chapter_goal": "",
        }
        # Used title blocks the only candidate → no extraction.
        assert _extract_concrete_title_phrase(chapter, ["青鸾令"]) is None

    def test_numbered_fallback_appends_interpunct_numeral(self) -> None:
        assert _numbered_title_fallback("后视镜", ["后视镜"]) == "后视镜·二"
        assert _numbered_title_fallback("后视镜", ["后视镜", "后视镜·二"]) == "后视镜·三"

    def test_repair_replaces_fragment_with_content_noun(self) -> None:
        chapters = [
            {"chapter_number": 1, "title": "后视镜"},
            {
                "chapter_number": 6,
                "title": "霍听澜把一份",
                "hook_description": "他在卷宗里发现了「青鸾令」的拓印。",
                "chapter_goal": "霍听澜把一份伪造的笔录压进卷宗",
            },
        ]
        changes = [(6, "后视镜", "霍听澜把一份")]
        repaired, repaired_changes = _repair_clipped_dedup_titles(
            chapters, changes, existing_titles=[(1, "后视镜")]
        )
        assert repaired[1]["title"] == "青鸾令"
        assert repaired_changes == [(6, "后视镜", "青鸾令")]
        # Inputs untouched (immutability).
        assert chapters[1]["title"] == "霍听澜把一份"

    def test_repair_falls_back_to_numbered_suffix_without_content(self) -> None:
        chapters = [
            {"chapter_number": 1, "title": "后视镜"},
            {"chapter_number": 6, "title": "试图让陈屿的"},
        ]
        changes = [(6, "后视镜", "试图让陈屿的")]
        repaired, repaired_changes = _repair_clipped_dedup_titles(
            chapters, changes, existing_titles=[(1, "后视镜")]
        )
        new_title = repaired[1]["title"]
        assert new_title == repaired_changes[0][2]
        assert "·" in new_title
        assert new_title.startswith("后")
        assert not _is_clipped_title_fragment(new_title)

    def test_repair_passes_through_clean_rewrites(self) -> None:
        chapters = [{"chapter_number": 6, "title": "青鸾令"}]
        changes = [(6, "后视镜", "青鸾令")]
        repaired, repaired_changes = _repair_clipped_dedup_titles(
            chapters, changes, existing_titles=[]
        )
        assert repaired is chapters
        assert repaired_changes is changes
