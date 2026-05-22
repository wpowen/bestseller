#!/usr/bin/env python3
"""Convert a local ebook library into UTF-8 text files for easy reading."""

from __future__ import annotations

import argparse
import csv
import html
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Iterable
from xml.etree import ElementTree


TEXT_SUFFIXES = {".txt"}
EPUB_SUFFIXES = {".epub"}
SKIP_NAMES = {".DS_Store"}
DETECTION_BYTES = 512 * 1024


@dataclass(frozen=True)
class DecodeResult:
    text: str
    encoding: str
    score: float


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    output: Path | None
    kind: str
    encoding: str
    status: str
    message: str = ""


class TextExtractor(HTMLParser):
    """Small HTML-to-text extractor for XHTML/HTML chapters inside EPUB files."""

    BLOCK_TAGS = {
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
        "figure",
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

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return html.unescape("".join(self._parts))


def decode_bytes(raw: bytes) -> DecodeResult:
    if raw.startswith(b"\xef\xbb\xbf"):
        return DecodeResult(raw.decode("utf-8-sig"), "utf-8-sig", 1_000_000)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return DecodeResult(raw.decode("utf-16"), "utf-16", 1_000_000)

    candidates = candidate_encodings(raw)
    sample = raw[:DETECTION_BYTES]
    decoded: list[DecodeResult] = []

    for encoding in candidates:
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        decoded.append(DecodeResult(text, encoding, score_text(text, encoding)))

    for decoded_sample in sorted(decoded, key=lambda item: item.score, reverse=True):
        try:
            full_text = raw.decode(decoded_sample.encoding)
        except UnicodeDecodeError:
            continue
        return DecodeResult(full_text, decoded_sample.encoding, decoded_sample.score)

    for encoding in candidates:
        try:
            return DecodeResult(raw.decode(encoding), encoding, 0)
        except UnicodeDecodeError:
            continue

    fallback_candidates = []
    for encoding in ("gb18030", "gbk", "utf-8", "big5hkscs", "utf-16le"):
        text = raw.decode(encoding, errors="replace")
        fallback_candidates.append(DecodeResult(text, f"{encoding}+replace", score_text(text, encoding)))
    return max(fallback_candidates, key=lambda item: item.score)


def candidate_encodings(raw: bytes) -> list[str]:
    null_even = raw[:4096:2].count(0)
    null_odd = raw[1:4096:2].count(0)
    utf16_first = []
    if null_odd > max(8, null_even * 4):
        utf16_first.append("utf-16le")
    if null_even > max(8, null_odd * 4):
        utf16_first.append("utf-16be")

    common = [
        "utf-8",
        "gb18030",
        "gbk",
        "big5hkscs",
        "big5",
        "cp950",
        "utf-16le",
        "utf-16be",
    ]
    return list(dict.fromkeys(utf16_first + common))


def score_text(text: str, encoding: str) -> float:
    if not text:
        return 0

    length = len(text)
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in text)
    ascii_printable = sum(char in "\n\r\t" or " " <= char <= "~" for char in text)
    common_cn_punct = sum(char in "，。！？；：《》（）【】、‘’“”" for char in text)
    replacement = text.count("\ufffd")
    nulls = text.count("\x00")
    controls = sum((ord(char) < 32 and char not in "\n\r\t") for char in text)
    private = sum("\ue000" <= char <= "\uf8ff" for char in text)
    mojibake = sum(char in "锘銆脙鈥" for char in text[:4000])

    score = chinese * 12 + common_cn_punct * 3 + ascii_printable * 0.4
    score -= replacement * 100 + nulls * 200 + controls * 80 + private * 20 + mojibake * 8

    if encoding.startswith("utf-16") and nulls:
        score -= length * 2
    if encoding in {"gb18030", "gbk"}:
        score += chinese * 0.5
    return score


def normalize_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = text.strip()
    return text + "\n" if text else ""


def output_path_for(source: Path, input_root: Path, output_root: Path, suffix: str = ".txt") -> Path:
    relative = source.relative_to(input_root)
    return output_root / relative.with_suffix(suffix)


def convert_txt(source: Path, input_root: Path, output_root: Path) -> ConversionResult:
    raw = source.read_bytes()
    decoded = decode_bytes(raw)
    text = normalize_text(decoded.text)
    output = output_path_for(source, input_root, output_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_text(output, text)
    return ConversionResult(source, output, "txt", decoded.encoding, "converted")


def convert_epub(source: Path, input_root: Path, output_root: Path) -> ConversionResult:
    output = output_path_for(source, input_root, output_root, ".txt")
    try:
        with zipfile.ZipFile(source) as archive:
            chapter_names = epub_chapter_names(archive)
            parts = []
            encodings = []
            for name in chapter_names:
                raw = archive.read(name)
                decoded = decode_bytes(raw)
                encodings.append(decoded.encoding)
                text = extract_html_text(decoded.text)
                if text.strip():
                    parts.append(text)
    except Exception as exc:  # noqa: BLE001 - report and continue batch conversion.
        return ConversionResult(source, None, "epub", "", "failed", str(exc))

    text = normalize_text("\n\n".join(parts))
    output.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_text(output, text)
    encoding = "+".join(sorted(set(encodings))) if encodings else "unknown"
    status = "converted" if text else "empty"
    return ConversionResult(source, output, "epub", encoding, status)


def epub_chapter_names(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    by_name = {name: name for name in names}
    opf_name = find_opf_name(archive)
    if opf_name:
        spine_names = chapter_names_from_opf(archive, opf_name)
        if spine_names:
            return spine_names

    return sorted(
        name
        for name in names
        if PurePosixPath(name).suffix.lower() in {".xhtml", ".html", ".htm", ".txt"}
        and not name.endswith("/")
        and by_name.get(name)
    )


def find_opf_name(archive: zipfile.ZipFile) -> str | None:
    try:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    except Exception:
        return None
    for elem in container.iter():
        if elem.tag.endswith("rootfile"):
            full_path = elem.attrib.get("full-path")
            if full_path:
                return full_path
    return None


def chapter_names_from_opf(archive: zipfile.ZipFile, opf_name: str) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read(opf_name))
    except Exception:
        return []

    manifest: dict[str, tuple[str, str]] = {}
    spine_ids: list[str] = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "item":
            item_id = elem.attrib.get("id")
            href = elem.attrib.get("href")
            media_type = elem.attrib.get("media-type", "")
            if item_id and href:
                manifest[item_id] = (href, media_type)
        elif tag == "itemref":
            item_id = elem.attrib.get("idref")
            if item_id:
                spine_ids.append(item_id)

    opf_dir = PurePosixPath(opf_name).parent
    chapters = []
    for item_id in spine_ids:
        href_media = manifest.get(item_id)
        if not href_media:
            continue
        href, media_type = href_media
        suffix = PurePosixPath(href).suffix.lower()
        if suffix not in {".xhtml", ".html", ".htm", ".txt"} and "html" not in media_type:
            continue
        normalized = str(opf_dir / href).replace("%20", " ")
        if normalized in archive.namelist():
            chapters.append(normalized)
    return chapters


def extract_html_text(text: str) -> str:
    extractor = TextExtractor()
    try:
        extractor.feed(text)
        extracted = extractor.text()
    except Exception:
        extracted = re.sub(r"<[^>]+>", "\n", text)
    extracted = re.sub(r"[ \t\f\v]+", " ", extracted)
    extracted = re.sub(r" *\n *", "\n", extracted)
    return extracted


def write_utf8_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def iter_books(input_root: Path) -> Iterable[Path]:
    for path in sorted(input_root.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        suffix = path.suffix.lower()
        if suffix in TEXT_SUFFIXES or suffix in EPUB_SUFFIXES:
            yield path


def write_report(results: list[ConversionResult], output_root: Path, input_root: Path) -> None:
    report_path = output_root / "_conversion_report.csv"
    index_path = output_root / "_index.md"

    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["status", "kind", "encoding", "source", "output", "message"])
        for result in results:
            writer.writerow(
                [
                    result.status,
                    result.kind,
                    result.encoding,
                    str(result.source),
                    str(result.output or ""),
                    result.message,
                ]
            )

    converted = [result for result in results if result.output and result.status in {"converted", "empty"}]
    lines = [
        "# UTF-8 Ebook Index",
        "",
        f"- Source: `{input_root}`",
        f"- Converted books: {sum(result.status == 'converted' for result in results)}",
        f"- Empty outputs: {sum(result.status == 'empty' for result in results)}",
        f"- Failed: {sum(result.status == 'failed' for result in results)}",
        f"- Report: `./{report_path.name}`",
        "",
        "## Books",
        "",
    ]
    for result in converted:
        assert result.output is not None
        title = result.output.stem
        relative = result.output.relative_to(output_root).as_posix()
        source_relative = result.source.relative_to(input_root).as_posix()
        lines.append(f"- [{title}]({relative}) `{result.encoding}` - `{source_relative}`")

    write_utf8_text(index_path, "\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/Volumes/书籍/Ebook"))
    parser.add_argument("--output", type=Path, default=Path("/Volumes/书籍/Ebook_UTF8"))
    parser.add_argument("--limit", type=int, default=0, help="Convert only the first N books.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_root = args.input.expanduser().resolve()
    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    books = list(iter_books(input_root))
    if args.limit:
        books = books[: args.limit]

    results: list[ConversionResult] = []
    for index, source in enumerate(books, 1):
        suffix = source.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            result = convert_txt(source, input_root, output_root)
        elif suffix in EPUB_SUFFIXES:
            result = convert_epub(source, input_root, output_root)
        else:
            continue
        results.append(result)
        print(
            f"[{index}/{len(books)}] {result.status}: {source} -> {result.output or '-'} ({result.encoding})",
            flush=True,
        )

    write_report(results, output_root, input_root)
    converted = sum(result.status == "converted" for result in results)
    failed = sum(result.status == "failed" for result in results)
    empty = sum(result.status == "empty" for result in results)
    print(f"\nDone. converted={converted} empty={empty} failed={failed}", flush=True)
    print(f"Output: {output_root}", flush=True)
    print(f"Index: {output_root / '_index.md'}", flush=True)
    print(f"Report: {output_root / '_conversion_report.csv'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
