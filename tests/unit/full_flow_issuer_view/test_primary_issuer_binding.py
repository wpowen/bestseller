"""Producer binding REDs; local archive capture only, no DB or paid issuance."""

from copy import deepcopy
import hashlib
import inspect
import json
from types import SimpleNamespace

from issuer_view_test_support import canonical, filesystem
import pytest

from bestseller.services import (
    pipelines,
    primary_review_transport,
    review_source_context,
    review_source_ordering,
)
from bestseller.services import retained_review_input_proof as retained
from bestseller.services import review_issuer_archive as archive
from bestseller.services.editable_plan_source import REVIEW_SOURCE_SCHEMA
from bestseller.settings import ArtifactStoreSettings

pytestmark = pytest.mark.unit


def native_source():
    # Synthetic source data, not a claim that a native selector or paid call ran.
    # Real capture/verification use the entire actual native policy inventory.
    return {
        "schema": review_source_context.SOURCE_POLICY,
        "adopted_continuity": {"schema": "planning-continuity.v1", "adopted_sources": []},
        "current_source_channels": {
            "source_selection_receipt": {
                "policy": "typed-world-and-relevant-current-state.v1",
                "source_ordering_policy": review_source_ordering.SOURCE_ORDERING_POLICY,
            },
            "story_bible": {"relationships": [{"order": 2}, {"order": 1}]},
            "typed_values": [False, 0, 0.0, None, "same words ", {"nested": [1, 2]}],
            "review_issuer": {"nested_data_must_remain_untouched": True},
        },
        "authority": retained._SOURCE_AUTHORITY,
    }


@pytest.fixture
def producer_case(source_copy, tmp_path):
    root = tmp_path / "producer-artifacts"
    return SimpleNamespace(
        source_root=source_copy,
        artifact_root=root,
        settings=SimpleNamespace(
            artifact_store=ArtifactStoreSettings(mode="local", local_dir=str(root))
        ),
    )


def forbid_capture(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Legacy, malformed or already-bound source cannot cause fresh capture")

    monkeypatch.setattr(archive, "capture_current_issuer_archive", forbidden)
    monkeypatch.setattr(archive, "verify_current_issuer_archive", forbidden)


def safe_difference(error, field):
    matches = [item for item in error.differences if item["field"] == field]
    assert len(matches) == 1
    for side in ("expected", "actual"):
        assert set(matches[0][side]) == {"type", "length", "sha256"}
    return matches[0]


def summary(value):
    return {
        "type": type(value).__name__,
        "length": len(value) if type(value) in (dict, list, str) else None,
        "sha256": hashlib.sha256(canonical(value).encode()).hexdigest(),
    }


def test_binding_api_is_sync_with_only_settings_and_source():
    function = archive.bind_primary_review_issuer
    assert not inspect.iscoroutinefunction(function)
    assert list(inspect.signature(function).parameters) == ["settings", "source"]


@pytest.mark.parametrize("key", ["approved", "capsule", "paths", "runtime_policy_sha256"])
def test_binding_accepts_no_caller_authority_or_archive_override(key):
    function = archive.bind_primary_review_issuer
    with pytest.raises(TypeError):
        function(None, native_source(), **{key: True})


@pytest.mark.parametrize("schema", [review_source_context.SOURCE_POLICY, REVIEW_SOURCE_SCHEMA])
def test_post_f0_binding_captures_native_inventory_and_detaches_every_source_field(
    producer_case,
    monkeypatch,
    schema,
):
    case = producer_case
    source = native_source()
    source["schema"] = schema
    if schema == REVIEW_SOURCE_SCHEMA:
        source["editable_plan"] = {"unit_data": [False, None, 0, 0.0]}
    original = deepcopy(source)
    actual_capture = archive.capture_current_issuer_archive
    captures = []

    def observed(settings):
        result = actual_capture(settings)
        captures.append(deepcopy(result))
        return result

    monkeypatch.setattr(archive, "capture_current_issuer_archive", observed)
    bound = archive.bind_primary_review_issuer(case.settings, source)
    assert len(captures) == 1
    assert bound is not source and canonical(source) == canonical(original)
    assert set(bound) == set(source) | {"review_issuer"}
    assert canonical(
        {key: value for key, value in bound.items() if key != "review_issuer"}
    ) == canonical(source)
    capsule = bound["review_issuer"]
    assert capsule == captures[0]["capsule"]
    assert capsule["runtime_policy_sha256"] == pipelines.chapter_review_recovery_source_hash()
    verified = archive.verify_current_issuer_archive(case.settings, capsule=capsule)
    assert verified["status"] == "equivalent" and verified["byte_identical"] is True
    for permission in (
        "grade_granted",
        "new_credit_allowed",
        "dispatch_authorized",
        "adoption_authorized",
        "unknown_reconciled",
        "paid_binding_verified",
        "issued_report_verified",
    ):
        assert captures[0][permission] is False
        assert verified[permission] is False
        assert permission not in bound
    original_archive = filesystem(case.artifact_root / capsule["archive_ref"])
    bound["current_source_channels"]["story_bible"]["relationships"].reverse()
    bound["review_issuer"]["runtime_policy_sha256"] = "0" * 64
    assert canonical(source) == canonical(original)
    assert (
        filesystem(case.artifact_root / captures[0]["capsule"]["archive_ref"]) == original_archive
    )


@pytest.mark.parametrize("shape", ["empty", "receipt_without_marker", "legacy_existing_issuer"])
def test_absent_marker_preserves_detached_legacy_source_without_capture(monkeypatch, shape):
    function = archive.bind_primary_review_issuer
    forbid_capture(monkeypatch)
    source = {} if shape == "empty" else native_source()
    if source:
        del source["current_source_channels"]["source_selection_receipt"]["source_ordering_policy"]
    if shape == "legacy_existing_issuer":
        source["review_issuer"] = {"legacy_data_is_not_authenticated": True}
    original = deepcopy(source)
    # None also proves the legacy path never needs artifact-store configuration.
    bound = function(None, source)
    assert bound is not source and canonical(bound) == canonical(original)
    bound["new_detached_data"] = []
    if shape == "legacy_existing_issuer":
        bound["review_issuer"]["legacy_data_is_not_authenticated"] = False
    assert canonical(source) == canonical(original)


@pytest.mark.parametrize("marker", [None, True, 1, "untrusted-future-epoch"])
def test_present_invalid_marker_refuses_capture_with_safe_typed_differences(monkeypatch, marker):
    function = archive.bind_primary_review_issuer
    forbid_capture(monkeypatch)
    source = native_source()
    source["current_source_channels"]["source_selection_receipt"]["source_ordering_policy"] = marker
    original = deepcopy(source)
    with pytest.raises(
        archive.IssuerArchiveError, match="^review_issuer_archive:primary_source_marker$"
    ) as caught:
        function(None, source)
    item = safe_difference(
        caught.value,
        "/source/current_source_channels/source_selection_receipt/source_ordering_policy",
    )
    assert item["expected"] == summary(review_source_ordering.SOURCE_ORDERING_POLICY)
    assert item["actual"] == summary(marker)
    assert canonical(source) == canonical(original)
    assert "untrusted-future-epoch" not in json.dumps(vars(caught.value))


@pytest.mark.parametrize(
    "fault", ["source_type", "channels_type", "receipt_type", "selection_policy", "schema"]
)
def test_invalid_native_source_shape_cannot_trigger_capture(monkeypatch, fault):
    function = archive.bind_primary_review_issuer
    forbid_capture(monkeypatch)
    source = native_source()
    if fault == "source_type":
        source, field = [], "/source"
    elif fault == "channels_type":
        source["current_source_channels"] = []
        field = "/source/current_source_channels"
    elif fault == "receipt_type":
        source["current_source_channels"]["source_selection_receipt"] = []
        field = "/source/current_source_channels/source_selection_receipt"
    elif fault == "selection_policy":
        source["current_source_channels"]["source_selection_receipt"]["policy"] = "invented"
        field = "/source/current_source_channels/source_selection_receipt/policy"
    else:
        source["schema"], field = "invented", "/source/schema"
    original = deepcopy(source)
    with pytest.raises(
        archive.IssuerArchiveError, match="^review_issuer_archive:primary_source_invalid$"
    ) as caught:
        function(None, source)
    safe_difference(caught.value, field)
    assert source == original


@pytest.mark.parametrize("existing", [None, {}, {"approved": True, "archive_ref": "caller-owned"}])
def test_post_f0_existing_issuer_is_not_replaced_or_re_signed(monkeypatch, existing):
    function = archive.bind_primary_review_issuer
    forbid_capture(monkeypatch)
    source = native_source()
    source["review_issuer"] = deepcopy(existing)
    original = deepcopy(source)
    with pytest.raises(
        archive.IssuerArchiveError, match="^review_issuer_archive:primary_source_already_bound$"
    ) as caught:
        function(None, source)
    item = safe_difference(caught.value, "/source/review_issuer")
    assert item["expected"] == {"type": "missing", "length": None, "sha256": None}
    assert item["actual"] == summary(existing)
    assert canonical(source) == canonical(original)
    assert "caller-owned" not in json.dumps(vars(caught.value))


def test_actual_capture_failure_propagates_without_partial_source_binding(producer_case):
    case = producer_case
    source = native_source()
    original = deepcopy(source)
    (case.source_root / "src/bestseller/services/reviews.py").unlink()
    with pytest.raises(archive.IssuerArchiveError, match="^review_issuer_archive:source_missing$"):
        archive.bind_primary_review_issuer(case.settings, source)
    assert canonical(source) == canonical(original) and "review_issuer" not in source


def test_bound_capsule_is_part_of_the_actual_primary_transport_source(producer_case):
    source = native_source()
    bound = archive.bind_primary_review_issuer(producer_case.settings, source)
    system, user, receipt = primary_review_transport.prepare_source_complete_prompt(
        system_prompt="Unit source transport.",
        user_prompt="Read the complete source.",
        source_context=bound,
        output_tokens=1024,
        tools=None,
    )
    primary_review_transport.verify_primary_review_transport(
        system,
        user,
        source_context=bound,
        receipt=receipt,
        output_tokens=1024,
        tools=None,
    )
    assert receipt["source_sha256"] == hashlib.sha256(canonical(bound).encode()).hexdigest()
    assert receipt["source_sha256"] != hashlib.sha256(canonical(source).encode()).hexdigest()
    with pytest.raises(ValueError):
        primary_review_transport.verify_primary_review_transport(
            system,
            user,
            source_context=source,
            receipt=receipt,
            output_tokens=1024,
            tools=None,
        )
