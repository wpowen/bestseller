"""Read-only paid-source issuer/policy views, without report quality authority.

Source custody and code equivalence are lower-level facts. They never replace
the separate original report/slot/aggregate/score/issues/raw quality proof.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID

_DENIALS = {
    "grade_granted": False,
    "new_credit_allowed": False,
    "dispatch_authorized": False,
    "adoption_authorized": False,
    "unknown_reconciled": False,
    "issued_report_verified": False,
    "full_quality_proof_verified": False,
    "current_applicability_checked": False,
}
_MISSING = object()


class ReviewIssuerViewError(ValueError):
    """Safe stable categories, without raw source or paid return values."""

    def __init__(self, code: str, *, differences=None, differences_truncated=False):
        self.code = code
        self.differences = deepcopy(differences) if differences is not None else []
        self.differences_truncated = differences_truncated
        super().__init__("review_issuer_view:" + code)


def _diagnostic_summary(value: object) -> dict:
    if value is _MISSING:
        return {"type": "missing", "length": None, "sha256": None}
    try:
        encoded = json.dumps(
            str(value) if isinstance(value, UUID) else value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        digest = hashlib.sha256(encoded).hexdigest()
    except (TypeError, ValueError, RecursionError, OverflowError):
        digest = None
    return {
        "type": type(value).__name__,
        "length": len(value) if type(value) in (dict, list, str) else None,
        "sha256": digest,
    }


def _reject(code: str, field: str, expected: dict, actual: object) -> None:
    raise ReviewIssuerViewError(
        code,
        differences=[{"field": field, "expected": expected, "actual": _diagnostic_summary(actual)}],
    )


def _require_type(field: str, value: object, kind: type, *, code: str) -> None:
    if type(value) is not kind:
        _reject(code, field, {"type": kind.__name__, "length": None, "sha256": None}, value)


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, RecursionError, OverflowError):
        _reject(
            "source_types_invalid",
            "/source",
            {"type": "JSON value", "length": None, "sha256": None},
            value,
        )


def _full_equal(expected: object, actual: object) -> bool:
    if type(expected) is not type(actual):
        return False
    if expected is _MISSING:
        return actual is _MISSING
    if isinstance(expected, UUID):
        return expected == actual
    if type(expected) is dict:
        return {(type(key), key) for key in expected} == {
            (type(key), key) for key in actual
        } and all(_full_equal(value, actual[key]) for key, value in expected.items())
    if type(expected) in (list, tuple):
        return len(expected) == len(actual) and all(
            _full_equal(left, right) for left, right in zip(expected, actual, strict=True)
        )
    return _json_bytes(expected) == _json_bytes(actual)


def _require_equal(field: str, expected: object, actual: object, *, code: str) -> None:
    # Full typed values decide equality. Summaries only describe a rejection.
    if _full_equal(expected, actual):
        return
    differences = []
    truncated = False

    def visit(path, left, right):
        nonlocal truncated
        if _full_equal(left, right):
            return
        if len(differences) >= 64:
            truncated = True
            return
        if (
            type(left) is dict
            and type(right) is dict
            and all(type(key) is str for key in left.keys() | right.keys())
        ):
            for key in sorted(left.keys() | right.keys()):
                visit(
                    path + "/" + key.replace("~", "~0").replace("/", "~1"),
                    left.get(key, _MISSING),
                    right.get(key, _MISSING),
                )
                if truncated:
                    break
            return
        if type(left) is list and type(right) is list:
            for index in range(max(len(left), len(right))):
                visit(
                    path + f"/{index}",
                    left[index] if index < len(left) else _MISSING,
                    right[index] if index < len(right) else _MISSING,
                )
                if truncated:
                    break
            return
        differences.append(
            {
                "field": path,
                "expected": _diagnostic_summary(left),
                "actual": _diagnostic_summary(right),
            }
        )

    visit(field, expected, actual)
    raise ReviewIssuerViewError(code, differences=differences, differences_truncated=truncated)


def _ids(project_id, chapter_id, draft_id, cycle_id) -> dict:
    for key, value in (
        ("project_id", project_id),
        ("chapter_id", chapter_id),
        ("draft_id", draft_id),
    ):
        if not isinstance(value, UUID):
            _reject(
                "native_ids_required",
                "/selectors/" + key,
                {"type": "UUID", "length": None, "sha256": None},
                value,
            )
    if not (
        type(cycle_id) is str
        and len(cycle_id) == 64
        and all(char in "0123456789abcdef" for char in cycle_id)
    ):
        _reject(
            "native_cycle_required",
            "/selectors/cycle_id",
            {"type": "lowercase-hex str", "length": 64, "sha256": None},
            cycle_id,
        )
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "draft_id": draft_id,
        "cycle_id": cycle_id,
    }


async def _native_state(session, ids, recorded_view, primary):
    from bestseller.infra.db import models as db
    from bestseller.services import pipelines, recorded_review_sources

    reader = recorded_review_sources._Reader(session)
    project = await reader.get(db.ProjectModel, ids["project_id"])
    chapter = await reader.get(db.ChapterModel, ids["chapter_id"])
    draft = await reader.get(db.ChapterDraftVersionModel, ids["draft_id"])
    report = await reader.get(db.ReviewReportModel, recorded_view.report_id)
    for name, row in (
        ("project", project),
        ("chapter", chapter),
        ("draft", draft),
        ("report", report),
    ):
        if row is None:
            _reject(
                "native_scope_changed",
                "/native/" + name,
                {"type": "native row", "length": None, "sha256": None},
                row,
            )
    for field, expected, actual in (
        ("chapter/project_id", project.id, chapter.project_id),
        ("draft/project_id", project.id, draft.project_id),
        ("report/project_id", project.id, report.project_id),
        ("draft/chapter_id", chapter.id, draft.chapter_id),
        ("report/target_type", "chapter", report.target_type),
        ("report/target_id", chapter.id, report.target_id),
    ):
        _require_equal("/native/" + field, expected, actual, code="native_scope_changed")
    metadata = chapter.metadata_json or {}
    _require_type("/chapter/metadata", metadata, dict, code="native_cycle_changed")
    cycles = metadata.get("chapter_critic_cycles", _MISSING)
    _require_type(
        "/chapter/metadata/chapter_critic_cycles", cycles, dict, code="native_cycle_changed"
    )
    cycle = cycles.get(ids["cycle_id"], _MISSING)
    _require_type("/cycle", cycle, dict, code="native_cycle_changed")
    body = pipelines.chapter_review_body_fingerprint(draft.content_md or "")
    output = report.structured_output
    _require_type("/report/structured_output", output, dict, code="native_source_changed")
    for key, expected in (("draft_id", str(draft.id)), ("reviewed_body_fingerprint", body)):
        _require_equal(
            "/report/structured_output/" + key,
            expected,
            output.get(key, _MISSING),
            code="native_source_changed",
        )
    for key, expected in (
        ("cycle_id", ids["cycle_id"]),
        ("state", "completed"),
        ("source_draft_id", str(draft.id)),
        ("source_body_hash", body["digest"]),
        ("review_workflow_run_id", str(primary.workflow_run_id)),
    ):
        _require_equal(
            "/cycle/" + key, expected, cycle.get(key, _MISSING), code="native_source_changed"
        )
    _require_equal(
        "/report/llm_run_id", primary.llm_run_id, report.llm_run_id, code="native_source_changed"
    )
    attempts = cycle.get("attempts", _MISSING)
    _require_type("/cycle/attempts", attempts, list, code="native_cycle_changed")
    if not attempts:
        _reject(
            "native_cycle_changed",
            "/cycle/attempts",
            {"type": "non-empty list", "length": None, "sha256": None},
            attempts,
        )
    last_path = f"/cycle/attempts/{len(attempts) - 1}"
    _require_type(last_path, attempts[-1], dict, code="native_cycle_changed")
    _require_type(
        last_path + "/attempt",
        attempts[-1].get("attempt", _MISSING),
        int,
        code="native_cycle_changed",
    )
    for key, expected in (
        ("state", "completed"),
        ("outcome", "available"),
        ("review_report_id", str(report.id)),
    ):
        _require_equal(
            last_path + "/" + key,
            expected,
            attempts[-1].get(key, _MISSING),
            code="native_cycle_changed",
        )
    # This is a stability seal over observed native state, NOT a reconstruction
    # of judgment/quality from the paid return. Its permission denials stay false.
    state = {
        "cycle": cycle,
        "body": body,
        "content_md": draft.content_md,
        "report_id": str(report.id),
        "report_run_id": str(report.llm_run_id),
        "report_body": output["reviewed_body_fingerprint"],
    }
    return {
        "reader": reader,
        "project": project,
        "chapter": chapter,
        "draft": draft,
        "cycle": deepcopy(cycle),
        "body": body,
        "snapshot": deepcopy(state),
    }


async def _unchanged(session, ids, recorded_view, primary, state, receipt) -> None:
    from bestseller.services import pipelines

    fresh = await _native_state(session, ids, recorded_view, primary)
    _require_equal(
        "/native_state", state["snapshot"], fresh["snapshot"], code="native_source_changed"
    )
    _require_equal(
        "/runtime_policy_sha256",
        receipt["current_runtime_policy_sha256"],
        pipelines.chapter_review_recovery_source_hash(),
        code="runtime_changed_during_view",
    )


async def _authenticate(session, settings, ids):
    from bestseller.services import recorded_review_sources as recorded
    from bestseller.services import review_issuer_archive, review_source_ordering

    view = await recorded.read_archived_review_sources(session, settings, **ids)
    for key, expected in ids.items():
        _require_equal(
            "/recorded/" + key, expected, getattr(view, key), code="native_scope_changed"
        )
    primary_calls = [
        call for call in view.calls if call.request.prompt_template == "chapter_review"
    ]
    _require_equal("/primary_calls/count", 1, len(primary_calls), code="primary_source_ambiguous")
    primary = primary_calls[0]
    source = primary.source
    _require_type("/primary/source", source, dict, code="primary_source_invalid")
    channels = source.get("current_source_channels", {})
    _require_type(
        "/primary/source/current_source_channels", channels, dict, code="source_epoch_invalid"
    )
    selection = channels.get("source_selection_receipt", {})
    selection_path = "/primary/source/current_source_channels/source_selection_receipt"
    _require_type(selection_path, selection, dict, code="source_epoch_invalid")
    if "source_ordering_policy" not in selection:
        return None  # No retroactive issuer, re-signing, or legacy compatibility claim.
    _require_equal(
        selection_path + "/source_ordering_policy",
        review_source_ordering.SOURCE_ORDERING_POLICY,
        selection["source_ordering_policy"],
        code="source_epoch_invalid",
    )
    _require_equal(
        selection_path + "/policy",
        "typed-world-and-relevant-current-state.v1",
        selection.get("policy", _MISSING),
        code="source_epoch_invalid",
    )
    if "review_issuer" not in source or source["review_issuer"] is None:
        _reject(
            "capsule_missing",
            "/primary/source/review_issuer",
            {"type": "dict", "length": None, "sha256": None},
            source.get("review_issuer", _MISSING),
        )
    capsule = deepcopy(source["review_issuer"])
    verified = review_issuer_archive.verify_current_issuer_archive(settings, capsule=capsule)
    _require_equal(
        "/issuer_verification/status",
        "equivalent",
        verified.get("status", _MISSING),
        code="issuer_equivalence_unproven",
    )
    # A capsule cannot cover a shared guard omitted from the native inventory.
    # Do not append a fallback entry or accept a caller-provided path list.
    root = Path(review_issuer_archive.__file__).absolute().parents[3]
    paths = review_issuer_archive._inventory(
        root,
        review_issuer_archive._read(
            root / "src/bestseller/services/pipelines.py", "source_missing"
        ),
    )
    if "src/bestseller/services/review_issuer_view.py" not in paths:
        _reject(
            "issuer_view_not_in_inventory",
            "/policy_inventory/required_path",
            _diagnostic_summary("src/bestseller/services/review_issuer_view.py"),
            _MISSING,
        )
    state = await _native_state(session, ids, view, primary)
    _require_equal(
        "/cycle/policy_source_hash",
        verified["issued_runtime_policy_sha256"],
        state["cycle"].get("policy_source_hash", _MISSING),
        code="cycle_policy_mismatch",
    )
    _require_equal(
        "/capsule/runtime_policy_sha256",
        verified["issued_runtime_policy_sha256"],
        capsule["runtime_policy_sha256"],
        code="cycle_policy_mismatch",
    )
    receipt = {
        "schema": "review-issuer-technical-view.v1",
        "status": "authenticated_source_capsule",
        "capsule": capsule,
        "binding": {
            **{key: str(value) for key, value in ids.items()},
            "report_id": str(view.report_id),
            "body_sha256": state["body"]["digest"],
        },
        "paid_calls": [
            {
                "llm_run_id": str(call.llm_run_id),
                "workflow_run_id": str(call.workflow_run_id),
                "operation_key": call.operation_key,
                "execution_scope_id": call.execution_scope_id,
                "attempt_id": call.original_attempt_id,
                "transport_operation_key": call.transport_operation_key,
                "request_sha256": call.request_sha256,
                "response_sha256": call.response_sha256,
                "effective_input_sha256": call.effective_input_sha256,
            }
            for call in view.calls
        ],
        "issued_runtime_policy_sha256": verified["issued_runtime_policy_sha256"],
        "current_runtime_policy_sha256": verified["current_runtime_policy_sha256"],
        "python_ast_sha256": verified["python_ast_sha256"],
        "non_python_sha256": verified["non_python_sha256"],
        "paid_input_custody_verified": True,
        "source_capsule_authenticated": True,
        **_DENIALS,
    }
    await _unchanged(session, ids, view, primary, state, receipt)
    return view, primary, state, receipt


async def auth_source_capsule(
    session, settings, *, project_id, chapter_id, draft_id, cycle_id
) -> dict | None:
    """Authenticate original native paid input/capsule only, without quality proof."""
    authenticated = await _authenticate(
        session, settings, _ids(project_id, chapter_id, draft_id, cycle_id)
    )
    return authenticated[3] if authenticated is not None else None


async def derive_current_primary_source(
    session, settings, *, project_id, chapter_id, draft_id, cycle_id
) -> dict | None:
    """Reinsert the original issuer only after native custody and AST equivalence."""
    from sqlalchemy import select

    from bestseller.infra.db import models as db
    from bestseller.services import context, retained_review_input_proof, review_source_context

    ids = _ids(project_id, chapter_id, draft_id, cycle_id)
    authenticated = await _authenticate(session, settings, ids)
    if authenticated is None:
        return None
    view, primary, state, receipt = authenticated
    # Existing current-data selectors may refresh ORM reads. Do not let them
    # overwrite pending caller changes or infer an uncommitted source as fact.
    for key in ("new", "dirty", "deleted"):
        _require_equal(
            "/session/" + key + "/count",
            0,
            len(getattr(session, key)),
            code="uncommitted_current_source",
        )
    current = await state["reader"].rows(
        db.ChapterDraftVersionModel,
        db.ChapterDraftVersionModel.project_id == project_id,
        db.ChapterDraftVersionModel.chapter_id == chapter_id,
        db.ChapterDraftVersionModel.is_current.is_(True),
    )
    _require_equal("/current_drafts/count", 1, len(current), code="current_draft_changed")
    _require_equal("/current_drafts/0/id", draft_id, current[0].id, code="current_draft_changed")
    readonly = retained_review_input_proof._ReadOnlySession(session)
    packet = await context.build_chapter_writer_context(
        readonly, settings, state["project"].slug, state["chapter"].chapter_number
    )
    scenes = await state["reader"].scalars(
        select(db.SceneCardModel)
        .where(
            db.SceneCardModel.project_id == project_id,
            db.SceneCardModel.chapter_id == chapter_id,
        )
        .order_by(db.SceneCardModel.scene_number)
    )
    current_source = await review_source_context.load_review_source_context(
        readonly,
        project=state["project"],
        chapter=state["chapter"],
        scenes=scenes,
        context_packet=packet,
        content_md=state["draft"].content_md,
        settings=settings,
    )
    _require_type("/current/source", current_source, dict, code="current_source_invalid")
    issued_base = deepcopy(primary.source)
    current_base = deepcopy(current_source)
    issued_base.pop("review_issuer")
    current_base.pop("review_issuer", None)
    _require_equal("/primary/source", issued_base, current_base, code="nonissuer_source_changed")
    derived = current_base
    derived["review_issuer"] = deepcopy(receipt["capsule"])
    _require_equal("/primary/source", primary.source, derived, code="nonissuer_source_changed")
    await _unchanged(session, ids, view, primary, state, receipt)
    # Check the current pointer once more after building the derived-only object.
    latest = await state["reader"].rows(
        db.ChapterDraftVersionModel,
        db.ChapterDraftVersionModel.project_id == project_id,
        db.ChapterDraftVersionModel.chapter_id == chapter_id,
        db.ChapterDraftVersionModel.is_current.is_(True),
    )
    _require_equal("/current_drafts/count", 1, len(latest), code="current_draft_changed")
    _require_equal("/current_drafts/0/id", draft_id, latest[0].id, code="current_draft_changed")
    return {**receipt, "status": "exact_primary_source", "source": derived}


async def require_cycle_policy(
    session, settings, *, project_id, chapter_id, draft_id, cycle_id
) -> dict | None:
    """Require issuer policy equivalence, never review/quality/adoption permission."""
    authenticated = await _authenticate(
        session, settings, _ids(project_id, chapter_id, draft_id, cycle_id)
    )
    if authenticated is None:
        return None
    return {**authenticated[3], "status": "policy_equivalent", "policy_equivalent": True}
