"""Pipeline caller RED: real native rows, denying boundaries, no quality PASS."""

from copy import deepcopy
from uuid import UUID, uuid4

from issuer_caller_test_support import (
    ReachedCommonPreflight,
    assert_denials,
    comment_upgrade,
    retained_matcher,
    snapshot,
)
import pytest

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import chapter_review_preflight as preflight
from bestseller.services import context, pipelines, review_source_context
from bestseller.services import retained_review_input_proof as retained

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def retry_case(issuer_case, monkeypatch):
    case = issuer_case
    comment_upgrade(case)

    async def semantic_data(session, settings, chapter):
        assert session is case.session and settings is case.settings
        assert chapter.id == case.chapter.id
        # Isolate the unrelated semantic dependency builder, not a review or
        # input proof. The fixture deliberately has no semantic/PASS attestation.
        assert "semantic_dependencies_sha256" not in case.cycle
        return None

    async def forbidden(*args, **kwargs):
        pytest.fail("A boundary test must stop before a cycle write or checkpoint")

    monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", semantic_data)
    # Keep the real read-only retained loader: native archived custody uses it.
    monkeypatch.setattr(pipelines, "_checkpoint_commit", forbidden)
    monkeypatch.setattr(
        retained, "build_chapter_writer_context", context.build_chapter_writer_context
    )
    monkeypatch.setattr(
        retained, "load_review_source_context", review_source_context.load_review_source_context
    )
    return case


async def invoke_retry(case, **kwargs):
    owner = await case.session.get(WorkflowRunModel, UUID(case.cycle["review_workflow_run_id"]))
    assert owner is not None
    return await pipelines._review_chapter_with_evidence_retry(
        case.session,
        case.settings,
        case.project.slug,
        case.chapter.chapter_number,
        workflow=owner,
        draft=case.draft,
        **kwargs,
    )


def stop_at_preflight(case, monkeypatch):
    calls = []

    async def stop(session, settings, chapter):
        assert session is case.session and settings is case.settings
        assert chapter.id == case.chapter.id and chapter.project_id == case.project.id
        cycle = chapter.metadata_json["chapter_critic_cycles"][case.ids["cycle_id"]]
        assert cycle == case.cycle
        assert cycle["source_draft_id"] == str(case.draft.id)
        assert (
            cycle["source_body_hash"]
            == pipelines.chapter_review_body_fingerprint(case.draft.content_md)["digest"]
        )
        calls.append(chapter.id)
        raise ReachedCommonPreflight

    monkeypatch.setattr(preflight, "validate_retained_review_before_processing", stop)
    return calls


async def test_retry_policy_drift_reaches_common_preflight_before_old_byte_guard(
    retry_case,
    monkeypatch,
):
    case = retry_case
    calls = stop_at_preflight(case, monkeypatch)
    before = await snapshot(case)
    with pytest.raises(ReachedCommonPreflight):
        await invoke_retry(case)
    assert calls == [case.chapter.id]
    assert await snapshot(case) == before


@pytest.mark.parametrize("field", ["source_draft_id", "source_body_hash"])
async def test_retry_rejects_wrong_native_cycle_source_before_common_preflight(
    retry_case,
    monkeypatch,
    field,
):
    case = retry_case
    changed = deepcopy(case.chapter.metadata_json)
    changed["chapter_critic_cycles"][case.ids["cycle_id"]][field] = (
        str(uuid4()) if field == "source_draft_id" else "0" * 64
    )
    case.chapter.metadata_json = changed
    await case.session.commit()
    calls = stop_at_preflight(case, monkeypatch)
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^chapter_critic_cycle_source_mismatch$"):
        await invoke_retry(case)
    assert calls == []
    assert await snapshot(case) == before


async def test_retry_caller_cycle_cannot_replace_native_active_cycle(retry_case, monkeypatch):
    case = retry_case
    calls = stop_at_preflight(case, monkeypatch)
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^chapter_critic_cycle_not_authorized$"):
        await invoke_retry(case, cycle_id="0" * 64)
    assert calls == []
    assert await snapshot(case) == before


async def test_retry_real_common_preflight_keeps_nonissuer_source_type_denial(retry_case):
    case = retry_case
    case.current_source["current_source_channels"]["literal_false"] = 0
    before = await snapshot(case)
    # The real public preflight, native custody and full source renderer execute.
    # This never installs a successful preflight/quality result.
    with pytest.raises(ValueError, match="^review_issuer_view:nonissuer_source_changed$"):
        await invoke_retry(case)
    assert await snapshot(case) == before


@pytest.mark.parametrize("issuer_case", [{"marker": None, "capsule": False}], indirect=True)
async def test_retry_pre_f0_drift_cannot_gain_post_f0_compatibility(retry_case):
    before = await snapshot(retry_case)
    with pytest.raises(
        ValueError, match="^chapter_critic_policy_changed_requires_explicit_recovery$"
    ):
        await invoke_retry(retry_case)
    assert await snapshot(retry_case) == before


async def matcher_operands(case, *, upgrade=True):
    if upgrade:
        comment_upgrade(case)
    technical = await case.module.require_cycle_policy(case.session, case.settings, **case.ids)
    assert_denials(technical)
    assert (
        technical["current_runtime_policy_sha256"] != case.cycle["policy_source_hash"]
    ) is upgrade
    # Local matcher operands only: NOT a preflight success and never fed into
    # run_chapter_pipeline. UNKNOWN/quality-zero and all denials remain intact.
    return {
        **deepcopy(technical),
        **deepcopy(technical["binding"]),
        "policy_source_hash": case.cycle["policy_source_hash"],
        "reuse_kind": "available_review",
    }


async def test_retained_matcher_uses_current_runtime_hash_and_preserves_issued_hash(issuer_case):
    case = issuer_case
    proof = await matcher_operands(case)
    before = await snapshot(case)
    original = deepcopy(proof)
    assert retained_matcher(case, proof)(case.draft) is True
    assert proof == original
    assert proof["policy_source_hash"] == proof["issued_runtime_policy_sha256"]
    assert case.report.verdict == "unknown" and case.quality.is_current is False
    assert await snapshot(case) == before


@pytest.mark.parametrize(
    "field", ["project_id", "chapter_id", "draft_id", "body_sha256", "cycle_id"]
)
async def test_retained_matcher_rejects_cross_source_or_cycle_operand(issuer_case, field):
    case = issuer_case
    proof = await matcher_operands(case, upgrade=False)
    # Actual equal issued/current bytes isolate native identity checks.
    proof[field] = "0" * 64 if field in {"cycle_id", "body_sha256"} else str(uuid4())
    before = await snapshot(case)
    assert retained_matcher(case, proof)(case.draft) is False
    assert await snapshot(case) == before


async def test_retained_matcher_rejects_runtime_drift_after_preflight(issuer_case):
    case = issuer_case
    proof = await matcher_operands(case, upgrade=False)
    proof["current_runtime_policy_sha256"] = "0" * 64
    before = await snapshot(case)
    assert retained_matcher(case, proof)(case.draft) is False
    assert await snapshot(case) == before
