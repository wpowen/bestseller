"""RED: exact comparison failures expose only typed, field-level summaries.

Public selectors and stable error codes stay unchanged. The proposed private
_require_equal(field, expected, actual, *, code) compares complete typed values;
_diagnostic_summary only formats failure evidence and grants no authority.
"""

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.services import review_source_ordering

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def summary(value):
    wire = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return {
        "type": type(value).__name__,
        "length": len(value) if type(value) in (str, dict, list) else None,
        "sha256": hashlib.sha256(wire).hexdigest(),
    }


def differences(error):
    result = error.differences
    assert type(result) is list and result
    for item in result:
        assert set(item) == {"field", "expected", "actual"}
        assert type(item["field"]) is str and item["field"].startswith("/")
        for side in ("expected", "actual"):
            assert set(item[side]) == {"type", "length", "sha256"}
            assert type(item[side]["type"]) is str
            length = item[side]["length"]
            assert length is None or (type(length) is int and length >= 0)
            digest = item[side]["sha256"]
            assert digest is None or (
                type(digest) is str
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
            )
    return result


def difference(error, field):
    matches = [item for item in differences(error) if item["field"] == field]
    assert len(matches) == 1, field
    return matches[0]


def exact_difference(error, field, expected, actual):
    assert difference(error, field) == {
        "field": field,
        "expected": summary(expected),
        "actual": summary(actual),
    }


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (False, 0),
        (0, 0.0),
        ({}, {"missing_was_not_null": None}),
        ([1, 2], [2, 1]),
        ("original text", "original text "),
        ({"deep": [{"value": False}]}, {"deep": [{"value": 0}]}),
    ],
)
async def test_complete_value_comparison_does_not_use_diagnostic_digest_as_truth(
    issuer_view,
    monkeypatch,
    expected,
    actual,
):
    # A deliberately colliding diagnostic must not make unequal full values
    # pass. This is a pure comparison negative, never a native proof substitute.
    monkeypatch.setattr(
        issuer_view,
        "_diagnostic_summary",
        lambda value: {"type": "redacted", "length": None, "sha256": "0" * 64},
    )
    with pytest.raises(
        issuer_view.ReviewIssuerViewError, match="^review_issuer_view:comparison_test$"
    ):
        issuer_view._require_equal("/test/value", expected, actual, code="comparison_test")


@pytest.mark.parametrize("key", ["project_id", "chapter_id", "draft_id"])
async def test_selector_type_denial_identifies_the_exact_field_without_raw_id(issuer_view, key):
    ids = dict(project_id=uuid4(), chapter_id=uuid4(), draft_id=uuid4(), cycle_id="a" * 64)
    ids[key] = str(ids[key])
    with pytest.raises(
        issuer_view.ReviewIssuerViewError, match="^review_issuer_view:native_ids_required$"
    ) as caught:
        await issuer_view.auth_source_capsule(None, None, **ids)
    item = difference(caught.value, "/selectors/" + key)
    assert item["expected"] == {"type": "UUID", "length": None, "sha256": None}
    assert item["actual"] == summary(ids[key])
    assert ids[key] not in json.dumps(vars(caught.value))


async def test_cycle_policy_difference_uses_authenticated_capsule_and_native_cycle(issuer_case):
    case = issuer_case
    actual = "0" * 64
    metadata = deepcopy(case.chapter.metadata_json)
    metadata["chapter_critic_cycles"][case.ids["cycle_id"]]["policy_source_hash"] = actual
    case.chapter.metadata_json = metadata
    await case.session.commit()
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:cycle_policy_mismatch$"
    ) as caught:
        await case.module.auth_source_capsule(case.session, case.settings, **case.ids)
    exact_difference(
        caught.value,
        "/cycle/policy_source_hash",
        case.capsule["runtime_policy_sha256"],
        actual,
    )
    assert str(caught.value) == "review_issuer_view:cycle_policy_mismatch"


@pytest.mark.parametrize(
    "issuer_case", [{"marker": True}, {"marker": 1}, {"marker": "unsupported-epoch"}], indirect=True
)
async def test_source_epoch_difference_is_typed_and_bound_to_original_paid_source(issuer_case):
    case = issuer_case
    actual = case.source["current_source_channels"]["source_selection_receipt"][
        "source_ordering_policy"
    ]
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:source_epoch_invalid$"
    ) as caught:
        await case.module.auth_source_capsule(case.session, case.settings, **case.ids)
    exact_difference(
        caught.value,
        "/primary/source/current_source_channels/source_selection_receipt/source_ordering_policy",
        review_source_ordering.SOURCE_ORDERING_POLICY,
        actual,
    )


@pytest.mark.parametrize("issuer_case", [{"capsule": False}], indirect=True)
async def test_missing_capsule_reports_missing_without_inventing_an_expected_archive(issuer_case):
    case = issuer_case
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:capsule_missing$"
    ) as caught:
        await case.module.auth_source_capsule(case.session, case.settings, **case.ids)
    assert difference(caught.value, "/primary/source/review_issuer") == {
        "field": "/primary/source/review_issuer",
        "expected": {"type": "dict", "length": None, "sha256": None},
        "actual": {"type": "missing", "length": None, "sha256": None},
    }


@pytest.mark.parametrize("fault", ["boolean", "numeric", "text", "list_order", "missing"])
async def test_nonissuer_failure_reports_exact_leaf_types_and_hashes_without_values(
    issuer_case, fault
):
    case = issuer_case
    channels = case.current_source["current_source_channels"]
    if fault == "boolean":
        field, expected, actual = "/primary/source/current_source_channels/literal_false", False, 0
        channels["literal_false"] = actual
    elif fault == "numeric":
        field, expected, actual = "/primary/source/current_source_channels/literal_zero", 0, 0.0
        channels["literal_zero"] = actual
    elif fault == "text":
        field, expected, actual = (
            "/primary/source/authority",
            case.source["authority"],
            "PRIVATE_SOURCE_VALUE_51a29",
        )
        case.current_source["authority"] = actual
    elif fault == "list_order":
        field, expected, actual = (
            "/primary/source/current_source_channels/story_bible/relationships/0/order",
            2,
            1,
        )
        channels["story_bible"]["relationships"].reverse()
    else:
        field = "/primary/source/current_source_channels/literal_zero"
        channels.pop("literal_zero")
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:nonissuer_source_changed$"
    ) as caught:
        await case.module.derive_current_primary_source(case.session, case.settings, **case.ids)
    if fault == "missing":
        assert difference(caught.value, field) == {
            "field": field,
            "expected": summary(0),
            "actual": {"type": "missing", "length": None, "sha256": None},
        }
    else:
        exact_difference(caught.value, field, expected, actual)
    safe = json.dumps(vars(caught.value), ensure_ascii=False)
    assert "PRIVATE_SOURCE_VALUE_51a29" not in safe
    assert case.source["authority"] not in safe
    assert case.draft.content_md not in safe


async def test_pending_source_denial_names_dirty_count_without_orm_payload(issuer_case):
    case = issuer_case
    case.chapter.chapter_goal = "PRIVATE_PENDING_VALUE_c412d"
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:uncommitted_current_source$"
    ) as caught:
        await case.module.derive_current_primary_source(case.session, case.settings, **case.ids)
    exact_difference(caught.value, "/session/dirty/count", 0, 1)
    assert "PRIVATE_PENDING_VALUE_c412d" not in json.dumps(vars(caught.value))
    assert case.chapter.chapter_goal == "PRIVATE_PENDING_VALUE_c412d"
    assert case.chapter in case.session.dirty


async def test_current_pointer_denial_names_native_cardinality(issuer_case):
    case = issuer_case
    case.draft.is_current = False
    await case.session.commit()
    with pytest.raises(
        case.module.ReviewIssuerViewError, match="^review_issuer_view:current_draft_changed$"
    ) as caught:
        await case.module.derive_current_primary_source(case.session, case.settings, **case.ids)
    exact_difference(caught.value, "/current_drafts/count", 1, 0)
