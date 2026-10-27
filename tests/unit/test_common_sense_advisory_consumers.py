"""Consumer action follows explicit blocking, without discarding diagnostics.

Pure local calls only. The knowledge heuristic does not prove actor identity or
ignorance; no review, promotion, or publication acceptance is claimed here.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import common_sense_gate as common
from bestseller.services import exports, reviews
from bestseller.services.chapter_constraint_manifest import (
    ChapterConstraintManifest,
    validate_chapter_prose_for_promotion,
)
from bestseller.services.chapter_quality_bundle import (
    ChapterQualityBundleContext,
    run_chapter_quality_bundle,
)
from bestseller.settings import load_settings

# Chinese punctuation deliberately exercises the real prose scanners.
# ruff: noqa: RUF001

pytestmark = pytest.mark.unit
CODE = "lay_character_rule_knowledge_leak"
BODY = "导师读着‘登记’：“登记以后还要核对登记记录。”"


def _finding(*, blocking):
    return common.CommonSenseFinding(
        code=CODE,
        severity="medium",
        blocking=blocking,
        message="Unverified lexical association; actor knowledge is unknown.",
        evidence={"window": BODY, "terms": ["登记"], "authority": "heuristic"},
    )


def _inject(monkeypatch, *, blocking):
    finding = _finding(blocking=blocking)
    calls = []

    def evaluate(text, **kwargs):
        calls.append((text, kwargs))
        return common.CommonSenseGateReport(passed=not blocking, findings=[finding])

    monkeypatch.setattr(common, "evaluate_common_sense_gate", evaluate)
    monkeypatch.setattr(
        "bestseller.services.chapter_constraint_manifest.evaluate_common_sense_gate",
        evaluate,
    )
    return finding, calls


def _review(finding_text=BODY):
    chapter = SimpleNamespace(
        chapter_number=2,
        title="核对记录",
        chapter_goal="核对记录",
        target_word_count=1000,
        metadata_json={},
    )
    draft = SimpleNamespace(
        content_md=finding_text,
        word_count=1000,
        assembled_from_scene_draft_ids=[],
    )
    return reviews.evaluate_chapter_draft(
        chapter=chapter,
        scenes=[SimpleNamespace(scene_number=1, title="记录")],
        draft=draft,
        settings=load_settings(env={}),
        genre="general-fiction",
    )


def _export_payload(*, text=BODY, state="ok"):
    project_id, chapter_id = uuid4(), uuid4()
    project = SimpleNamespace(
        id=project_id,
        slug="advisory-consumer",
        language="zh-CN",
        genre="general-fiction",
        sub_genre=None,
        target_chapters=3,
    )
    chapter = SimpleNamespace(
        id=chapter_id,
        chapter_number=2,
        status="complete",
        production_state=state,
        metadata_json={},
        title="记录",
        target_word_count=2200,
    )
    draft = SimpleNamespace(
        id=uuid4(),
        content_md=text,
        word_count=len(text),
        version_no=1,
        is_current=True,
        promotion_state="candidate",
        metadata_json={},
        assembled_from_scene_draft_ids=["chapter_first_scene:2"],
    )
    return project, chapter, draft


@pytest.mark.parametrize("blocking", [False, True])
def test_bundle_retains_diagnostic_and_uses_blocking_flag(monkeypatch, blocking):
    original, calls = _inject(monkeypatch, blocking=blocking)
    before = original.model_dump(mode="json")
    report = run_chapter_quality_bundle(
        BODY,
        ChapterQualityBundleContext(chapter_number=2, target_chapter_words=2200),
    )
    assert calls
    finding = next(item for item in report.findings if item.code == CODE.upper())
    assert finding.severity == "medium" and finding.blocking is blocking
    assert finding.evidence == before["evidence"]
    assert finding.repair_hint == before["message"]
    assert (finding in report.blocking_findings) is blocking
    retained = next(item for item in report.to_dict()["findings"] if item["code"] == CODE.upper())
    assert retained["blocking"] is blocking and retained["evidence"] == before["evidence"]
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize("blocking", [False, True])
def test_direct_review_admits_only_blocking_common_sense_findings(monkeypatch, blocking):
    original, calls = _inject(monkeypatch, blocking=blocking)
    before = original.model_dump(mode="json")
    result = _review()
    assert calls
    matching = [
        item for item in result.findings if item.category == "common_sense" and CODE in item.message
    ]
    assert bool(matching) is blocking
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize("blocking", [False, True])
def test_manifest_promotion_respects_action_without_ignoring_other_contracts(monkeypatch, blocking):
    original, calls = _inject(monkeypatch, blocking=blocking)
    before = original.model_dump(mode="json")
    report = validate_chapter_prose_for_promotion(
        BODY,
        ChapterConstraintManifest(chapter_number=2),
    )
    assert calls
    assert any(CODE in item for item in report.violations) is blocking
    assert report.passed is (not blocking)
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize("blocking", [False, True])
def test_publication_respects_action_without_promotion_or_debt_exemption(monkeypatch, blocking):
    original, calls = _inject(monkeypatch, blocking=blocking)
    project, chapter, draft = _export_payload()
    original_metadata = deepcopy(chapter.metadata_json)
    # Isolate the independent publication consumer, rather than letting a
    # second bundle invocation provide the same blocker for the wrong reason.
    settings = load_settings(env={})
    settings.pipeline.commercial_strict_quality_mode = False
    with monkeypatch.context() as local:
        local.setattr("bestseller.settings.get_settings", lambda: settings)
        blockers = exports.collect_publication_blockers(project, [(chapter, draft)])
    assert calls
    assert any(CODE in item for item in blockers) is blocking
    assert chapter.metadata_json == original_metadata
    assert original.evidence["authority"] == "heuristic"


def test_explicit_forbidden_term_still_blocks_manifest_with_advisory_knowledge(monkeypatch):
    _inject(monkeypatch, blocking=False)
    manifest = ChapterConstraintManifest(
        chapter_number=2,
        forbidden_terms=["AUTHOR_FORBIDDEN_TOKEN"],
    )
    report = validate_chapter_prose_for_promotion(BODY + " AUTHOR_FORBIDDEN_TOKEN", manifest)
    assert report.passed is False
    assert any("forbidden term 'AUTHOR_FORBIDDEN_TOKEN'" in item for item in report.violations)
    assert not any(CODE in item for item in report.violations)


def test_real_length_hard_gate_remains_blocking_in_bundle(monkeypatch):
    _inject(monkeypatch, blocking=False)
    report = run_chapter_quality_bundle(
        BODY,
        ChapterQualityBundleContext(chapter_number=2, target_chapter_words=2200),
    )
    assert report.passed is False
    assert any(item.code == "CHAPTER_TOO_SHORT" for item in report.blocking_findings)
    assert all(item.code != CODE.upper() for item in report.blocking_findings)


def test_export_structural_state_and_length_remain_blocking(monkeypatch):
    _inject(monkeypatch, blocking=False)
    settings = load_settings(env={})
    settings.pipeline.commercial_strict_quality_mode = False
    project, chapter, draft = _export_payload(state="blocked")
    with monkeypatch.context() as local:
        local.setattr("bestseller.settings.get_settings", lambda: settings)
        blockers = exports.collect_publication_blockers(project, [(chapter, draft)])
    assert any("门禁状态" in item for item in blockers)
    assert any("商业硬底线" in item for item in blockers)
    assert not any(CODE in item for item in blockers)


def test_real_knowledge_heuristic_retains_unverified_diagnostic_without_false_actor_authority():
    text = (
        "墙上贴着告示：「录名」之后须核对记录。录名者交回凭条。"
        "管理员再次检查录名记录。\n"
        "导师站在柜台旁：“这次录名已经登记好了。”"
    )
    report = common.evaluate_common_sense_gate(text, genre="general-fiction", chapter_number=2)
    findings = [item for item in report.findings if item.code == CODE]
    assert findings and all(
        item.blocking is False and item.severity == "medium" for item in findings
    )
    assert all(item.evidence.get("authority") == "heuristic_only" for item in findings)
    assert all("非专业角色" not in item.message for item in findings)
    assert not any(
        item.blocking and item.severity in {"high", "medium"} for item in report.findings
    )
    assert report.passed is True


def test_other_real_common_sense_hard_findings_keep_default_blocking():
    report = common.evaluate_common_sense_gate(
        "还剩十五分钟。走了十分钟，还剩十四分钟。",
        chapter_number=2,
    )
    matching = [
        item for item in report.findings if item.code == "remaining_time_arithmetic_conflict"
    ]
    assert matching and all(item.blocking is True for item in matching)
    assert report.passed is False
