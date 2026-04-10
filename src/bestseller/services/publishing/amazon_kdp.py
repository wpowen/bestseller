from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.project import AmazonKdpPublicationProfile
from bestseller.infra.db.models import ExportArtifactModel, ProjectModel
from bestseller.services.exports import (
    build_docx_bytes,
    build_epub_bytes,
    build_markdown_reading_stats,
    create_export_artifact,
    load_project_export_content,
    write_binary_output,
    write_markdown_output,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings

_PUBLISHING_KEY = "publishing"
_AMAZON_KDP_KEY = "amazon_kdp"
_BONUS_CONTENT_PATTERN = re.compile(
    r"(?im)^(?:#+\s*)?(bonus chapter|bonus content|reading group guide|discussion questions)\b"
)
_EXTERNAL_CTA_PATTERN = re.compile(
    r"(?i)\b(patreon|discord|newsletter|subscribe|buy on|free gift|exclusive bonus|review us)\b"
)
_URL_PATTERN = re.compile(r"https?://\S+")


class AmazonKdpValidationFinding(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    blocking: bool = True
    fix_hint: str | None = None


class AmazonKdpValidationReport(BaseModel):
    project_slug: str = Field(min_length=1)
    format: str = Field(default="ebook", min_length=1)
    status: str = Field(min_length=1)
    blocking_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    findings: list[AmazonKdpValidationFinding] = Field(default_factory=list)
    manuscript_stats: dict[str, int] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)


class AmazonKdpPackageResult(BaseModel):
    project_slug: str = Field(min_length=1)
    package_dir: str = Field(min_length=1)
    manifest_path: str = Field(min_length=1)
    validation_status: str = Field(min_length=1)
    output_files: dict[str, str] = Field(default_factory=dict)
    validation: AmazonKdpValidationReport


def build_default_amazon_kdp_profile(project: ProjectModel) -> AmazonKdpPublicationProfile:
    metadata = dict(project.metadata_json or {})
    description = metadata.get("logline") or metadata.get("reader_promise")
    raw_keywords = metadata.get("trope_keywords")
    keywords = [str(item) for item in raw_keywords[:7]] if isinstance(raw_keywords, list) else []
    return AmazonKdpPublicationProfile(
        language=project.language or "en-US",
        book_title=project.title,
        author_display_name=str(metadata.get("author_display_name")).strip() or None
        if metadata.get("author_display_name")
        else None,
        description=str(description).strip() if isinstance(description, str) and description.strip() else None,
        keywords=keywords,
        target_formats=["ebook"],
    )


def _publishing_payload(project: ProjectModel) -> dict[str, Any]:
    metadata = dict(project.metadata_json or {})
    publishing = metadata.get(_PUBLISHING_KEY)
    if not isinstance(publishing, dict):
        return {}
    payload = publishing.get(_AMAZON_KDP_KEY)
    return dict(payload) if isinstance(payload, dict) else {}


def get_amazon_kdp_profile(project: ProjectModel) -> AmazonKdpPublicationProfile:
    payload = _publishing_payload(project)
    if payload:
        return AmazonKdpPublicationProfile.model_validate(payload)
    return build_default_amazon_kdp_profile(project)


def _store_amazon_kdp_profile(
    project: ProjectModel,
    profile: AmazonKdpPublicationProfile,
) -> AmazonKdpPublicationProfile:
    metadata = dict(project.metadata_json or {})
    publishing = dict(metadata.get(_PUBLISHING_KEY) or {})
    publishing[_AMAZON_KDP_KEY] = profile.model_dump(mode="json", exclude_none=True)
    metadata[_PUBLISHING_KEY] = publishing
    project.metadata_json = metadata
    return profile


async def init_amazon_kdp_profile(
    session: AsyncSession,
    project_slug: str,
    *,
    overwrite: bool = False,
) -> AmazonKdpPublicationProfile:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    if _publishing_payload(project) and not overwrite:
        return get_amazon_kdp_profile(project)

    profile = build_default_amazon_kdp_profile(project)
    _store_amazon_kdp_profile(project, profile)
    await session.flush()
    return profile


async def show_amazon_kdp_profile(
    session: AsyncSession,
    project_slug: str,
) -> AmazonKdpPublicationProfile:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    return get_amazon_kdp_profile(project)


def _make_finding(
    code: str,
    message: str,
    *,
    severity: str,
    blocking: bool,
    fix_hint: str | None = None,
) -> AmazonKdpValidationFinding:
    return AmazonKdpValidationFinding(
        code=code,
        message=message,
        severity=severity,
        blocking=blocking,
        fix_hint=fix_hint,
    )


def validate_amazon_kdp_ready_package(
    project: ProjectModel,
    profile: AmazonKdpPublicationProfile,
    manuscript_md: str,
) -> AmazonKdpValidationReport:
    findings: list[AmazonKdpValidationFinding] = []
    package_title = (profile.book_title or "").strip()
    author_name = (profile.author_display_name or "").strip()
    description = (profile.description or "").strip()

    if not profile.language.lower().startswith("en"):
        findings.append(
            _make_finding(
                "language_not_english",
                "Amazon KDP English packaging currently requires an English language code.",
                severity="error",
                blocking=True,
                fix_hint="Set the publication profile language to en-US or another English locale.",
            )
        )

    if not package_title:
        findings.append(
            _make_finding(
                "missing_book_title",
                "Amazon KDP packaging requires a book title.",
                severity="error",
                blocking=True,
                fix_hint="Fill in publishing.amazon_kdp.book_title.",
            )
        )

    if not author_name:
        findings.append(
            _make_finding(
                "missing_author_display_name",
                "Amazon KDP packaging requires an author display name.",
                severity="error",
                blocking=True,
                fix_hint="Fill in publishing.amazon_kdp.author_display_name with the public pen name or legal display name.",
            )
        )

    if not description:
        findings.append(
            _make_finding(
                "missing_description",
                "Amazon KDP packaging requires a book description.",
                severity="error",
                blocking=True,
                fix_hint="Fill in publishing.amazon_kdp.description.",
            )
        )

    if not profile.categories:
        findings.append(
            _make_finding(
                "missing_categories",
                "Amazon KDP packaging requires at least one category selection.",
                severity="error",
                blocking=True,
                fix_hint="Add 1-3 category strings to publishing.amazon_kdp.categories.",
            )
        )

    if profile.ai_generated_text == "unknown" or profile.ai_generated_images == "unknown":
        findings.append(
            _make_finding(
                "missing_ai_disclosure",
                "Amazon KDP packaging requires explicit AI disclosure choices before upload.",
                severity="error",
                blocking=True,
                fix_hint="Set ai_generated_text and ai_generated_images to none, generated, or assisted.",
            )
        )

    cover_path = Path(profile.ebook.cover_image_path).expanduser() if profile.ebook.cover_image_path else None
    if cover_path is None or not cover_path.exists():
        findings.append(
            _make_finding(
                "missing_ebook_cover",
                "Amazon KDP ebook packaging requires a local cover image path that exists.",
                severity="error",
                blocking=True,
                fix_hint="Provide publishing.amazon_kdp.ebook.cover_image_path and make sure the file exists.",
            )
        )

    if not manuscript_md.strip():
        findings.append(
            _make_finding(
                "empty_manuscript",
                "The current project manuscript is empty, so KDP artifacts cannot be generated.",
                severity="error",
                blocking=True,
            )
        )

    if len(profile.keywords) == 0:
        findings.append(
            _make_finding(
                "missing_keywords",
                "No Amazon KDP keyword phrases are set yet.",
                severity="warning",
                blocking=False,
                fix_hint="Add up to 7 keyword phrases to improve discoverability.",
            )
        )

    bonus_match = _BONUS_CONTENT_PATTERN.search(manuscript_md)
    if bonus_match is not None:
        if profile.contains_bonus_content and bonus_match.start() < int(len(manuscript_md) * 0.8):
            findings.append(
                _make_finding(
                    "bonus_content_too_early",
                    "Bonus content appears before the end of the manuscript, which is risky for Amazon KDP packaging.",
                    severity="error",
                    blocking=True,
                    fix_hint="Move bonus chapters and discussion material to the end matter section.",
                )
            )
        elif not profile.contains_bonus_content:
            findings.append(
                _make_finding(
                    "unexpected_bonus_content",
                    "The manuscript looks like it includes bonus content, but the publication profile says it does not.",
                    severity="warning",
                    blocking=False,
                    fix_hint="Either remove the bonus markers or set contains_bonus_content to true.",
                )
            )

    if _EXTERNAL_CTA_PATTERN.search(manuscript_md):
        findings.append(
            _make_finding(
                "external_cta_detected",
                "The manuscript appears to contain external calls to action that should be reviewed before Amazon KDP upload.",
                severity="warning",
                blocking=False,
                fix_hint="Review newsletter, Discord, Patreon, subscribe, or giveaway language in the end matter.",
            )
        )

    if _URL_PATTERN.search(manuscript_md):
        findings.append(
            _make_finding(
                "url_detected",
                "The manuscript contains one or more URLs. Review them before uploading to Amazon KDP.",
                severity="warning",
                blocking=False,
            )
        )

    if not profile.identity_verified:
        findings.append(
            _make_finding(
                "identity_not_ready",
                "Identity verification is not marked complete in the publication profile.",
                severity="warning",
                blocking=False,
                fix_hint="Complete KDP identity verification before final upload.",
            )
        )

    if not profile.tax_profile_complete:
        findings.append(
            _make_finding(
                "tax_profile_not_ready",
                "Tax profile readiness is not marked complete in the publication profile.",
                severity="warning",
                blocking=False,
                fix_hint="Complete the KDP tax interview before final upload.",
            )
        )

    if not profile.payout_method_ready:
        findings.append(
            _make_finding(
                "payout_not_ready",
                "Payout method readiness is not marked complete in the publication profile.",
                severity="warning",
                blocking=False,
                fix_hint="Confirm a supported bank account or PSP before publishing.",
            )
        )

    blocking_count = len([finding for finding in findings if finding.blocking])
    warning_count = len([finding for finding in findings if not finding.blocking])
    status = "fail" if blocking_count else "pass_with_warnings" if warning_count else "pass"
    return AmazonKdpValidationReport(
        project_slug=project.slug,
        format="ebook",
        status=status,
        blocking_count=blocking_count,
        warning_count=warning_count,
        findings=findings,
        manuscript_stats=build_markdown_reading_stats(manuscript_md),
        profile=profile.model_dump(mode="json", exclude_none=True),
    )


async def validate_amazon_kdp_project(
    session: AsyncSession,
    project_slug: str,
) -> AmazonKdpValidationReport:
    project, manuscript_md = await load_project_export_content(session, project_slug)
    profile = get_amazon_kdp_profile(project)
    return validate_amazon_kdp_ready_package(project, profile, manuscript_md)


def _render_qa_markdown(report: AmazonKdpValidationReport) -> str:
    lines = [
        f"# Amazon KDP QA Report — {report.project_slug}",
        "",
        f"- Status: `{report.status}`",
        f"- Blocking findings: `{report.blocking_count}`",
        f"- Warnings: `{report.warning_count}`",
        f"- Word count: `{report.manuscript_stats.get('word_count', 0)}`",
        "",
        "## Findings",
        "",
    ]
    if not report.findings:
        lines.append("- No findings.")
        return "\n".join(lines).strip() + "\n"
    for finding in report.findings:
        hint = f" Fix: {finding.fix_hint}" if finding.fix_hint else ""
        lines.append(
            f"- `{finding.severity}` `{finding.code}`: {finding.message}{hint}"
        )
    return "\n".join(lines).strip() + "\n"


def _render_upload_checklist(
    profile: AmazonKdpPublicationProfile,
    validation: AmazonKdpValidationReport,
) -> str:
    lines = [
        "# Amazon KDP Upload Checklist",
        "",
        "1. Open Amazon KDP Bookshelf and start a new Kindle eBook.",
        "2. Copy the metadata values from `metadata.json` into the KDP detail page.",
        "3. Upload `ebook/book.epub` as the manuscript file.",
        "4. Upload `assets/cover` as the cover image.",
        "5. Confirm AI disclosure matches the values in the publication profile.",
        "6. Recheck categories, keywords, and description before saving the draft.",
        "7. Complete pricing, territories, tax, and payout setup directly in KDP.",
        "",
        "## Profile Readiness",
        "",
        f"- Identity verified: `{profile.identity_verified}`",
        f"- Tax profile complete: `{profile.tax_profile_complete}`",
        f"- Payout method ready: `{profile.payout_method_ready}`",
        "",
        "## Validation Summary",
        "",
        f"- Status: `{validation.status}`",
        f"- Blocking findings: `{validation.blocking_count}`",
        f"- Warnings: `{validation.warning_count}`",
    ]
    return "\n".join(lines).strip() + "\n"


async def _register_text_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    source_id: UUID,
    export_type: str,
    output_path: Path,
    content: str,
    version_label: str,
) -> ExportArtifactModel:
    storage_uri, checksum = write_markdown_output(output_path, content)
    artifact = create_export_artifact(
        project_id=project_id,
        export_type=export_type,
        source_scope="project",
        source_id=source_id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=version_label,
        created_by_run_id=None,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def _register_binary_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    source_id: UUID,
    export_type: str,
    output_path: Path,
    content: bytes,
    version_label: str,
) -> ExportArtifactModel:
    storage_uri, checksum = write_binary_output(output_path, content)
    artifact = create_export_artifact(
        project_id=project_id,
        export_type=export_type,
        source_scope="project",
        source_id=source_id,
        storage_uri=storage_uri,
        checksum=checksum,
        version_label=version_label,
        created_by_run_id=None,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def package_amazon_kdp_project(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    strict: bool = True,
) -> AmazonKdpPackageResult:
    project, manuscript_md = await load_project_export_content(session, project_slug)
    profile = get_amazon_kdp_profile(project)
    validation = validate_amazon_kdp_ready_package(project, profile, manuscript_md)
    if strict and validation.blocking_count:
        raise ValueError(
            f"Amazon KDP package blocked by {validation.blocking_count} validation finding(s)."
        )

    package_dir = Path(settings.output.base_dir) / project.slug / "amazon-kdp"
    ebook_dir = package_dir / "ebook"
    assets_dir = package_dir / "assets"
    ebook_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    ebook_title = profile.book_title or project.title
    epub_bytes = build_epub_bytes(
        ebook_title,
        manuscript_md,
        language=profile.language,
        author=profile.author_display_name,
        identifier=f"{project.slug}-amazon-kdp",
    )
    docx_bytes = build_docx_bytes(ebook_title, manuscript_md, author=profile.author_display_name)

    epub_artifact = await _register_binary_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-epub",
        output_path=ebook_dir / "book.epub",
        content=epub_bytes,
        version_label="amazon-kdp-ebook-current",
    )
    docx_artifact = await _register_binary_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-docx",
        output_path=ebook_dir / "book.docx",
        content=docx_bytes,
        version_label="amazon-kdp-ebook-current",
    )

    copied_cover_path: Path | None = None
    if profile.ebook.cover_image_path:
        source_cover = Path(profile.ebook.cover_image_path).expanduser()
        if source_cover.exists():
            copied_cover_path = assets_dir / f"cover{source_cover.suffix.lower()}"
            cover_artifact = await _register_binary_artifact(
                session,
                project_id=project.id,
                source_id=project.id,
                export_type="amazon-kdp-cover",
                output_path=copied_cover_path,
                content=source_cover.read_bytes(),
                version_label="amazon-kdp-ebook-current",
            )
        else:
            cover_artifact = None
    else:
        cover_artifact = None

    metadata_path = package_dir / "metadata.json"
    metadata_payload = json.dumps(profile.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2)
    metadata_artifact = await _register_text_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-metadata",
        output_path=metadata_path,
        content=metadata_payload,
        version_label="amazon-kdp-ebook-current",
    )

    qa_report_json_path = package_dir / "qa-report.json"
    qa_report_json = json.dumps(validation.model_dump(mode="json"), ensure_ascii=False, indent=2)
    qa_json_artifact = await _register_text_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-qa-json",
        output_path=qa_report_json_path,
        content=qa_report_json,
        version_label="amazon-kdp-ebook-current",
    )

    qa_report_md_path = package_dir / "qa-report.md"
    qa_markdown = _render_qa_markdown(validation)
    qa_md_artifact = await _register_text_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-qa-md",
        output_path=qa_report_md_path,
        content=qa_markdown,
        version_label="amazon-kdp-ebook-current",
    )

    checklist_path = package_dir / "upload-checklist.md"
    checklist_markdown = _render_upload_checklist(profile, validation)
    checklist_artifact = await _register_text_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-checklist",
        output_path=checklist_path,
        content=checklist_markdown,
        version_label="amazon-kdp-ebook-current",
    )

    output_files = {
        "metadata": str(metadata_path.resolve()),
        "qa_report_json": str(qa_report_json_path.resolve()),
        "qa_report_md": str(qa_report_md_path.resolve()),
        "upload_checklist": str(checklist_path.resolve()),
        "ebook_epub": str((ebook_dir / "book.epub").resolve()),
        "ebook_docx": str((ebook_dir / "book.docx").resolve()),
    }
    if copied_cover_path is not None:
        output_files["cover"] = str(copied_cover_path.resolve())

    manifest_payload = {
        "project_slug": project.slug,
        "book_title": ebook_title,
        "language": profile.language,
        "validation_status": validation.status,
        "blocking_findings": validation.blocking_count,
        "warning_findings": validation.warning_count,
        "formats": ["ebook"],
        "artifacts": {
            "metadata": {
                "path": metadata_artifact.storage_uri,
                "checksum": metadata_artifact.checksum,
            },
            "qa_report_json": {
                "path": qa_json_artifact.storage_uri,
                "checksum": qa_json_artifact.checksum,
            },
            "qa_report_md": {
                "path": qa_md_artifact.storage_uri,
                "checksum": qa_md_artifact.checksum,
            },
            "upload_checklist": {
                "path": checklist_artifact.storage_uri,
                "checksum": checklist_artifact.checksum,
            },
            "ebook_epub": {
                "path": epub_artifact.storage_uri,
                "checksum": epub_artifact.checksum,
            },
            "ebook_docx": {
                "path": docx_artifact.storage_uri,
                "checksum": docx_artifact.checksum,
            },
        },
        "manual_actions": [
            "Upload the EPUB file in KDP Bookshelf.",
            "Upload the cover image in KDP Bookshelf.",
            "Paste the metadata from metadata.json.",
            "Confirm tax, banking, and pricing directly in KDP.",
        ],
    }
    if cover_artifact is not None:
        manifest_payload["artifacts"]["cover"] = {
            "path": cover_artifact.storage_uri,
            "checksum": cover_artifact.checksum,
        }

    manifest_path = package_dir / "manifest.json"
    manifest_content = json.dumps(manifest_payload, ensure_ascii=False, indent=2)
    await _register_text_artifact(
        session,
        project_id=project.id,
        source_id=project.id,
        export_type="amazon-kdp-manifest",
        output_path=manifest_path,
        content=manifest_content,
        version_label="amazon-kdp-ebook-current",
    )

    return AmazonKdpPackageResult(
        project_slug=project.slug,
        package_dir=str(package_dir.resolve()),
        manifest_path=str(manifest_path.resolve()),
        validation_status=validation.status,
        output_files=output_files,
        validation=validation,
    )
