"""Build a reader-only Markdown package from a book directory."""

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

import yaml


INTERNAL_LINE = re.compile(
    r"^\s*(?:"
    r"\[(?:内部|检查|模型|去\s*AI|deai)[^\]]*\]|"
    r">\s*(?:内部|检查|模型|状态)[^：:]{0,12}[：:]|"
    r"(?:model|path|模型|文件路径|检查标记|内部状态)[：:]"
    r").*$",
    re.I,
)
HEADING = re.compile(r"^\s*#{1,6}\s*第\s*\d+\s*章[^\n]*\n?", re.M)


def parse_chapter_spec(value):
    if not value:
        return []
    result = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start, end = token.split("-", 1)
            if not start.isdigit() or not end.isdigit() or int(start) > int(end):
                raise ValueError(f"无效章段：{token}")
            result.update(range(int(start), int(end) + 1))
        elif token.isdigit() and int(token) > 0:
            result.add(int(token))
        else:
            raise ValueError(f"无效章段：{token}")
    return sorted(result)


def is_system_temp(path):
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    return any(path.resolve().is_relative_to(root) for root in roots)


def _title(book):
    path = book / "story_bible.yaml"
    if path.is_file():
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(value, dict) and value.get("title"):
            return str(value["title"]).strip("《》 ")
    return book.name


def _chapter_title(book, number):
    path = book / f"chapters/{number:03d}.json"
    if path.is_file() and not path.is_symlink():
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("title"):
            return str(value["title"]).strip()
    return ""


def _chapter_path(book, number):
    for relative in (
        f"final/chapter-{number:03d}.md",
        f"chapter-{number:03d}.body.md",
        f"chapter-{number:03d}.md",
    ):
        path = book / relative
        if path.is_file() and not path.is_symlink():
            return path
    return None


def _reader_body(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if text.lstrip().startswith("---"):
        text = re.sub(r"^\s*---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    text = HEADING.sub("", text, count=1)
    lines = [line for line in text.splitlines() if not INTERNAL_LINE.match(line)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def available_chapters(book):
    book = Path(book)
    numbers = set()
    for pattern in ("final/chapter-*.md", "chapter-*.body.md", "chapter-*.md"):
        for path in book.glob(pattern):
            match = re.search(r"chapter-(\d+)", path.name)
            if match:
                numbers.add(int(match[1]))
    return sorted(numbers)


def create_reading_pack(book, *, output=None, chapter_spec=None):
    book = Path(book).resolve()
    selected = parse_chapter_spec(chapter_spec) if isinstance(chapter_spec, str) else sorted(chapter_spec or [])
    selected = selected or available_chapters(book)
    if not selected:
        raise ValueError("书目录里没有可读章节")
    missing = [number for number in selected if _chapter_path(book, number) is None]
    if missing:
        raise ValueError("缺少章节：" + ", ".join(map(str, missing)))
    if output is None:
        output = Path(tempfile.mkdtemp(prefix="bestseller-review-pack-")) / "reading-pack.md"
    output = Path(output)
    if not is_system_temp(output):
        raise ValueError("阅读包只能写入系统临时目录")
    output.parent.mkdir(parents=True, exist_ok=True)
    title = _title(book)
    sections = [f"# {title}"]
    for number in selected:
        path = _chapter_path(book, number)
        body = _reader_body(path.read_text(encoding="utf-8"))
        heading = f"## 第{number}章"
        chapter_title = _chapter_title(book, number)
        if chapter_title:
            heading += " " + chapter_title
        sections.append(heading + "\n\n" + body)
    output.write_text("\n\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return {"title": title, "chapters": selected, "path": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--chapters", help="例如 1-10,15")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = create_reading_pack(args.book, output=args.output, chapter_spec=args.chapters)
    sys.stdout.write(result["path"] + "\n")


if __name__ == "__main__":
    main()
