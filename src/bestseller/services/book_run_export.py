"""Complete closure-draft exports bind selected qualified versions, not current."""

import hashlib
from pathlib import Path
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc


async def load_selected_sources(session, settings, slug, manifest):
    project = await session.scalar(select(m.ProjectModel).where(m.ProjectModel.slug == slug))
    if project is None or set(manifest) != set(map(str, range(1, project.target_chapters + 1))):
        raise ValueError("book_export_chapter_inventory")
    payloads = []
    for number in range(1, project.target_chapters + 1):
        source = manifest[str(number)]
        chapter = await session.get(
            m.ChapterModel, UUID(source["chapter_id"]), populate_existing=True
        )
        draft = await session.get(
            m.ChapterDraftVersionModel, UUID(source["draft_id"]), populate_existing=True
        )
        if (
            chapter is None
            or draft is None
            or chapter.project_id != project.id
            or chapter.chapter_number != number
            or draft.project_id != project.id
            or draft.chapter_id != chapter.id
            or dc.body_hash(draft.content_md) != source["body_sha256"]
        ):
            raise ValueError("book_export_source_changed")
        pair = await dc.select_reference(session, settings, project=project, chapter=chapter)
        if pair is None or pair[0].id != draft.id or pair[1] != source["reference"]:
            raise ValueError("book_export_reference_changed")
        payloads.append((chapter, draft))
    return project, payloads


async def verify_export(session, settings, *, project, manifest, artifact_id, book_run_id):
    from bestseller.services.prompt_source_policy import content_hash
    from bestseller.services.exports import build_project_markdown

    artifact = await session.get(
        m.ExportArtifactModel, UUID(str(artifact_id)), populate_existing=True
    )
    if (
        artifact is None
        or artifact.project_id != project.id
        or artifact.export_type != "markdown_draft"
        or artifact.source_scope != "project"
        or artifact.source_id != project.id
        or str(artifact.created_by_run_id) != str(book_run_id)
    ):
        raise ValueError("book_export_artifact_scope")
    goal = await session.get(m.WorkflowRunModel, UUID(str(book_run_id)), populate_existing=True)
    if (
        goal is None
        or goal.project_id != project.id
        or goal.metadata_json.get("schema") != "source-bound-book-run.v1"
        or goal.metadata_json.get("selected") != manifest
    ):
        raise ValueError("book_export_run_manifest_changed")
    meta = artifact.metadata_json or {}
    if (
        meta.get("book_run_id") != str(book_run_id)
        or meta.get("selected_manifest") != manifest
        or meta.get("book_run_manifest_sha256") != content_hash(manifest)
    ):
        raise ValueError("book_export_manifest_changed")
    expected = [
        {
            "chapter_id": manifest[str(n)]["chapter_id"],
            "chapter_number": n,
            "draft_id": manifest[str(n)]["draft_id"],
            "body_sha256": manifest[str(n)]["body_sha256"],
        }
        for n in range(1, project.target_chapters + 1)
    ]
    sources = meta.get("source_drafts")
    if (
        not isinstance(sources, list)
        or [{k: row.get(k) for k in expected[0]} for row in sources] != expected
    ):
        raise ValueError("book_export_artifact_sources_changed")
    _, payloads = await load_selected_sources(session, settings, project.slug, manifest)
    raw = Path(artifact.storage_uri).read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact.checksum:
        raise ValueError("book_export_file_changed")
    disclaimer = (
        "> **状态：整书草稿已闭环，但未通过严格出版门禁。** 该文件包含已接受的质量债，"
        "仅用于通读、编辑和后续定稿，不代表可直接发布。\n\n"
    )
    if raw != (disclaimer + build_project_markdown(project, payloads)).encode("utf-8"):
        raise ValueError("book_export_bytes_do_not_match_selected_manuscript")
    return {
        "verified": True,
        "artifact_id": str(artifact.id),
        "path": artifact.storage_uri,
        "sha256": artifact.checksum,
        "chapters": len(payloads),
        "manifest_sha256": content_hash(manifest),
        "publication_ready": False,
    }
