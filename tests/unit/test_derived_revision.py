"""Contract tests for bounded derived-input revision and review."""
# ruff: noqa: E501, RUF001

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.services import derived_revision as revision
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.story_rebuild_workspace import source_content_hash

pytestmark = pytest.mark.unit


PROJECT_ID = "00000000-0000-0000-0000-000000000001"
SOURCE_ID = "00000000-0000-0000-0000-000000000002"
PREMISE_ID = "00000000-0000-0000-0000-000000000003"


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        id=PROJECT_ID,
        title="山城符铺",
        slug="book",
        genre="玄幻",
        sub_genre=None,
        language="zh-CN",
        audience="男频",
        target_chapters=50,
        metadata_json={
            "book_design_snapshot_status": "locked",
            "book_design_snapshot_hash": "current",
            "book_design_snapshot": {
                "snapshot_id": "snapshot-id",
                "source_hash": "current",
                "schema_version": "book-design.v1",
                "protagonist": {"name": "纪年"},
                "reader_promise": "纪年开符铺",
                "core_story_engine": "卖符换来新生意",
                "tone": "轻松喜剧",
                "creation_intent": "爽点前置",
            },
            "story_enhancers": {"cost_style": "minimal"},
            "writing_profile": {
                "character": {"power_tiers": ["初阶", "高阶"]},
                "world": {"power_system_style": "越阶后手抖；不会退阶。"},
            },
            "growth_curve": "第50章在县衙对质，把符匾钉回正街。",
            "golden_finger": "怕字瞳",
            "api_key": "must-not-be-copied",
        },
    )


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        id=SOURCE_ID,
        version_no=6,
        status="approved",
        schema_version="1.0",
        content={"_meta": {"source_snapshot_hash": "old"}, "rules": []},
    )


def _parent() -> dict:
    from bestseller.services.world_recompile_candidate import build_world_recompile_candidate

    return build_world_recompile_candidate(
        _project(),
        premise="纪年在山城开符铺。",
        source_artifact=_source(),
        premise_artifact_id=PREMISE_ID,
    )


def _issues(parent: dict) -> list[dict]:
    return [
        {
            "path": ["writing_profile", "world", "power_system_style"],
            "quote": "越阶后手抖；不会退阶。",
            "problem": "把动作不适误当成主角长期削弱。",
            "required_outcome": "保留能力反差与不会退阶保护，明确可持续使用。",
        },
        {
            "path": ["growth_curve"],
            "quote": "第50章在县衙对质，把符匾钉回正街。",
            "problem": "成长终点需要与本期承诺明确对齐。",
            "required_outcome": "保留第50章兑现目标，不推迟到下一期。",
        },
    ]


def _payload() -> dict:
    return {
        "changes": [
            {
                "path": ["writing_profile", "world", "power_system_style"],
                "after": "越阶时手会抖，但符可持续画成，且不会退阶。",
                "reason": "区分短时动作限制与长期主角削弱，保留保护条款。",
            },
            {
                "path": ["growth_curve"],
                "after": "第50章在县衙三方对质，以一镇怕字图谱把符匾钉回正街。",
                "reason": "把本期成长终点写成批准承诺中的可验证兑现。",
            },
        ]
    }


def test_two_whitelisted_paths_preserve_locked_identity_and_options():
    parent = _parent()
    before = deepcopy(parent["frozen_inputs"])
    report = revision.build_derived_revision(parent, issues=_issues(parent), payload=_payload())

    assert [c["path"] for c in report["changes"]] == [
        ["writing_profile", "world", "power_system_style"], ["growth_curve"]
    ]
    frozen = report["candidate"]["frozen_inputs"]
    assert frozen["project"] == before["project"]
    assert frozen["metadata"]["book_design_snapshot"] == before["metadata"]["book_design_snapshot"]
    assert frozen["metadata"]["story_enhancers"] == before["metadata"]["story_enhancers"]
    assert frozen["metadata"]["golden_finger"] == before["metadata"]["golden_finger"]
    assert report["status"] == "needs_review"
    assert report["review_input_hash"]


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda p: p.update({"changes": p["changes"] + [{"path": ["growth_curve"], "after": "x", "reason": "x"}]}), "INCOMPLETE_CHANGES"),
        (lambda p: p["changes"].__setitem__(1, deepcopy(p["changes"][0])), "UNAUTHORIZED_CHANGE"),
        (lambda p: p["changes"][0].update({"after": "越阶后手抖；不会退阶。"}), "NO_CHANGE"),
    ],
)
def test_extra_duplicate_or_noop_change_is_rejected(mutator, match):
    parent = _parent()
    payload = _payload()
    mutator(payload)
    with pytest.raises(ValueError, match=f"DERIVED_REVISION_{match}"):
        revision.build_derived_revision(parent, issues=_issues(parent), payload=payload)


def test_stale_issue_quote_is_rejected():
    parent = _parent()
    issues = _issues(parent)
    issues[0]["quote"] = "不存在的原文"
    with pytest.raises(ValueError, match="ISSUE_NOT_IN_SOURCE"):
        revision.validate_revision_issues(parent, issues)


def test_verify_rejects_rehashed_candidate_tampered_with_without_raw_replay():
    report = revision.build_derived_revision(_parent(), issues=_issues(_parent()), payload=_payload())
    report["status"] = "review_passed"
    report["editor_receipt"] = {
        "llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop",
        "fallback_used": False, "raw_response": json.dumps(_payload(), ensure_ascii=False),
    }
    checks = _checks(report)
    report["reviews"] = [
        {"llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop",
         "fallback_used": False, "raw_response": json.dumps(checks, ensure_ascii=False),
         "checks": checks["checks"]},
        {"llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop",
         "fallback_used": False, "raw_response": json.dumps(checks, ensure_ascii=False),
         "checks": checks["checks"]},
    ]
    report["candidate"]["candidate_world"]["world_name"] = "被篡改"
    with pytest.raises(ValueError, match="REPLAY_MISMATCH:candidate"):
        revision.verify_derived_revision_report(report)


def test_legacy_source_bound_candidate_replays_after_projection_fields_are_added():
    """A persisted pre-projection receipt must remain exactly replayable."""

    report = revision.build_derived_revision(_parent(), issues=_issues(_parent()), payload=_payload())
    legacy_candidate = deepcopy(report["candidate"])
    series_engine = legacy_candidate["candidate_book"]["series_engine"]
    for key in ("first_three_chapter_hook", "chapter_ending_hook_strategy", "payoff_rhythm"):
        series_engine.pop(key, None)
    for key in (
        "approved_story_design",
        "approved_story_design_source_hash",
        "approved_story_design_missing",
        "approved_story_design_derived",
    ):
        legacy_candidate["candidate_book"]["_meta"].pop(key, None)
    legacy_candidate["book_hash"] = source_content_hash(legacy_candidate["candidate_book"])
    legacy_candidate["candidate_world"]["_meta"]["book_spec_hash"] = legacy_candidate["book_hash"]
    legacy_candidate["candidate_hash"] = source_content_hash(legacy_candidate["candidate_world"])
    report["candidate"] = legacy_candidate
    report["status"] = "review_passed"
    report["editor_receipt"] = {
        "llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop",
        "fallback_used": False, "raw_response": json.dumps(_payload(), ensure_ascii=False),
    }
    checks = _checks(report)
    report["reviews"] = [
        {"llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop",
         "fallback_used": False, "raw_response": json.dumps(checks, ensure_ascii=False),
         "checks": checks["checks"]},
        {"llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop",
         "fallback_used": False, "raw_response": json.dumps(checks, ensure_ascii=False),
         "checks": checks["checks"]},
    ]
    revision.verify_derived_revision_report(report)


def _checks(report: dict, *, verdict: str = "pass") -> dict:
    index = revision.world_authority_source_index(revision.revision_review_sources(report))
    by_domain: dict[str, str] = {}
    for entry in index:
        by_domain.setdefault(entry["path"][0], entry["source_id"])
    after = by_domain["after"]
    return {
        "review_input_hash": report["review_input_hash"],
        "checks": [
            {"axis": "mechanism_preservation", "verdict": verdict, "reason": "前后机制可追溯。", "source_ids": [by_domain["before"], after]},
            {"axis": "cost_compatibility", "verdict": verdict, "reason": "符合正式代价政策。", "source_ids": [by_domain["authority"], after]},
            {"axis": "growth_deadline", "verdict": verdict, "reason": "终点仍在本期。", "source_ids": [by_domain["authority"], after]},
            {"axis": "story_identity", "verdict": verdict, "reason": "主角和风格未变。", "source_ids": [by_domain["authority"], after]},
        ],
    }


def test_review_sources_include_authority_before_and_after():
    report = revision.build_derived_revision(_parent(), issues=_issues(_parent()), payload=_payload())
    sources = revision.revision_review_sources(report)
    assert sources["authority"]["design"]["protagonist"] == {"name": "纪年"}
    assert {tuple(row["path"]) for row in sources["before"]["fields"]} == {
        ("writing_profile", "world", "power_system_style"), ("growth_curve",)
    }
    assert {tuple(row["path"]) for row in sources["after"]["fields"]} == {
        ("writing_profile", "world", "power_system_style"), ("growth_curve",)
    }


@pytest.mark.parametrize("bad", ["nonpass", "fallback", "duplicate"])
def test_verify_rejects_nonpass_fallback_or_duplicate_receipts(bad):
    report = revision.build_derived_revision(_parent(), issues=_issues(_parent()), payload=_payload())
    editor = {"llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop", "fallback_used": False,
              "raw_response": json.dumps(_payload(), ensure_ascii=False)}
    rebuilt = revision.build_derived_revision(report["parent"], issues=report["issues"], payload=_payload())
    reviews = []
    for _ in range(2):
        checks = _checks(rebuilt)
        reviews.append({"llm_run_id": str(uuid4()), "provider": "openai", "finish_reason": "stop", "fallback_used": False,
                        "raw_response": json.dumps(checks, ensure_ascii=False), "checks": checks["checks"]})
    if bad == "nonpass":
        checks = _checks(rebuilt, verdict="revise")
        reviews[0].update(raw_response=json.dumps(checks), checks=checks["checks"])
    elif bad == "fallback":
        reviews[0]["fallback_used"] = True
    else:
        reviews[1]["llm_run_id"] = editor["llm_run_id"]
    report.update(rebuilt, editor_receipt=editor, reviews=reviews, status="review_passed")
    with pytest.raises(ValueError):
        revision.verify_derived_revision_report(report)


def _fake_result(content: str, *, fallback: bool = False, run_id: UUID | None = None) -> LLMCompletionResult:
    return LLMCompletionResult(content=content, provider="fixture", model_name="fixture", llm_run_id=run_id or uuid4(),
                               fallback_used=fallback, finish_reason="stop")


@pytest.mark.asyncio
async def test_async_review_makes_exactly_editor_plus_two_independent_calls(monkeypatch):
    calls: list[str] = []

    async def fake_complete(_session, _settings, request):
        calls.append(request.logical_role)
        if request.logical_role == "editor":
            return _fake_result(json.dumps(_payload(), ensure_ascii=False))
        prompt = json.loads(request.user_prompt.split("\n")[-1])
        # Reconstruct enough of the report for source IDs from the editor output.
        interim = revision.build_derived_revision(_parent(), issues=_issues(_parent()), payload=_payload())
        checks = _checks(interim)
        checks["review_input_hash"] = prompt["review_input_hash"]
        return _fake_result(json.dumps(checks, ensure_ascii=False))

    monkeypatch.setattr(revision, "complete_text", fake_complete)
    result = await revision.propose_and_review_derived_revision(None, SimpleNamespace(), parent=_parent(), issues=_issues(_parent()))
    assert calls == ["editor", "critic", "critic"]
    assert result["status"] == "review_passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fallback", "duplicate"])
async def test_async_failure_is_captured_without_outer_retry(monkeypatch, failure):
    calls: list[str] = []
    editor_id = uuid4()

    async def fake_complete(_session, _settings, request):
        calls.append(request.logical_role)
        if request.logical_role == "editor":
            return _fake_result(json.dumps(_payload(), ensure_ascii=False), run_id=editor_id)
        interim = revision.build_derived_revision(_parent(), issues=_issues(_parent()), payload=_payload())
        checks = _checks(interim)
        return _fake_result(json.dumps(checks), fallback=failure == "fallback", run_id=editor_id if failure == "duplicate" else None)

    monkeypatch.setattr(revision, "complete_text", fake_complete)
    with pytest.raises(revision.DerivedRevisionError) as exc:
        await revision.propose_and_review_derived_revision(None, SimpleNamespace(), parent=_parent(), issues=_issues(_parent()))
    assert len(calls) == (2 if failure == "fallback" else 3)
    assert exc.value.report["status"] == "needs_review"
