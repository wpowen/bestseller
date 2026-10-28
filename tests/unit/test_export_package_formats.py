from __future__ import annotations

from datetime import UTC, datetime
import io
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import pytest

from bestseller.services import exports
from bestseller.services.publishing import amazon_kdp
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

TITLE = "A & B"
MANUSCRIPT = (
    f"# {TITLE}\n\n# Chapter 1\n\nFirst **bold** & safe.\n"
    "Second line &copy; 2026.\n\n---\n\n# Chapter 2\n\nLast paragraph."
)
OPF = "{http://www.idpf.org/2007/opf}"
XHTML = "{http://www.w3.org/1999/xhtml}"
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture
def frozen_export_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 28, 1, 2, 3, tzinfo=UTC)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(exports, "datetime", FrozenDateTime, raising=False)


@pytest.fixture(params=["direct", "kdp"])
async def epub_payload(
    request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_export_clock
):
    if request.param == "direct":
        return exports.build_epub_bytes(
            TITLE, MANUSCRIPT, language="en-US", author="Writer & Co", identifier="synthetic-book"
        )

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"synthetic-cover")
    project = SimpleNamespace(
        id=uuid4(),
        slug="synthetic-book",
        title=TITLE,
        language="en-US",
        metadata_json={
            "publishing": {
                "amazon_kdp": {
                    "language": "en-US",
                    "book_title": TITLE,
                    "author_display_name": "Writer & Co",
                    "description": "A synthetic test book.",
                    "categories": ["Fiction / Fantasy"],
                    "keywords": ["test book"],
                    "ai_generated_text": "none",
                    "ai_generated_images": "none",
                    "identity_verified": True,
                    "tax_profile_complete": True,
                    "payout_method_ready": True,
                    "ebook": {"enabled": True, "cover_image_path": str(cover)},
                }
            }
        },
    )
    monkeypatch.setattr(
        amazon_kdp, "load_project_export_content", AsyncMock(return_value=(project, MANUSCRIPT))
    )

    def add_artifact(artifact):
        artifact.id = uuid4()

    # Artifact persistence is a fake; the package writer and builders are real.
    session = SimpleNamespace(add=add_artifact, flush=AsyncMock())
    settings = load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=tmp_path / "absent.yaml",
        env={"BESTSELLER__OUTPUT__BASE_DIR": str(tmp_path / "output")},
    )
    result = await amazon_kdp.package_amazon_kdp_project(session, settings, project.slug)
    assert result.validation_status == "pass"
    return Path(result.output_files["ebook_epub"]).read_bytes()


def test_epub_all_xml_members_parse(epub_payload: bytes) -> None:
    errors = []
    with ZipFile(io.BytesIO(epub_payload)) as archive:
        assert archive.testzip() is None
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        members = [
            name for name in archive.namelist() if name.endswith((".xhtml", ".opf", ".ncx", ".xml"))
        ]
        assert "OEBPS/nav.xhtml" in members
        assert "OEBPS/content.opf" in members
        assert len([name for name in members if name.endswith(".xhtml")]) >= 3
        for name in members:
            try:
                ET.fromstring(archive.read(name))
            except ET.ParseError as error:
                errors.append(f"{name}: {error}")
        assert errors == []
        nav = ET.fromstring(archive.read("OEBPS/nav.xhtml"))
        toc = nav.find(f"{XHTML}body/{XHTML}nav")
        assert toc is not None
        assert toc.get("{http://www.idpf.org/2007/ops}type") == "toc"
        body = ET.fromstring(archive.read("OEBPS/chapter-0002.xhtml"))
        assert "Second line © 2026." in "".join(body.itertext())


def test_epub_modified_is_single_frozen_utc_value(epub_payload: bytes) -> None:
    with ZipFile(io.BytesIO(epub_payload)) as archive:
        opf = ET.fromstring(archive.read("OEBPS/content.opf"))
    modified = opf.findall(f"{OPF}metadata/{OPF}meta[@property='dcterms:modified']")
    assert len(modified) == 1
    value = modified[0].text
    assert value is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
    assert datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ") == datetime(2026, 10, 28, 1, 2, 3)


@pytest.mark.parametrize("heading", [f"# {TITLE}", "", "# Chapter 1"])
def test_docx_contains_one_book_title_and_preserves_body(heading: str) -> None:
    payload = exports.build_docx_bytes(TITLE, f"\n{heading}\n\nA **bold** sentence.\n\n## Section")
    with ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        document = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = ["".join(p.itertext()) for p in document.iter(f"{WORD}p")]
    assert paragraphs.count(TITLE) == 1
    assert "A bold sentence." in paragraphs
    assert "Section" in paragraphs
    assert document.find(f".//{WORD}b") is not None
    if heading == "# Chapter 1":
        assert "Chapter 1" in paragraphs


def test_docx_preserves_later_matching_heading_and_plain_title_text() -> None:
    payload = exports.build_docx_bytes(TITLE, f"{TITLE}\n\n# {TITLE}\n\nBody.")
    with ZipFile(io.BytesIO(payload)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = ["".join(p.itertext()) for p in document.iter(f"{WORD}p")]
    assert paragraphs.count(TITLE) == 3  # Only a leading matching H1 is redundant.
    assert "Body." in paragraphs
