from __future__ import annotations

import hashlib
from html import escape
import io
import logging
import math
from pathlib import Path
import re
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import markdown as markdown_lib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    ProjectModel,
)
from bestseller.services.drafts import format_chapter_heading, sanitize_novel_markdown_content
from bestseller.services.projects import get_project_by_slug
from bestseller.services.writing_profile import normalize_language
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_CJK_CHAR_PATTERN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_LATIN_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’._-][A-Za-z0-9]+)*")


def _ensure_chapter_heading(
    chapter: ChapterModel,
    content_md: str,
    *,
    language: str | None = None,
) -> str:
    """Prepend a canonical chapter heading if the content lacks one."""
    if content_md.startswith(f"# 第{chapter.chapter_number}章") or content_md.startswith(
        f"# Chapter {chapter.chapter_number}"
    ):
        return content_md
    heading = format_chapter_heading(chapter.chapter_number, chapter.title, language=language)
    return f"{heading}\n\n{content_md}"


def build_project_markdown(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
) -> str:
    project_language = normalize_language(getattr(project, "language", None))
    is_en = project_language.lower().startswith("en")
    header = [f"# {project.title}", f"> {'Genre' if is_en else '类型'}：{project.genre}"]
    sections = [
        _ensure_chapter_heading(
            ch,
            sanitize_novel_markdown_content(draft.content_md),
            language=project_language,
        )
        for ch, draft in chapter_payloads
    ]
    return "\n\n".join(header + sections).strip()


def write_markdown_output(
    output_path: Path,
    content_md: str,
) -> tuple[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content_md, encoding="utf-8")
    checksum = hashlib.sha256(content_md.encode("utf-8")).hexdigest()
    return str(output_path.resolve()), checksum


def write_binary_output(
    output_path: Path,
    content_bytes: bytes,
) -> tuple[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content_bytes)
    checksum = hashlib.sha256(content_bytes).hexdigest()
    return str(output_path.resolve()), checksum


def _parse_markdown_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if stripped.startswith("# "):
        return "h1", stripped[2:].strip()
    if stripped.startswith("## "):
        return "h2", stripped[3:].strip()
    if stripped.startswith("> "):
        return "quote", stripped[2:].strip()
    if stripped.startswith("- "):
        return "li", stripped[2:].strip()
    return "p", stripped


def markdown_to_plain_text(content_md: str) -> str:
    lines: list[str] = []
    for raw_line in content_md.splitlines():
        if not raw_line.strip():
            lines.append("")
            continue
        _, text = _parse_markdown_line(raw_line)
        lines.append(text)
    return "\n".join(lines).strip()


def markdown_to_html(content_md: str) -> str:
    rendered = markdown_lib.markdown(
        sanitize_novel_markdown_content(content_md),
        extensions=[
            "extra",
            "sane_lists",
            "nl2br",
        ],
        output_format="html5",
    )
    return rendered.strip()


def build_markdown_reading_stats(content_md: str) -> dict[str, int]:
    plain_text = markdown_to_plain_text(content_md)
    non_whitespace_text = re.sub(r"\s+", "", plain_text)
    word_count = len(_CJK_CHAR_PATTERN.findall(non_whitespace_text)) + len(
        _LATIN_WORD_PATTERN.findall(plain_text)
    )
    character_count = len(non_whitespace_text)
    paragraph_count = len([line for line in plain_text.splitlines() if line.strip()])
    estimated_read_minutes = math.ceil(word_count / 500) if word_count > 0 else 0
    return {
        "word_count": word_count,
        "character_count": character_count,
        "paragraph_count": paragraph_count,
        "estimated_read_minutes": estimated_read_minutes,
    }


def build_docx_bytes(title: str, content_md: str, *, author: str | None = None) -> bytes:
    lines = [line for line in content_md.splitlines() if line.strip()]
    paragraph_xml: list[str] = []
    if title:
        paragraph_xml.append(
            "<w:p><w:pPr><w:pStyle w:val=\"Title\"/></w:pPr>"
            f"<w:r><w:t>{escape(title)}</w:t></w:r></w:p>"
        )
    for raw_line in lines:
        block_type, text = _parse_markdown_line(raw_line)
        style = {
            "h1": "Heading1",
            "h2": "Heading2",
            "quote": "Quote",
            "li": "ListParagraph",
        }.get(block_type)
        style_xml = (
            f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style is not None else ""
        )
        paragraph_xml.append(
            f"<w:p>{style_xml}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"
        )

    document_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:body>"
        f"{''.join(paragraph_xml)}"
        "<w:sectPr>"
        "<w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/>"
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )
    styles_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:styles xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\">"
        "<w:name w:val=\"Normal\"/></w:style>"
        "<w:style w:type=\"paragraph\" w:styleId=\"Title\"><w:name w:val=\"Title\"/></w:style>"
        "<w:style w:type=\"paragraph\" w:styleId=\"Heading1\"><w:name w:val=\"heading 1\"/></w:style>"
        "<w:style w:type=\"paragraph\" w:styleId=\"Heading2\"><w:name w:val=\"heading 2\"/></w:style>"
        "<w:style w:type=\"paragraph\" w:styleId=\"Quote\"><w:name w:val=\"Quote\"/></w:style>"
        "<w:style w:type=\"paragraph\" w:styleId=\"ListParagraph\"><w:name w:val=\"List Paragraph\"/></w:style>"
        "</w:styles>"
    )
    content_types_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Override PartName=\"/word/document.xml\" "
        "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
        "<Override PartName=\"/word/styles.xml\" "
        "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/>"
        "<Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/>"
        "<Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/>"
        "</Types>"
    )
    rels_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
        "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties\" Target=\"docProps/core.xml\"/>"
        "<Relationship Id=\"rId3\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties\" Target=\"docProps/app.xml\"/>"
        "</Relationships>"
    )
    app_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\" "
        "xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes\">"
        "<Application>BestSeller</Application></Properties>"
    )
    core_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<cp:coreProperties xmlns:cp=\"http://schemas.openxmlformats.org/package/2006/metadata/core-properties\" "
        "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" "
        "xmlns:dcterms=\"http://purl.org/dc/terms/\" "
        "xmlns:dcmitype=\"http://purl.org/dc/dcmitype/\" "
        "xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">"
        f"<dc:title>{escape(title)}</dc:title><dc:creator>{escape(author or 'BestSeller')}</dc:creator>"
        "</cp:coreProperties>"
    )

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("docProps/core.xml", core_xml)
    return buffer.getvalue()


def build_epub_bytes(
    title: str,
    content_md: str,
    *,
    language: str = "zh-CN",
    author: str | None = None,
    identifier: str = "bestseller-export",
) -> bytes:
    html_body = markdown_to_html(content_md)
    nav_title = "Table of Contents" if language.lower().startswith("en") else "目录"
    escaped_author = escape(author) if author else None
    content_xhtml = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        f"<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"{escape(language)}\">"
        f"<head><title>{escape(title)}</title><meta charset=\"utf-8\"/></head>"
        f"<body>{html_body}</body></html>"
    )
    nav_xhtml = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        f"<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"{escape(language)}\">"
        f"<head><title>{escape(title)} {escape(nav_title)}</title></head>"
        "<body><nav epub:type=\"toc\" id=\"toc\">"
        f"<ol><li><a href=\"content.xhtml\">{escape(title)}</a></li></ol>"
        "</nav></body></html>"
    )
    content_opf = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        "<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bookid\" version=\"3.0\">"
        "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        f"<dc:identifier id=\"bookid\">{escape(identifier)}</dc:identifier>"
        f"<dc:title>{escape(title)}</dc:title>"
        f"{f'<dc:creator>{escaped_author}</dc:creator>' if escaped_author else ''}"
        f"<dc:language>{escape(language)}</dc:language>"
        "</metadata>"
        "<manifest>"
        "<item id=\"nav\" href=\"nav.xhtml\" media-type=\"application/xhtml+xml\" properties=\"nav\"/>"
        "<item id=\"content\" href=\"content.xhtml\" media-type=\"application/xhtml+xml\"/>"
        "</manifest>"
        "<spine><itemref idref=\"content\"/></spine>"
        "</package>"
    )
    container_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">"
        "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/></rootfiles>"
        "</container>"
    )

    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", container_xml, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/content.xhtml", content_xhtml, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", content_opf, compress_type=ZIP_DEFLATED)
    return buffer.getvalue()


def build_pdf_bytes(title: str, content_md: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.pdfmetrics import registerFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PDF export requires reportlab. Install optional dependencies with bestseller[export]."
        ) from exc

    registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BestsellerTitle",
        parent=styles["Title"],
        fontName="STSong-Light",
        fontSize=18,
        leading=24,
    )
    heading_style = ParagraphStyle(
        "BestsellerHeading",
        parent=styles["Heading2"],
        fontName="STSong-Light",
        fontSize=14,
        leading=18,
    )
    body_style = ParagraphStyle(
        "BestsellerBody",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=11,
        leading=16,
    )
    quote_style = ParagraphStyle(
        "BestsellerQuote",
        parent=body_style,
        leftIndent=10,
        textColor="#555555",
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=title,
    )

    story = [Paragraph(escape(title), title_style), Spacer(1, 8)]
    for raw_line in content_md.splitlines():
        if not raw_line.strip():
            story.append(Spacer(1, 6))
            continue
        block_type, text = _parse_markdown_line(raw_line)
        style = body_style
        if block_type == "h1":
            style = title_style
        elif block_type == "h2":
            style = heading_style
        elif block_type == "quote":
            style = quote_style
        story.append(Paragraph(escape(text), style))
        story.append(Spacer(1, 4))

    document.build(story)
    return buffer.getvalue()


async def _load_chapter_export_payload(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
) -> tuple[ProjectModel, ChapterModel, ChapterDraftVersionModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise ValueError(
            f"Chapter {chapter_number} does not have a current assembled draft to export."
        )
    return project, chapter, draft


async def _load_project_export_payload(
    session: AsyncSession,
    project_slug: str,
) -> tuple[ProjectModel, list[tuple[ChapterModel, ChapterDraftVersionModel]]]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    if not chapters:
        raise ValueError(f"Project '{project_slug}' does not have any chapters to export.")

    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] = []
    for chapter in chapters:
        draft = await session.scalar(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        if draft is None:
            continue
        chapter_payloads.append((chapter, draft))

    if not chapter_payloads:
        raise ValueError(
            f"Project '{project_slug}' does not have any current chapter drafts to export."
        )
    return project, chapter_payloads


async def load_project_export_content(
    session: AsyncSession,
    project_slug: str,
) -> tuple[ProjectModel, str]:
    project, chapter_payloads = await _load_project_export_payload(session, project_slug)
    return project, build_project_markdown(project, chapter_payloads)


def create_export_artifact(
    *,
    project_id: UUID,
    export_type: str,
    source_scope: str,
    source_id: UUID,
    storage_uri: str,
    checksum: str,
    version_label: str,
    created_by_run_id: UUID | None,
) -> ExportArtifactModel:
    return ExportArtifactModel(
        project_id=project_id,
        export_type=export_type,
        source_scope=source_scope,
        source_id=source_id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=version_label,
        created_by_run_id=created_by_run_id,
    )


async def preflight_export_check(
    session: AsyncSession,
    project_id: UUID,
) -> list[str]:
    """Run pre-export quality checks. Returns warning messages (empty = all clear)."""
    warnings: list[str] = []

    try:
        # 1. Check for incomplete chapters (missing current drafts)
        chapters = (await session.scalars(
            select(ChapterModel).where(ChapterModel.project_id == project_id)
        )).all()
        for ch in chapters:
            draft = await session.scalar(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id == ch.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
            if draft is None:
                warnings.append(f"第{ch.chapter_number}章缺少当前草稿")
    except Exception:
        logger.debug("Preflight check: chapter completeness check failed", exc_info=True)

    try:
        # 2. Check for unresolved clues
        from bestseller.infra.db.models import ClueModel

        stale_clues = (await session.scalars(
            select(ClueModel).where(
                ClueModel.project_id == project_id,
                ClueModel.actual_paid_off_chapter_number.is_(None),
            ).limit(10)
        )).all()
        planted_clues = [c for c in stale_clues if c.planted_in_chapter_number is not None]
        if planted_clues:
            warnings.append(f"有{len(planted_clues)}条伏笔尚未回收")
    except Exception:
        logger.debug("Preflight check: clue resolution check failed", exc_info=True)

    try:
        # 3. Check for incomplete arcs
        from bestseller.infra.db.models import PlotArcModel

        open_arcs = (await session.scalars(
            select(PlotArcModel).where(
                PlotArcModel.project_id == project_id,
                PlotArcModel.status.in_(["active", "rising"]),
            ).limit(10)
        )).all()
        if open_arcs:
            warnings.append(f"有{len(open_arcs)}条叙事弧尚未完结")
    except Exception:
        logger.debug("Preflight check: arc completeness check failed", exc_info=True)

    return warnings


async def export_chapter_markdown(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter, draft = await _load_chapter_export_payload(session, project_slug, chapter_number)
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.md"
    content_md = _ensure_chapter_heading(
        chapter,
        sanitize_novel_markdown_content(draft.content_md),
        language=project.language,
    )
    storage_uri, checksum = write_markdown_output(output_path, content_md)
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=f"chapter-{chapter.chapter_number:03d}-v{draft.version_no}",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_project_markdown(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter_payloads = await _load_project_export_payload(session, project_slug)
    preflight_warnings = await preflight_export_check(session, project.id)
    if preflight_warnings:
        logger.warning("Export pre-flight warnings for %s: %s", project_slug, "; ".join(preflight_warnings))
    content_md = build_project_markdown(project, chapter_payloads)
    output_path = Path(settings.output.base_dir) / project.slug / "project.md"
    storage_uri, checksum = write_markdown_output(output_path, content_md)
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label="project-current",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_chapter_docx(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter, draft = await _load_chapter_export_payload(session, project_slug, chapter_number)
    title = f"第{chapter.chapter_number}章 {chapter.title or ''}".strip()
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.docx"
    storage_uri, checksum = write_binary_output(output_path, build_docx_bytes(title, draft.content_md))
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="docx",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=f"chapter-{chapter.chapter_number:03d}-v{draft.version_no}",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_project_docx(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter_payloads = await _load_project_export_payload(session, project_slug)
    preflight_warnings = await preflight_export_check(session, project.id)
    if preflight_warnings:
        logger.warning("Export pre-flight warnings for %s: %s", project_slug, "; ".join(preflight_warnings))
    content_md = build_project_markdown(project, chapter_payloads)
    output_path = Path(settings.output.base_dir) / project.slug / "project.docx"
    storage_uri, checksum = write_binary_output(output_path, build_docx_bytes(project.title, content_md))
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="docx",
        source_scope="project",
        source_id=project.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label="project-current",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_chapter_epub(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter, draft = await _load_chapter_export_payload(session, project_slug, chapter_number)
    title = f"第{chapter.chapter_number}章 {chapter.title or ''}".strip()
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.epub"
    storage_uri, checksum = write_binary_output(
        output_path,
        build_epub_bytes(title, draft.content_md, language=project.language),
    )
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="epub",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=f"chapter-{chapter.chapter_number:03d}-v{draft.version_no}",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_project_epub(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter_payloads = await _load_project_export_payload(session, project_slug)
    preflight_warnings = await preflight_export_check(session, project.id)
    if preflight_warnings:
        logger.warning("Export pre-flight warnings for %s: %s", project_slug, "; ".join(preflight_warnings))
    content_md = build_project_markdown(project, chapter_payloads)
    output_path = Path(settings.output.base_dir) / project.slug / "project.epub"
    storage_uri, checksum = write_binary_output(
        output_path,
        build_epub_bytes(project.title, content_md, language=project.language or "zh-CN"),
    )
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="epub",
        source_scope="project",
        source_id=project.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label="project-current",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_chapter_pdf(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter, draft = await _load_chapter_export_payload(session, project_slug, chapter_number)
    title = f"第{chapter.chapter_number}章 {chapter.title or ''}".strip()
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.pdf"
    storage_uri, checksum = write_binary_output(output_path, build_pdf_bytes(title, draft.content_md))
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="pdf",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=f"chapter-{chapter.chapter_number:03d}-v{draft.version_no}",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_project_pdf(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter_payloads = await _load_project_export_payload(session, project_slug)
    preflight_warnings = await preflight_export_check(session, project.id)
    if preflight_warnings:
        logger.warning("Export pre-flight warnings for %s: %s", project_slug, "; ".join(preflight_warnings))
    content_md = build_project_markdown(project, chapter_payloads)
    output_path = Path(settings.output.base_dir) / project.slug / "project.pdf"
    storage_uri, checksum = write_binary_output(output_path, build_pdf_bytes(project.title, content_md))
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="pdf",
        source_scope="project",
        source_id=project.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label="project-current",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path
