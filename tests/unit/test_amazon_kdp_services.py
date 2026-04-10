from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.project import AmazonKdpPublicationProfile
from bestseller.infra.db.models import ProjectModel
from bestseller.services.publishing import amazon_kdp as amazon_kdp_services
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


def build_settings(tmp_path: Path):
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={"BESTSELLER__OUTPUT__BASE_DIR": str(tmp_path / "output")},
    )


def build_project(**overrides):
    payload = {
        "id": uuid4(),
        "slug": "my-story",
        "title": "My Story",
        "language": "en-US",
        "genre": "fantasy",
        "target_word_count": 90000,
        "target_chapters": 30,
        "metadata_json": {},
    }
    payload.update(overrides)
    return ProjectModel(**payload)


@pytest.mark.asyncio
async def test_init_amazon_kdp_profile_persists_default_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    project = build_project(metadata_json={"logline": "A daring fantasy launch."})

    async def fake_get_project_by_slug(session, slug):
        assert slug == "my-story"
        return project

    monkeypatch.setattr(amazon_kdp_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession()

    profile = await amazon_kdp_services.init_amazon_kdp_profile(session, "my-story")

    assert profile.book_title == "My Story"
    assert project.metadata_json["publishing"]["amazon_kdp"]["language"] == "en-US"
    assert project.metadata_json["publishing"]["amazon_kdp"]["description"] == "A daring fantasy launch."


def test_validate_amazon_kdp_ready_package_reports_blockers(tmp_path: Path) -> None:
    project = build_project()
    profile = AmazonKdpPublicationProfile(
        language="en-US",
        book_title="My Story",
        ai_generated_text="unknown",
        ai_generated_images="unknown",
    )

    report = amazon_kdp_services.validate_amazon_kdp_ready_package(
        project,
        profile,
        "# My Story\n\nBonus Chapter\n\nPatreon link soon",
    )

    assert report.status == "fail"
    assert report.blocking_count >= 4
    codes = {finding.code for finding in report.findings}
    assert "missing_author_display_name" in codes
    assert "missing_ebook_cover" in codes
    assert "missing_ai_disclosure" in codes


@pytest.mark.asyncio
async def test_package_amazon_kdp_project_writes_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"fake-cover")
    project = build_project()
    profile = AmazonKdpPublicationProfile(
        language="en-US",
        book_title="My Story",
        author_display_name="Owen Example",
        description="An English fantasy novel.",
        categories=["Fiction / Fantasy / Epic"],
        keywords=["epic fantasy", "dragon rider"],
        ai_generated_text="assisted",
        ai_generated_images="none",
        identity_verified=True,
        tax_profile_complete=True,
        payout_method_ready=True,
        ebook={"enabled": True, "cover_image_path": str(cover_path)},
    )
    project.metadata_json = {
        "publishing": {
            "amazon_kdp": profile.model_dump(mode="json", exclude_none=True),
        }
    }

    async def fake_load_project_export_content(session, project_slug):
        assert project_slug == "my-story"
        return project, "# My Story\n\n## Chapter 1\n\nThe launch begins."

    monkeypatch.setattr(
        amazon_kdp_services,
        "load_project_export_content",
        fake_load_project_export_content,
    )
    session = FakeSession()

    result = await amazon_kdp_services.package_amazon_kdp_project(
        session,
        build_settings(tmp_path),
        "my-story",
    )

    assert result.validation_status == "pass"
    assert (tmp_path / "output" / "my-story" / "amazon-kdp" / "manifest.json").exists()
    assert (tmp_path / "output" / "my-story" / "amazon-kdp" / "ebook" / "book.epub").exists()
    assert (tmp_path / "output" / "my-story" / "amazon-kdp" / "ebook" / "book.docx").exists()
    assert (tmp_path / "output" / "my-story" / "amazon-kdp" / "assets" / "cover.jpg").exists()
    assert "ebook_epub" in result.output_files
