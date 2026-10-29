"""Original-wire epoch presence REDs without changing the active shared fixture."""

from copy import deepcopy
import hashlib

from issuer_view_test_support import canonical, filesystem, rows
import pytest

from bestseller.services import primary_review_transport, review_source_ordering
from bestseller.services import recorded_review_sources as recorded
from bestseller.services import review_issuer_archive as archive

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def summary(value):
    return {
        "type": type(value).__name__,
        "length": len(value) if type(value) in (dict, list, str) else None,
        "sha256": hashlib.sha256(canonical(value).encode()).hexdigest(),
    }


@pytest.fixture(autouse=True)
def issued_epoch_shape(request, monkeypatch):
    """Alter only synthetic input DATA, before any wire/receipt/hash is produced.

    This module-local autouse fixture precedes issuer_case. It does not edit
    conftest, rewrite paid files, replace a custody reader or return an approval.
    The wrapper always invokes the original native primary transport function.
    """
    mode = request.param
    actual = primary_review_transport.prepare_source_complete_prompt
    rendered = []

    def prepare(**kwargs):
        source = kwargs["source_context"]
        channels = source["current_source_channels"]
        selection = channels["source_selection_receipt"]
        if mode == "absent":
            selection.pop("source_ordering_policy")
        elif mode == "null":
            selection["source_ordering_policy"] = None
        elif mode == "channels_list":
            source["current_source_channels"] = []
        elif mode == "receipt_list":
            channels["source_selection_receipt"] = []
        else:
            raise AssertionError("Unrecognized synthetic epoch case")
        result = actual(**kwargs)
        rendered.append(deepcopy(source))
        return result

    monkeypatch.setattr(primary_review_transport, "prepare_source_complete_prompt", prepare)
    return mode, rendered


@pytest.mark.parametrize(
    "issued_epoch_shape",
    ["absent", "null", "channels_list", "receipt_list"],
    indirect=True,
)
async def test_only_genuine_absence_can_take_legacy_route(
    issuer_case,
    issued_epoch_shape,
    monkeypatch,
):
    case = issuer_case
    mode, rendered = issued_epoch_shape
    assert rendered == [case.source]
    actual_reader = recorded.read_archived_review_sources
    native_sources = []

    async def read(session, settings, **ids):
        view = await actual_reader(session, settings, **ids)
        primary = next(
            call for call in view.calls if call.request.prompt_template == "chapter_review"
        )
        native_sources.append(deepcopy(primary.source))
        return view

    def forbidden(*args, **kwargs):
        pytest.fail("Absent or malformed epoch cannot verify/capture a post-F0 issuer")

    monkeypatch.setattr(recorded, "read_archived_review_sources", read)
    monkeypatch.setattr(archive, "capture_current_issuer_archive", forbidden)
    monkeypatch.setattr(archive, "verify_current_issuer_archive", forbidden)
    before = await rows(case.session, case.models), filesystem(case.artifact_root)
    for name in ("auth_source_capsule", "derive_current_primary_source", "require_cycle_policy"):
        function = getattr(case.module, name)
        if mode == "absent":
            assert await function(case.session, case.settings, **case.ids) is None
            continue
        with pytest.raises(
            case.module.ReviewIssuerViewError,
            match="^review_issuer_view:source_epoch_invalid$",
        ) as caught:
            await function(case.session, case.settings, **case.ids)
        if mode == "null":
            field = (
                "/primary/source/current_source_channels/"
                "source_selection_receipt/source_ordering_policy"
            )
            expected = summary(review_source_ordering.SOURCE_ORDERING_POLICY)
            actual = summary(None)
        else:
            field = "/primary/source/current_source_channels"
            if mode == "receipt_list":
                field += "/source_selection_receipt"
            expected = {"type": "dict", "length": None, "sha256": None}
            actual = summary([])
        matching = [item for item in caught.value.differences if item["field"] == field]
        assert matching == [{"field": field, "expected": expected, "actual": actual}]
    assert native_sources == [case.source] * 3
    assert case.source_reads == []
    assert (await rows(case.session, case.models), filesystem(case.artifact_root)) == before
