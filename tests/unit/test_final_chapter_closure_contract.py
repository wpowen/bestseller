import copy
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.settings import load_settings


def _card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "methodology_contract": {
            "is_climax": True,
            "pacing_mode": "resolution",
            "hooks_to_plant": [],
        },
        "causal_contract": {
            "gain_or_reveal": "妹妹脱离祭阵并拿到真相证据",
            "state_change": "兄妹离开宗门，追杀关系被改写",
            "final_state": "两人站在山门外，决定以证据重建生活",
        },
    }
    for key, value in overrides.items():
        card[key] = value
    return card


def test_unresolved_mid_story_escape_card_is_rejected_as_finite_finale() -> None:
    card = _card(
        methodology_contract={
            "is_climax": False,
            "pacing_mode": "climax",
            "hooks_to_plant": ["掌门继续逼近"],
        },
        causal_contract={
            "gain_or_reveal": "拿到推进下一步的玉牌",
            "state_change": "推出阵外但掌门逼近",
        },
    )

    findings = planner._final_chapter_closure_findings(card)

    assert "methodology_contract.pacing_mode must declare resolution" in findings
    assert "causal_contract.final_state is required" in findings
    assert "final chapter cannot plant an unresolved hook" in findings


def test_aftermath_card_may_follow_climax_in_previous_chapter() -> None:
    card = _card(
        methodology_contract={
            "is_climax": False,
            "pacing_mode": "resolution",
            "hooks_to_plant": [],
        },
    )
    assert planner._final_chapter_closure_findings(card) == []


def test_final_chapter_closure_requires_boolean_climax_flag() -> None:
    card = _card(methodology_contract={"is_climax": "false", "pacing_mode": "resolution", "hooks_to_plant": []})
    findings = planner._final_chapter_closure_findings(card)
    assert "methodology_contract.is_climax must be a boolean" in findings


def test_terminal_landing_card_passes() -> None:
    assert planner._final_chapter_closure_findings(_card()) == []


def test_real_outline_prompt_is_finale_scoped_to_target_range() -> None:
    project = _target_twenty_project()
    args = (
        project,
        {"title": "终局测试"},
        {"protagonist": {"name": "陆衡"}},
        [{"volume_number": 1, "chapter_count_target": 20}],
    )
    _system, finale_prompt = planner._volume_outline_prompts(
        *args,
        volume_entry={
            "volume_number": 1,
            "chapter_count_target": 1,
            "chapter_range": [20, 20],
            "outline_batch_range": [20, 20],
        },
    )
    _system, middle_prompt = planner._volume_outline_prompts(
        *args,
        volume_entry={
            "volume_number": 1,
            "chapter_count_target": 1,
            "chapter_range": [19, 19],
            "outline_batch_range": [19, 19],
        },
    )

    assert "有限篇幅终章硬契约" in finale_prompt
    assert "有限篇幅终章硬契约" not in middle_prompt


def test_fallback_finale_does_not_emit_serial_continuation_hook() -> None:
    finale = planner._fallback_finale_hook_description(protagonist="陆衡", language="zh")
    ordinary = planner._fallback_hook_description(
        project_slug="demo",
        chapter_number=19,
        phase="climax",
        unique_beat="掌门逼近",
        protagonist="陆衡",
        language="zh",
    )

    assert "收束画面" in finale
    assert "却发现" in ordinary


def _target_twenty_project() -> ProjectModel:
    project = ProjectModel(
        slug="finale-contract-test",
        title="终局合同测试",
        genre="science-fantasy",
        target_word_count=40000,
        target_chapters=20,
        audience="web-serial",
        metadata_json={"premise": "一名导航员发现帝国正在篡改航线记录。"},
    )
    project.id = uuid4()
    return project


def test_real_fallback_batch_final_card_survives_model_validation() -> None:
    project = _target_twenty_project()
    premise = project.metadata_json["premise"]
    book = planner._fallback_book_spec(project, premise)
    world = planner._fallback_world_spec(project, premise, book)
    cast = planner._fallback_cast_spec(project, premise, book, world)
    batch = planner._fallback_chapter_outline_batch(
        project,
        book,
        cast,
        [{"volume_number": 1, "chapter_count_target": 20, "chapter_range": [1, 20]}],
    )

    from bestseller.domain.workflow import ChapterOutlineBatchInput

    normalized = ChapterOutlineBatchInput.model_validate(batch)
    final = normalized.chapters[-1].model_dump(mode="json")
    assert final["methodology_contract"]["is_climax"] is True
    assert final["causal_contract"]["final_state"]
    assert planner._final_chapter_closure_findings(final) == []


class _EmptyResult:
    def all(self) -> list[object]:
        return []

    def scalars(self) -> "_EmptyResult":
        return self


class _EmptySession:
    async def execute(self, _statement: object) -> _EmptyResult:
        return _EmptyResult()

    async def scalar(self, _statement: object) -> None:
        return None

    async def scalars(self, _statement: object) -> _EmptyResult:
        return _EmptyResult()


@pytest.mark.asyncio
async def test_finale_failure_enters_existing_repair_loop_with_targeted_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _target_twenty_project()
    premise = project.metadata_json["premise"]
    book = planner._fallback_book_spec(project, premise)
    world = planner._fallback_world_spec(project, premise, book)
    cast = planner._fallback_cast_spec(project, premise, book, world)
    full = planner._fallback_chapter_outline_batch(
        project,
        book,
        cast,
        [{"volume_number": 1, "chapter_count_target": 20, "chapter_range": [1, 20]}],
    )
    valid = {"batch_name": "final", "chapters": [full["chapters"][-1]]}
    invalid = copy.deepcopy(valid)
    invalid["chapters"][0]["methodology_contract"].update(
        {"is_climax": False, "pacing_mode": "climax", "hooks_to_plant": ["掌门继续逼近"]}
    )
    invalid["chapters"][0]["causal_contract"].pop("final_state", None)
    settings = load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )
    settings.pipeline.enable_rolling_outline = False
    settings.pipeline.chapter_outline_repair_attempts = 2
    prompts: list[str] = []

    async def fake_generate(*_args: object, **kwargs: object):
        prompts.append(str(kwargs["user_prompt"]))
        return (invalid if len(prompts) == 1 else valid), uuid4()

    def fake_outline_prompts(*_args: object, **kwargs: object) -> tuple[str, str]:
        constraints = kwargs.get("extra_constraints") or []
        return "system", "user\n" + "\n".join(str(item) for item in constraints)

    monkeypatch.setattr(planner, "_volume_outline_prompts", fake_outline_prompts)
    monkeypatch.setattr(
        planner,
        "_compile_volume_outline_prompt",
        lambda *_a, **kwargs: (kwargs["system_prompt"], kwargs["user_prompt"]),
    )
    monkeypatch.setattr(planner, "_generate_structured_artifact", fake_generate)
    result, _run_id, history = await planner._generate_volume_outline_with_repair_loop(
        _EmptySession(),
        settings,
        project=project,
        workflow_run_id=uuid4(),
        logical_name="final_chapter_outline",
        book_spec=book,
        cast_spec=cast,
        volume_plan=[{"volume_number": 1, "chapter_count_target": 20, "chapter_range": [1, 20]}],
        volume_entry={
            "volume_number": 1,
            "chapter_count_target": 1,
            "chapter_range": [20, 20],
            "outline_batch_range": [20, 20],
        },
        fallback_payload=valid,
        volume_number=1,
        expected_count=1,
        chapter_number_offset=20,
        revealed_ledger_block=None,
        base_constraints=[],
    )

    assert len(prompts) == 2
    assert history[0]["status"] == "failed"
    assert "FINAL_CHAPTER_CLOSURE_CONTRACT_FAILED" in history[0]["error"]
    assert "只修复目标终章的故事落点" in prompts[1]
    assert result["chapters"][0]["causal_contract"]["final_state"]
