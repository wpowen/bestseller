"""Execute owned preprocessing/guard slices of the real chapter pipeline.

These tests do not reproduce the branch predicates. AST extraction retains the
actual source statements and imports; only storage/paid stage boundaries are
stubbed. This is local orchestration evidence, not a full pipeline/model run.
"""

import ast
from copy import deepcopy
import logging
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from bestseller.domain.enums import ChapterStatus, WorkflowStatus
from bestseller.infra.db.models import ChapterDraftVersionModel
from bestseller.services import litstyle_prose_judge as litstyle
from bestseller.services import (
    llm,
    pipelines,
    reader_judge,
    reviews,
    world_law_consistency_gate,
    world_model_injection,
)

pytestmark = pytest.mark.unit
SOURCE = Path(pipelines.__file__)
PIPELINE = next(
    node
    for node in ast.parse(SOURCE.read_text()).body
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_chapter_pipeline"
)


class ForbiddenDispatch(BaseException):
    """Best-effort exception handlers must not swallow a real provider call."""


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise ForbiddenDispatch("strict resume tests forbid PG/provider/network")

    async def paid_forbidden(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for module in (llm, reviews, reader_judge, litstyle):
        monkeypatch.setattr(module, "complete_text", paid_forbidden)
    monkeypatch.setenv("LITSTYLE_LONGFORM_ADVISORY", "1")


def calls(node, name):
    return any(
        isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == name
        for item in ast.walk(node)
    )


def branch_for(name):
    candidates = [
        node
        for node in ast.walk(PIPELINE)
        if isinstance(node, ast.If)
        and calls(node, name)
        and any(
            isinstance(term, ast.Name) and term.id == "resume_existing_draft_only"
            for term in ast.walk(node.test)
        )
    ]
    assert candidates, f"actual pipeline block missing: {name}"
    return min(candidates, key=lambda node: node.end_lineno - node.lineno)


async def execute(nodes, environment):
    wrapper = ast.AsyncFunctionDef(
        name="source_slice",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            kwonlyargs=[ast.arg(arg=name) for name in environment],
            kw_defaults=[None] * len(environment),
            defaults=[],
        ),
        body=[
            *deepcopy(nodes),
            ast.Return(
                value=ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[])
            ),
        ],
        decorator_list=[],
    )
    namespace = dict(environment)
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return await namespace["source_slice"](**environment)


def environment(*, strict=False, retained=False):
    project = SimpleNamespace(
        id=uuid4(),
        language="zh-CN",
        genre="玄幻",
        sub_genre=None,
        invariants_json={},
        metadata_json={"world_model": {"laws": ["A sealed letter stays sealed."]}},
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        status="review",
        production_state="review_unknown",
        metadata_json={
            "chapter_review_attempts_active": 2,
            "chapter_critic_cycles": {"original": {"state": "prepared", "attempts": [1, 2]}},
            "chapter_review_active_cycle": {"cycle_id": "original", "state": "prepared"},
        },
    )
    draft = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        content_md="守塔人把封好的航图交给领航员。领航员当面锁上了匣子。",
        is_current=True,
        promotion_state="candidate",
    )
    return {
        "session": SimpleNamespace(),
        "settings": SimpleNamespace(pipeline=SimpleNamespace()),
        "project": project,
        "project_id": project.id,
        "project_slug": "unrelated-lighthouse",
        "chapter": chapter,
        "chapter_id": chapter.id,
        "chapter_number": 1,
        "chapter_draft": draft,
        "workflow_run": SimpleNamespace(id=uuid4(), metadata_json={}),
        "step_order": 0,
        "scene_requires_human_review": False,
        "resume_existing_draft_only": strict,
        "retained_review_reuse": {"retained": True} if retained else None,
        "retained_review_matches": lambda draft: retained,
        "requested_by": "test",
        "create_workflow_step_run": AsyncMock(),
        "ChapterStatus": ChapterStatus,
        "WorkflowStatus": WorkflowStatus,
        "logger": logging.getLogger(__name__),
        "os": os,
    }


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("retained", [False, True])
async def test_preprocessing_backfills_skip_strict_but_keep_invariants_and_truth(strict, retained):
    env = environment(strict=strict, retained=retained)
    events = []
    names = (
        "_ensure_project_invariants",
        "_ensure_emotion_kernel_backfill_for_pipeline",
        "_ensure_public_emotion_kernel_backfill_for_pipeline",
        "_ensure_entry_system_backfill_for_pipeline",
        "_enforce_truth_version_guard",
    )
    for name in names:

        async def record(*args, _name=name, **kwargs):
            events.append(_name)

        env[name] = record
    start = next(
        i
        for i, node in enumerate(PIPELINE.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "invariants_were_missing" for t in node.targets)
    )
    end = next(
        i
        for i, node in enumerate(PIPELINE.body)
        if isinstance(node, ast.Expr) and calls(node, "_enforce_truth_version_guard")
    )
    before = deepcopy((vars(env["chapter"]), vars(env["chapter_draft"])))
    await execute(PIPELINE.body[start : end + 1], env)
    expected = [names[0], *([] if strict or retained else names[1:4]), names[-1]]
    assert events == expected
    assert (vars(env["chapter"]), vars(env["chapter_draft"])) == before


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("stage", ["litstyle", "world_law"])
async def test_optional_advisories_skip_strict_and_preserve_ordinary_calls(
    monkeypatch, strict, retained, stage
):
    env = environment(strict=strict, retained=retained)
    env["is_english_language"] = lambda language: False
    env["_wm_meta"] = env["project"].metadata_json
    if stage == "litstyle":
        name = "judge_chapter_litstyle_stable"
        result = SimpleNamespace(top_issues=[], final_score=68, level="B", ai_tone_penalty=0)
        spy = AsyncMock(return_value=result)
        monkeypatch.setattr(litstyle, name, spy)
    else:
        name = "evaluate_world_law_consistency_llm"
        result = SimpleNamespace(to_checker_report=lambda: SimpleNamespace(issues=[]))
        spy = AsyncMock(return_value=result)
        monkeypatch.setattr(world_law_consistency_gate, name, spy)
        monkeypatch.setattr(
            world_model_injection, "extract_world_model", lambda meta: meta["world_model"]
        )
    before = deepcopy((vars(env["chapter"]), vars(env["chapter_draft"])))
    await execute([branch_for(name)], env)
    assert spy.await_count == int(not strict and not retained)
    if spy.await_count:
        assert spy.call_args.kwargs["project_id"] == env["project"].id
        assert spy.call_args.kwargs["workflow_run_id"] == env["workflow_run"].id
    assert (vars(env["chapter"]), vars(env["chapter_draft"])) == before


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("audit_only", [False, True])
@pytest.mark.parametrize("enforce_voice", [False, True])
async def test_reader_skips_only_optional_audit_and_preserves_required_voice(
    monkeypatch, strict, audit_only, enforce_voice
):
    env = environment(strict=strict)
    env.update(
        _rq_cfg=SimpleNamespace(
            enable_llm_reader_judge=True,
            reader_judge_audit_only=audit_only,
            enforce_reader_judge_voice_axes=enforce_voice,
        ),
        _grade_text=env["chapter_draft"].content_md,
        _prose_quality_score=None,
    )
    result = reader_judge.ReaderJudgeResult(
        prose_quality_score=0.2,
        dimensions={"ai_taste": 0.1, "human_voice": 0.2},
        comment="fixture",
        used_llm=False,
    )
    spy = AsyncMock(return_value=result)
    monkeypatch.setattr(reader_judge, "judge_chapter_readability", spy)
    before = deepcopy(env["chapter"].metadata_json)
    output = await execute([branch_for("judge_chapter_readability")], env)
    expected = not (strict and audit_only and not enforce_voice)
    assert spy.await_count == int(expected)
    metadata = env["chapter"].metadata_json
    for key, value in before.items():
        assert metadata[key] == value
    if expected:
        assert metadata["reader_judge"]["audit_only"] is audit_only
        assert bool(metadata["reader_judge_voice_failures"]) is enforce_voice
        assert metadata["reader_judge_voice_rewrite_count"] == int(enforce_voice)
        assert output.get("_prose_quality_score") == (None if audit_only else 0.2)
        assert spy.call_args.args[2] == env["chapter_draft"].content_md
    else:
        assert metadata == before


@pytest.mark.parametrize("strict", [False, True])
async def test_disabled_reader_stays_disabled(monkeypatch, strict):
    env = environment(strict=strict)
    env["_rq_cfg"] = SimpleNamespace(enable_llm_reader_judge=False)
    spy = AsyncMock(side_effect=ForbiddenDispatch("disabled reader dispatched"))
    monkeypatch.setattr(reader_judge, "judge_chapter_readability", spy)
    await execute([branch_for("judge_chapter_readability")], env)
    spy.assert_not_awaited()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("blocking", [False, True])
@pytest.mark.parametrize("enabled,retained", [(True, False), (False, False), (True, True)])
async def test_ranking_skips_only_nonblocking_strict_or_existing_retained_gate(
    strict, blocking, enabled, retained
):
    env = environment(strict=strict, retained=retained)
    env["settings"].pipeline = SimpleNamespace(
        enable_fanqie_long_ranking_gate=enabled, fanqie_long_ranking_block_on_failure=blocking
    )
    spy = AsyncMock(return_value={"blocks_write": False})
    env["_run_fanqie_long_gate_for_chapter"] = spy
    output = await execute([branch_for("_run_fanqie_long_gate_for_chapter")], env)
    expected = enabled and not retained and (not strict or blocking)
    assert spy.await_count == int(expected)
    if expected:
        assert spy.call_args.kwargs["block_on_failure"] is blocking
        assert spy.call_args.kwargs["chapter_draft"] is env["chapter_draft"]
    assert output.get("scene_requires_human_review", False) is False
    assert env["chapter"].production_state == "review_unknown"


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("unavailable", [False, True])
async def test_required_ranking_still_blocks_on_failure_or_unavailable(strict, unavailable):
    env = environment(strict=strict)
    env["settings"].pipeline = SimpleNamespace(
        enable_fanqie_long_ranking_gate=True, fanqie_long_ranking_block_on_failure=True
    )
    env["_run_fanqie_long_gate_for_chapter"] = AsyncMock(
        return_value={"blocks_write": True},
        side_effect=RuntimeError("fixture unavailable") if unavailable else None,
    )
    before = deepcopy(env["chapter"].metadata_json)
    output = await execute([branch_for("_run_fanqie_long_gate_for_chapter")], env)
    assert output["scene_requires_human_review"] is True
    assert env["chapter"].status == ChapterStatus.REVISION.value
    assert env["chapter"].production_state == "blocked"
    assert env["chapter"].metadata_json == before
    metadata = env["workflow_run"].metadata_json
    assert metadata["blocked_by_fanqie_long_ranking_gate"] is True
    assert metadata["fanqie_long_ranking_gate"]["blocks_write"] is True
    if unavailable:
        assert metadata["fanqie_long_ranking_gate"]["error"] == "review_unavailable"


@pytest.mark.parametrize("fault", [None, "draft_id", "body", "missing", "export", "supersede"])
async def test_strict_entry_keeps_exact_current_body_and_incompatible_mode_guards(fault):
    env = environment(strict=True)
    draft = env["chapter_draft"]
    binding = {
        "draft_id": str(draft.id),
        "body_hash": pipelines.chapter_review_body_fingerprint(draft.content_md)["digest"],
    }
    if fault == "draft_id":
        binding["draft_id"] = str(uuid4())
    if fault == "body":
        binding["body_hash"] = "changed-body"
    scalar = AsyncMock(return_value=None if fault == "missing" else draft)
    env.update(
        session=SimpleNamespace(scalar=scalar),
        select=select,
        ChapterDraftVersionModel=ChapterDraftVersionModel,
        chapter_review_body_fingerprint=pipelines.chapter_review_body_fingerprint,
        export_markdown=fault == "export",
        supersede_pending_rewrites=fault == "supersede",
        review_source_binding=binding,
    )
    entry = next(
        node
        for node in PIPELINE.body
        if isinstance(node, ast.If) and calls(node, "chapter_review_body_fingerprint")
    )
    before = deepcopy((vars(env["chapter"]), vars(draft)))
    if fault:
        code = (
            "incompatible_mode"
            if fault in {"export", "supersede"}
            else "current_draft_missing"
            if fault == "missing"
            else "source_mismatch"
        )
        with pytest.raises(ValueError, match=f"chapter_review_recovery_{code}"):
            await execute([entry], env)
    else:
        result = await execute([entry], env)
        assert result["locked_review_source"] == binding
    if fault not in {"export", "supersede"}:
        query = scalar.call_args.args[0]
        sql = str(query)
        assert "chapter_draft_versions.project_id =" in sql
        assert "chapter_draft_versions.chapter_id =" in sql
        assert "chapter_draft_versions.is_current IS true" in sql
        assert set(query.compile().params.values()) == {env["project_id"], env["chapter_id"]}
        assert query.get_execution_options()["populate_existing"] is True
    else:
        scalar.assert_not_awaited()
    assert (vars(env["chapter"]), vars(draft)) == before


@pytest.mark.parametrize("fault", [None, "draft_id", "body", "policy", "absent"])
async def test_retained_match_still_requires_draft_body_and_policy(fault):
    env = environment(strict=True)
    draft = env["chapter_draft"]
    proof = {
        "draft_id": str(draft.id),
        "body_sha256": pipelines.chapter_review_body_fingerprint(draft.content_md)["digest"],
        "policy_source_hash": "current-policy",
    }
    if fault == "draft_id":
        proof["draft_id"] = str(uuid4())
    elif fault == "body":
        proof["body_sha256"] = "old-body"
    elif fault == "policy":
        proof["policy_source_hash"] = "old-policy"
    env.update(
        retained_review_reuse=None if fault == "absent" else proof,
        chapter_review_body_fingerprint=pipelines.chapter_review_body_fingerprint,
        chapter_review_recovery_source_hash=lambda: "current-policy",
    )
    function = next(
        node
        for node in PIPELINE.body
        if isinstance(node, ast.FunctionDef) and node.name == "retained_review_matches"
    )
    result = await execute([function], env)
    assert result["retained_review_matches"](draft) is (fault is None)


@pytest.mark.parametrize("fault", [None, "current_body", "current_id", "in_memory_body", "missing"])
async def test_strict_review_rechecks_current_body_before_critic_without_resetting_cycle(fault):
    env = environment(strict=True)
    draft = env["chapter_draft"]
    current = deepcopy(draft)
    binding = {
        "draft_id": str(draft.id),
        "body_hash": pipelines.chapter_review_body_fingerprint(draft.content_md)["digest"],
    }
    if fault == "current_body":
        current.content_md += " changed"
    elif fault == "current_id":
        current.id = uuid4()
    elif fault == "in_memory_body":
        draft.content_md += " unpersisted"
    elif fault == "missing":
        current = None
    finish = AsyncMock(return_value="unknown-source-changed")
    env.update(
        session=SimpleNamespace(scalar=AsyncMock(return_value=current)),
        locked_review_source=binding,
        select=select,
        ChapterDraftVersionModel=ChapterDraftVersionModel,
        _chapter_review_source_matches=pipelines._chapter_review_source_matches,
        _finish_unknown_chapter_review=finish,
    )
    loop = next(
        node
        for node in ast.walk(PIPELINE)
        if isinstance(node, ast.While) and calls(node, "_review_chapter_with_evidence_retry")
    )
    guard = next(
        node
        for node in loop.body
        if isinstance(node, ast.If) and calls(node, "_chapter_review_source_matches")
    )
    before = deepcopy(env["chapter"].metadata_json)
    result = await execute([guard], env)
    if fault:
        assert result == "unknown-source-changed"
        assert finish.call_args.kwargs["reason"] == "chapter_review_source_changed"
    else:
        finish.assert_not_awaited()
        assert result["chapter_draft"] is current
    assert env["chapter"].metadata_json == before
