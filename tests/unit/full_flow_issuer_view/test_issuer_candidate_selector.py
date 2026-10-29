"""Proposed native selected-draft seam RED; current-only API must stay strict."""

from copy import deepcopy
import inspect
from uuid import UUID

from issuer_caller_test_support import (
    ReachedCommonPreflight,
    assert_denials,
    comment_upgrade,
    selection_call_probe,
    snapshot,
)
import pytest

from bestseller.services import chapter_draft_candidate_eligibility as eligibility
from bestseller.services import chapter_review_preflight as preflight
from bestseller.services import pipelines

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def selected_case(issuer_case):
    case = issuer_case
    # Real native selector fact; no promotion/grade/score is fabricated. This
    # fixture has no replacement current draft and cannot prove stall adoption.
    case.draft.is_current = False
    await case.session.commit()
    comment_upgrade(case)
    return case


async def test_current_only_source_view_still_rejects_noncurrent_candidate(selected_case):
    case = selected_case
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^review_issuer_view:current_draft_changed$"):
        await case.module.derive_current_primary_source(case.session, case.settings, **case.ids)
    assert await snapshot(case) == before


async def test_selected_source_view_binds_native_draft_cycle_and_original_paid_source(
    selected_case,
):
    case = selected_case
    # Separate ID-only API, not an allow_noncurrent/approved flag on the current
    # renderer. Its result remains a technical source view with zero authority.
    derive = getattr(case.module, "derive_selected_primary_source")
    before = await snapshot(case)
    result = await derive(case.session, case.settings, **case.ids)
    assert result["source"] == case.source
    assert result["binding"] == {
        **{key: str(value) for key, value in case.ids.items()},
        "report_id": str(case.report.id),
        "body_sha256": case.cycle["source_body_hash"],
    }
    assert result["issued_runtime_policy_sha256"] == case.cycle["policy_source_hash"]
    assert result["current_runtime_policy_sha256"] != case.cycle["policy_source_hash"]
    assert_denials(result)
    assert case.source_reads and case.draft.is_current is False
    assert case.report.verdict == "unknown" and case.quality.is_current is False
    assert await snapshot(case) == before


async def test_selected_source_view_compares_all_nonissuer_fields_and_types(selected_case):
    case = selected_case
    derive = getattr(case.module, "derive_selected_primary_source")
    case.current_source["current_source_channels"]["literal_false"] = 0
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^review_issuer_view:nonissuer_source_changed$") as caught:
        await derive(case.session, case.settings, **case.ids)
    assert any(
        item["field"] == "/primary/source/current_source_channels/literal_false"
        and item["expected"]["type"] == "bool"
        and item["actual"]["type"] == "int"
        for item in caught.value.differences
    )
    assert await snapshot(case) == before


async def test_selected_source_view_cannot_hide_unknown_paid_attempt(selected_case):
    case = selected_case
    derive = getattr(case.module, "derive_selected_primary_source")
    case.attempt.status = "unknown"
    await case.session.commit()
    before = await snapshot(case)
    with pytest.raises(ValueError):
        await derive(case.session, case.settings, **case.ids)
    assert await snapshot(case) == before


@pytest.mark.parametrize("issuer_case", [{"marker": None, "capsule": False}], indirect=True)
async def test_selected_pre_f0_source_does_not_receive_an_issuer_upgrade(selected_case):
    case = selected_case
    derive = getattr(case.module, "derive_selected_primary_source")
    before = await snapshot(case)
    assert await derive(case.session, case.settings, **case.ids) is None
    assert await snapshot(case) == before


async def test_candidate_eligibility_keeps_strict_legacy_default_without_settings():
    parameter = inspect.signature(eligibility.eligible_reviewed_candidates).parameters.get(
        "settings"
    )
    assert parameter is not None, "Native candidate verification needs actual settings"
    assert parameter.default is None


@pytest.mark.parametrize("supplied", [False, True])
async def test_candidate_without_settings_cannot_gain_comment_drift_compatibility(
    selected_case,
    monkeypatch,
    supplied,
):
    case = selected_case

    async def forbidden(*args, **kwargs):
        pytest.fail("No settings means the original strict legacy policy path only")

    monkeypatch.setattr(preflight, "validate_retained_review_candidate", forbidden, raising=False)
    before = await snapshot(case)
    eligible, decisions = await eligibility.eligible_reviewed_candidates(
        case.session,
        chapter=case.chapter,
        rows=[(case.draft, case.quality)],
        semantic_hash=None,
        policy_hash=pipelines.chapter_review_recovery_source_hash(),
        fingerprint=pipelines.chapter_review_body_fingerprint,
        retained_result=forbidden,
        evidence_unknown=pipelines._chapter_critic_evidence_unknown,
        **({"settings": None} if supplied else {}),
    )
    assert eligible == []
    assert "evaluation_policy_changed" in decisions[0]["reasons"]
    assert await snapshot(case) == before


async def test_candidate_drift_routes_native_ids_through_common_candidate_preflight(
    selected_case,
    monkeypatch,
):
    case = selected_case
    calls = []

    async def stop(session, settings, *, project_id, chapter_id, draft_id, cycle_id):
        assert session is case.session and settings is case.settings
        assert (
            dict(project_id=project_id, chapter_id=chapter_id, draft_id=draft_id, cycle_id=cycle_id)
            == case.ids
        )
        calls.append(draft_id)
        raise ReachedCommonPreflight

    # Missing future seam may be installed only as an unconditional STOP.
    # This is a caller-routing test; it cannot provide a success/proof dictionary.
    monkeypatch.setattr(preflight, "validate_retained_review_candidate", stop, raising=False)
    before = await snapshot(case)
    with pytest.raises(ReachedCommonPreflight):
        await eligibility.eligible_reviewed_candidates(
            case.session,
            settings=case.settings,
            chapter=case.chapter,
            rows=[(case.draft, case.quality)],
            semantic_hash=None,
            policy_hash=pipelines.chapter_review_recovery_source_hash(),
            fingerprint=pipelines.chapter_review_body_fingerprint,
            retained_result=pipelines._retained_chapter_critic_result,
            evidence_unknown=pipelines._chapter_critic_evidence_unknown,
        )
    assert calls == [case.draft.id]
    assert await snapshot(case) == before


async def test_stall_caller_passes_native_settings_and_original_candidate_rows(issuer_case):
    case = issuer_case
    native_rows = [(case.draft, case.quality)]
    calls = []

    async def stop(session, **kwargs):
        assert session is case.session
        assert kwargs.get("settings") is case.settings
        assert kwargs["chapter"] is case.chapter and kwargs["rows"] is native_rows
        assert kwargs["retained_result"] is pipelines._retained_chapter_critic_result
        assert kwargs["fingerprint"] is pipelines.chapter_review_body_fingerprint
        calls.append(True)
        raise ReachedCommonPreflight

    before = await snapshot(case)
    probe = selection_call_probe(
        {
            "session": case.session,
            "settings": case.settings,
            "chapter": case.chapter,
            "rows": native_rows,
            "semantic_hash": None,
            "policy_hash": pipelines.chapter_review_recovery_source_hash(),
            "eligible_reviewed_candidates": stop,
        }
    )
    with pytest.raises(ReachedCommonPreflight):
        await probe()
    assert calls == [True]
    assert await snapshot(case) == before


@pytest.mark.parametrize("override", ["source", "issuer_proof", "approved"])
async def test_candidate_preflight_does_not_accept_caller_truth(override):
    validate = getattr(preflight, "validate_retained_review_candidate")
    with pytest.raises(TypeError):
        inspect.signature(validate).bind(
            None,
            None,
            project_id=UUID(int=1),
            chapter_id=UUID(int=2),
            draft_id=UUID(int=3),
            cycle_id="0" * 64,
            **{override: True},
        )


async def test_candidate_preflight_full_input_denial_is_not_replaced_by_custody_only(
    selected_case,
    monkeypatch,
):
    from bestseller.services import context, review_source_context
    from bestseller.services import retained_review_input_proof as retained

    case = selected_case
    validate = getattr(preflight, "validate_retained_review_candidate")
    monkeypatch.setattr(
        retained, "build_chapter_writer_context", context.build_chapter_writer_context
    )
    monkeypatch.setattr(
        retained, "load_review_source_context", review_source_context.load_review_source_context
    )
    calls = []
    original = retained.verify_retained_review_inputs

    async def observed(session, settings, **kwargs):
        assert kwargs["draft"].id == case.draft.id and kwargs["report"].id == case.report.id
        assert kwargs["cycle"] == case.cycle
        calls.append(deepcopy(kwargs["cycle"]))
        return await original(session, settings, **kwargs)

    monkeypatch.setattr(retained, "verify_retained_review_inputs", observed)
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^retained_review_input_commercial_result_missing$"):
        await validate(case.session, case.settings, **case.ids)
    assert calls == [case.cycle]
    assert await snapshot(case) == before
