"""Committed same-source repair observations, never credit or dispatch authority.

Collector owns a NEW session, SELECT-only/no-autoflush guard and native readback.
The pure comparator accepts that fresh snapshot, not arbitrary metadata flags.
Only observer state is returned; worker owns its persistence and all stop/budget
checks. No writer producer, model call, commit, or paid/review mutation occurs.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, contextmanager
from copy import deepcopy
import hashlib
import json
import re
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.outline_source_entry import require_current_outline_source_admission
from bestseller.services.rewrite_outline_binding import validate_source_plan_binding
from bestseller.settings import AppSettings

SCHEMA = "self-heal-repair-progress-snapshot.v1"
STATE_SCHEMA = "self-heal-repair-progress-state.v1"
COMPARISON_SCHEMA = "self-heal-repair-progress-comparison.v1"
STATE_KEY = "self_heal_repair_progress"
HYPE = "source-plan-observation-restoration.v1"
POLICY = "pending-review-policy-rebind.v1"
AUTHORITY = "observer_progress_only_no_credit_dispatch_grade_or_debt_authority"
DENIALS = dict(
    new_credit_allowed=False,
    dispatch_authorized=False,
    grade_granted=False,
    budget_reset_allowed=False,
    debt_cleared=False,
    unknown_reconciled=False,
)


class RepairProgressError(ValueError):
    pass


def _need(ok, code):
    if not ok:
        raise RepairProgressError(code)


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _signed(value, schema, key="receipt_sha256"):
    _need(type(value) is dict and value.get("schema") == schema, "receipt_schema")
    plain = deepcopy(value)
    recorded = plain.pop(key, None)
    _need(_sha(recorded) and _digest(plain) == recorded, "receipt_digest")


@contextmanager
def _select_only(session, connection):
    def sql(conn, cursor, statement, parameters, context, many):
        compiled = getattr(context, "compiled", None)
        st = getattr(compiled, "statement", None)
        _need(
            st is not None
            and getattr(st, "is_select", False)
            and getattr(st, "_for_update_arg", None) is None,
            "select_only_violation",
        )

    def flush(*args):
        raise RepairProgressError("observer_flush_forbidden")

    event.listen(connection.sync_connection, "before_cursor_execute", sql)
    event.listen(session.sync_session, "before_flush", flush)
    event.listen(session.sync_session, "before_commit", flush)
    try:
        yield
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", sql)
        event.remove(session.sync_session, "before_flush", flush)
        event.remove(session.sync_session, "before_commit", flush)


def _epoch(project):
    m = project.metadata_json or {}
    # Stable execution/creation roots only, never current body, projection,
    # policy, heartbeat, error, repair UUID, or generated candidate version.
    return _digest(
        dict(
            project_id=str(project.id),
            execution_scope_id=m.get("execution_scope_id"),
            canonical_id=m.get("creation_canonical_snapshot_id"),
            canonical_hash=m.get("creation_canonical_snapshot_hash"),
            intent_id=m.get("creation_intent_snapshot_id"),
            intent_hash=m.get("creation_intent_contract_hash"),
        )
    )


def _hype_key(pid, cid, marker):
    return _digest(
        dict(
            family=HYPE,
            project_id=pid,
            chapter_id=cid,
            origin={k: marker[k] for k in ("source_content_sha256", "projection_hash")},
            target=["hype_type", "hype_intensity"],
        )
    )


def _hype_source(cid, body, projection, marker):
    return _digest(
        dict(
            family=HYPE,
            chapter_id=cid,
            body_sha256=body,
            projection_sha256=projection,
            origin={k: marker.get(k) for k in ("source_content_sha256", "projection_hash")}
            if isinstance(marker, dict)
            else marker,
        )
    )


def _policy_key(pid, cid, proof):
    # Reclosing the same body's pending-policy association is one effect,
    # even when wrapper/cycle/owner/policy UUIDs change.
    return _digest(
        dict(
            family=POLICY,
            project_id=pid,
            chapter_id=cid,
            body_sha256=proof["source_body_fingerprint"]["digest"],
            target="pending_review_policy_fields",
        )
    )


def _policy_source(cid, body, cycle, policy, semantic):
    return _digest(
        dict(
            family=POLICY,
            chapter_id=cid,
            body_sha256=body,
            policy_source_hash=policy,
            semantic_dependencies_sha256=semantic,
        )
    )


def _hype_history(project, chapter, receipt):
    _signed(receipt, HYPE)
    pid, cid = str(project.id), str(chapter.id)
    marker = validate_source_plan_binding(receipt.get("original_marker"))
    _need(
        receipt["project_id"] == pid
        and receipt["chapter_id"] == cid
        and all(
            _sha(receipt.get(k))
            for k in ("body_sha256", "restored_projection_sha256", "previous_projection_sha256")
        ),
        "hype_source_root",
    )
    key = _hype_key(pid, cid, marker)
    historic = _hype_source(
        cid, receipt["body_sha256"], receipt["restored_projection_sha256"], marker
    )
    # A structural historical key only suppresses replays. It never grants
    # progress until the complete CURRENT source and postcondition below pass.
    _need(
        set(receipt["original_values"])
        == set(receipt["previous_values"])
        == {"hype_type", "hype_intensity"},
        "hype_fields",
    )
    _need(receipt["original_values"] != receipt["previous_values"], "hype_noop")
    return key, historic, marker


async def _hype_current(session, settings, project, chapter, draft, scenes, receipt, marker):
    _need(
        validate_source_plan_binding((chapter.metadata_json or {}).get("outline_source_admission"))
        == marker,
        "hype_current_origin",
    )
    _need(
        draft is not None
        and draft.is_current
        and str(draft.id) == receipt["draft_id"]
        and draft.project_id == project.id
        and draft.chapter_id == chapter.id
        and isinstance(draft.content_md, str)
        and hashlib.sha256(draft.content_md.encode()).hexdigest() == receipt["body_sha256"],
        "hype_draft_body",
    )
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

    _need(draft.promoted_at is None and draft.promotion_state != "promoted", "hype_source_adopted")
    adopted, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    _need(adopted is None or adopted.id != draft.id, "hype_source_adopted")
    _need(
        receipt.get("adoption_authorized") is False
        and receipt.get("paid_result_authority") is False,
        "hype_authority",
    )
    _need(
        {k: getattr(chapter, k) for k in ("hype_type", "hype_intensity")}
        == receipt["original_values"],
        "hype_current_fields",
    )
    actual = outline_prefix_projection_hash(chapter, scenes)
    _need(
        actual == marker["projection_hash"] == receipt["restored_projection_sha256"],
        "hype_current_projection",
    )
    before = SimpleNamespace(
        **{
            field.key: deepcopy(getattr(chapter, field.key))
            for field in inspect(ChapterModel).column_attrs
        }
    )
    for key, value in receipt["previous_values"].items():
        setattr(before, key, value)
    _need(
        outline_prefix_projection_hash(before, scenes)
        == receipt["previous_projection_sha256"]
        != actual,
        "hype_before_projection",
    )
    admission = await require_current_outline_source_admission(
        session, settings, project=project, chapter=chapter, scenes=scenes
    )
    _need(
        admission is not None and _digest(admission) == receipt["admission_sha256"],
        "hype_native_admission",
    )
    return dict(
        origin=deepcopy(marker),
        current_projection_sha256=actual,
        admission_sha256=_digest(admission),
        adoption_authorized=False,
        paid_result_authority=False,
    )


async def collect_repair_progress(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    settings: AppSettings,
    project_id: UUID | str,
) -> dict:
    """Fresh-session SELECT-only snapshot; never accepts caller ORM/approved flags.

    SQLite/PG visibility is the database transaction's responsibility. The
    factory must create a distinct clean AsyncSession, not share an uncommitted
    caller connection. Rejected receipts are observations, not success tokens.
    """
    pid = UUID(str(project_id))
    async with session_factory() as session:
        _need(
            isinstance(session, AsyncSession)
            and isinstance(session.bind, AsyncEngine)
            and not session.in_transaction()
            and not session.identity_map
            and not session.new
            and not session.dirty
            and not session.deleted,
            "fresh_session_required",
        )
        with session.no_autoflush:
            connection = await session.connection()
            with _select_only(session, connection):
                project = await session.get(ProjectModel, pid)
                _need(project is not None, "project_missing")
                epoch = _epoch(project)
                effects = []
                sources = set()
                known = set()
                historic = set()
                rejected = []
                chapters = list(
                    await session.scalars(
                        select(ChapterModel)
                        .where(ChapterModel.project_id == pid)
                        .order_by(ChapterModel.id)
                    )
                )
                for chapter in chapters:
                    cid = str(chapter.id)
                    meta = chapter.metadata_json or {}
                    drafts = list(
                        await session.scalars(
                            select(ChapterDraftVersionModel).where(
                                ChapterDraftVersionModel.project_id == pid,
                                ChapterDraftVersionModel.chapter_id == chapter.id,
                                ChapterDraftVersionModel.is_current.is_(True),
                            )
                        )
                    )
                    draft = drafts[0] if len(drafts) == 1 else None
                    scenes = list(
                        await session.scalars(
                            select(SceneCardModel)
                            .where(
                                SceneCardModel.project_id == pid,
                                SceneCardModel.chapter_id == chapter.id,
                            )
                            .order_by(SceneCardModel.scene_number)
                        )
                    )
                    body = (
                        hashlib.sha256(draft.content_md.encode()).hexdigest()
                        if draft and isinstance(draft.content_md, str)
                        else None
                    )
                    marker = meta.get("outline_source_admission")
                    if marker is not None:
                        sources.add(
                            _hype_source(
                                cid, body, outline_prefix_projection_hash(chapter, scenes), marker
                            )
                        )
                    history = meta.get("deterministic_audit_source_plan_field_recovery", [])
                    if type(history) is not list:
                        rejected.append(
                            dict(chapter_id=cid, family=HYPE, reason="hype_history_shape")
                        )
                        history = []
                    for receipt in history:
                        try:
                            key, old_source, native_marker = _hype_history(
                                project, chapter, receipt
                            )
                            known.add(key)
                            historic.add(old_source)
                            proof = await _hype_current(
                                session,
                                settings,
                                project,
                                chapter,
                                draft,
                                scenes,
                                receipt,
                                native_marker,
                            )
                            effects.append(
                                dict(
                                    family=HYPE,
                                    effect_key=key,
                                    source_key=old_source,
                                    chapter_id=cid,
                                    draft_id=str(draft.id),
                                    body_sha256=body,
                                    receipt_sha256=receipt["receipt_sha256"],
                                    receipt_locator="chapter.metadata_json.deterministic_audit_source_plan_field_recovery",
                                    native_after_proof=proof,
                                )
                            )
                        except (ValueError, KeyError, TypeError, AttributeError) as exc:
                            rejected.append(
                                dict(
                                    chapter_id=cid,
                                    family=HYPE,
                                    reason=str(exc)
                                    if isinstance(exc, ValueError)
                                    else type(exc).__name__,
                                )
                            )
                    active = meta.get("chapter_review_active_cycle") or {}
                    active_id = active.get("cycle_id") if isinstance(active, dict) else None
                    cycles = meta.get("chapter_critic_cycles") or {}
                    _need(isinstance(cycles, dict), "policy_cycles_shape")
                    # Historical receipts only seed denial/de-duplication. An
                    # inactive source can never qualify, even with a valid hash.
                    for cycle_id, cycle in sorted(cycles.items()):
                        if not isinstance(cycle, dict):
                            rejected.append(
                                dict(chapter_id=cid, family=POLICY, reason="policy_cycle_shape")
                            )
                            continue
                        if cycle_id == active_id:
                            sources.add(
                                _policy_source(
                                    cid,
                                    body,
                                    cycle_id,
                                    cycle.get("policy_source_hash"),
                                    cycle.get("semantic_dependencies_sha256"),
                                )
                            )
                        pending_history = cycle.get("policy_rebind_history", [])
                        if type(pending_history) is not list:
                            rejected.append(
                                dict(chapter_id=cid, family=POLICY, reason="policy_history_shape")
                            )
                            pending_history = []
                        for receipt in pending_history:
                            try:
                                from bestseller.services import (
                                    chapter_pending_review_policy_recovery as policy,
                                )
                                from bestseller.services.pipelines import (
                                    chapter_review_recovery_source_hash,
                                )

                                _signed(receipt, POLICY)
                                proof = receipt["proof"]
                                _signed(proof, policy.SCHEMA, "proof_sha256")
                                _need(
                                    proof["project_id"] == str(pid)
                                    and proof["chapter_id"] == cid
                                    and proof["cycle_id"] == cycle_id,
                                    "policy_source_root",
                                )
                                key = _policy_key(str(pid), cid, proof)
                                known.add(key)
                                source = _policy_source(
                                    cid,
                                    proof["source_body_fingerprint"]["digest"],
                                    cycle_id,
                                    proof["current_policy_source_hash"],
                                    proof["semantic_dependencies_sha256"],
                                )
                                historic.add(source)
                                _need(cycle_id == active_id, "policy_inactive_cycle")
                                _need(
                                    receipt["model_calls"] == 0
                                    and all(
                                        receipt[k] is False
                                        for k in (
                                            "new_attempt_credit",
                                            "grade_granted",
                                            "dispatch_authorized",
                                        )
                                    ),
                                    "policy_authority",
                                )
                                _need(
                                    draft is not None
                                    and str(draft.id) == proof["draft_id"]
                                    and body == proof["source_body_fingerprint"]["digest"],
                                    "policy_draft_body",
                                )
                                records, _ = policy._records(meta, cycle_id)
                                _need(
                                    policy._after(proof["before_records"], receipt) == records,
                                    "policy_after_records",
                                )
                                owner = await session.get(
                                    WorkflowRunModel, UUID(proof["previous_workflow_run_id"])
                                )
                                _need(owner is not None, "policy_owner_missing")
                                current = await policy._validate(
                                    session,
                                    settings,
                                    project,
                                    chapter,
                                    draft,
                                    owner,
                                    proof["execution_scope_id"],
                                    chapter_review_recovery_source_hash(),
                                    replay=receipt,
                                )
                                _need(current == proof, "policy_native_replay")
                                effects.append(
                                    dict(
                                        family=POLICY,
                                        effect_key=key,
                                        source_key=source,
                                        chapter_id=cid,
                                        draft_id=str(draft.id),
                                        body_sha256=body,
                                        receipt_sha256=receipt["receipt_sha256"],
                                        receipt_locator="chapter.metadata_json.chapter_critic_cycles.policy_rebind_history",
                                        native_after_proof=dict(
                                            origin={
                                                k: deepcopy(proof[k])
                                                for k in (
                                                    "project_id",
                                                    "chapter_id",
                                                    "draft_id",
                                                    "previous_workflow_run_id",
                                                    "execution_scope_id",
                                                    "source_body_fingerprint",
                                                    "cycle_id",
                                                    "operation_namespace",
                                                    "attempt",
                                                )
                                            },
                                            cycle_id=cycle_id,
                                            proof_sha256=proof["proof_sha256"],
                                            current_policy_source_hash=proof[
                                                "current_policy_source_hash"
                                            ],
                                            after_records_sha256=_digest(records),
                                            semantic_dependencies_sha256=proof[
                                                "semantic_dependencies_sha256"
                                            ],
                                            ledger_sha256=proof["ledger_sha256"],
                                            budget_usage=deepcopy(proof["budget_usage"]),
                                            model_calls=0,
                                            new_attempt_credit=False,
                                            grade_granted=False,
                                            dispatch_authorized=False,
                                        ),
                                    )
                                )
                            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                                rejected.append(
                                    dict(
                                        chapter_id=cid,
                                        family=POLICY,
                                        reason=str(exc)
                                        if isinstance(exc, ValueError)
                                        else type(exc).__name__,
                                    )
                                )
                _need(
                    not session.new and not session.dirty and not session.deleted,
                    "reader_mutated_session",
                )
                again = (
                    await session.execute(
                        select(ProjectModel.metadata_json).where(ProjectModel.id == pid)
                    )
                ).scalar_one()
                _need(
                    _epoch(SimpleNamespace(id=pid, metadata_json=again)) == epoch,
                    "source_epoch_changed_during_read",
                )
                snapshot = dict(
                    schema=SCHEMA,
                    project_id=str(pid),
                    source_epoch=epoch,
                    effects=sorted(effects, key=lambda e: (e["effect_key"], e["receipt_sha256"])),
                    current_sources=sorted(sources),
                    known_effect_keys=sorted(known),
                    historical_source_keys=sorted(historic),
                    rejected=rejected,
                    unsupported_families=[],
                    authority=AUTHORITY,
                    **DENIALS,
                )
                snapshot["snapshot_sha256"] = _digest(snapshot)
                return snapshot


def _effect_binding(snapshot, effect):
    """Recompute stable identity from collector's fully checked native source.

    Digests provide consistency, not a DB signature. The caller MUST obtain a
    new snapshot with collect_repair_progress; persisted arbitrary JSON is not
    a replacement for that native readback.
    """
    _need(
        all(_sha(effect.get(k)) for k in ("receipt_sha256", "body_sha256")),
        "effect_receipt_binding",
    )
    pid, cid = snapshot["project_id"], effect["chapter_id"]
    _need(
        str(UUID(cid)) == cid and str(UUID(effect["draft_id"])) == effect["draft_id"],
        "effect_identity",
    )
    proof = effect["native_after_proof"]
    if effect["family"] == HYPE:
        marker = validate_source_plan_binding(proof.get("origin"))
        projection = proof.get("current_projection_sha256")
        _need(
            _sha(proof.get("admission_sha256"))
            and projection == marker["projection_hash"]
            and proof.get("adoption_authorized") is False
            and proof.get("paid_result_authority") is False,
            "hype_after_proof",
        )
        key = _hype_key(pid, cid, marker)
        source = _hype_source(cid, effect["body_sha256"], projection, marker)
    else:
        origin = proof.get("origin")
        _need(
            type(origin) is dict
            and origin.get("project_id") == pid
            and origin.get("chapter_id") == cid
            and origin.get("draft_id") == effect["draft_id"]
            and origin.get("source_body_fingerprint", {}).get("digest") == effect["body_sha256"]
            and origin.get("cycle_id") == proof.get("cycle_id")
            and type(proof.get("model_calls")) is int
            and proof["model_calls"] == 0
            and all(
                proof.get(k) is False
                for k in ("new_attempt_credit", "grade_granted", "dispatch_authorized")
            )
            and all(
                _sha(proof.get(k))
                for k in (
                    "proof_sha256",
                    "current_policy_source_hash",
                    "after_records_sha256",
                    "ledger_sha256",
                    "semantic_dependencies_sha256",
                )
            ),
            "policy_after_proof",
        )
        key = _policy_key(pid, cid, origin)
        source = _policy_source(
            cid,
            effect["body_sha256"],
            origin["cycle_id"],
            proof["current_policy_source_hash"],
            proof["semantic_dependencies_sha256"],
        )
    _need(effect["effect_key"] == key and effect["source_key"] == source, "effect_source_binding")


def compare_repair_progress(previous_state: object, snapshot: dict) -> dict:
    """Compare collector output only; never read/return/overwrite paid counters.

    All historical keys survive epoch switches; missing/corrupt state only
    establishes a conservative baseline. Returning to any observed source or
    re-closing an observed effect cannot renew the watchdog.
    """
    _need(type(snapshot) is dict and snapshot.get("schema") == SCHEMA, "snapshot_schema")
    plain = deepcopy(snapshot)
    recorded = plain.pop("snapshot_sha256", None)
    _need(
        _sha(recorded)
        and _digest(plain) == recorded
        and snapshot.get("authority") == AUTHORITY
        and all(snapshot.get(k) is False for k in DENIALS),
        "snapshot_binding",
    )
    _need(_sha(snapshot["source_epoch"]), "snapshot_epoch")
    for key in ("known_effect_keys", "historical_source_keys", "current_sources"):
        _need(type(snapshot[key]) is list and all(_sha(v) for v in snapshot[key]), "snapshot_keys")
    previous = deepcopy(previous_state) if type(previous_state) is dict else {}
    old = deepcopy(previous)
    old_hash = old.pop("state_sha256", None)
    try:
        valid = bool(
            _sha(old_hash)
            and _digest(old) == old_hash
            and old.get("schema") == STATE_SCHEMA
            and old.get("project_id") == snapshot["project_id"]
            and _sha(old.get("source_epoch"))
            and type(old.get("seen_effect_keys")) is list
            and all(_sha(x) for x in old["seen_effect_keys"])
            and type(old.get("seen_source_keys")) is list
            and all(_sha(x) for x in old["seen_source_keys"])
        )
    except (ValueError, TypeError, RecursionError):
        valid = False
    baseline = not valid or previous["source_epoch"] != snapshot["source_epoch"]
    seen = set(previous["seen_effect_keys"]) if valid else set()
    source_seen = set(previous["seen_source_keys"]) if valid else set()
    new = set()
    for effect in snapshot["effects"]:
        _need(
            type(effect) is dict
            and effect.get("family") in {HYPE, POLICY}
            and _sha(effect.get("effect_key"))
            and _sha(effect.get("source_key"))
            and effect["effect_key"] in snapshot["known_effect_keys"]
            and effect["source_key"] in snapshot["historical_source_keys"]
            and effect["source_key"] in snapshot["current_sources"]
            and type(effect.get("native_after_proof")) is dict,
            "effect_shape",
        )
        _effect_binding(snapshot, effect)
        if (
            not baseline
            and effect["effect_key"] not in seen
            and effect["source_key"] not in source_seen
        ):
            new.add(effect["effect_key"])
    state = dict(
        schema=STATE_SCHEMA,
        project_id=snapshot["project_id"],
        source_epoch=snapshot["source_epoch"],
        seen_effect_keys=sorted(seen | set(snapshot["known_effect_keys"])),
        seen_source_keys=sorted(
            source_seen | set(snapshot["historical_source_keys"]) | set(snapshot["current_sources"])
        ),
    )
    state["state_sha256"] = _digest(state)
    return dict(
        schema=COMPARISON_SCHEMA,
        observer_progress_eligible=bool(new),
        baseline_only=baseline,
        new_effect_keys=sorted(new),
        next_state=state,
        authority=AUTHORITY,
        **DENIALS,
    )
