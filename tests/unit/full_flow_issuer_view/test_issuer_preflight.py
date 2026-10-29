"""Additional preflight RED: original native inputs, no fabricated reuse grade."""

from copy import deepcopy

from issuer_view_test_support import filesystem, rows
import pytest

from bestseller.services import chapter_review_preflight as preflight
from bestseller.services import context, review_source_context
from bestseller.services import retained_review_input_proof as retained
from bestseller.services import review_issuer_archive as archive

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def preflight_case(issuer_case, monkeypatch):
    # Substitute DATA readers only. Actual archived custody, code equivalence,
    # paid/input/primary/commercial proofs and normal preflight all execute.
    monkeypatch.setattr(
        retained, "build_chapter_writer_context", context.build_chapter_writer_context
    )
    monkeypatch.setattr(
        retained, "load_review_source_context", review_source_context.load_review_source_context
    )
    path = issuer_case.root / "src/bestseller/services/reviews.py"
    path.write_bytes(path.read_bytes() + b"\n# preflight unit comment-equivalence input\n")
    return issuer_case


async def invoke(case):
    return await preflight.validate_retained_review_before_processing(
        case.session,
        case.settings,
        case.chapter,
    )


async def snapshot(case):
    return await rows(case.session, case.models), filesystem(case.artifact_root)


async def test_normal_preflight_runs_native_policy_and_full_input_proof_before_drift_admission(
    preflight_case,
    monkeypatch,
):
    case = preflight_case
    actual_policy = case.module.require_cycle_policy
    actual_inputs = retained.verify_retained_review_inputs
    policies, inputs = [], []

    async def policy(session, settings, **ids):
        assert ids == case.ids
        result = await actual_policy(session, settings, **ids)
        policies.append(deepcopy(result))
        return result

    async def full_inputs(session, settings, **kwargs):
        inputs.append((kwargs["draft"].id, kwargs["report"].id, kwargs["cycle"]["cycle_id"]))
        return await actual_inputs(session, settings, **kwargs)

    monkeypatch.setattr(case.module, "require_cycle_policy", policy)
    monkeypatch.setattr(retained, "verify_retained_review_inputs", full_inputs)
    assert case.settings.pipeline.enable_chapter_llm_commercial_judge is True
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^retained_review_input_commercial_result_missing$"):
        await invoke(case)
    assert policies and inputs == [(case.draft.id, case.report.id, case.ids["cycle_id"])]
    assert policies[-1]["issued_runtime_policy_sha256"] == case.cycle["policy_source_hash"]
    assert policies[-1]["current_runtime_policy_sha256"] != case.cycle["policy_source_hash"]
    for key in (
        "grade_granted",
        "new_credit_allowed",
        "dispatch_authorized",
        "adoption_authorized",
        "full_quality_proof_verified",
    ):
        assert policies[-1][key] is False
    assert await snapshot(case) == before


async def test_normal_preflight_does_not_ignore_nonissuer_type_change(preflight_case):
    case = preflight_case
    case.current_source["current_source_channels"]["literal_false"] = 0
    before = await snapshot(case)
    with pytest.raises(ValueError, match="^review_issuer_view:nonissuer_source_changed$") as caught:
        await invoke(case)
    assert any(
        item["field"] == "/primary/source/current_source_channels/literal_false"
        and item["expected"]["type"] == "bool"
        and item["actual"]["type"] == "int"
        for item in caught.value.differences
    )
    assert await snapshot(case) == before


@pytest.mark.parametrize("issuer_case", [{"capsule": False}], indirect=True)
async def test_normal_preflight_cannot_waive_missing_original_capsule(preflight_case):
    before = await snapshot(preflight_case)
    with pytest.raises(ValueError, match="^review_issuer_view:capsule_missing$"):
        await invoke(preflight_case)
    assert await snapshot(preflight_case) == before


async def test_normal_preflight_rejects_actual_python_semantic_change(preflight_case):
    case = preflight_case
    path = case.root / "src/bestseller/services/reviews.py"
    path.write_bytes(path.read_bytes() + b"\nISSUER_PREFLIGHT_UNIT_CHANGED = True\n")
    before = await snapshot(case)
    with pytest.raises(
        archive.IssuerArchiveError, match="^review_issuer_archive:python_ast_changed$"
    ):
        await invoke(case)
    assert await snapshot(case) == before


@pytest.mark.parametrize("issuer_case", [{"marker": None, "capsule": False}], indirect=True)
async def test_pre_f0_normal_preflight_keeps_original_drift_denial(preflight_case, monkeypatch):
    case = preflight_case

    def forbidden(*args, **kwargs):
        pytest.fail("Original pre-F0 inputs cannot be assigned a current issuer")

    monkeypatch.setattr(archive, "capture_current_issuer_archive", forbidden)
    monkeypatch.setattr(archive, "verify_current_issuer_archive", forbidden)
    before = await snapshot(case)
    with pytest.raises(
        ValueError, match="^chapter_critic_policy_changed_requires_explicit_recovery$"
    ):
        await invoke(case)
    assert await snapshot(case) == before


@pytest.mark.parametrize("override", ["expected_policy_hash", "issuer_proof", "approved"])
async def test_public_preflight_has_no_caller_policy_or_proof_override(override):
    with pytest.raises(TypeError):
        await preflight.validate_retained_review_before_processing(
            None,
            None,
            None,
            **{override: True},
        )
