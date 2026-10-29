"""Consumer REDs: real native custody, no synthetic grade or reuse positive.

The fixture's primary return is deliberately not a literary judgment. Its
UNKNOWN report must remain unusable even when source/code custody authenticates.
Main executes these tests; they are not native C0/C1/C2 issuance evidence.
"""

import ast
from copy import deepcopy
import hashlib
from pathlib import Path

from issuer_view_test_support import canonical, filesystem, rows
import pytest

from bestseller.services import chapter_review_reconciliation as reconciliation
from bestseller.services import context, review_source_context
from bestseller.services import recorded_review_sources as recorded
from bestseller.services import retained_review_input_proof as retained
from bestseller.services import review_issuer_archive as archive

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# Local integration regression snapshots, NOT accepted issuer hashes, historical
# transition pins, paid evidence, or a substitute for behavioral quality proof.
# The bounded hook must leave each entire existing comparison function intact.
UNCHANGED_PROOF_AST = {
    "_primary_source_integrity": "b1dca5b839bd7116dc9aef72a79ea93fd51a87230abfa1a9094d3148d4b794f2",
    "_physical_return": "59d793e49a3dc5e1166df82e4f157c41485fc5047acbd7e7ffb91cff30f16e36",
    "_projection_audit_compatibility": (
        "2afc8949fcc76fa9cc870b98abc78053f62"
        "e9979dc346925bd9aec6b3a95324c"
    ),
    "_commercial_proof": "1e60b85aa7beed83be4070b843796a2538d4b9f5b78f9c4654de190428dc8679",
}


@pytest.fixture
def consumer_case(issuer_case, monkeypatch):
    # Same current DATA providers as the issuer fixture, including retained's
    # imported aliases. Neither a verifier nor an authority result is replaced.
    monkeypatch.setattr(
        retained, "build_chapter_writer_context", context.build_chapter_writer_context
    )
    monkeypatch.setattr(
        retained, "load_review_source_context", review_source_context.load_review_source_context
    )
    assert issuer_case.settings.pipeline.enable_chapter_llm_commercial_judge is True
    return issuer_case


async def snapshot(case):
    return await rows(case.session, case.models), filesystem(case.artifact_root)


async def retained_consumer(case):
    return await retained.verify_retained_review_inputs(
        case.session,
        case.settings,
        project=case.project,
        chapter=case.chapter,
        draft=case.draft,
        report=case.report,
        cycle=deepcopy(case.cycle),
    )


async def resume_consumer(case):
    return await reconciliation.prepare_native_review_resume(
        case.session,
        case.settings,
        project_id=case.project.id,
        chapter_id=case.chapter.id,
    )


def observe_view(case, monkeypatch, name):
    actual = getattr(case.module, name)
    results = []

    async def observed(session, settings, **ids):
        assert ids == case.ids
        result = await actual(session, settings, **ids)
        results.append(deepcopy(result))
        return result

    monkeypatch.setattr(case.module, name, observed)
    return results


def assert_technical_only(result):
    for key in (
        "grade_granted",
        "new_credit_allowed",
        "dispatch_authorized",
        "adoption_authorized",
        "unknown_reconciled",
        "issued_report_verified",
        "full_quality_proof_verified",
        "current_applicability_checked",
    ):
        assert result[key] is False, key


@pytest.mark.parametrize("name", list(UNCHANGED_PROOF_AST))
async def test_consumer_hook_preserves_complete_existing_proof_function_ast(name):
    tree = ast.parse(Path(retained.__file__).read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    actual = hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest()
    assert actual == UNCHANGED_PROOF_AST[name]


async def test_retained_consumer_uses_native_view_then_keeps_commercial_denial(
    consumer_case,
    monkeypatch,
):
    case = consumer_case
    derived = observe_view(case, monkeypatch, "derive_current_primary_source")
    actual_reader = recorded.read_archived_review_sources
    actual_integrity = retained._primary_source_integrity
    actual_commercial = retained._commercial_proof
    native_reads, source_checks, commercial_checks = [], [], []

    async def reader(session, settings, **ids):
        native_reads.append(deepcopy(ids))
        return await actual_reader(session, settings, **ids)

    def integrity(request, receipt, source):
        source_checks.append(deepcopy(source))
        return actual_integrity(request, receipt, source)

    async def commercial(*args, **kwargs):
        commercial_checks.append(canonical(kwargs["report"].structured_output))
        return await actual_commercial(*args, **kwargs)

    monkeypatch.setattr(recorded, "read_archived_review_sources", reader)
    monkeypatch.setattr(retained, "_primary_source_integrity", integrity)
    monkeypatch.setattr(retained, "_commercial_proof", commercial)
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^retained_review_input_commercial_result_missing$"):
        await retained_consumer(case)
    assert derived and native_reads and all(ids == case.ids for ids in native_reads)
    assert source_checks == [case.source]
    assert commercial_checks == [canonical(case.report.structured_output)]
    assert derived[-1]["source"]["review_issuer"] == case.capsule
    assert_technical_only(derived[-1])
    assert await snapshot(case) == before


@pytest.mark.parametrize("fault", ["bool", "number", "list_order", "text", "nested_issuer"])
async def test_retained_consumer_rejects_every_nonissuer_source_change(consumer_case, fault):
    case = consumer_case
    channels = case.current_source["current_source_channels"]
    if fault == "bool":
        channels["literal_false"] = 0
    elif fault == "number":
        channels["literal_zero"] = 0.0
    elif fault == "list_order":
        channels["story_bible"]["relationships"].reverse()
    elif fault == "text":
        case.current_source["authority"] += " "
    else:
        channels["review_issuer"] = deepcopy(case.capsule)
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^review_issuer_view:nonissuer_source_changed$"):
        await retained_consumer(case)
    assert await snapshot(case) == before


async def test_retained_consumer_comment_equivalence_does_not_waive_quality(
    consumer_case,
    monkeypatch,
):
    case = consumer_case
    derived = observe_view(case, monkeypatch, "derive_current_primary_source")
    before = await snapshot(case)
    path = case.root / "src/bestseller/services/reviews.py"
    path.write_bytes(path.read_bytes() + b"\n# consumer source-equivalence unit control\n")
    with pytest.raises(ValueError, match="^retained_review_input_commercial_result_missing$"):
        await retained_consumer(case)
    assert derived and derived[-1]["source"] == case.source
    assert derived[-1]["issued_runtime_policy_sha256"] == case.cycle["policy_source_hash"]
    assert derived[-1]["current_runtime_policy_sha256"] != case.cycle["policy_source_hash"]
    assert_technical_only(derived[-1])
    assert await snapshot(case) == before


@pytest.mark.parametrize("issuer_case", [{"capsule": False}], indirect=True)
@pytest.mark.parametrize("consumer", [retained_consumer, resume_consumer])
async def test_actual_consumers_reject_post_f0_missing_capsule(consumer_case, consumer):
    before = await snapshot(consumer_case)
    with pytest.raises(ValueError, match="^review_issuer_view:capsule_missing$"):
        await consumer(consumer_case)
    assert await snapshot(consumer_case) == before


@pytest.mark.parametrize("consumer", [retained_consumer, resume_consumer])
async def test_actual_consumers_reject_unsettled_original_paid_attempt(consumer_case, consumer):
    case = consumer_case
    case.attempt.status = "unknown"
    await case.session.commit()
    before = await snapshot(case)
    # Either the existing paid guard or the native view can reject first. A
    # technical capsule must never waive this uncertainty or open paid capacity.
    with pytest.raises(ValueError):
        await consumer(case)
    assert await snapshot(case) == before


async def test_resume_consumer_authenticates_native_policy_without_alias_or_grade(
    consumer_case,
    monkeypatch,
):
    case = consumer_case
    policies = observe_view(case, monkeypatch, "require_cycle_policy")
    before = await snapshot(case)
    result = await resume_consumer(case)
    assert (
        policies
        and policies[-1]["issued_runtime_policy_sha256"] == case.cycle["policy_source_hash"]
    )
    assert_technical_only(policies[-1])
    assert result["status"] == "unchanged" and result["chapter_receipts"] == []
    assert result["provider_calls"] == result["new_attempts"] == 0
    assert (
        result["credit_released"]
        is result["dispatch_authorized"]
        is result["adoption_authorized"]
        is False
    )
    assert case.report.verdict == "unknown" and case.quality.is_current is False
    assert await snapshot(case) == before


@pytest.mark.parametrize("issuer_case", [{"marker": None, "capsule": False}], indirect=True)
async def test_pre_f0_consumers_keep_legacy_route_without_capture_or_upgrade(
    consumer_case,
    monkeypatch,
):
    case = consumer_case
    derived = observe_view(case, monkeypatch, "derive_current_primary_source")
    policies = observe_view(case, monkeypatch, "require_cycle_policy")

    def forbidden(*args, **kwargs):
        pytest.fail("A pre-F0 paid source must not be captured or assigned a new issuer")

    monkeypatch.setattr(archive, "capture_current_issuer_archive", forbidden)
    monkeypatch.setattr(archive, "verify_current_issuer_archive", forbidden)
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^retained_review_input_commercial_result_missing$"):
        await retained_consumer(case)
    result = await resume_consumer(case)
    assert derived and all(value is None for value in derived)
    assert policies and all(value is None for value in policies)
    assert result["status"] == "unchanged" and result["chapter_receipts"] == []
    assert await snapshot(case) == before


async def test_explicit_reconciliation_still_refuses_unknown_after_native_capsule_auth(
    consumer_case,
):
    case = consumer_case
    before = await snapshot(case)
    technical = await case.module.auth_source_capsule(case.session, case.settings, **case.ids)
    assert_technical_only(technical)
    with pytest.raises(ValueError, match="^review_reconciliation_unknown_evidence$"):
        await reconciliation.reconcile_retained_chapter_review(
            case.session,
            case.settings,
            project_id=case.project.id,
            chapter_id=case.chapter.id,
            expected_cycle_id=case.ids["cycle_id"],
            expected_current_draft_id=case.draft.id,
            previous_pipeline_source=b"caller bytes cannot supply issuer or quality proof",
            actor="issuer-consumer-unit",
            evidence_ref="unit-negative-only",
            allow_current_automatic_cycle=True,
        )
    assert await snapshot(case) == before
