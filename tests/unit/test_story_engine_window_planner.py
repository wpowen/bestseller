from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.domain.story_engine import canonical_json_hash
from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services import story_engine_window_planner as window_planner
from bestseller.services.story_engine_window_planner import (
    StoryEngineWindowPlanningError,
    build_story_engine_window_planner_prompt,
    generate_story_engine_shadow_window,
)


def _engine_artifact() -> PlanningArtifactVersionModel:
    engine = {
        "engine_id": "engine-1",
        "version": 2,
        "initial_state": {
            "pressure": {"category": "exposure", "value": 0}
        },
        "choices": [],
        "chapters": [],
        "reader_promise": "每章用主动选择改变局势",
        "change_vectors": ["暴露", "关系"],
        "engine_invariants": ["连续章节不得复用行动"],
    }
    return PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=uuid4(),
        artifact_type="story_engine_v2",
        version_no=1,
        status="structure_only",
        schema_version="2.0",
        content={
            "artifact_type": "story_engine_v2",
            "schema_version": "2.0",
            "projection_status": "structure_only",
            "maturity": "structure_only",
            "validity": "valid",
            "can_drive_generation": False,
            "blocking_codes": ["LEGACY_REAL_CHOICES_UNAVAILABLE"],
            "warnings": [],
            "source_hash": "source-hash",
            "engine": engine,
            "_meta": {
                "engine_hash": canonical_json_hash(engine),
                "source_snapshot_hash": "snapshot-hash",
            },
        },
        idempotency_key="engine-1",
    )


def _outlines() -> list[dict[str, object]]:
    return [
        {
            "chapter_number": 1,
            "title": "公开档案",
            "chapter_goal": "林澈必须在午夜前公开原始档案",
            "opening_pressure": "管理层正在销毁档案",
            "main_conflict": "公开会暴露证人,隐藏会失去窗口",
            "chapter_concrete_actions": ["林澈把档案投上会议大屏"],
            "causal_contract": {"protagonist_choice": "公开档案"},
            "selected_effect_skills": {
                "primary": "brainhole_engine",
                "secondary": "tension_pressure_engine",
                "tertiary": "must_not_leak",
                "reason": "现代审计流程与证据危机形成反差",
                "growth_stage_fit": "opening",
            },
            "brainhole_contract": {
                "one_sentence_sell": "把公司档案审计变成公开证据战",
                "protagonist_decision": "公开档案",
                "plot_consequence": "权限被冻结,证人被追查",
            },
        }
    ]


def _window_payload(engine_artifact: PlanningArtifactVersionModel) -> dict[str, object]:
    pre_state = {"pressure": {"category": "exposure", "value": 0}}
    post_state = {"pressure": {"category": "exposure", "value": 1}}
    post_hash = canonical_json_hash(post_state)
    return {
        "window_id": "window-1",
        "engine_id": "engine-1",
        "engine_version": 2,
        "engine_artifact_id": str(engine_artifact.id),
        "source_engine_hash": engine_artifact.content["_meta"]["engine_hash"],
        "projections": [
            {
                "chapter_number": 1,
                "choice_id": "publish",
                "pre_state": pre_state,
                "pressure": "管理层正在销毁档案",
                "known_facts": ["档案将在午夜销毁"],
                "options": [
                    {
                        "choice_id": "publish",
                        "label": "公开档案",
                        "reachable_state_hash": post_hash,
                    },
                    {
                        "choice_id": "hide",
                        "label": "隐藏档案",
                        "reachable_state_hash": "distinct-shadow-state",
                    },
                ],
                "chosen_option_id": "publish",
                "chosen_path": "当众公开档案并承担暴露代价",
                "alternative_costs": ["隐藏会错过最后窗口"],
                "opponent_strategy": "冻结权限并追查证人",
                "due_obligations": ["保护证人"],
                "required_state_changes": [
                    {
                        "key": "pressure",
                        "category": "exposure",
                        "before": 0,
                        "operator": "set",
                        "after": 1,
                        "evidence": "待正文验证:公开档案",
                        "monotonic": "non_decreasing",
                    }
                ],
                "expected_post_state_hash": post_hash,
                "fingerprint": "publish|archive|exposure",
            }
        ],
    }


def test_window_prompt_expands_only_selected_effect_contracts() -> None:
    _, user_prompt, input_hash = build_story_engine_window_planner_prompt(
        engine_artifact=_engine_artifact(),
        chapter_outlines=_outlines(),
    )

    assert '"primary": "brainhole_engine"' in user_prompt
    assert '"secondary": "tension_pressure_engine"' in user_prompt
    assert "must_not_leak" not in user_prompt
    assert "把公司档案审计变成公开证据战" in user_prompt
    assert "story_effect_skill_catalog" not in user_prompt
    assert input_hash


def test_window_prompt_rejects_noncontiguous_chapter_scope() -> None:
    outlines = _outlines()
    second = dict(outlines[0])
    second["chapter_number"] = 3

    with pytest.raises(StoryEngineWindowPlanningError, match="contiguous"):
        build_story_engine_window_planner_prompt(
            engine_artifact=_engine_artifact(),
            chapter_outlines=[*outlines, second],
        )


def test_window_json_parser_handles_fences_and_invalid_text() -> None:
    assert window_planner._parse_json_object("not-json") == {}
    assert window_planner._parse_json_object("```json\n{\"window_id\":\"w1\"}\n```") == {
        "window_id": "w1"
    }


def test_outline_mapping_accepts_model_dump_and_rejects_unknown_objects() -> None:
    outline = SimpleNamespace(model_dump=lambda **_: {"chapter_number": 1})

    assert window_planner._outline_mapping(outline) == {"chapter_number": 1}
    with pytest.raises(StoryEngineWindowPlanningError, match="must be a mapping"):
        window_planner._outline_mapping(object())


def test_window_prompt_rejects_tampered_engine_hash() -> None:
    engine_artifact = _engine_artifact()
    engine_artifact.content["_meta"]["engine_hash"] = "tampered"

    with pytest.raises(StoryEngineWindowPlanningError, match="hash mismatch"):
        build_story_engine_window_planner_prompt(
            engine_artifact=engine_artifact,
            chapter_outlines=_outlines(),
        )


@pytest.mark.asyncio
async def test_generated_window_is_shadow_only_and_persisted_with_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_artifact = _engine_artifact()
    completion = SimpleNamespace(
        content=window_planner.json.dumps(
            {"window": _window_payload(engine_artifact)}, ensure_ascii=False
        ),
        llm_run_id=uuid4(),
    )
    monkeypatch.setattr(window_planner, "complete_text", AsyncMock(return_value=completion))
    persisted = SimpleNamespace(id=uuid4(), content={})
    create = AsyncMock(return_value=persisted)
    monkeypatch.setattr(window_planner, "create_story_engine_window_artifact", create)

    result = await generate_story_engine_shadow_window(
        AsyncMock(),
        SimpleNamespace(),  # type: ignore[arg-type]
        project_id=engine_artifact.project_id,
        engine_artifact=engine_artifact,
        chapter_outlines=_outlines(),
        workflow_run_id=uuid4(),
    )

    assert result is persisted
    content = create.await_args.kwargs["content"]
    assert content["maturity"] == "shadow_validated"
    assert content["can_drive_generation"] is False
    assert content["_meta"]["source_outline_hash"]
    assert content["_meta"]["llm_run_id"] == str(completion.llm_run_id)


@pytest.mark.asyncio
async def test_invalid_model_window_never_reaches_artifact_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_artifact = _engine_artifact()
    monkeypatch.setattr(
        window_planner,
        "complete_text",
        AsyncMock(return_value=SimpleNamespace(content="{}", llm_run_id=uuid4())),
    )
    create = AsyncMock()
    monkeypatch.setattr(window_planner, "create_story_engine_window_artifact", create)

    with pytest.raises(StoryEngineWindowPlanningError):
        await generate_story_engine_shadow_window(
            AsyncMock(),
            SimpleNamespace(),  # type: ignore[arg-type]
            project_id=engine_artifact.project_id,
            engine_artifact=engine_artifact,
            chapter_outlines=_outlines(),
            workflow_run_id=uuid4(),
        )

    create.assert_not_awaited()
