"""Current native source selection for operator and consumer, with no dispatch.

Assembly must come from read_adopted_assembly, in the caller's fresh read-only
snapshot. This factory checks source joins, not KG closure or semantic verdicts.
"""

from copy import deepcopy
import hashlib
import json
from uuid import UUID

from sqlalchemy import bindparam, select

from bestseller.domain.book_design_snapshot import BookDesignSnapshot
from bestseller.domain.fanqie_short import DEFAULT_UNLOCK_LINE_RATIO
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    PlanningArtifactVersionModel,
    ProjectModel,
)
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story

CURRENT_SCOPE = "fanqie-adopted-whole"
OPERATION_NAMESPACE = "fanqie-whole-semantic-v5"
CONTRACT_KEYS = (
    "creation_intent_contract",
    "reader_promise",
    "concept_seed",
    "concept_contract",
    "book_design_snapshot",
    "pov",
)
APPROVAL_FIELDS = (
    "conception_approved",
    "planning_status",
    "creation_canonical_snapshot_id",
    "creation_canonical_snapshot_hash",
    "book_design_adoption_source",
    "book_design_snapshot_id",
    "book_design_snapshot_hash",
    "book_design_snapshot_status",
    "creation_intent_snapshot_id",
    "creation_intent_contract_hash",
    "title_revision_status",
)
PROJECT_FIELDS = ("title", "genre", "language", "target_chapters", "target_word_count")
# Original title metadata plus the separate paid-selection lineage. Project
# status/export receipts must never make a later export invalidate its binding.
TITLE_METADATA_FIELDS = (
    "title_revision_status",
    "title_workflow_primary",
    "title_workflow",
    "title_candidates",
    "writing_profile",
    "title_revision_history",
    "title_repair_lineage",
    "title_paid_selection_lineage",
    "book_design_snapshot",
    "book_design_snapshot_id",
    "book_design_snapshot_hash",
    "book_design_snapshot_status",
    "creation_canonical_snapshot_id",
    "creation_canonical_snapshot_hash",
)


def project_title_projection(project):
    """Stable native projection only; this does not validate/adopt a title."""
    _need(type(project.metadata_json) is dict, "title_project_metadata")
    return {
        "id": _uuid(project.id),
        "title": project.title,
        "metadata_json": {
            k: deepcopy(project.metadata_json[k])
            for k in TITLE_METADATA_FIELDS
            if k in project.metadata_json
        },
    }


def _publication_evidence(evidence, project, manifest):
    """Select original fields, leaving the full paid proof to native/v4 checks."""
    proof = deepcopy(evidence)
    _need(type(proof) is dict and type(proof.get("project")) is dict, "native_title_shape")
    projected = project_title_projection(project)
    exported = proof["project"]
    _need(
        exported.get("id") == projected["id"]
        and exported.get("title") == projected["title"]
        and type(exported.get("metadata_json")) is dict,
        "native_title_project",
    )
    exported_meta = {
        k: exported["metadata_json"][k]
        for k in TITLE_METADATA_FIELDS
        if k in exported["metadata_json"]
    }
    _need(_hash(exported_meta) == _hash(projected["metadata_json"]), "native_title_metadata_drift")
    if proof.get("schema") == "native-paid-title-selection-evidence.v1":
        # Preserve the old six-character adoption node. The wrapper's current
        # selection is not a new Canonical approval or the old failed status.
        origin = proof.get("origin")
        _need(type(origin) is dict
              and origin.get("schema") == "native-publishing-title-evidence.v1",
              "native_title_selection_origin")
        paid = origin.get("paid_return")
        _need(type(paid) is dict and type(paid.get("plan")) is dict,
              "native_title_selection_paid_plan")
        source = paid["plan"].get("literal_source")
    else:
        workflow = proof.get("workflow", {})
        source = workflow.get("metadata_json", {}).get("literal_source", {})
    _need(
        type(source) is dict
        and type(source.get("manifest")) is dict
        and type(source.get("old_native_title_metadata")) is dict,
        "native_title_literal_source",
    )
    before = deepcopy(source["manifest"])
    _need(type(before.get("project")) is dict, "native_title_old_manifest")
    before["project"]["title"] = project.title
    _need(_hash(before) == _hash(manifest), "native_title_manifest_changed_beyond_title")
    proof["project"] = projected  # Do not bind status/updated_at/export receipts.
    return proof, deepcopy(source["old_native_title_metadata"])


def _need(ok, code):
    if not ok:
        raise ValueError("semantic_inputs:" + code)


def _uuid(value):
    _need(isinstance(value, (str, UUID)), "native_uuid_required")
    parsed = UUID(str(value))
    _need(str(parsed) == str(value), "noncanonical_uuid")
    return str(parsed)


def _text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash(value):
    # Same canonical JSON encoding as conception_approval_bridge._hash, for
    # native JSON content. Never rebuild/repair source IDs, hashes or contracts.
    return _text_hash(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    )


def _sha(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _reject_pending(session, project_id, artifact_ids, draft_ids=()):
    for row in (*session.new, *session.dirty):
        owned = (
            (isinstance(row, ProjectModel) and str(row.id) == project_id)
            or (isinstance(row, PlanningArtifactVersionModel) and str(row.id) in artifact_ids)
            or (isinstance(row, ChapterDraftVersionModel) and str(row.id) in draft_ids)
        )
        _need(not owned, "pending_source_changes")


async def _artifact(session, *, identity, project_id, kind, status):
    row = await session.get(PlanningArtifactVersionModel, UUID(identity), populate_existing=True)
    _need(row is not None, "missing_" + kind)
    _need(
        _uuid(row.id) == identity and _uuid(row.project_id) == project_id,
        "native_artifact_ownership",
    )
    _need(row.artifact_type == kind and row.status == status, "native_artifact_kind_or_status")
    _need(type(row.content) is dict, "native_artifact_content")
    return {
        "id": identity,
        "project_id": project_id,
        "artifact_type": row.artifact_type,
        "status": row.status,
        "content": deepcopy(row.content),
    }


async def _read_recovery_parent(session, *, project_id, full_text, title):
    """Select persisted same-source history, never latest/success/caller JSON.

    All statuses/schemas remain in the query. None is not recovery authority:
    adapter outer must independently reject ambiguity/illegal history and prove
    native custody plus the single permitted protocol recovery.
    """
    model = PlanningArtifactVersionModel
    statement = select(model.id, model.project_id, model.artifact_type, model.status,
                       model.schema_version, model.source_run_id, model.content).where(
        model.project_id == bindparam("semantic_parent_project_id", UUID(project_id)),
        model.artifact_type == bindparam("semantic_parent_artifact_type", "fanqie_whole_semantic_review"),
        model.content["request_payload"]["full_text"].as_string()
        == bindparam("semantic_parent_full_text", full_text),
        model.content["request_payload"]["title"].as_string()
        == bindparam("semantic_parent_title", title),
    ).execution_options(autoflush=False)
    with session.no_autoflush:
        rows = (await session.execute(statement)).mappings().all()
    eligible = []
    for row in rows:
        report = row["content"]
        _need(_uuid(row["project_id"]) == project_id
              and row["artifact_type"] == "fanqie_whole_semantic_review", "parent_native_owner")
        _need(type(report) is dict and type(report.get("request_payload")) is dict
              and report["request_payload"].get("full_text") == full_text
              and report["request_payload"].get("title") == title, "parent_native_source")
        if (row["status"] == "failed"
                and row["schema_version"] == report.get("schema")
                == report["request_payload"].get("schema") == "fanqie-short-semantic-stage.v4"
                and isinstance(report.get("error"), str) and bool(report["error"].strip())):
            eligible.append(row)
    if len(eligible) != 1:
        return None
    parent = eligible[0]
    report = parent["content"]
    _need(isinstance(report.get("raw_response"), str), "parent_raw_response_required")
    from bestseller.services.fanqie_short_semantic_consumer import digest
    return {"artifact_id": _uuid(parent["id"]), "workflow_id": _uuid(parent["source_run_id"]),
            "content_sha256": digest(report), "error": report["error"],
            "raw_response_sha256": _text_hash(report["raw_response"])}


async def build_current_inputs(
    session, *, project, assembly, workflow_run_id, native_title_evidence=None
):
    """Return complete kwargs for v4.prepare_review; perform only session.get.

    Never creates a workflow/scope/budget, flushes, commits, or calls a model.
    No-autoflush covers every SELECT; dirty source rows are rejected before
    populate_existing can discard them. Caller owns fresh Project/assembly
    readback, transaction isolation and final adapter revalidation.
    """
    pid, wid = _uuid(project.id), _uuid(workflow_run_id)
    meta = deepcopy(project.metadata_json)
    _need(type(meta) is dict, "project_metadata")
    _need(type(project.target_chapters) is int and project.target_chapters > 0, "project_target")
    _need(isinstance(project.title, str) and bool(project.title.strip()), "project_title")
    _need(all(k in meta for k in CONTRACT_KEYS), "contract_projection_missing")
    canonical_id = _uuid(meta.get("creation_canonical_snapshot_id"))
    intent_id = _uuid(meta.get("creation_intent_snapshot_id"))
    _need(canonical_id != intent_id, "duplicate_native_artifact_ids")
    _reject_pending(session, pid, {canonical_id, intent_id})
    with session.no_autoflush:
        canonical = await _artifact(
            session,
            identity=canonical_id,
            project_id=pid,
            kind="conception_snapshot",
            status="canonical",
        )
        intent = await _artifact(
            session, identity=intent_id, project_id=pid, kind="creation_intent", status="validated"
        )

    canonical_hash, intent_hash = _hash(canonical["content"]), _hash(intent["content"])
    _need(meta.get("creation_canonical_snapshot_hash") == canonical_hash, "canonical_hash")
    _need(
        meta.get("creation_intent_contract_hash") == intent_hash
        and canonical["content"].get("creation_intent_hash") == intent_hash,
        "intent_hash",
    )
    _need(
        meta.get("book_design_adoption_source")
        == {
            "kind": "adopted_conception",
            "snapshot_id": canonical_id,
            "content_hash": canonical_hash,
        },
        "native_adoption_source",
    )
    _need(
        meta.get("conception_approved") is True
        and meta.get("planning_status") == "conception_approved",
        "native_approval_state",
    )
    _need(meta["creation_intent_contract"] == intent["content"], "native_intent_projection")
    _need(
        meta["concept_contract"] == canonical["content"].get("concept_contract"),
        "native_canonical_projection",
    )
    book = meta["book_design_snapshot"]
    _need(type(book) is dict, "design_snapshot_missing")
    parsed = BookDesignSnapshot.model_validate(book)  # Never from_mapping/hash repair.
    _need(
        parsed.source_hash == parsed.canonical_hash()
        and parsed.snapshot_id == parsed.source_hash[:16],
        "design_content_hash",
    )
    _need(
        meta.get("book_design_snapshot_id") == book.get("snapshot_id")
        and meta.get("book_design_snapshot_hash") == book.get("source_hash")
        and meta.get("book_design_snapshot_status") == "locked",
        "native_design_identity",
    )
    _need(book.get("creation_intent") == intent["content"], "native_design_intent")
    _need(
        meta["concept_seed"] == intent["content"].get("concept_seed")
        and meta["pov"] == intent["content"].get("pov"),
        "native_intent_fields",
    )
    _need(
        parsed.chapter_budget.total_chapters == project.target_chapters
        and intent["content"].get("chapter_count") == project.target_chapters,
        "native_chapter_target",
    )

    manifest = deepcopy(assembly.manifest)
    full_text = assembly.full_text
    _need(isinstance(full_text, str) and bool(full_text.strip()), "assembly_body")
    _need(
        type(manifest) is dict and manifest.get("schema") == "fanqie-whole-source-export.v1",
        "native_manifest_schema",
    )
    _need(manifest.get("project_id") == pid, "manifest_project")
    _need(
        manifest.get("project") == {k: getattr(project, k) for k in PROJECT_FIELDS},
        "manifest_project_or_title",
    )
    _need(
        manifest.get("canonical_id") == canonical_id
        and manifest.get("canonical_hash") == canonical_hash,
        "manifest_canonical",
    )
    _need(manifest.get("assembly_sha256") == _text_hash(full_text), "manifest_assembly_hash")
    scope = meta.get("execution_scope_id")
    _need(
        isinstance(scope, str)
        and bool(scope.strip())
        and manifest.get("execution_scope_id") == scope,
        "manifest_execution_scope",
    )
    _need(
        manifest.get("reader_contract")
        == {
            "pov": meta.get("pov"),
            "unlock_line_ratio": meta.get("unlock_line_ratio"),
        },
        "manifest_reader_contract",
    )
    sources = manifest.get("sources")
    _need(
        type(sources) is list and len(sources) == project.target_chapters, "manifest_source_count"
    )
    normalized, draft_ids, chapter_ids = [], set(), set()
    for number, source in enumerate(sources, 1):
        _need(
            type(source) is dict
            and type(source.get("chapter_number")) is int
            and source["chapter_number"] == number,
            "manifest_source_order",
        )
        did, cid = _uuid(source.get("draft_id")), _uuid(source.get("chapter_id"))
        body_hash, lineage = source.get("raw_body_sha256"), source.get("lineage")
        _need(did not in draft_ids and cid not in chapter_ids, "duplicate_manifest_source")
        draft_ids.add(did)
        chapter_ids.add(cid)
        _need(_sha(body_hash) and type(lineage) is dict, "native_source_lineage")
        _need(
            lineage.get("schema") == "adopted-knowledge.v1"
            and lineage.get("project_id") == pid
            and lineage.get("chapter_id") == cid
            and lineage.get("source_chapter_draft_version_id") == did
            and lineage.get("source_body_sha256") == body_hash,
            "native_source_identity_or_hash",
        )
        _need(
            lineage.get("source_promotion_state") == "promoted"
            and lineage.get("adoption_state") == "promoted",
            "native_source_not_promoted",
        )
        _reject_pending(session, pid, {canonical_id, intent_id}, draft_ids={did})
        with session.no_autoflush:
            draft = await session.get(ChapterDraftVersionModel, UUID(did), populate_existing=True)
        _need(draft is not None, "source_draft_missing")
        _need(
            _uuid(draft.id) == did
            and _uuid(draft.project_id) == pid
            and _uuid(draft.chapter_id) == cid,
            "source_draft_ownership",
        )
        _need(
            isinstance(draft.content_md, str) and _text_hash(draft.content_md) == body_hash,
            "source_draft_body_hash",
        )
        state = getattr(draft, "promotion_state", None)
        _need(
            state == "promoted" and state == lineage["source_promotion_state"],
            "source_draft_not_promoted",
        )
        if "source_version_no" in lineage:
            _need(lineage["source_version_no"] == draft.version_no, "source_draft_version")
        normalized.append(
            {
                "chapter": number,
                "draft_id": did,
                "body_sha256": body_hash,
                "promotion_state": state,
            }
        )

    raw_ratio = meta.get("unlock_line_ratio")
    ratio = DEFAULT_UNLOCK_LINE_RATIO if raw_ratio is None else raw_ratio
    _need(type(ratio) in (float, int) and 0 < ratio < 1, "unlock_line_ratio")
    protagonist = "我" if meta["pov"] == "first_person" else parsed.protagonist.name
    _need(isinstance(protagonist, str) and bool(protagonist.strip()), "protagonist_missing")
    mode = meta.get("methodology_contract_mode")
    _need(mode in {"strict", "warn", "off"}, "methodology_contract_mode")
    contract = {k: deepcopy(meta[k]) for k in CONTRACT_KEYS}
    approval = {k: deepcopy(meta[k]) for k in APPROVAL_FIELDS if k in meta}
    approval.update(project_id=pid, canonical_snapshot=canonical, intent_snapshot=intent)
    if native_title_evidence is not None:
        proof, old_title_metadata = _publication_evidence(native_title_evidence, project, manifest)
        approval["native_publishing_title_evidence"] = proof
        # The old failed status remains the base approval's literal historical
        # value. The new success belongs ONLY to the native title proof.
        approval.pop("title_revision_status", None)
        if "title_revision_status" in old_title_metadata:
            approval["title_revision_status"] = deepcopy(
                old_title_metadata["title_revision_status"]
            )
    contract["approval_evidence"] = approval
    report = review_whole_fanqie_short_story(
        full_text, title=project.title, unlock_line_ratio=ratio, protagonist_name=protagonist
    ).to_dict()
    recovery_parent = await _read_recovery_parent(
        session, project_id=pid, full_text=full_text, title=project.title)
    return {
        "project_id": pid,
        "workflow_run_id": wid,
        "current_scope": CURRENT_SCOPE,
        "full_text": full_text,
        "title": project.title,
        "protagonist": protagonist,
        "manifest": (
            deepcopy(manifest)
            if native_title_evidence is not None
            else {"assembly_sha256": _text_hash(full_text), "sources": normalized}
        ),
        "approved_contract": contract,
        "original_report": deepcopy(report),
        "operation_namespace": OPERATION_NAMESPACE,
        "execution_scope_id": scope,
        "unlock_line_ratio": ratio,
        "methodology_contract_mode": mode,
        "recovery_parent": recovery_parent,
    }
