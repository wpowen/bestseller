# ruff: noqa: ANN001, ANN201, ANN202
"""Publish only the current, knowledge-closed adopted short-story assembly.

Whole-story style/finalizer outputs remain immutable candidates. This service
neither promotes drafts nor changes knowledge, old reviews, tasks or ledgers.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ProjectModel,
    RewriteTaskModel,
)

POLICY = "fanqie-whole-source-export.v1"
EXPORT_ARCHIVE_SCHEMA = "fanqie-export-history.v1"
DELIVERY_SCHEMA = "fanqie-delivery.v2"


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _immutable_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != text:
            raise ValueError("fanqie_candidate_archive_conflict") from None


def _whole_output_bytes(output_dir):
    retained = {}
    for name in ("exports", "rejected-drafts", "finalized"):
        directory = output_dir / name
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("fanqie_export_archive_unsafe_path")
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("fanqie_export_archive_unsafe_path")
            if path.is_file():
                retained[path.relative_to(output_dir).as_posix()] = path.read_bytes()
            elif not path.is_dir():
                raise ValueError("fanqie_export_archive_unsafe_path")
    # The existing rejected exporter also refreshes this candidate ledger.
    guardrails = output_dir / "story-bible" / "canon-guardrails.json"
    if guardrails.parent.is_symlink() or guardrails.is_symlink():
        raise ValueError("fanqie_export_archive_unsafe_path")
    if guardrails.exists():
        if not guardrails.is_file():
            raise ValueError("fanqie_export_archive_unsafe_path")
        retained[guardrails.relative_to(output_dir).as_posix()] = guardrails.read_bytes()
    return retained


def _immutable_archive_bytes(path, data):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("fanqie_export_archive_conflict")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        with path.open("rb") as stream:
            if stream.read() != data:
                raise ValueError("fanqie_export_archive_conflict") from None
            os.fsync(stream.fileno())


def archive_existing_whole_outputs(output_dir):
    """Retain opaque old outputs byte-for-byte before any replacement write."""
    output_dir = Path(output_dir).resolve()
    retained = _whole_output_bytes(output_dir)
    if not retained:
        return None
    manifest = {
        "schema": EXPORT_ARCHIVE_SCHEMA,
        "files": {
            relative: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for relative, data in sorted(retained.items())
        },
        "source_authority_granted": False,
        "grade_granted": False,
    }
    snapshot_hash = _sha(manifest)
    directory = output_dir / "export-history" / snapshot_hash
    manifest_path = directory / "manifest.json"
    encoded = _json(manifest).encode()
    if manifest_path.exists():
        _immutable_archive_bytes(manifest_path, encoded)
    for relative, data in retained.items():
        _immutable_archive_bytes(directory / "files" / relative, data)
    _immutable_archive_bytes(manifest_path, encoded)
    directories = {directory, directory.parent, output_dir}
    for relative in retained:
        directories.update(
            parent
            for parent in (directory / "files" / relative).parents
            if parent.is_relative_to(directory)
        )
    for path in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if _whole_output_bytes(output_dir) != retained:
        raise ValueError("fanqie_export_archive_source_changed")
    return {
        "schema": EXPORT_ARCHIVE_SCHEMA,
        "snapshot_sha256": snapshot_hash,
        "directory": str(directory),
        "manifest_path": str(manifest_path),
        "file_count": len(retained),
    }


def preserve_whole_candidate(output_dir, *, producer, body, source_manifest=None, evidence=None):
    """Archive exact returned text; evidence is an observation, never a grade."""
    document = {
        "schema": POLICY,
        "status": "candidate_pending_adoption",
        "producer": producer,
        "body_sha256": _text_sha(body),
        "source_manifest": deepcopy(source_manifest),
        "evidence": deepcopy(evidence or {}),
        "adopted": False,
        "knowledge_rebound": False,
        "source_authority_granted": False,
    }
    directory = Path(output_dir) / "candidates" / "whole-story" / _sha(document)
    body_path, report_path = directory / "candidate.md", directory / "candidate.json"
    _immutable_file(body_path, body)
    _immutable_file(report_path, _json(document))
    return {
        "candidate_markdown_path": str(body_path.resolve()),
        "candidate_report_path": str(report_path.resolve()),
    }


@dataclass(frozen=True)
class AdoptedAssembly:
    full_text: str
    manifest_json: str

    @property
    def manifest(self):
        return json.loads(self.manifest_json)


async def _project_guard(session, project_id, *, lock):
    from bestseller.services.budget import current_execution_scope, raise_if_execution_stopped
    from bestseller.services.gate_registry import (
        project_blocks_all_prose_generation,
        project_resume_is_terminally_blocked,
    )

    raise_if_execution_stopped()
    # Fresh reads must not flush or discard caller-owned source/control edits.
    for row in tuple(session.dirty) + tuple(session.new):
        owned = (isinstance(row, ProjectModel) and row.id == project_id) or (
            isinstance(
                row,
                (
                    BookProductionControlModel,
                    ChapterModel,
                    ChapterDraftVersionModel,
                    ChapterStateSnapshotModel,
                ),
            )
            and row.project_id == project_id
        )
        if owned and (row in session.new or session.is_modified(row, include_collections=True)):
            raise ValueError("fanqie_export_pending_source_state")
    control_query = (
        select(BookProductionControlModel)
        .where(
            BookProductionControlModel.project_id == project_id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        control_query = control_query.with_for_update()
    control = await session.scalar(control_query)
    if control is not None and control.desired_state != "run":
        raise ValueError("fanqie_export_halted")
    query = (
        select(ProjectModel)
        .where(ProjectModel.id == project_id)
        .execution_options(
            populate_existing=True,
        )
    )
    if lock:
        query = query.with_for_update()
    project = await session.scalar(query)
    if project is None or project.status not in {"planning", "writing", "revising", "completed"}:
        raise ValueError("fanqie_export_project_state")
    if project_blocks_all_prose_generation(project) or project_resume_is_terminally_blocked(
        project.metadata_json
    ):
        raise ValueError("fanqie_export_structurally_blocked")
    scope = (project.metadata_json or {}).get("execution_scope_id")
    if not scope or (current_execution_scope() is not None and current_execution_scope() != scope):
        raise ValueError("fanqie_export_scope_changed")
    return project, int(control.command_serial or 0) if control is not None else 0


async def read_adopted_assembly(session, project_id, *, lock=False):
    from bestseller.services.adopted_knowledge_resume import adopted_source_read_batch

    async with adopted_source_read_batch(session, project_id, enabled=not lock):
        return await _read_adopted_assembly(session, project_id, lock=lock)


async def _read_adopted_assembly(session, project_id, *, lock):
    from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
    from bestseller.services.book_closure import evaluate_book_closure
    from bestseller.services.book_delivery_readiness import closure_promotion_evidence
    from bestseller.services.fanqie_short_pipeline import (
        _load_chapter_drafts,
        assemble_fanqie_whole_story,
    )

    with session.no_autoflush:
        project, serial = await _project_guard(session, project_id, lock=lock)
        if lock:
            # All chapters precede all drafts, then snapshots. No network under locks.
            for model, order in (
                (ChapterModel, (ChapterModel.chapter_number, ChapterModel.id)),
                (
                    ChapterDraftVersionModel,
                    (ChapterDraftVersionModel.chapter_id, ChapterDraftVersionModel.id),
                ),
                (ChapterStateSnapshotModel, (ChapterStateSnapshotModel.chapter_id,)),
            ):
                await session.scalars(
                    select(model)
                    .where(model.project_id == project_id)
                    .order_by(*order)
                    .with_for_update()
                )
        payloads = await _load_chapter_drafts(session, project_id)
        census = evaluate_book_closure(
            [chapter for chapter, _ in payloads],
            expected_chapters=project.target_chapters,
        )
        if census.reason != "adoption_knowledge_required":
            raise ValueError("fanqie_export_chapters_not_ready:" + census.reason)
        sources = []
        for chapter, draft in payloads:
            if await closure_promotion_evidence(session, draft):
                raise ValueError("fanqie_export_legacy_closure_promotion_requires_readoption")
            lineage, knowledge = await read_adopted_knowledge(
                session,
                project_id=project_id,
                chapter_id=chapter.id,
                source_chapter_draft_version_id=draft.id,
                chapter_md=draft.content_md,
                require_bible=True,
            )
            if not knowledge["closed"]:
                raise ValueError("fanqie_export_knowledge_not_closed")
            sources.append(
                {
                    "chapter_number": chapter.chapter_number,
                    "chapter_id": str(chapter.id),
                    "draft_id": str(draft.id),
                    "raw_body_sha256": _text_sha(draft.content_md),
                    "lineage": lineage,
                }
            )
        full_text = assemble_fanqie_whole_story(project, payloads)
        if not full_text.strip():
            raise ValueError("fanqie_export_empty_assembly")
        metadata = project.metadata_json or {}
        manifest = {
            "schema": POLICY,
            "project_id": str(project_id),
            "sources": sources,
            "assembly_sha256": _text_sha(full_text),
            "control_serial": serial,
            "project": {
                key: getattr(project, key)
                for key in (
                    "title",
                    "genre",
                    "language",
                    "target_chapters",
                    "target_word_count",
                )
            },
            "execution_scope_id": metadata["execution_scope_id"],
            "canonical_id": metadata.get("creation_canonical_snapshot_id"),
            "canonical_hash": metadata.get("creation_canonical_snapshot_hash"),
            "reader_contract": {
                key: deepcopy(metadata.get(key)) for key in ("pov", "unlock_line_ratio")
            },
        }
        return AdoptedAssembly(full_text, _json(manifest))


async def checkpoint_writing(session, project_id):
    from bestseller.services.pipelines import _checkpoint_commit

    with session.no_autoflush:
        project, _ = await _project_guard(session, project_id, lock=True)
        if project.status != "completed":
            await session.execute(
                update(ProjectModel).where(ProjectModel.id == project_id).values(status="writing")
            )
    await _checkpoint_commit(session)


async def _revision_tasks(session, project, current, review, unlock_segment, *, semantic_view=None):
    """Stage new, exact-parent tasks; never rebind previously issued work.

    The assembly reader remains responsible for adoption and knowledge proof.
    This producer rechecks each body and current outline before issuing a task.
    All findings for one chapter share one new task, while a title finding has
    a project-level exit and cannot accidentally dispatch the body editor.
    """
    from bestseller.services.fanqie_short_gate_v2 import build_fanqie_short_v2_rewrite_instructions
    from bestseller.services.fanqie_short_pipeline import (
        _ranking_finding_chapter_numbers, _short_v2_finding_chapter_numbers,
    )
    from bestseller.services.fanqie_short_ranking_gate import (
        FanqieRankingFinding, FanqieRankingGateReport, build_fanqie_ranking_rewrite_instructions,
    )
    from bestseller.services.reviews import rewrite_source_binding
    from bestseller.services.rewrite_patch import text_hash

    existing = list(await session.scalars(select(RewriteTaskModel).where(
        RewriteTaskModel.project_id == project.id,
    ).order_by(RewriteTaskModel.id).execution_options(populate_existing=True)))
    by_key = {(row.metadata_json or {}).get("whole_revision_key"): row for row in existing}
    sources = {row["chapter_number"]: row for row in current.manifest["sources"]}
    if len(sources) != len(current.manifest["sources"]):
        raise ValueError("fanqie_whole_duplicate_source")
    grouped, titles = {}, []
    reports = [
        ("ranking", review.ranking_report, _ranking_finding_chapter_numbers,
         build_fanqie_ranking_rewrite_instructions),
        ("short_v2", review.short_v2_report, _short_v2_finding_chapter_numbers,
         build_fanqie_short_v2_rewrite_instructions),
    ]
    # New generation contracts get explicit length work. Legacy delivery keeps
    # its historical protocol and separate upload flag (U2).
    protocol = (project.metadata_json or {}).get("book_length_control") or {}
    if protocol.get("protocol") == "book-length-control-v1":
        from bestseller.services.drafts import count_words
        words, target = count_words(current.full_text), project.target_word_count
        if target <= 0:
            raise ValueError("fanqie_whole_word_target_invalid")
        if words * 100 < target * 85 or words * 100 > target * 115:
            code = "whole_word_count_under" if words < target else "whole_word_count_over"
            finding = FanqieRankingFinding(
                code=code, severity="critical", phase="whole_story", target="whole_story",
                message=f"Adopted body has {words} words; book target is {target}. Follow the frozen chapter budget.",
                evidence=f"actual={words};target={target};counting=export-body-words-v1",
            )
            reports.append(("short_v2", FanqieRankingGateReport(
                passed=False, phase="whole_story", findings=(finding,)),
                _short_v2_finding_chapter_numbers, build_fanqie_short_v2_rewrite_instructions))
    for kind, report, route, render in reports:
        if report is None or report.passed:
            continue
        for finding in report.findings:
            if finding.severity != "critical":
                continue
            if semantic_view is not None:
                removed = semantic_view["semantic_derived_review"]["adjudicated_nonblocking"]
                if any(row["gate"] == kind + "_report"
                       and _json(row["finding"]) == _json(finding.to_dict()) for row in removed):
                    continue
            if finding.target == "title" or finding.phase == "title":
                titles.append({"gate": kind, "finding": finding.to_dict()})
                continue
            for number in route(finding, segment_total=project.target_chapters,
                                unlock_segment=unlock_segment):
                if number not in sources:
                    raise ValueError("fanqie_whole_task_source_missing")
                grouped.setdefault(number, []).append((kind, finding, render))
    if titles:
        title_request = {"schema": "fanqie-whole-title-repair.v1", "status": "pending",
                         "source_manifest": deepcopy(current.manifest), "findings": titles,
                         "grade_asserted": False}
        title_request["source_sha256"] = _sha(title_request)
        project.metadata_json = {**(project.metadata_json or {}),
                                 "fanqie_whole_title_repair": title_request}
    counts, task_ids = {"ranking": 0, "short_v2": 0}, []
    # The publisher already locks chapters before drafts. Recheck fresh columns
    # here as well, so direct component callers cannot supply a stale manifest.
    for number in sorted(grouped):
        source, findings = sources[number], grouped[number]
        serial = [{"gate": kind, "finding": finding.to_dict()} for kind, finding, _ in findings]
        serial.sort(key=_json)
        key = _sha({"schema": "whole-chapter-task.v2", "source": current.manifest,
                    "chapter": number, "findings": serial})
        if key in by_key:
            task_ids.append(str(by_key[key].id))
            continue
        chapter = await session.scalar(select(ChapterModel).where(
            ChapterModel.id == UUID(source["chapter_id"]), ChapterModel.project_id == project.id,
        ).execution_options(populate_existing=True))
        draft = await session.scalar(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.id == UUID(source["draft_id"]),
            ChapterDraftVersionModel.project_id == project.id,
        ).execution_options(populate_existing=True))
        if (chapter is None or draft is None or draft.chapter_id != chapter.id
            or chapter.chapter_number != number or not draft.is_current
            or text_hash(draft.content_md) != source["raw_body_sha256"]):
            raise ValueError("fanqie_whole_task_source_changed")
        binding = rewrite_source_binding(draft, chapter=chapter)
        task = RewriteTaskModel(
            project_id=project.id, trigger_type="fanqie_whole_gate",
            trigger_source_id=chapter.id, rewrite_strategy="whole_chapter_repair",
            priority=1, status="pending",
            instructions="\n\n".join(render(FanqieRankingGateReport(
                passed=False, phase=finding.phase, findings=(finding,)))
                for _kind, finding, render in findings),
            context_required=["chapter_draft", "fanqie_whole_source"],
            metadata_json={**binding, "chapter_id": str(chapter.id), "chapter_number": number,
                           "whole_revision_key": key, "whole_findings": serial,
                           "finding": serial[0]["finding"],
                           "source_manifest": deepcopy(current.manifest), "grade_asserted": False},
        )
        session.add(task)
        await session.flush()
        by_key[key] = task
        task_ids.append(str(task.id))
        counts[findings[0][0]] += 1
    return counts, task_ids


def _verify_delivery_files(
    output_dir, current, paths, *, readiness_inputs, expected_verification=None,
    semantic_context=None,
):
    """Bind the platform's own package to the exact assembly, not generic project.md."""
    from bestseller.services.drafts import count_words
    from bestseller.services.fanqie_short_export import (
        _clean_fanqie_short_body,
        build_signing_readiness_report,
        insert_unlock_line_marker,
    )

    if (
        not isinstance(readiness_inputs, dict)
        or set(readiness_inputs) != {"unlock_line_ratio", "protagonist_name"}
        or type(readiness_inputs["unlock_line_ratio"]) not in (int, float)
        or not 0 <= readiness_inputs["unlock_line_ratio"] <= 1
        or (
            readiness_inputs["protagonist_name"] is not None
            and not isinstance(readiness_inputs["protagonist_name"], str)
        )
    ):
        raise ValueError("fanqie_delivery_readiness_inputs_invalid")

    output_dir = Path(output_dir).resolve()
    filenames = {
        "markdown_path": "fanqie-short.md",
        "readiness_path": "signing-readiness.json",
        "source_receipt_path": "adopted-source.json",
    }
    files = {}
    for key, filename in filenames.items():
        path = output_dir / "exports" / filename
        if key not in paths or Path(paths[key]).resolve() != path:
            raise ValueError("fanqie_delivery_path_mismatch:" + key)
        data = path.read_bytes()
        text = data.decode("utf-8")
        if key == "markdown_path":
            expected = "# " + current.manifest["project"]["title"] + "\n\n"
            expected += _clean_fanqie_short_body(current.full_text)
            if data != expected.encode("utf-8"):
                raise ValueError("fanqie_delivery_body_mismatch")
        elif key == "source_receipt_path":
            if json.loads(text) != current.manifest:
                raise ValueError("fanqie_delivery_source_mismatch")
        else:
            report = json.loads(text)
            words = count_words(current.full_text)
            if (
                not isinstance(report, dict)
                or report.get("platform") != "tomato_short"
                or report.get("total_words") != words
                or report.get("target_word_count")
                != (current.manifest["project"]["target_word_count"] or words)
                or any(
                    type(report.get(key)) is not bool
                    for key in (
                        "opening_gate_passed",
                        "ranking_gate_passed",
                        "short_v2_gate_passed",
                        "ready_for_upload",
                    )
                )
            ):
                raise ValueError("fanqie_delivery_readiness_invalid")
            # Recompute from this exact adopted body/target, not asserted flags.
            expected_report = build_signing_readiness_report(
                current.full_text,
                title=current.manifest["project"]["title"],
                target_word_count=current.manifest["project"]["target_word_count"],
                semantic_context=semantic_context,
                **readiness_inputs,
            )
            _, unlock_pos = insert_unlock_line_marker(
                current.full_text, unlock_line_ratio=readiness_inputs["unlock_line_ratio"]
            )
            expected_report["unlock_line_char_position"] = unlock_pos
            if _json(report) != _json(expected_report):
                raise ValueError("fanqie_delivery_readiness_mismatch")
        files[key] = {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    verified = {
        "schema": DELIVERY_SCHEMA,
        "output_dir": str(output_dir),
        "files": files,
        "source_manifest_sha256": _sha(current.manifest),
        "assembly_sha256": _text_sha(current.full_text),
        "readiness_inputs": deepcopy(readiness_inputs),
        "ready_for_upload": report["ready_for_upload"],
    }
    if expected_verification is not None and _json(verified) != _json(expected_verification):
        raise ValueError("fanqie_delivery_checksum_mismatch")
    return verified


async def _read_fanqie_package_evidence(session, project_id, *, lock=False, settings=None):
    """Revalidate a prior normal package independently of pending rewrite work.

    Caller supplies a clean session (the generic delivery reader uses a separate
    identity map). Legacy v1 receipts lack verified readiness inputs and require
    a normal publication retry; old completion/upload flags grant no evidence.
    """
    current = await read_adopted_assembly(session, project_id, lock=lock)
    project = await session.get(ProjectModel, project_id)
    receipt = (project.metadata_json or {}).get("fanqie_whole_export_receipt") or {}
    verification = receipt.get("delivery_verification") or {}
    if (
        receipt.get("schema") != POLICY
        or receipt.get("status") != "pass"
        or receipt.get("source_manifest") != current.manifest
        or verification.get("schema") != DELIVERY_SCHEMA
    ):
        raise ValueError("fanqie_delivery_receipt_unverified")
    from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
    from bestseller.services.fanqie_short_semantic_readiness import (
        load_upload_semantic_context, semantic_readiness_view,
    )
    inputs = verification.get("readiness_inputs")
    if not isinstance(inputs, dict) or set(inputs) != {"unlock_line_ratio", "protagonist_name"}:
        raise ValueError("fanqie_delivery_readiness_inputs_invalid")
    original_report = review_whole_fanqie_short_story(current.full_text, title=project.title, **inputs)
    if _json(receipt.get("whole_review")) != _json(original_report.to_dict()):
        raise ValueError("fanqie_delivery_raw_review_mismatch")
    context = await load_upload_semantic_context(
        session, settings=settings, project=project, assembly=current, original_report=original_report,
    )
    view = semantic_readiness_view(context, full_text=current.full_text, title=project.title, **inputs)
    effective = original_report.passed if view is None else not view[
        "semantic_derived_review"]["remaining_blockers"]
    if not effective:
        raise ValueError("fanqie_delivery_effective_review_blocked")
    if (_json(receipt.get("current_semantic")) != _json(view)
            or ("effective_passed" in receipt and receipt["effective_passed"] is not effective)
            or (view is not None and "effective_passed" not in receipt)):
        raise ValueError("fanqie_delivery_semantic_receipt_mismatch")
    return _verify_delivery_files(
        verification["output_dir"],
        current,
        receipt.get("export_paths") or {},
        readiness_inputs=verification.get("readiness_inputs"),
        semantic_context=context,
        expected_verification=verification,
    )


async def read_fanqie_delivery_evidence(session, project_id, *, lock=False, settings=None):
    """Read package evidence and require the formal delivery rewrite loop closed."""
    verification = await _read_fanqie_package_evidence(session, project_id, lock=lock, settings=settings)
    pending = await session.scalar(
        select(RewriteTaskModel.id)
        .where(
            RewriteTaskModel.project_id == project_id,
            RewriteTaskModel.status.in_(("pending", "queued")),
        )
        .limit(1)
    )
    if pending is not None:
        raise ValueError("fanqie_delivery_pending_rewrite")
    return verification


async def publish_adopted_assembly(
    session,
    *,
    project_id,
    output_dir,
    expected,
    unlock_line_ratio,
    protagonist_name,
    export_markdown=True,
    unlock_segment=1,
    settings=None,
):
    """Fresh source verification and pure whole gate precede every official write."""
    from bestseller.services.fanqie_short_export import (
        export_fanqie_short_markdown,
        export_fanqie_short_rejected_draft,
    )
    from bestseller.services.fanqie_short_finalizer import finalize_fanqie_short_for_upload
    from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
    from bestseller.services.pipelines import _checkpoint_commit
    from bestseller.services.fanqie_short_semantic_readiness import (
        load_upload_semantic_context,
        semantic_readiness_view,
    )

    current = await read_adopted_assembly(session, project_id, lock=True)
    if current.manifest_json != expected.manifest_json or current.full_text != expected.full_text:
        raise ValueError("fanqie_export_source_changed")
    project = await session.get(ProjectModel, project_id)
    review = review_whole_fanqie_short_story(
        current.full_text,
        title=project.title,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    context = await load_upload_semantic_context(
        session, settings=settings, project=project, assembly=current, original_report=review,
    )
    semantic_view = semantic_readiness_view(
        context, full_text=current.full_text, title=project.title,
        unlock_line_ratio=unlock_line_ratio, protagonist_name=protagonist_name,
    )
    effective_passed = review.passed if semantic_view is None else not semantic_view[
        "semantic_derived_review"]["remaining_blockers"]
    previous_outputs_archive = (
        archive_existing_whole_outputs(output_dir) if export_markdown else None
    )
    counts, task_ids = ({"ranking": 0, "short_v2": 0}, [])
    if not effective_passed:
        counts, task_ids = await _revision_tasks(
            session, project, current, review, unlock_segment, semantic_view=semantic_view,
        )
    export_paths, rejected_paths, candidate_paths = {}, {}, {}
    if export_markdown and effective_passed:
        export_paths = export_fanqie_short_markdown(
            Path(output_dir),
            title=project.title,
            genre=project.genre,
            full_text=current.full_text,
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist_name,
            target_word_count=project.target_word_count,
            semantic_context=context,
        )
        receipt_path = Path(output_dir) / "exports" / "adopted-source.json"
        receipt_path.write_text(_json(current.manifest), encoding="utf-8")
        export_paths["source_receipt_path"] = str(receipt_path.resolve())
    elif export_markdown:
        candidate = finalize_fanqie_short_for_upload(
            Path(output_dir),
            title=project.title,
            genre=project.genre,
            full_text=current.full_text,
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist_name,
            target_word_count=project.target_word_count,
            source_manifest=current.manifest,
        )
        candidate_paths = candidate.export_paths
        rejected_paths = export_fanqie_short_rejected_draft(
            Path(output_dir),
            title=project.title,
            genre=project.genre,
            full_text=current.full_text,
            review_report=review.to_dict(),
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist_name,
            target_word_count=project.target_word_count,
            semantic_context=context,
        )
    verification = None
    if export_paths:
        latest = await read_adopted_assembly(session, project_id, lock=True)
        if latest != current:
            raise ValueError("fanqie_export_source_changed_after_write")
        # The publisher must re-read native evidence/factory after the write;
        # the exported context is never reused as authority for readback.
        latest_context = await load_upload_semantic_context(
            session, settings=settings, project=project, assembly=latest, original_report=review,
        )
        verification = _verify_delivery_files(
            output_dir,
            latest,
            export_paths,
            readiness_inputs={
                "unlock_line_ratio": unlock_line_ratio,
                "protagonist_name": protagonist_name,
            },
            semantic_context=latest_context,
        )
    pending = list(
        await session.scalars(
            select(RewriteTaskModel.id).where(
                RewriteTaskModel.project_id == project_id,
                RewriteTaskModel.status.in_(("pending", "queued")),
            )
        )
    )
    complete = bool(verification and effective_passed and not pending)
    # Formal delivery closure and platform readiness are independent predicates.
    ready_for_upload = verification["ready_for_upload"] if verification else False
    status = "completed" if complete else "writing" if effective_passed else "revising"
    receipt = {
        "schema": POLICY,
        "status": "pass" if effective_passed else "needs_revision",
        "source_manifest": current.manifest,
        "export_paths": export_paths,
        "candidate_paths": candidate_paths,
        "previous_outputs_archive": previous_outputs_archive,
        "knowledge_closed": True,
        "normal_rewrite_task_ids": task_ids,
        "whole_review": review.to_dict(),
        "model_grade_asserted": False,
        "delivery_verification": verification,
        "whole_book_complete": complete,
        "effective_passed": effective_passed,
        "current_semantic": deepcopy(semantic_view),
    }
    if export_markdown or not effective_passed:
        metadata = dict(project.metadata_json or {})
        previous = metadata.get("fanqie_whole_export_receipt")
        if previous and previous != receipt:
            metadata["fanqie_whole_export_history"] = [
                *metadata.get("fanqie_whole_export_history", []),
                deepcopy(previous),
            ]
        metadata = {
            **metadata,
            "fanqie_short_ready_for_upload": ready_for_upload,
            "fanqie_whole_export_receipt": receipt,
        }
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(status=status, metadata_json=metadata)
        )
    else:
        # An evaluation-only call neither reopens a completed book nor publishes it.
        complete = False
        try:
            prior_verification = await _read_fanqie_package_evidence(
                session, project_id, lock=True, settings=settings,
            )
            ready_for_upload = prior_verification["ready_for_upload"]
            complete = project.status == "completed" and not pending
        except (ValueError, OSError, KeyError):
            complete = False
    await _checkpoint_commit(session)
    return {
        "review": review,
        "effective_passed": effective_passed,
        "current_semantic": semantic_view,
        "full_text": current.full_text,
        "export_paths": export_paths,
        "rejected_paths": rejected_paths,
        "candidate_paths": candidate_paths,
        "previous_outputs_archive": previous_outputs_archive,
        "whole_book_complete": complete,
        "ready_for_upload": ready_for_upload,
        "status": receipt["status"],
        "revision_task_counts": counts,
        "normal_rewrite_task_ids": task_ids,
    }
