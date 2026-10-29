"""RED technical contracts; never evidence of normal issuance or review quality."""

from copy import deepcopy
import inspect
import json
from pathlib import Path
from uuid import uuid4

from issuer_view_test_support import canonical, filesystem, rows
import pytest

from bestseller.services import chapter_review_reconciliation, pipelines
from bestseller.services import recorded_review_sources as recorded
from bestseller.services import retained_review_input_proof as retained
from bestseller.services import review_issuer_archive as archive

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
APIS = ("auth_source_capsule", "derive_current_primary_source", "require_cycle_policy")
DENIALS = (
    "grade_granted",
    "new_credit_allowed",
    "dispatch_authorized",
    "adoption_authorized",
    "unknown_reconciled",
    "issued_report_verified",
    "full_quality_proof_verified",
    "current_applicability_checked",
)


async def invoke(case, name="auth_source_capsule", **ids):
    return await getattr(case.module, name)(case.session, case.settings, **{**case.ids, **ids})


def denials(result):
    for key in DENIALS:
        assert key in result and result[key] is False, key
    assert result.get("reuse_kind") not in {"available_review", "resume_pending_review"}


async def reject(case, code, name="auth_source_capsule", **ids):
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:" + code + "$"
    ) as caught:
        await invoke(case, name, **ids)
    assert caught.value.code == code


async def test_shared_view_must_be_registered_in_the_native_policy_inventory(issuer_view):
    root = Path(pipelines.__file__).resolve().parents[3]
    paths = chapter_review_reconciliation._review_policy_inventory(
        root, Path(pipelines.__file__).read_text()
    )
    assert root / "src/bestseller/services/review_issuer_view.py" in paths


@pytest.mark.parametrize("name", APIS)
async def test_api_accepts_only_native_selectors(issuer_view, name):
    function = getattr(issuer_view, name)
    assert inspect.iscoroutinefunction(function)
    parameters = inspect.signature(function).parameters
    assert list(parameters) == [
        "session",
        "settings",
        "project_id",
        "chapter_id",
        "draft_id",
        "cycle_id",
    ]
    assert all(
        parameters[key].kind is inspect.Parameter.KEYWORD_ONLY
        for key in ("project_id", "chapter_id", "draft_id", "cycle_id")
    )


@pytest.mark.parametrize(
    "key", ["source", "capsule", "approved", "expected_policy_hash", "cycle", "view"]
)
async def test_caller_cannot_supply_source_hash_approval_or_prevalidated_view(issuer_case, key):
    for name in APIS:
        with pytest.raises(TypeError):
            await getattr(issuer_case.module, name)(
                issuer_case.session, issuer_case.settings, **issuer_case.ids, **{key: True}
            )


async def test_authentication_uses_the_real_archived_reader_and_preserves_all_rows(
    issuer_case, monkeypatch
):
    case = issuer_case
    actual = recorded.read_archived_review_sources
    calls = []

    async def observed(session, settings, **ids):
        calls.append(deepcopy(ids))
        return await actual(session, settings, **ids)

    monkeypatch.setattr(recorded, "read_archived_review_sources", observed)
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    result = await invoke(case)
    assert calls and all(item == case.ids for item in calls)
    assert result["status"] == "authenticated_source_capsule"
    assert result["capsule"] == case.capsule
    assert result["paid_input_custody_verified"] is True
    assert result["source_capsule_authenticated"] is True
    assert result["binding"] == {
        **{key: str(value) for key, value in case.ids.items()},
        "report_id": str(case.report.id),
        "body_sha256": case.cycle["source_body_hash"],
    }
    assert result["paid_calls"][0]["llm_run_id"] == str(case.run.id)
    denials(result)
    assert case.source_reads == []
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before


@pytest.mark.parametrize("name", APIS)
async def test_neither_layer_calls_the_retained_verifier_or_issues_a_new_capsule(
    issuer_case, monkeypatch, name
):
    case = issuer_case

    async def recursion(*args, **kwargs):
        pytest.fail("auth/derived policy must not recurse through the retained-input verifier")

    def issuance(*args, **kwargs):
        pytest.fail("read-only derived input must not issue or archive a fresh capsule")

    monkeypatch.setattr(retained, "verify_retained_review_inputs", recursion)
    monkeypatch.setattr(archive, "capture_current_issuer_archive", issuance)
    result = await invoke(case, name)
    denials(result)


@pytest.mark.parametrize(
    "issuer_case", [{"marker": None, "capsule": False}, {"marker": None}], indirect=True
)
async def test_pre_f0_cannot_be_retroactively_signed_or_automatically_compatible(
    issuer_case, monkeypatch
):
    case = issuer_case

    def forbidden(*args, **kwargs):
        pytest.fail("a pre-F0 source cannot acquire a current capsule or compatibility proof")

    monkeypatch.setattr(archive, "verify_current_issuer_archive", forbidden)
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    for name in APIS:
        assert await invoke(case, name) is None
    assert case.source_reads == []
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before


@pytest.mark.parametrize("issuer_case", [{"capsule": False}], indirect=True)
async def test_post_f0_missing_capsule_is_denied_without_legacy_downgrade(issuer_case):
    for name in APIS:
        await reject(issuer_case, "capsule_missing", name)


@pytest.mark.parametrize(
    "issuer_case",
    [{"marker": True}, {"marker": 1}, {"marker": "future-unreviewed-order"}],
    indirect=True,
)
async def test_unknown_or_type_coerced_source_epoch_is_denied(issuer_case):
    await reject(issuer_case, "source_epoch_invalid")


async def test_metadata_only_capsule_cannot_substitute_for_original_paid_source(issuer_case):
    case = issuer_case
    # This changes only a synthetic unit run row, never a real paid artifact.
    case.run.metadata_json = {
        **case.run.metadata_json,
        "review_issuer": {**case.capsule, "approved": True},
    }
    await case.session.commit()
    result = await invoke(case)
    assert result["capsule"] == case.source["review_issuer"]
    denials(result)


@pytest.mark.parametrize(
    "fault", ["request", "response", "operation", "attempt_unknown", "transport"]
)
async def test_real_custody_failures_cannot_be_turned_into_legacy_or_equivalence(
    issuer_case, fault
):
    case = issuer_case
    if fault in {"request", "response"}:
        ref = getattr(case.run, fault + "_payload_ref")
        path = case.artifact_root / ref
        payload = json.loads(path.read_bytes())
        if fault == "request":
            payload["messages"]["user"]["text"] += "changed source"
        else:
            payload["content"]["text"] += "changed return"
        path.write_text(canonical(payload))
    elif fault == "operation":
        case.operation.input_hash = "0" * 64
    elif fault == "attempt_unknown":
        case.attempt.status = "unknown"
    else:
        payload = deepcopy(case.transport.result_json)
        payload["result"]["metadata"]["effective_input_receipt"]["hash"] = "0" * 64
        case.transport.result_json = payload
    await case.session.commit()
    with pytest.raises(ValueError):
        await invoke(case)


async def test_issuer_runtime_must_match_the_native_original_cycle_policy(issuer_case):
    case = issuer_case
    changed = deepcopy(case.chapter.metadata_json)
    changed["chapter_critic_cycles"][case.ids["cycle_id"]]["policy_source_hash"] = "0" * 64
    case.chapter.metadata_json = changed
    await case.session.commit()
    await reject(case, "cycle_policy_mismatch")


async def test_capsule_authentication_rejects_primitive_archive_tampering(issuer_case):
    case = issuer_case
    path = case.artifact_root / case.capsule["archive_ref"] / "manifest.json"
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        await invoke(case)


@pytest.mark.parametrize("key", ["project_id", "chapter_id", "draft_id", "cycle_id"])
async def test_foreign_native_ids_cannot_select_another_issuer(issuer_case, key):
    value = "f" * 64 if key == "cycle_id" else uuid4()
    with pytest.raises(ValueError):
        await invoke(issuer_case, **{key: value})


@pytest.mark.parametrize("key", ["project_id", "chapter_id", "draft_id", "cycle_id"])
async def test_native_selectors_are_not_coerced_from_caller_values(issuer_case, key):
    case = issuer_case
    value = True if key == "cycle_id" else str(case.ids[key])
    for name in APIS:
        with pytest.raises(ValueError):
            await invoke(case, name, **{key: value})


async def test_derived_view_rejects_pending_source_without_flushing_it(issuer_case):
    case = issuer_case
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    case.chapter.chapter_goal = "pending caller edit must remain pending"
    await reject(case, "uncommitted_current_source", "derive_current_primary_source")
    assert case.chapter.chapter_goal == "pending caller edit must remain pending"
    assert case.chapter in case.session.dirty
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before


async def test_archived_capsule_does_not_claim_current_draft_applicability(issuer_case):
    case = issuer_case
    case.draft.is_current = False
    await case.session.commit()
    result = await invoke(case)
    denials(result)
    assert result["current_applicability_checked"] is False
    await reject(case, "current_draft_changed", "derive_current_primary_source")


async def test_mutating_a_returned_receipt_is_never_a_later_input(issuer_case):
    case = issuer_case
    first = await invoke(case)
    first["capsule"]["runtime_policy_sha256"] = "0" * 64
    first["grade_granted"] = True
    second = await invoke(case)
    assert second["capsule"] == case.capsule
    denials(second)


@pytest.mark.parametrize("name", APIS)
async def test_readonly_view_does_not_flush_commit_or_capture_local_artifacts(
    issuer_case, monkeypatch, name
):
    case = issuer_case

    async def no_write(*args, **kwargs):
        pytest.fail("a read-only issuer view cannot flush or commit the caller session")

    monkeypatch.setattr(case.session, "flush", no_write)
    monkeypatch.setattr(case.session, "commit", no_write)
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    result = await invoke(case, name)
    denials(result)
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before


async def test_current_source_derivation_reembeds_only_the_authenticated_original_capsule(
    issuer_case,
):
    case = issuer_case
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    result = await invoke(case, "derive_current_primary_source")
    assert result["status"] == "exact_primary_source"
    assert result["source"] == case.source
    assert canonical(result["source"]) == canonical(case.source)
    assert case.source_reads
    denials(result)
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before


@pytest.mark.parametrize(
    "fault", ["boolean", "numeric", "order", "text", "nested_issuer", "source_receipt", "extra_key"]
)
async def test_every_nonissuer_field_remains_type_and_byte_exact(issuer_case, fault):
    case = issuer_case
    channels = case.current_source["current_source_channels"]
    if fault == "boolean":
        channels["literal_false"] = 0
    elif fault == "numeric":
        channels["literal_zero"] = 0.0
    elif fault == "order":
        channels["story_bible"]["relationships"].reverse()
    elif fault == "text":
        case.current_source["authority"] += " "
    elif fault == "nested_issuer":
        channels["review_issuer"] = deepcopy(case.capsule)
    elif fault == "source_receipt":
        channels["source_selection_receipt"]["original_source_sha256"] = "0" * 64
    else:
        case.current_source["new_unbound_field"] = None
    before = deepcopy(case.current_source)
    await reject(case, "nonissuer_source_changed", "derive_current_primary_source")
    assert case.current_source == before


async def test_two_comment_upgrades_keep_original_cycle_report_paid_inputs_and_denials(issuer_case):
    case = issuer_case
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    issued = case.capsule["runtime_policy_sha256"]
    seen = []
    for number in (1, 2):
        path = case.root / "src/bestseller/services/reviews.py"
        path.write_bytes(
            path.read_bytes() + f"\n# technical unit comment upgrade {number}\n".encode()
        )
        policy = await invoke(case, "require_cycle_policy")
        assert policy["policy_equivalent"] is True
        assert policy["issued_runtime_policy_sha256"] == issued
        assert policy["current_runtime_policy_sha256"] != issued
        seen.append(policy["current_runtime_policy_sha256"])
        denials(policy)
        source = await invoke(case, "derive_current_primary_source")
        assert source["source"]["review_issuer"] == case.capsule
        assert canonical(source["source"]) == canonical(case.source)
        denials(source)
    assert len(set(seen)) == 2
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before


@pytest.mark.parametrize("fault", ["python_ast", "non_python_bytes"])
async def test_policy_guard_rejects_real_code_or_config_drift(issuer_case, fault):
    case = issuer_case
    path = case.root / (
        "src/bestseller/services/reviews.py"
        if fault == "python_ast"
        else "config/quality_gates.yaml"
    )
    path.write_bytes(
        path.read_bytes() + (b"\nISSUER_CHANGED = True\n" if fault == "python_ast" else b"\n")
    )
    with pytest.raises(ValueError):
        await invoke(case, "require_cycle_policy")


async def test_authenticated_capsule_never_claims_full_report_or_quality_reconstruction(
    issuer_case,
):
    case = issuer_case
    assert case.report.verdict == "unknown" and case.quality.score_overall == 0.0
    for name in APIS:
        result = await invoke(case, name)
        denials(result)
        assert result["issued_report_verified"] is False
        assert result["full_quality_proof_verified"] is False
        assert "scores" not in result and "verdict" not in result
