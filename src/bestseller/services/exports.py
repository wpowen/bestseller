from __future__ import annotations

from collections.abc import Callable
import hashlib
from html import escape, unescape
import io
import json
import logging
import math
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import markdown as markdown_lib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import DraftPromotionState
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    ProjectModel,
)
from bestseller.services.book_listing import (
    build_book_listing_profile,
    write_platform_title_workflow_artifacts,
)
from bestseller.services.drafts import (
    count_words,
    format_chapter_heading,
    sanitize_novel_markdown_content,
)
from bestseller.services.output_hygiene import collect_unfinished_artifact_issues
from bestseller.services.projects import get_project_by_slug
from bestseller.services.writing_profile import normalize_language
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


class ProjectExportIncompleteError(ValueError):
    """Raised when a publication export would silently omit chapters."""

    def __init__(self, project_slug: str, missing_chapters: list[int]) -> None:
        self.project_slug = project_slug
        self.missing_chapters = tuple(missing_chapters)
        joined = ", ".join(str(number) for number in missing_chapters)
        super().__init__(
            f"Project '{project_slug}' cannot be exported: chapters without a "
            f"promoted draft: {joined}. Use the explicit closure-draft export "
            "path for non-publication diagnostics."
        )


def _bundle_hook_domain_tokens(project) -> tuple[str, ...]:
    """Book-derived hook vocabulary for the quality bundle's hook-echo check.

    Same source as the production-side injection (imagery anchors) so the
    duty block and validation always extract the same token set. Fails to
    () — the generic extraction layers carry the gate without it.
    """

    try:
        from bestseller.services.imagery_system_design import (
            imagery_anchor_phrases,
        )

        return imagery_anchor_phrases(project)
    except Exception:
        return ()



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


def _prepare_chapter_content(
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    *,
    language: str | None = None,
) -> str:
    """Sanitise and ensure heading for a single chapter's draft content.

    Mirrors what ``build_project_markdown`` does per chapter so that
    single-chapter binary exports (DOCX/EPUB/PDF) get the same treatment
    as project-level exports.  Single-chapter Markdown export already
    does this inline (see ``export_chapter_markdown``).
    """
    clean = sanitize_novel_markdown_content(draft.content_md, language=language)
    return _ensure_chapter_heading(chapter, clean, language=language)


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
            sanitize_novel_markdown_content(draft.content_md, language=project_language),
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


def _metadata_dict(project: ProjectModel) -> dict:
    metadata = getattr(project, "metadata_json", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _flatten_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "；".join(
            part for part in (_flatten_text(item) for item in value.values()) if part
        )
    if isinstance(value, (list, tuple, set)):
        return "；".join(part for part in (_flatten_text(item) for item in value) if part)
    return str(value).strip()


def _metadata_first_text(metadata: dict, *keys: str) -> str:
    for key in keys:
        text = _flatten_text(metadata.get(key))
        if text:
            return text
    return ""


def _metadata_text_list(metadata: dict, *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = metadata.get(key)
        if isinstance(raw, str):
            if raw.strip():
                values.append(raw.strip())
            continue
        if isinstance(raw, dict):
            for item in raw.values():
                text = _flatten_text(item)
                if text:
                    values.append(text)
            continue
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                text = _flatten_text(item)
                if text:
                    values.append(text)
            continue
        text = _flatten_text(raw)
        if text:
            values.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _csv_cell(value: object) -> str:
    text = _flatten_text(value).replace('"', '""')
    return f'"{text}"'


def _coerce_chapter_range(value: object) -> tuple[int, int] | None:
    if isinstance(value, str):
        match = re.search(r"(\d+)\s*[-–]\s*(\d+)", value)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _metadata_volume_plan_rows(
    metadata: dict,
    *,
    max_generated: int,
    fallback_target: int,
    reader_promise: str,
) -> list[str]:
    plan = metadata.get("premium_volume_plan")
    if not isinstance(plan, list) or not plan:
        plan = metadata.get("volume_plan")
    if not isinstance(plan, list) or not plan:
        return [
            ",".join(
                [
                    _csv_cell("1"),
                    _csv_cell("1"),
                    _csv_cell(str(fallback_target or max_generated or 1)),
                    _csv_cell("writing"),
                    _csv_cell(reader_promise),
                ]
            )
        ]

    rows: list[str] = []
    for index, item in enumerate(plan, start=1):
        if not isinstance(item, dict):
            continue
        volume_no = int(item.get("volume_number") or index)
        chapter_range = _coerce_chapter_range(item.get("chapter_range")) or (
            (volume_no - 1) * 50 + 1,
            volume_no * 50,
        )
        start, end = chapter_range
        if max_generated >= end:
            status = "drafted"
        elif start <= max_generated <= end:
            status = "active"
        else:
            status = "planned"
        goal = _flatten_text(item.get("volume_goal") or item.get("volume_title") or item.get("core_payoff"))
        rows.append(
            ",".join(
                [
                    _csv_cell(str(volume_no)),
                    _csv_cell(str(start)),
                    _csv_cell(str(end)),
                    _csv_cell(status),
                    _csv_cell(goal or reader_promise),
                ]
            )
        )
    return rows


def _metadata_batch_queue_rows(metadata: dict, *, max_generated: int, reader_promise: str) -> list[str]:
    volume_plan = metadata.get("premium_volume_plan")
    if not isinstance(volume_plan, list) or not volume_plan:
        volume_plan = metadata.get("volume_plan")
    if not isinstance(volume_plan, list) or not volume_plan:
        if not max_generated:
            return [
                ",".join(
                    [_csv_cell("1"), _csv_cell("1"), _csv_cell("0"), _csv_cell(reader_promise), _csv_cell("empty")]
                )
            ]
        rows = []
        batch_size = 10
        for batch_no, start in enumerate(range(1, max_generated + 1, batch_size), start=1):
            end = min(start + batch_size - 1, max_generated)
            rows.append(
                ",".join(
                    [
                        _csv_cell(str(batch_no)),
                        _csv_cell(str(start)),
                        _csv_cell(str(end)),
                        _csv_cell(reader_promise),
                        _csv_cell("drafted"),
                    ]
                )
            )
        return rows

    rows: list[str] = []
    for index, item in enumerate(volume_plan, start=1):
        if not isinstance(item, dict):
            continue
        volume_no = int(item.get("volume_number") or index)
        chapter_range = _coerce_chapter_range(item.get("chapter_range")) or (
            (volume_no - 1) * 50 + 1,
            volume_no * 50,
        )
        start, end = chapter_range
        if max_generated >= end:
            status = "drafted"
        elif start <= max_generated <= end:
            status = "active"
        else:
            status = "planned"
        callback = _flatten_text(
            item.get("reader_hook_to_next")
            or item.get("foreshadowing_planted")
            or item.get("core_payoff")
            or item.get("volume_goal")
        )
        rows.append(
            ",".join(
                [
                    _csv_cell(f"{volume_no}A"),
                    _csv_cell(str(start)),
                    _csv_cell(str(end)),
                    _csv_cell(callback or reader_promise),
                    _csv_cell(status),
                ]
            )
        )
    return rows


def _write_commercial_package_sidecars(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    package_root: Path,
) -> None:
    """Write the lightweight story-bible files required by commercial gates.

    The database remains canonical; these files are a synchronized submission
    snapshot so reviewers and package-level gates do not inspect stale or
    missing planning material.
    """
    metadata = _metadata_dict(project)
    story_bible_dir = package_root / "story-bible"
    story_bible_dir.mkdir(parents=True, exist_ok=True)

    title = getattr(project, "title", "") or getattr(project, "slug", "")
    logline = _metadata_first_text(metadata, "logline", "premise", "synopsis")
    reader_promise = _metadata_first_text(metadata, "reader_promise")
    selling_points = _metadata_text_list(metadata, "selling_points", "tags", "trope_keywords")
    audiences = _metadata_text_list(metadata, "target_audiences")
    series_engine = metadata.get("series_engine") if isinstance(metadata.get("series_engine"), dict) else {}
    stakes = metadata.get("stakes") if isinstance(metadata.get("stakes"), dict) else {}

    series_lines = [
        f"# Series Brief — {title}",
        "",
        "## Logline",
        logline or "_(尚未生成)_",
        "",
        "## Reader Promise",
        reader_promise or "_(尚未生成)_",
        "",
        "## Core Loop",
        _flatten_text(series_engine.get("core_loop")) or "_(尚未生成)_",
        "",
        "## Stakes",
    ]
    if stakes:
        series_lines.extend(f"- {key}: {_flatten_text(value)}" for key, value in stakes.items())
    else:
        series_lines.append("_(尚未生成)_")
    (story_bible_dir / "series-brief.md").write_text("\n".join(series_lines).strip() + "\n", encoding="utf-8")

    desire_lines = [
        f"# Reader Desire Map — {title}",
        "",
        "## Promise",
        reader_promise or "_(尚未生成)_",
        "",
        "## Target Audiences",
    ]
    desire_lines.extend(f"- {item}" for item in audiences) if audiences else desire_lines.append("_(尚未生成)_")
    desire_lines.extend(["", "## Selling Points"])
    desire_lines.extend(f"- {item}" for item in selling_points) if selling_points else desire_lines.append("_(尚未生成)_")
    desire_lines.extend(["", "## Payoff Rhythm", _flatten_text(series_engine.get("payoff_rhythm")) or "_(尚未生成)_"])
    (story_bible_dir / "reader-desire-map.md").write_text("\n".join(desire_lines).strip() + "\n", encoding="utf-8")

    bible_lines = [
        f"# Series Bible — {title}",
        "",
        "## Premise",
        logline or "_(尚未生成)_",
        "",
        "## World / Power",
        _metadata_first_text(metadata, "world_premise", "world_spec", "power_system") or "_(尚未生成)_",
        "",
        "## Protagonist",
        _metadata_first_text(metadata, "protagonist", "protagonist_archetype", "golden_finger") or "_(尚未生成)_",
        "",
        "## Chapter Discipline",
        _flatten_text(series_engine.get("chapter_hook_strategy") or metadata.get("chapter_hook_strategy")) or "_(尚未生成)_",
    ]
    (story_bible_dir / "series-bible.md").write_text("\n".join(bible_lines).strip() + "\n", encoding="utf-8")

    ledger_lines = [
        f"# Continuity Ledger — {title}",
        "",
        "## Exported Current Drafts",
        "| Chapter | Title | Word Count | Draft Version |",
        "|---:|---|---:|---:|",
    ]
    for chapter, draft in chapter_payloads:
        ledger_lines.append(
            f"| {chapter.chapter_number} | {_flatten_text(chapter.title)} | "
            f"{draft.word_count} | {draft.version_no} |"
        )
    (story_bible_dir / "continuity-ledger.md").write_text("\n".join(ledger_lines).strip() + "\n", encoding="utf-8")

    target = max(int(getattr(project, "target_chapters", 0) or 0), len(chapter_payloads))
    generated = [int(ch.chapter_number) for ch, _ in chapter_payloads]
    max_generated = max(generated) if generated else 0
    volume_rows = ['"volume","start_chapter","end_chapter","status","goal"']
    volume_rows.extend(
        _metadata_volume_plan_rows(
            metadata,
            max_generated=max_generated,
            fallback_target=target,
            reader_promise=_flatten_text(series_engine.get("core_engine")) or reader_promise,
        )
    )
    (story_bible_dir / "volume-plan.csv").write_text("\n".join(volume_rows) + "\n", encoding="utf-8")

    batch_rows = ['"batch","start_chapter","end_chapter","required_callbacks","status"']
    batch_rows.extend(_metadata_batch_queue_rows(metadata, max_generated=max_generated, reader_promise=reader_promise))
    (story_bible_dir / "batch-queue.csv").write_text("\n".join(batch_rows) + "\n", encoding="utf-8")

    listing_dir = package_root / "listing"
    listing_dir.mkdir(parents=True, exist_ok=True)
    listing_metadata = {
        "book_id": getattr(project, "slug", ""),
        "primary_title": title,
        "genre": getattr(project, "genre", "") or "",
        "sub_genre": getattr(project, "sub_genre", "") or "",
        "logline": logline,
        # synopsis 是唯一简介真源(T7, 2026-07-09)：已过 blurb_pathology 病理检测器 +
        # blurb_copywriter 淘汰赛；promotional_brief.blurb 现在直接消费它(见
        # planner._resolve_promotional_brief_blurb)，三者同源，此处取值序不必再改。
        "short_intro": _metadata_first_text(metadata, "synopsis", "premise", "promotional_brief"),
        "reader_promise": [reader_promise] if reader_promise else [],
        "selling_points": selling_points,
        "target_audiences": audiences,
        "tags": _metadata_text_list(metadata, "tags", "trope_keywords"),
    }
    (listing_dir / "book-listing-metadata.json").write_text(
        json.dumps(listing_metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    listing_profile = build_book_listing_profile(
        project=SimpleNamespace(
            slug=getattr(project, "slug", ""),
            title=title,
            genre=getattr(project, "genre", "") or "",
            sub_genre=getattr(project, "sub_genre", "") or "",
            audience=getattr(project, "audience", "") or "",
            status=getattr(project, "status", "") or "writing",
            language=getattr(project, "language", "") or "zh-CN",
            metadata_json=listing_metadata,
        ),
        writing_profile=(
            metadata.get("writing_profile")
            if isinstance(metadata.get("writing_profile"), dict)
            else {}
        ),
        story_bible=None,
    )
    write_platform_title_workflow_artifacts(listing_profile, listing_dir)
    detail_lines = [
        f"# {title}",
        "",
        "## 读者承诺",
        reader_promise or "_(尚未生成)_",
        "",
        "## 简介",
        listing_metadata["short_intro"] or logline or "_(尚未生成)_",
        "",
        "## 卖点",
    ]
    detail_lines.extend(f"- {item}" for item in selling_points) if selling_points else detail_lines.append("_(尚未生成)_")
    (listing_dir / "book-detail-page.md").write_text(
        "\n".join(detail_lines).strip() + "\n",
        encoding="utf-8",
    )


def write_commercial_package_sidecars(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    package_root: Path,
) -> None:
    _write_commercial_package_sidecars(project, chapter_payloads, package_root)


def _sync_project_chapter_markdown_files(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    package_root: Path,
) -> None:
    """Mirror current DB drafts into per-chapter markdown files.

    Project exports historically wrote only ``project.md``.  That left stale
    ``chapter-XXX.md`` files in ``output/<slug>/`` after rewrites, which made
    package review disagree with the database.  Keep the chapter files as a
    synchronized cache of current drafts.
    """
    package_root.mkdir(parents=True, exist_ok=True)
    current_numbers: set[int] = set()
    for chapter, draft in chapter_payloads:
        chapter_number = int(chapter.chapter_number)
        current_numbers.add(chapter_number)
        path = package_root / f"chapter-{chapter_number:03d}.md"
        content_md = _ensure_chapter_heading(
            chapter,
            sanitize_novel_markdown_content(draft.content_md, language=project.language),
            language=project.language,
        )
        write_markdown_output(path, content_md)

    for path in package_root.glob("chapter-*.md"):
        match = re.fullmatch(r"chapter-(\d{3})\.md", path.name)
        if match is None:
            continue
        if int(match.group(1)) not in current_numbers:
            path.unlink()


def _parse_markdown_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if stripped.startswith("### "):
        return "h3", stripped[4:].strip()
    if stripped.startswith("# "):
        return "h1", stripped[2:].strip()
    if stripped.startswith("## "):
        return "h2", stripped[3:].strip()
    if stripped.startswith("> "):
        return "quote", stripped[2:].strip()
    if stripped.startswith("- "):
        return "li", stripped[2:].strip()
    return "p", stripped


# Inline markdown pattern for **bold** and *italic*
_INLINE_MARK = re.compile(r"(\*\*.+?\*\*|\*.+?\*)")


def _render_inline_runs(text: str) -> str:
    """Split a line into OOXML runs, handling **bold** and *italic*.

    Every segment (including plain text) is wrapped in <w:r><w:t> so the
    output is valid OOXML. Escapes HTML entities in each segment.
    """
    runs: list[str] = []
    for seg in _INLINE_MARK.split(text):
        if not seg:
            continue
        if seg.startswith("**") and seg.endswith("**") and len(seg) > 4:
            runs.append(
                f"<w:r><w:rPr><w:b/></w:rPr>"
                f"<w:t xml:space=\"preserve\">{escape(seg[2:-2])}</w:t></w:r>"
            )
        elif seg.startswith("*") and seg.endswith("*") and len(seg) > 2:
            runs.append(
                f"<w:r><w:rPr><w:i/></w:rPr>"
                f"<w:t xml:space=\"preserve\">{escape(seg[1:-1])}</w:t></w:r>"
            )
        else:
            runs.append(
                f"<w:r><w:t xml:space=\"preserve\">{escape(seg)}</w:t></w:r>"
            )
    return "".join(runs)


def markdown_to_plain_text(content_md: str) -> str:
    cleaned = str(content_md or "").replace("\ufeff", "")
    cleaned = re.sub(r"\A\s*---\s*\n.*?\n---\s*(?:\n|$)", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"!\[[^\]]*]\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", cleaned)
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        if not raw_line.strip():
            lines.append("")
            continue
        _, text = _parse_markdown_line(raw_line)
        lines.append(text)
    return "\n".join(lines).strip()


def markdown_to_html(content_md: str, *, language: str | None = None) -> str:
    rendered = markdown_lib.markdown(
        sanitize_novel_markdown_content(content_md, language=language),
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
    word_count = count_words(plain_text)
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
            "h3": "Heading2",  # h3 maps to Heading2 since we only define 2 levels
            "quote": "Quote",
            "li": "ListParagraph",
        }.get(block_type)
        style_xml = (
            f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style is not None else ""
        )
        # Insert page break before h1 (chapter heading), except the first
        if block_type == "h1" and paragraph_xml:
            paragraph_xml.append(
                "<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>"
            )
        # Use inline run renderer for bold/italic support
        runs_xml = _render_inline_runs(text)
        paragraph_xml.append(
            f"<w:p>{style_xml}{runs_xml}</w:p>"
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
    identifier: str | None = None,
) -> bytes:
    """Build an EPUB3 from markdown content.

    Splits content into per-chapter XHTML files when ``# `` headings are
    detected, so readers can navigate by chapter. Falls back to a single
    file when no chapter headings are found.
    """
    from uuid import uuid4

    if identifier is None:
        identifier = f"bestseller-{uuid4().hex[:12]}"

    nav_title = "Table of Contents" if language.lower().startswith("en") else "目录"
    escaped_author = escape(author) if author else None

    # Split content into chapters by h1 headings
    chapter_splits: list[tuple[str, str]] = []
    current_heading = title
    current_lines: list[str] = []
    for raw_line in content_md.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("# "):
            if current_lines:
                chapter_splits.append((current_heading, "\n".join(current_lines)))
            current_heading = stripped[2:].strip()
            current_lines = [raw_line]
        else:
            current_lines.append(raw_line)
    if current_lines:
        chapter_splits.append((current_heading, "\n".join(current_lines)))

    # If only one chunk and it has no h1, use the title as heading
    if len(chapter_splits) == 1 and chapter_splits[0][0] == title:
        chapter_splits = [(title, content_md)]

    # Build per-chapter XHTML
    chapter_files: list[tuple[str, str, str]] = []  # (filename, heading, xhtml)
    nav_items: list[tuple[str, str]] = []
    manifest_items: list[str] = []
    spine_items: list[str] = []

    for idx, (heading, md_content) in enumerate(chapter_splits):
        filename = f"chapter-{idx + 1:04d}.xhtml"
        html_body = markdown_to_html(md_content, language=language)
        xhtml = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            f"<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"{escape(language)}\">"
            f"<head><title>{escape(heading)}</title><meta charset=\"utf-8\"/></head>"
            f"<body>{html_body}</body></html>"
        )
        chapter_files.append((filename, heading, xhtml))
        nav_items.append((filename, heading))
        item_id = f"ch{idx + 1:04d}"
        manifest_items.append(
            f"<item id=\"{item_id}\" href=\"{filename}\" media-type=\"application/xhtml+xml\"/>"
        )
        spine_items.append(f"<itemref idref=\"{item_id}\"/>")

    # Build nav
    nav_entries = "\n".join(
        f"<li><a href=\"{fn}\">{escape(h)}</a></li>" for fn, h in nav_items
    )
    nav_xhtml = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        f"<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"{escape(language)}\">"
        f"<head><title>{escape(title)} {escape(nav_title)}</title></head>"
        "<body><nav epub:type=\"toc\" id=\"toc\">"
        f"<h1>{escape(nav_title)}</h1><ol>{nav_entries}</ol>"
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
        + "".join(manifest_items)
        + "</manifest>"
        "<spine>" + "".join(spine_items) + "</spine>"
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
        for filename, _heading, xhtml in chapter_files:
            archive.writestr(f"OEBPS/{filename}", xhtml, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", content_opf, compress_type=ZIP_DEFLATED)
    return buffer.getvalue()


def build_pdf_bytes(title: str, content_md: str, *, language: str = "zh-CN") -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.pdfmetrics import registerFont
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PDF export requires reportlab. Install optional dependencies with bestseller[export]."
        ) from exc

    is_en = language.lower().startswith("en")
    # Helvetica is a reportlab built-in Type1 font; STSong-Light is a CID font.
    if is_en:
        base_font = "Helvetica"
    else:
        registerFont(UnicodeCIDFont("STSong-Light"))
        base_font = "STSong-Light"

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BestsellerTitle",
        parent=styles["Title"],
        fontName=base_font,
        fontSize=18,
        leading=24,
    )
    heading_style = ParagraphStyle(
        "BestsellerHeading",
        parent=styles["Heading2"],
        fontName=base_font,
        fontSize=14,
        leading=18,
    )
    body_style = ParagraphStyle(
        "BestsellerBody",
        parent=styles["BodyText"],
        fontName=base_font,
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

    # Convert markdown to HTML so reportlab Paragraph can parse inline tags
    # (<b>, <i>, <strong>, <em>) instead of rendering **bold** literally.
    html_body = markdown_lib.markdown(
        content_md,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )

    story: list = [Paragraph(title, title_style), Spacer(1, 8)]
    for raw_line in html_body.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            story.append(Spacer(1, 6))
            continue
        if stripped.startswith("<h1>"):
            heading_text = unescape(re.sub(r"<[^>]+>", "", stripped)).strip()
            if heading_text == title.strip():
                continue
            story.append(PageBreak())
            story.append(Paragraph(stripped, title_style))
        elif stripped.startswith("<h2>") or stripped.startswith("<h3>"):
            story.append(Paragraph(stripped, heading_style))
        elif stripped.startswith("<blockquote>"):
            story.append(Paragraph(stripped, quote_style))
        elif stripped.startswith("<p>") or stripped.startswith("<li>"):
            story.append(Paragraph(stripped, body_style))
        else:
            story.append(Paragraph(stripped, body_style))
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
            ChapterDraftVersionModel.promotion_state == DraftPromotionState.PROMOTED.value,
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
) -> tuple[ProjectModel, list[tuple[ChapterModel, ChapterDraftVersionModel]], list[int]]:
    """Load project + promoted chapter drafts.

    Publication exports are all-or-nothing. A chapter without a promoted draft
    raises :class:`ProjectExportIncompleteError`; partial diagnostic material is
    available only through the explicit closure-draft loader below.
    """
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
    skipped: list[int] = []
    for chapter in chapters:
        draft = await session.scalar(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.promotion_state == DraftPromotionState.PROMOTED.value,
            )
        )
        if draft is None:
            skipped.append(chapter.chapter_number)
            continue
        chapter_payloads.append((chapter, draft))

    if skipped:
        raise ProjectExportIncompleteError(project_slug, skipped)
    if not chapter_payloads:
        raise ValueError(
            f"Project '{project_slug}' does not have any current chapter drafts to export."
        )
    return project, chapter_payloads, skipped


async def _load_project_closure_draft_payload(
    session: AsyncSession,
    project_slug: str,
) -> tuple[ProjectModel, list[tuple[ChapterModel, ChapterDraftVersionModel]]]:
    """Load every chapter's current draft for an explicitly non-publication artifact."""

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

    payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] = []
    missing: list[int] = []
    for chapter in chapters:
        draft = await session.scalar(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        if draft is None:
            missing.append(int(chapter.chapter_number))
        else:
            payloads.append((chapter, draft))
    if missing:
        raise ValueError(
            f"Project '{project_slug}' is not draft-complete; missing current drafts for "
            f"chapters {missing}."
        )
    return project, payloads


def _closure_draft_blockers(
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    *,
    language: str | None,
) -> list[str]:
    """Block only corrupt/incomplete artifacts; quality debt remains a warning."""

    blockers: list[str] = []
    for chapter, draft in chapter_payloads:
        number = int(chapter.chapter_number)
        if not list(getattr(draft, "assembled_from_scene_draft_ids", None) or []):
            blockers.append(f"第{number}章当前稿缺少场景来源记录")
        for issue in collect_unfinished_artifact_issues(
            draft.content_md or "", language=language
        ):
            blockers.append(f"第{number}章：{issue}")
    return blockers


async def load_project_export_content(
    session: AsyncSession,
    project_slug: str,
) -> tuple[ProjectModel, str]:
    project, chapter_payloads, _skipped = await _load_project_export_payload(session, project_slug)
    return project, build_project_markdown(project, chapter_payloads)


def _build_project_export_warnings(
    *,
    skipped_chapters: list[int],
    preflight_warnings: list[str],
    language: str | None,
) -> list[str]:
    warnings: list[str] = []
    if skipped_chapters:
        numbers = ", ".join(str(number) for number in skipped_chapters)
        if normalize_language(language).lower().startswith("en"):
            warnings.append(f"Chapters {numbers} have no current draft and were skipped")
        else:
            warnings.append(f"第 {numbers.replace(', ', '、')} 章无当前稿件，已跳过")
    warnings.extend(str(item) for item in preflight_warnings if str(item).strip())
    return warnings


def _attach_project_export_warnings(
    artifact: ExportArtifactModel,
    *,
    skipped_chapters: list[int],
    preflight_warnings: list[str],
    language: str | None,
    word_count: int,
) -> None:
    metadata = dict(getattr(artifact, "metadata_json", None) or {})
    metadata.update(
        {
            "warnings": _build_project_export_warnings(
                skipped_chapters=skipped_chapters,
                preflight_warnings=preflight_warnings,
                language=language,
            ),
            "skipped_chapters": list(skipped_chapters),
            "word_count": word_count,
        }
    )
    artifact.metadata_json = metadata


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
    metadata_json: dict | None = None,
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
        metadata_json=metadata_json or {},
    )


async def load_publication_comparison_payloads(
    session: AsyncSession,
    project_id: UUID,
    *,
    through_chapter_number: int | None = None,
) -> list[tuple[ChapterModel, ChapterDraftVersionModel]]:
    """Load promoted chapter drafts used by publication/export safety gates."""
    stmt = (
        select(ChapterModel, ChapterDraftVersionModel)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project_id,
            ChapterDraftVersionModel.promotion_state == DraftPromotionState.PROMOTED.value,
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    if through_chapter_number is not None:
        stmt = stmt.where(ChapterModel.chapter_number <= through_chapter_number)
    result = await session.execute(stmt)
    return list(result.all())


def collect_publication_blockers(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    *,
    comparison_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] | None = None,
) -> list[str]:
    language = getattr(project, "language", None)
    is_en = normalize_language(language).lower().startswith("en")
    blockers: list[str] = []
    target_chapter_numbers = {
        int(chapter.chapter_number)
        for chapter, _draft in chapter_payloads
        if getattr(chapter, "chapter_number", None) is not None
    }
    comparison_for_quality = comparison_payloads if comparison_payloads is not None else chapter_payloads
    all_quality_texts = [
        (int(chapter.chapter_number), draft.content_md or "")
        for chapter, draft in comparison_for_quality
        if getattr(chapter, "chapter_number", None) is not None
    ]
    all_quality_texts.sort(key=lambda item: item[0])

    for chapter, draft in chapter_payloads:
        chapter_number = int(chapter.chapter_number)
        status = (getattr(chapter, "status", "") or "").lower()
        production_state = (getattr(chapter, "production_state", "") or "").lower()
        status_publishable = status == "complete" or (
            status == "revision" and production_state == "ok"
        )
        if not status_publishable:
            blockers.append(
                (
                    f"Chapter {chapter_number}: status is {status or 'unset'}, not publishable"
                    if is_en else f"第{chapter_number}章：状态为{status or '未设置'}，不是可发布状态，禁止发布"
                )
            )
        if production_state != "ok":
            blockers.append(
                (
                    f"Chapter {chapter_number}: production_state is {production_state or 'unset'}, not ok"
                    if is_en else f"第{chapter_number}章：门禁状态为{production_state or '未设置'}，不是 ok，禁止发布"
                )
            )
        if not list(getattr(draft, "assembled_from_scene_draft_ids", None) or []):
            blockers.append(
                (
                    f"Chapter {chapter_number}: current draft has no scene provenance, export blocked"
                    if is_en else f"第{chapter_number}章：当前稿缺少场景来源记录，禁止发布"
                )
            )
        if not is_en:
            try:
                from bestseller.services.chapter_quality_bundle import (
                    ChapterQualityBundleContext,
                    run_chapter_quality_bundle,
                )
                from bestseller.settings import get_settings as _get_settings

                _cfg = _get_settings()
                chapter_meta = (
                    chapter.metadata_json
                    if isinstance(chapter.metadata_json, dict)
                    else {}
                )
                has_quality_snapshot = isinstance(chapter_meta.get("quality_bundle"), dict)
                commercial_quality_required = (
                    bool(_cfg.pipeline.commercial_strict_quality_mode)
                    and int(getattr(project, "target_chapters", 0) or 0)
                    >= int(_cfg.pipeline.commercial_planning_min_target_chapters)
                )
                if has_quality_snapshot or commercial_quality_required:
                    prior_texts = tuple(
                        (number, text)
                        for number, text in all_quality_texts
                        if number < chapter_number
                    )
                    previous_number = prior_texts[-1][0] if prior_texts else None
                    previous_text = prior_texts[-1][1] if prior_texts else None
                    quality_report = run_chapter_quality_bundle(
                        draft.content_md or "",
                        ChapterQualityBundleContext(
                            chapter_number=chapter_number,
                            previous_chapter_text=previous_text,
                            previous_chapter_position=previous_number,
                            previous_chapter_texts=prior_texts,
                            total_chapters=int(getattr(project, "target_chapters", 0) or 500),
                            language=language or "zh-CN",
                            target_chapter_words=int(_cfg.generation.words_per_chapter.target),
                            commercial_strict=True,
                            hook_domain_tokens=_bundle_hook_domain_tokens(project),
                        ),
                    )
                    if quality_report.blocking_findings:
                        codes = ", ".join(quality_report.to_dict()["blocking_codes"])
                        blockers.append(
                            f"第{chapter_number}章：统一质量快照未通过（{codes}），禁止发布"
                        )
            except Exception:
                blockers.append(
                    f"第{chapter_number}章：统一质量快照执行失败，严格商业模式禁止发布"
                )
        # Word-count deviations are logged as warnings but never block export.
        target = max(int(chapter.target_word_count or 0), 0)
        if target > 0:
            ratio = draft.word_count / max(target, 1)
            if ratio < 0.7 or ratio > 1.3:
                logger.warning(
                    "Chapter %d word count deviates from target: %d vs %d (%.0f%%).",
                    chapter.chapter_number,
                    draft.word_count,
                    target,
                    ratio * 100,
                )
        # Export is the final defense for historical chapters stamped ok
        # before the commercial chapter-length floor existed.
        try:
            from bestseller.services.drafts import count_words as _count_words
            from bestseller.services.length_stability_gate import (
                CHINESE_CHAPTER_HARD_MAX_WORDS,
                CHINESE_CHAPTER_HARD_MIN_WORDS,
                evaluate_chapter_length,
            )
            from bestseller.settings import get_settings as _get_settings
            _cfg = _get_settings()
            _budget = _cfg.generation.words_per_chapter
            _wc = _count_words(draft.content_md or "")
            _min_words = int(_budget.min)
            _target_words = int(_budget.target)
            _max_words = int(_budget.max)
            if not is_en:
                # Export should enforce the commercial hard range for Chinese
                # chapters, not turn the framework's softer default target
                # window into a publish blocker for otherwise valid legacy
                # drafts.
                _max_words = max(_max_words, CHINESE_CHAPTER_HARD_MAX_WORDS)
            _length_report = evaluate_chapter_length(
                word_count=_wc,
                min_words=_min_words,
                target_words=_target_words,
                max_words=_max_words,
                warn_margin=float(
                    getattr(
                        getattr(_cfg, "pipeline", object()),
                        "length_stability_warn_margin",
                        0.10,
                    )
                ),
                hard_min_words=None if is_en else CHINESE_CHAPTER_HARD_MIN_WORDS,
                hard_max_words=None if is_en else CHINESE_CHAPTER_HARD_MAX_WORDS,
                enabled=True,
            )
            if not is_en and _wc < CHINESE_CHAPTER_HARD_MIN_WORDS:
                blockers.append(
                    (
                        f"Chapter {chapter_number}: chapter length {_wc} below commercial floor "
                        f"{CHINESE_CHAPTER_HARD_MIN_WORDS}, export blocked"
                        if is_en
                        else f"第{chapter_number}章：章节体量 {_wc} 字低于商业硬底线 "
                        f"{CHINESE_CHAPTER_HARD_MIN_WORDS} 字，禁止发布"
                )
            )
            elif _length_report.is_warning or _length_report.is_blocking:
                if _length_report.is_blocking and not is_en:
                    blockers.append(
                        (
                            f"Chapter {chapter_number}: chapter length {_wc} outside commercial "
                            f"window {CHINESE_CHAPTER_HARD_MIN_WORDS}-{CHINESE_CHAPTER_HARD_MAX_WORDS}, "
                            "export blocked"
                            if is_en
                            else f"第{chapter_number}章：章节体量 {_wc} 字超出商业硬范围 "
                            f"{CHINESE_CHAPTER_HARD_MIN_WORDS}-{CHINESE_CHAPTER_HARD_MAX_WORDS} 字，禁止发布"
                        )
                    )
                    continue
                logger.warning(
                    "Chapter %d length-stability warning at export: %s wc=%d "
                    "min=%d target=%d max=%d.",
                    chapter.chapter_number,
                    _length_report.band.value,
                    _wc,
                    _length_report.min_words,
                    _length_report.target_words,
                    _length_report.max_words,
                )
        except Exception as e:
            logger.warning("Length stability check failed for chapter %s (non-fatal): %s", chapter.chapter_number, e)
        hygiene_issues = collect_unfinished_artifact_issues(draft.content_md, language=language)
        for issue in hygiene_issues:
            blockers.append(
                (
                    f"Chapter {chapter.chapter_number}: {issue}"
                    if is_en else f"第{chapter.chapter_number}章：{issue}"
                )
            )
        try:
            from bestseller.services.common_sense_gate import evaluate_common_sense_gate

            common_sense = evaluate_common_sense_gate(
                draft.content_md or "",
                genre=getattr(project, "genre", None),
                sub_genre=getattr(project, "sub_genre", None),
                chapter_number=chapter_number,
            )
            # Codes an LLM adjudicator already dismissed as false positives at
            # review time (context made the flagged phenomenon legitimate). The
            # export gate re-runs the same blind regex, so honor that ruling
            # instead of re-litigating prose causality without context.
            _adjudicated_clear = set(
                (getattr(chapter, "metadata_json", None) or {}).get(
                    "common_sense_dismissed_codes"
                )
                or []
            )
            for finding in common_sense.findings[:5]:
                if finding.severity not in {"high", "medium"}:
                    continue
                if finding.code in _adjudicated_clear:
                    continue
                blockers.append(
                    (
                        f"Chapter {chapter_number}: common-sense gate {finding.code}: {finding.message}"
                        if is_en
                        else f"第{chapter_number}章：常识因果门禁 {finding.code}：{finding.message}"
                    )
                )
        except Exception as e:
            logger.warning("Publication gate: common-sense check failed for chapter %s (non-fatal): %s", chapter_number, e)
        try:
            from bestseller.services.deduplication import (
                detect_chapter_text_loop,
                detect_intra_chapter_repetition,
                detect_short_cluster_near_repeat,
            )

            local_findings = (
                detect_chapter_text_loop(draft.content_md or "")
                + detect_short_cluster_near_repeat(draft.content_md or "")
                + detect_intra_chapter_repetition(draft.content_md or "")
            )
            for finding in local_findings[:5]:
                message = str(finding.get("message") or "duplicate content")
                blockers.append(
                    (
                        f"Chapter {chapter_number}: {message}"
                        if is_en else f"第{chapter_number}章：{message}"
                    )
                )
            if len(local_findings) > 5:
                blockers.append(
                    (
                        f"Chapter {chapter_number}: {len(local_findings) - 5} more duplicate findings"
                        if is_en else f"第{chapter_number}章：另有{len(local_findings) - 5}条重复问题"
                    )
                )
        except Exception as e:
            logger.warning("Publication gate: local duplicate check failed for chapter %s (non-fatal): %s", chapter_number, e)

    try:
        from bestseller.services.chapter_batch_quality_gate import (
            evaluate_chapter_batch_quality,
        )

        batch_report = evaluate_chapter_batch_quality(all_quality_texts)
        cross_findings = (
            [finding.to_dict() for finding in batch_report.findings]
            if batch_report is not None
            else []
        )
        emitted = 0
        for finding in cross_findings:
            finding_chapter = int(
                finding.get("chapter")
                or finding.get("chapter_number")
                or 0
            )
            if finding_chapter not in target_chapter_numbers:
                continue
            message = str(finding.get("message") or "batch quality regression")
            code = str(finding.get("code") or "")
            if code == "CROSS_CHAPTER_REPETITION" and "跨章段落重复" not in message:
                message = f"跨章段落重复/整体重复：{message}"
            blockers.append(message if not is_en else f"Chapter {finding_chapter}: {message}")
            emitted += 1
            if emitted >= 10:
                break
        remaining = len([
            finding for finding in cross_findings
            if int(finding.get("chapter") or finding.get("chapter_number") or 0)
            in target_chapter_numbers
        ]) - emitted
        if remaining > 0:
            blockers.append(
                (
                    f"{remaining} more cross-chapter duplicate finding(s)"
                    if is_en else f"另有{remaining}条跨章重复问题"
                )
            )
    except Exception as e:
        logger.warning("Publication gate: cross-chapter duplicate check failed (non-fatal): %s", e)

    return blockers


def _raise_if_export_blocked(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    *,
    comparison_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] | None = None,
) -> None:
    blockers = collect_publication_blockers(
        project,
        chapter_payloads,
        comparison_payloads=comparison_payloads,
    )
    if not blockers:
        return
    raise ValueError("; ".join(blockers))


def _run_terminal_export_gate(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
    *,
    settings: AppSettings | None = None,
) -> None:
    """Re-check exact export bytes for direct (non-pipeline) exports."""
    # Imported lazily because pipelines imports this module for its exporter.
    from bestseller.services.pipelines import run_final_quality_gates

    for chapter, draft in chapter_payloads:
        result = run_final_quality_gates(
            chapter_number=int(chapter.chapter_number),
            content_md=draft.content_md or "",
            project=project,
            settings=settings,
        )
        if result.patched_text is not None:
            draft.content_md = result.patched_text
        if not result.passed:
            details = "; ".join([*result.errors, *result.issues])
            raise ValueError(
                f"final_quality_gate_blocked chapter {chapter.chapter_number}: {details}"
            )
        chapter.metadata_json = {
            **(chapter.metadata_json or {}),
            "terminal_quality_gate_content_hash": hashlib.sha256(
                (draft.content_md or "").encode("utf-8")
            ).hexdigest(),
        }


async def preflight_export_check(
    session: AsyncSession,
    project_id: UUID,
    *,
    language: str | None = None,
) -> list[str]:
    """Run pre-export quality checks. Returns warning messages (empty = all clear)."""
    _is_en = (language or "").lower().startswith("en")
    warnings: list[str] = []

    try:
        # 1. Check for incomplete chapters (missing promoted drafts)
        chapters = (await session.scalars(
            select(ChapterModel).where(ChapterModel.project_id == project_id)
        )).all()
        for ch in chapters:
            draft = await session.scalar(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id == ch.id,
                    ChapterDraftVersionModel.promotion_state == DraftPromotionState.PROMOTED.value,
                )
            )
            if draft is None:
                warnings.append(
                    f"Chapter {ch.chapter_number} is missing a promoted draft" if _is_en
                    else f"第{ch.chapter_number}章缺少已晋升草稿"
                )
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
            warnings.append(
                f"{len(planted_clues)} clue(s) remain unresolved" if _is_en
                else f"有{len(planted_clues)}条伏笔尚未回收"
            )
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
            warnings.append(
                f"{len(open_arcs)} narrative arc(s) remain unfinished" if _is_en
                else f"有{len(open_arcs)}条叙事弧尚未完结"
            )
    except Exception:
        logger.debug("Preflight check: arc completeness check failed", exc_info=True)

    return warnings


def _is_mode_b_project(project) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    return bool(metadata.get("mode_b"))


async def _resolve_chapter_export_path(
    session: AsyncSession,
    settings: AppSettings,
    project,
    chapter,
) -> Path:
    """Resolve the markdown output path, honoring the Mode B layout.

    Production projects export to ``output/{slug}/chapter-NNN.md`` (flat).
    Mode B projects (``metadata_json.mode_b`` true) export to the
    ``output/ai-generated/{slug}/volumes/vol-NN/ch-NNN.md`` layout the
    dialogue orchestrator expects, so gates/readers find the file.
    """

    base = Path(settings.output.base_dir)
    if not _is_mode_b_project(project):
        return base / project.slug / f"chapter-{chapter.chapter_number:03d}.md"

    volume_number = 1
    volume_id = getattr(chapter, "volume_id", None)
    if volume_id is not None:
        from bestseller.infra.db.models import VolumeModel

        vol = await session.scalar(
            select(VolumeModel).where(VolumeModel.id == volume_id)
        )
        if vol is not None:
            volume_number = int(getattr(vol, "volume_number", 1) or 1)
    return (
        base
        / "ai-generated"
        / project.slug
        / "volumes"
        / f"vol-{volume_number:02d}"
        / f"ch-{chapter.chapter_number:03d}.md"
    )


async def export_chapter_markdown(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter, draft = await _load_chapter_export_payload(session, project_slug, chapter_number)
    comparison_payloads = await load_publication_comparison_payloads(
        session,
        project.id,
        through_chapter_number=chapter.chapter_number,
    )
    _raise_if_export_blocked(
        project,
        [(chapter, draft)],
        comparison_payloads=comparison_payloads,
    )
    _run_terminal_export_gate(project, [(chapter, draft)], settings=settings)
    output_path = await _resolve_chapter_export_path(
        session, settings, project, chapter
    )
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
    final_quality_gate: Callable[..., object] | None = None,
) -> tuple[ExportArtifactModel, Path]:
    project, chapter_payloads, skipped = await _load_project_export_payload(session, project_slug)
    _raise_if_export_blocked(project, chapter_payloads)
    if final_quality_gate is not None:
        gate_failures: list[str] = []
        for chapter, draft in chapter_payloads:
            result = final_quality_gate(
                chapter_number=int(chapter.chapter_number),
                content_md=draft.content_md or "",
                project=project,
                settings=settings,
            )
            if getattr(result, "patched_text", None) is not None:
                # The callback may perform a localized patch. Persisting that
                # exact text here keeps the export bytes and gate bytes equal.
                draft.content_md = result.patched_text
            if not bool(getattr(result, "passed", False)):
                details = [
                    *list(getattr(result, "errors", ()) or ()),
                    *list(getattr(result, "issues", ()) or ()),
                ]
                gate_failures.append(
                    f"Chapter {chapter.chapter_number}: "
                    + ("; ".join(details) or "final quality gate failed")
                )
        if gate_failures:
            raise ValueError("Final quality gate blocked export: " + " | ".join(gate_failures))
    else:
        _run_terminal_export_gate(project, chapter_payloads, settings=settings)
    preflight_warnings = await preflight_export_check(session, project.id, language=project.language)
    if preflight_warnings:
        logger.warning("Export pre-flight warnings for %s: %s", project_slug, "; ".join(preflight_warnings))
    content_md = build_project_markdown(project, chapter_payloads)
    package_root = Path(settings.output.base_dir) / project.slug
    output_path = package_root / "project.md"
    storage_uri, checksum = write_markdown_output(output_path, content_md)
    _sync_project_chapter_markdown_files(project, chapter_payloads, package_root)
    _write_commercial_package_sidecars(project, chapter_payloads, package_root)
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
    _attach_project_export_warnings(
        artifact,
        skipped_chapters=skipped,
        preflight_warnings=preflight_warnings,
        language=project.language,
        word_count=build_markdown_reading_stats(content_md)["word_count"],
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path


async def export_project_closure_draft_markdown(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    created_by_run_id: UUID | None = None,
) -> tuple[ExportArtifactModel, Path]:
    """Export a complete readable manuscript without claiming publication readiness.

    Strict ``export_project_markdown`` remains promoted-only. This separate
    artifact is the honest closure path for a full manuscript whose bounded
    rewrites ended with accepted quality debt.
    """

    project, chapter_payloads = await _load_project_closure_draft_payload(
        session, project_slug
    )
    blockers = _closure_draft_blockers(chapter_payloads, language=project.language)
    if blockers:
        raise ValueError("; ".join(blockers))

    quality_debt_chapters = [
        int(chapter.chapter_number)
        for chapter, _draft in chapter_payloads
        if (
            bool((chapter.metadata_json or {}).get("chapter_quality_debt"))
            or str(chapter.production_state or "").lower() != "ok"
        )
    ]
    manuscript = build_project_markdown(project, chapter_payloads)
    disclaimer = (
        "> **状态：整书草稿已闭环，但未通过严格出版门禁。** 该文件包含已接受的质量债，"
        "仅用于通读、编辑和后续定稿，不代表可直接发布。\n\n"
    )
    content_md = disclaimer + manuscript
    package_root = Path(settings.output.base_dir) / project.slug
    output_path = package_root / "project-draft-with-quality-debt.md"
    storage_uri, checksum = write_markdown_output(output_path, content_md)
    artifact = create_export_artifact(
        project_id=project.id,
        export_type="markdown_draft",
        source_scope="project",
        source_id=project.id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label="project-draft-quality-debt",
        created_by_run_id=created_by_run_id,
        metadata_json={
            "closure_state": "draft_complete_with_quality_debt",
            "publication_ready": False,
            "chapter_count": len(chapter_payloads),
            "quality_debt_chapters": quality_debt_chapters,
            "warnings": [
                "整书草稿已闭环，但未通过严格出版门禁",
                f"含质量债章节：{quality_debt_chapters}",
            ],
        },
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
    comparison_payloads = await load_publication_comparison_payloads(
        session,
        project.id,
        through_chapter_number=chapter.chapter_number,
    )
    _raise_if_export_blocked(
        project,
        [(chapter, draft)],
        comparison_payloads=comparison_payloads,
    )
    _run_terminal_export_gate(project, [(chapter, draft)], settings=settings)
    title = format_chapter_heading(chapter.chapter_number, chapter.title, language=project.language).lstrip("# ").strip()
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.docx"
    clean_content = _prepare_chapter_content(chapter, draft, language=project.language)
    storage_uri, checksum = write_binary_output(output_path, build_docx_bytes(title, clean_content))
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
    project, chapter_payloads, skipped = await _load_project_export_payload(session, project_slug)
    _raise_if_export_blocked(project, chapter_payloads)
    _run_terminal_export_gate(project, chapter_payloads, settings=settings)
    preflight_warnings = await preflight_export_check(session, project.id, language=project.language)
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
    _attach_project_export_warnings(
        artifact,
        skipped_chapters=skipped,
        preflight_warnings=preflight_warnings,
        language=project.language,
        word_count=build_markdown_reading_stats(content_md)["word_count"],
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
    comparison_payloads = await load_publication_comparison_payloads(
        session,
        project.id,
        through_chapter_number=chapter.chapter_number,
    )
    _raise_if_export_blocked(
        project,
        [(chapter, draft)],
        comparison_payloads=comparison_payloads,
    )
    _run_terminal_export_gate(project, [(chapter, draft)], settings=settings)
    title = format_chapter_heading(chapter.chapter_number, chapter.title, language=project.language).lstrip("# ").strip()
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.epub"
    clean_content = _prepare_chapter_content(chapter, draft, language=project.language)
    storage_uri, checksum = write_binary_output(
        output_path,
        build_epub_bytes(title, clean_content, language=project.language),
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
    project, chapter_payloads, skipped = await _load_project_export_payload(session, project_slug)
    _raise_if_export_blocked(project, chapter_payloads)
    _run_terminal_export_gate(project, chapter_payloads, settings=settings)
    preflight_warnings = await preflight_export_check(session, project.id, language=project.language)
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
    _attach_project_export_warnings(
        artifact,
        skipped_chapters=skipped,
        preflight_warnings=preflight_warnings,
        language=project.language,
        word_count=build_markdown_reading_stats(content_md)["word_count"],
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
    comparison_payloads = await load_publication_comparison_payloads(
        session,
        project.id,
        through_chapter_number=chapter.chapter_number,
    )
    _raise_if_export_blocked(
        project,
        [(chapter, draft)],
        comparison_payloads=comparison_payloads,
    )
    _run_terminal_export_gate(project, [(chapter, draft)], settings=settings)
    title = format_chapter_heading(chapter.chapter_number, chapter.title, language=project.language).lstrip("# ").strip()
    output_path = Path(settings.output.base_dir) / project.slug / f"chapter-{chapter.chapter_number:03d}.pdf"
    clean_content = _prepare_chapter_content(chapter, draft, language=project.language)
    storage_uri, checksum = write_binary_output(output_path, build_pdf_bytes(title, clean_content, language=project.language or "zh-CN"))
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
    project, chapter_payloads, skipped = await _load_project_export_payload(session, project_slug)
    _raise_if_export_blocked(project, chapter_payloads)
    _run_terminal_export_gate(project, chapter_payloads, settings=settings)
    preflight_warnings = await preflight_export_check(session, project.id, language=project.language)
    if preflight_warnings:
        logger.warning("Export pre-flight warnings for %s: %s", project_slug, "; ".join(preflight_warnings))
    content_md = build_project_markdown(project, chapter_payloads)
    output_path = Path(settings.output.base_dir) / project.slug / "project.pdf"
    storage_uri, checksum = write_binary_output(output_path, build_pdf_bytes(project.title, content_md, language=project.language or "zh-CN"))
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
    _attach_project_export_warnings(
        artifact,
        skipped_chapters=skipped,
        preflight_warnings=preflight_warnings,
        language=project.language,
        word_count=build_markdown_reading_stats(content_md)["word_count"],
    )
    session.add(artifact)
    await session.flush()
    return artifact, output_path
