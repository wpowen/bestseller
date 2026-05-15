"""Source-book parsing for the distillation pipeline.

This module converts supported book files into normalized text plus anonymous
chapter slices. It deliberately keeps source titles and authors in memory only;
callers decide whether to store private metadata.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import ClassVar
import unicodedata
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

TEXT_ENCODINGS: tuple[str, ...] = (
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-16le",
    "utf-16be",
    "gb18030",
    "gbk",
    "big5",
    "cp950",
    "shift_jis",
    "euc_jp",
)

TEXT_FORMATS: frozenset[str] = frozenset({"txt", "md", "markdown"})
HTML_FORMATS: frozenset[str] = frozenset({"html", "htm", "xhtml"})
CALIBRE_FORMATS: frozenset[str] = frozenset({"mobi", "azw3"})
SUPPORTED_FORMATS: frozenset[str] = (
    TEXT_FORMATS | HTML_FORMATS | frozenset({"epub"}) | CALIBRE_FORMATS
)
CN_NUMERAL_CLASS = r"一二三四五六七八九十百千万两0-9\uff10-\uff19"
HEADING_SEPARATOR_CLASS = r"\uff1a:、.\-\s"

VOLUME_CHAPTER_RE = re.compile(
    rf"^\s*(第[{CN_NUMERAL_CLASS}]+[集卷部篇])\s*"
    rf"(第[{CN_NUMERAL_CLASS}]+[章节回])"
    rf"\s*[{HEADING_SEPARATOR_CLASS}]*(.*?)\s*$",
    re.M,
)
CHINESE_CHAPTER_RE = re.compile(
    rf"^\s*(第[{CN_NUMERAL_CLASS}]+[章节回])"
    rf"\s*[{HEADING_SEPARATOR_CLASS}]*(.*?)\s*$",
    re.M,
)
ENGLISH_CHAPTER_RE = re.compile(
    r"^\s*(chapter\s+(?:[0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten))"
    rf"\s*[{HEADING_SEPARATOR_CLASS}]?\s*(.*?)\s*$",
    re.I | re.M,
)
MARKDOWN_CHAPTER_RE = re.compile(
    rf"^\s{{0,3}}#{{1,3}}\s+((?:第[{CN_NUMERAL_CLASS}]+[章节回]|chapter\s+\S+).*)$",
    re.I | re.M,
)
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class BookMetadata:
    title: str | None = None
    author: str | None = None
    language: str | None = None
    metadata_source: str = "unknown"


@dataclass(frozen=True)
class ChapterSlice:
    abs_chapter_no: int
    volume_label: str
    volume_no: int
    chapter_label: str
    title: str
    body: str
    source_char_start: int
    source_char_end: int
    boundary_type: str


@dataclass(frozen=True)
class ParsedBook:
    source_format: str
    text: str
    encoding: str
    metadata: BookMetadata
    chapters: tuple[ChapterSlice, ...]
    parser_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _TextPayload:
    text: str
    encoding: str
    metadata: BookMetadata
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HeadingMatch:
    start: int
    end: int
    volume_label: str
    chapter_label: str
    title: str
    boundary_type: str


class BookParseError(ValueError):
    """Raised when a source book cannot be parsed into text."""


class _TextExtractingHTMLParser(HTMLParser):
    _BLOCK_TAGS: ClassVar[frozenset[str]] = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "figcaption",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "td",
            "th",
            "tr",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in {"script", "style"}:
            self._ignored_depth += 1
            return
        if normalized in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if normalized in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return normalize_text(" ".join(self._chunks))


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u3000", " ")
    text = WHITESPACE_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def decode_text_bytes(raw: bytes) -> tuple[str, str]:
    for encoding in TEXT_ENCODINGS:
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_like_text(decoded):
            return normalize_text(decoded), encoding
    return normalize_text(raw.decode("utf-8", errors="replace")), "utf-8-replace"


def normalize_title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).lower()
    normalized = re.sub(r"\.[a-z0-9]{2,5}$", "", normalized)
    normalized = re.sub(r"[\(\uFF08\[\u3010].*?[\)\uFF09\]\u3011]", "", normalized)
    normalized = re.sub(
        r"(精校版|校对版|修订版|完整版|完结版|完本|全本|全集|繁体|简体|txt|epub|mobi|azw3)",
        "",
        normalized,
    )
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(
        r"[\-_.\u00b7,\uFF0C\u3002:\uFF1A;\uFF1B!\uFF01?\uFF1F\u3001|]+",
        "",
        normalized,
    )
    return normalized.strip()


def parse_source_book(source_path: Path) -> ParsedBook:
    source_format = _source_format(source_path)
    if source_format not in SUPPORTED_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_FORMATS))
        raise BookParseError(f"Unsupported source format: .{source_format}. Supported: {supported}")

    payload = _extract_text_payload(source_path, source_format)
    if len(payload.text) < 100:
        raise BookParseError(f"{source_path.name}: extracted text is too short for distillation")

    chapters, split_warnings = split_chapters(payload.text)
    warnings = tuple(payload.warnings) + split_warnings
    return ParsedBook(
        source_format=source_format,
        text=payload.text,
        encoding=payload.encoding,
        metadata=payload.metadata,
        chapters=tuple(chapters),
        parser_warnings=warnings,
    )


def split_chapters(
    text: str,
    *,
    fallback_chunk_chars: int = 6500,
) -> tuple[list[ChapterSlice], tuple[str, ...]]:
    normalized = normalize_text(text)
    matches = _collect_heading_matches(normalized)
    if matches:
        return _chapters_from_matches(normalized, matches), ()
    return _fallback_chunks(normalized, chunk_chars=fallback_chunk_chars), (
        "No chapter headings matched; fell back to fixed-size anonymous chunks.",
    )


def strip_html_to_text(html: str) -> str:
    parser = _TextExtractingHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.text()


def _source_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "text":
        return "txt"
    return suffix


def _looks_like_text(text: str) -> bool:
    if not text:
        return False
    sample = text[:4000]
    control_count = sum(
        1
        for char in sample
        if unicodedata.category(char).startswith("C") and char not in "\n\t"
    )
    replacement_count = sample.count("\ufffd")
    return (control_count + replacement_count) / max(len(sample), 1) < 0.08


def _parse_xml_bytes(raw: bytes) -> ElementTree.Element:
    header = raw[:4096].lower()
    if b"<!doctype" in header or b"<!entity" in header:
        raise BookParseError("XML with DOCTYPE or ENTITY declarations is not accepted")
    return ElementTree.fromstring(raw)  # noqa: S314


def _run_calibre_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def resolve_calibre_executable(binary: str) -> str | None:
    """Resolve ``ebook-convert`` / ``ebook-meta`` without requiring PATH.

    Order: explicit env override → :func:`shutil.which` → well-known install paths
    (macOS ``/Applications/calibre.app/...``, Windows ``Calibre2``).
    """
    env_names = {
        "ebook-convert": (
            "BESTSELLER__DISTILLATION__EBOOK_CONVERT",
            "EBOOK_CONVERT",
        ),
        "ebook-meta": (
            "BESTSELLER__DISTILLATION__EBOOK_META",
            "EBOOK_META",
        ),
    }
    for env_key in env_names.get(binary, ()):
        candidate = os.environ.get(env_key, "").strip()
        if candidate and Path(candidate).is_file():
            return candidate

    which = shutil.which(binary)
    if which:
        return which

    if sys.platform == "darwin":
        bundle = Path("/Applications/calibre.app/Contents/MacOS") / binary
        if bundle.is_file():
            return str(bundle)
    if sys.platform == "win32":
        for base in (
            Path(r"C:\Program Files\Calibre2"),
            Path(r"C:\Program Files (x86)\Calibre2"),
        ):
            exe = base / f"{binary}.exe"
            if exe.is_file():
                return str(exe)
    return None


def _extract_text_payload(source_path: Path, source_format: str) -> _TextPayload:
    if source_format in TEXT_FORMATS:
        text, encoding = decode_text_bytes(source_path.read_bytes())
        return _TextPayload(
            text=text,
            encoding=encoding,
            metadata=BookMetadata(title=source_path.stem, metadata_source="file_stem"),
        )
    if source_format in HTML_FORMATS:
        text, encoding = decode_text_bytes(source_path.read_bytes())
        return _TextPayload(
            text=strip_html_to_text(text),
            encoding=encoding,
            metadata=BookMetadata(title=source_path.stem, metadata_source="file_stem"),
        )
    if source_format == "epub":
        return _extract_epub_payload(source_path)
    if source_format in CALIBRE_FORMATS:
        return _extract_calibre_payload(source_path, source_format)
    raise BookParseError(f"Unsupported source format: .{source_format}")


def _extract_epub_payload(source_path: Path) -> _TextPayload:
    try:
        with ZipFile(source_path) as archive:
            opf_path = _find_epub_opf_path(archive)
            opf_root = _parse_xml_bytes(archive.read(opf_path))
            metadata = _metadata_from_opf(opf_root, metadata_source="epub_opf")
            html_paths = _epub_spine_html_paths(opf_root, opf_path)
            if not html_paths:
                html_paths = sorted(
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".html", ".htm", ".xhtml"))
                )
            texts = []
            encodings: list[str] = []
            for html_path in html_paths:
                try:
                    raw = archive.read(html_path)
                except KeyError:
                    continue
                html, encoding = decode_text_bytes(raw)
                encodings.append(encoding)
                extracted = strip_html_to_text(html)
                if extracted:
                    texts.append(extracted)
            if not texts:
                raise BookParseError(
                    f"{source_path.name}: EPUB has no readable HTML spine content"
                )
            encoding = _summarize_encodings(encodings)
            resolved_metadata = (
                metadata
                if metadata.title
                else BookMetadata(source_path.stem, metadata_source="file_stem")
            )
            return _TextPayload(
                text=normalize_text("\n\n".join(texts)),
                encoding=encoding,
                metadata=resolved_metadata,
            )
    except BadZipFile as exc:
        raise BookParseError(f"{source_path.name}: invalid EPUB zip container") from exc
    except ElementTree.ParseError as exc:
        raise BookParseError(f"{source_path.name}: invalid EPUB metadata XML") from exc


def _extract_with_ebook_convert(
    source_path: Path, source_format: str, ebook_convert: str
) -> _TextPayload:
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="bestseller-ebook-") as tmp:
        output = Path(tmp) / "source.txt"
        command = [
            ebook_convert,
            str(source_path),
            str(output),
            "--txt-output-formatting=plain",
        ]
        completed = _run_calibre_command(command, timeout=180)
        if completed.returncode != 0:
            error = completed.stderr.strip() or completed.stdout.strip()
            raise BookParseError(f"ebook-convert failed for .{source_format}: {error}")
        text, encoding = decode_text_bytes(output.read_bytes())
        metadata = _metadata_from_calibre(source_path) or BookMetadata(
            title=source_path.stem,
            metadata_source="file_stem",
        )
        if metadata.metadata_source == "file_stem":
            warnings.append(
                "Calibre metadata extraction unavailable; file stem was used for private title key."
            )
        return _TextPayload(
            text=text,
            encoding=f"calibre:{encoding}",
            metadata=metadata,
            warnings=tuple(warnings),
        )


def _extract_calibre_via_mobi_library(source_path: Path, source_format: str) -> _TextPayload:
    """Unpack MOBI/KF8 using the PyPI ``mobi`` package (KindleUnpack) when Calibre is absent."""
    try:
        import mobi  # type: ignore[import-untyped]
    except ImportError as exc:
        raise BookParseError(
            f".{source_format}: Calibre `ebook-convert` not found and the `mobi` package is not "
            "installed. Run `uv sync --extra distillation` or `uv pip install 'mobi>=0.4.1'`."
        ) from exc

    base_warn = (
        "Decoded Kindle file with the Python `mobi` unpacker (KindleUnpack fork); "
        "Calibre was not used or failed.",
    )
    try:
        tempdir, extracted = mobi.extract(str(source_path))
    except Exception as exc:  # noqa: BLE001 — third-party unpack surface
        raise BookParseError(f"{source_path.name}: Kindle unpack failed: {exc}") from exc

    try:
        extracted_path = Path(extracted)
        if extracted_path.suffix.lower() == ".epub":
            inner = _extract_epub_payload(extracted_path)
            return _TextPayload(
                text=inner.text,
                encoding=f"mobi-lib+{inner.encoding}",
                metadata=inner.metadata,
                warnings=base_warn + inner.warnings,
            )
        if extracted_path.suffix.lower() in {".html", ".htm"}:
            raw = extracted_path.read_bytes()
            html, encoding = decode_text_bytes(raw)
            text = normalize_text(strip_html_to_text(html))
            meta = BookMetadata(title=source_path.stem, metadata_source="file_stem")
            return _TextPayload(
                text=text,
                encoding=f"mobi-lib-html:{encoding}",
                metadata=meta,
                warnings=base_warn,
            )
        if extracted_path.suffix.lower() == ".pdf":
            raise BookParseError(
                f"{source_path.name}: unpack produced PDF; install Calibre to convert this title."
            )
        raise BookParseError(
            f"{source_path.name}: unpack produced unsupported artifact: {extracted_path.name}"
        )
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _extract_calibre_payload(source_path: Path, source_format: str) -> _TextPayload:
    calibre_error: BookParseError | None = None
    ebook_convert = resolve_calibre_executable("ebook-convert")
    if ebook_convert:
        try:
            return _extract_with_ebook_convert(source_path, source_format, ebook_convert)
        except BookParseError as exc:
            calibre_error = exc
    try:
        return _extract_calibre_via_mobi_library(source_path, source_format)
    except BookParseError as exc2:
        if calibre_error is not None:
            raise BookParseError(
                f"{source_path.name}: Calibre failed ({calibre_error}); "
                f"Python Kindle unpack failed ({exc2})."
            ) from exc2
        raise


def _metadata_from_calibre(source_path: Path) -> BookMetadata | None:
    ebook_meta = resolve_calibre_executable("ebook-meta")
    if not ebook_meta:
        return None
    with tempfile.TemporaryDirectory(prefix="bestseller-meta-") as tmp:
        output = Path(tmp) / "metadata.opf"
        completed = _run_calibre_command(
            [ebook_meta, str(source_path), "--to-opf", str(output)],
            timeout=60,
        )
        if completed.returncode != 0 or not output.exists():
            return None
        try:
            root = _parse_xml_bytes(output.read_bytes())
        except (BookParseError, ElementTree.ParseError):
            return None
    return _metadata_from_opf(root, metadata_source="calibre_opf")


def _find_epub_opf_path(archive: ZipFile) -> str:
    try:
        container = _parse_xml_bytes(archive.read("META-INF/container.xml"))
        for elem in container.iter():
            if _local_name(elem.tag) == "rootfile":
                full_path = elem.attrib.get("full-path")
                if full_path:
                    return full_path
    except (KeyError, ElementTree.ParseError):
        pass
    for name in archive.namelist():
        if name.lower().endswith(".opf"):
            return name
    raise BookParseError("EPUB container does not reference an OPF package file")


def _metadata_from_opf(root: ElementTree.Element, *, metadata_source: str) -> BookMetadata:
    title: str | None = None
    author: str | None = None
    language: str | None = None
    for elem in root.iter():
        local = _local_name(elem.tag)
        text = (elem.text or "").strip()
        if not text:
            continue
        if local == "title" and title is None:
            title = text
        elif local in {"creator", "author"} and author is None:
            author = text
        elif local == "language" and language is None:
            language = text
    return BookMetadata(
        title=title,
        author=author,
        language=language,
        metadata_source=metadata_source,
    )


def _epub_spine_html_paths(root: ElementTree.Element, opf_path: str) -> list[str]:
    manifest: dict[str, str] = {}
    spine_ids: list[str] = []
    for elem in root.iter():
        local = _local_name(elem.tag)
        if local == "item":
            item_id = elem.attrib.get("id")
            href = elem.attrib.get("href")
            if item_id and href:
                manifest[item_id] = href
        elif local == "itemref":
            idref = elem.attrib.get("idref")
            if idref:
                spine_ids.append(idref)

    base = PurePosixPath(opf_path).parent
    paths: list[str] = []
    for idref in spine_ids:
        href = manifest.get(idref)
        if not href:
            continue
        clean_href = href.split("#", 1)[0]
        path = str(base / clean_href) if str(base) != "." else clean_href
        if path not in paths:
            paths.append(path)
    return paths


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _summarize_encodings(encodings: Iterable[str]) -> str:
    unique: list[str] = []
    for encoding in encodings:
        if encoding not in unique:
            unique.append(encoding)
    return "+".join(unique) if unique else "unknown"


def _collect_heading_matches(text: str) -> list[_HeadingMatch]:
    matches: list[_HeadingMatch] = []
    for match in VOLUME_CHAPTER_RE.finditer(text):
        title = match.group(3).strip()
        matches.append(
            _HeadingMatch(
                start=match.start(),
                end=match.end(),
                volume_label=match.group(1),
                chapter_label=match.group(2),
                title=title,
                boundary_type="volume_chapter_heading",
            )
        )
    if matches:
        return _dedupe_heading_matches(matches)

    for pattern, boundary_type in (
        (CHINESE_CHAPTER_RE, "chinese_chapter_heading"),
        (ENGLISH_CHAPTER_RE, "english_chapter_heading"),
    ):
        matches = [
            _HeadingMatch(
                start=match.start(),
                end=match.end(),
                volume_label="volume-unknown",
                chapter_label=match.group(1).strip(),
                title=(match.group(2) or "").strip(),
                boundary_type=boundary_type,
            )
            for match in pattern.finditer(text)
            if _plausible_heading(match.group(0))
        ]
        if matches:
            return _dedupe_heading_matches(matches)

    markdown_matches = [
        _HeadingMatch(
            start=match.start(),
            end=match.end(),
            volume_label="volume-unknown",
            chapter_label=f"markdown-heading-{idx:04d}",
            title=match.group(1).strip(),
            boundary_type="markdown_heading",
        )
        for idx, match in enumerate(MARKDOWN_CHAPTER_RE.finditer(text), start=1)
        if _plausible_heading(match.group(0))
    ]
    return _dedupe_heading_matches(markdown_matches)


def _dedupe_heading_matches(matches: list[_HeadingMatch]) -> list[_HeadingMatch]:
    output: list[_HeadingMatch] = []
    last_start = -1
    for match in sorted(matches, key=lambda item: item.start):
        if match.start == last_start:
            continue
        output.append(match)
        last_start = match.start
    return output


def _plausible_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return len(stripped) <= 120


def _chapters_from_matches(text: str, matches: list[_HeadingMatch]) -> list[ChapterSlice]:
    seen_volumes: dict[str, int] = {}
    chapters: list[ChapterSlice] = []
    for idx, match in enumerate(matches, start=1):
        body_start = match.end
        body_end = matches[idx].start if idx < len(matches) else len(text)
        volume_no = _volume_no(match.volume_label, seen_volumes)
        body = text[body_start:body_end].strip()
        chapters.append(
            ChapterSlice(
                abs_chapter_no=idx,
                volume_label=match.volume_label,
                volume_no=volume_no,
                chapter_label=match.chapter_label,
                title=match.title,
                body=body,
                source_char_start=body_start,
                source_char_end=body_end,
                boundary_type=match.boundary_type,
            )
        )
    return [chapter for chapter in chapters if chapter.body]


def _fallback_chunks(text: str, *, chunk_chars: int) -> list[ChapterSlice]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        if len(paragraph) > chunk_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(paragraph), chunk_chars):
                chunks.append(paragraph[start : start + chunk_chars])
            continue
        if current and current_len + len(paragraph) > chunk_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))

    if not chunks:
        chunks = [text]

    chapters: list[ChapterSlice] = []
    cursor = 0
    for idx, chunk in enumerate(chunks, start=1):
        start = text.find(chunk, cursor)
        if start < 0:
            start = cursor
        end = start + len(chunk)
        cursor = end
        chapters.append(
            ChapterSlice(
                abs_chapter_no=idx,
                volume_label="volume-01",
                volume_no=1,
                chapter_label=f"chunk-{idx:04d}",
                title="",
                body=chunk,
                source_char_start=start,
                source_char_end=end,
                boundary_type="fixed_size_fallback",
            )
        )
    return chapters


def _volume_no(volume_label: str, seen: dict[str, int]) -> int:
    if volume_label not in seen:
        seen[volume_label] = len(seen) + 1
    return seen[volume_label]
