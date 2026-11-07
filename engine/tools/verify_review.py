"""Verify chapter-scoped quotations literally; editorial claims need human review."""

import argparse
import json
from pathlib import Path
import re
import sys

import yaml

try:
    from tools.redact import Redactor
    from tools.books_guard import repository_books
except ModuleNotFoundError:
    from redact import Redactor
    from books_guard import repository_books

NUMBER = r"[0-9零〇一二两三四五六七八九十百千]+"
CHAPTER = re.compile(r"第\s*(" + NUMBER + r"(?:\s*[、，,–—至到-]\s*" + NUMBER + r")*)\s*章")
QUOTES = re.compile(r'“([^”\n]+)”|「([^」\n]+)」|『([^』\n]+)』|《([^》\n]+)》|`([^`\n]+)`|"([^"\n]+)"')


def number(value):
    value = re.sub(r"\s+", "", value)
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
    return total + current


def chapters(value):
    result = []
    for match in CHAPTER.finditer(value):
        parts = re.split(r"\s*([、，,–—至到-])\s*", match[1])
        values = [number(v) for v in parts[::2]]
        if len(values) == 2 and parts[1] in "–—至到-":
            result.extend(range(values[0], min(values[1], values[0] + 2000) + 1))
        else:
            result.extend(values)
    return list(dict.fromkeys(result))


def compact(value):
    return re.sub(r"\s+", "", value)


def sources(book, n):
    result = []
    for name in (f"final/chapter-{n:03d}.md", f"chapter-{n:03d}.body.md", f"chapter-{n:03d}.md"):
        path = book / name
        if path.is_file() and not path.is_symlink():
            result.append((name, path.read_text("utf-8")))
    path = book / f"chapters/{n:03d}.json"
    if path.is_file():
        title = json.loads(path.read_text("utf-8")).get("title", "")
        result.append(("目录", f"第{n}章 {title}"))
    return result


def _character_index(book):
    path = Path(book) / "story_bible.yaml"
    if not path.is_file():
        return {}, ""
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    characters = value.get("characters", []) if isinstance(value, dict) else []
    aliases, protagonist = {}, ""
    for item in characters:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            continue
        name = str(item["name"]).strip()
        names = [name, item.get("address", ""), *(item.get("aliases", []) or [])]
        for alias in names:
            if isinstance(alias, str) and alias.strip():
                aliases[alias.strip()] = name
        role = str(item.get("role", "")).lower()
        if not protagonist and role in ("protagonist", "main", "hero"):
            protagonist = name
    return aliases, protagonist


def _match_context(content, quote):
    normalized, positions = [], []
    for index, char in enumerate(content):
        if not char.isspace():
            normalized.append(char)
            positions.append(index)
    needle = compact(quote)
    offset = "".join(normalized).find(needle)
    if offset < 0:
        return ""
    start = positions[offset]
    end = positions[min(len(positions) - 1, offset + len(needle) - 1)] + 1
    return content[max(0, start - 160) : min(len(content), end + 40)]


def _actual_subject(content, quote, aliases):
    context = _match_context(content, quote)
    occurrences = []
    for alias, canonical in aliases.items():
        for match in re.finditer(re.escape(alias), context):
            occurrences.append((match.start(), len(alias), canonical))
    if not occurrences:
        return ""
    return max(occurrences, key=lambda item: (item[0], item[1]))[2]


def _book_chapters(book):
    numbers = set()
    for pattern in ("final/chapter-*.md", "chapter-*.body.md", "chapter-*.md", "chapters/*.json"):
        for path in Path(book).glob(pattern):
            match = re.search(r"(?:chapter-)?(\d+)", path.name)
            if match:
                numbers.add(int(match[1]))
    return sorted(numbers)


def verify_claims(book, claims):
    """Classify structured claims by chapter, literal quote and named subject."""
    book = Path(book)
    aliases, protagonist = _character_index(book)
    chapter_sources = {number: sources(book, number) for number in _book_chapters(book)}
    rows = []
    for claim in claims:
        row = dict(claim)
        chapter = claim.get("chapter")
        quote = str(claim.get("quote", "")).strip()
        expected = str(claim.get("subject", "")).strip()
        if expected in ("主角", "男主", "女主"):
            expected = protagonist
        cited = chapter_sources.get(chapter, []) if type(chapter) is int else []
        cited_hit = next(((name, content) for name, content in cited if compact(quote) in compact(content)), None)
        anywhere = [
            (number, name, content)
            for number, items in chapter_sources.items()
            for name, content in items
            if quote and compact(quote) in compact(content)
        ]
        status, reason, actual, source = "Evidence", "引文和章号均匹配", "", ""
        if not quote:
            status, reason = "Unknown", "结论未附引文"
        elif cited_hit is None and anywhere:
            status = "Unknown"
            reason = "引文存在，但章号不匹配：" + ", ".join(str(item[0]) for item in anywhere[:5])
        elif cited_hit is None:
            status, reason = "可能捏造", "引文在全书可用正文和目录中均不存在"
        else:
            source, content = cited_hit
            if expected:
                actual = _actual_subject(content, quote, aliases)
                if not actual:
                    status, reason = "Unknown", "引文存在，但无法确定动作指向的人物"
                elif actual != expected:
                    status, reason = "Unknown", f"人物归属不匹配：结论指向{expected}，引文指向{actual}"
        row.update(status=status, reason=reason, source=source, expected_subject=expected, actual_subject=actual)
        rows.append(row)
    return rows


def verify(book, review):
    book = Path(book)
    rows = []
    for line_number, line in enumerate(review.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        quotes = list(QUOTES.finditer(line))
        last_refs = []
        for index, quote in enumerate(quotes):
            value = next(v for v in quote.groups() if v is not None)
            before = line[: quote.start()]
            # Semicolons reset chapter scope; quoted punctuation never does.
            previous = quotes[index - 1].end() if index else 0
            scope = before[max(previous, before.rfind("；") + 1, before.rfind(";") + 1) :]
            refs = chapters(value)
            following = line[quote.end() : quotes[index + 1].start() if index + 1 < len(quotes) else len(line)]
            if not refs and re.match(r"\s*[（(]", following):
                refs = chapters(following.split("）", 1)[0].split(")", 1)[0])
            if not refs:
                matches = list(CHAPTER.finditer(scope))
                refs = chapters(matches[-1][0]) if matches else last_refs
            last_refs = refs
            if not refs:
                rows.append(dict(line=line_number, chapter=None, quote=value, status="无引文", source="缺少章号"))
            for n in refs:
                alternatives = value.split("/")
                hit = next(
                    (
                        name + (f"（选项：{part}）" if len(alternatives) > 1 else "")
                        for name, content in sources(book, n)
                        if not ("章名" in line or quote.group(4) or chapters(value)) or name == "目录"
                        for part in alternatives
                        if compact(part) and compact(part) in compact(content)
                    ),
                    "",
                )
                rows.append(
                    dict(line=line_number, chapter=n, quote=value, status="核实" if hit else "找不到", source=hit)
                )
        if not quotes:
            for n in chapters(line):
                rows.append(dict(line=line_number, chapter=n, quote="", status="无引文", source="需人工核对事件判断"))
    return rows


def table(rows):
    lines = ["| 评审行 | 章 | 引文 | 结果 | 位置 / 说明 |", "|---|---|---|---|---|"]
    for row in rows:
        cell = lambda v: str(v if v is not None else "—").replace("|", "\\|").replace("\n", " ")
        lines.append("| " + " | ".join(cell(row[k]) for k in ("line", "chapter", "quote", "status", "source")) + " |")
    missing = {r["line"] for r in rows if r["status"] == "找不到"}
    lines.append(f"\n找不到：{sum(r['status'] == '找不到' for r in rows)} 处引文，涉及 {len(missing)} 条评审。")
    lines.append("核实仅表示引文存在（忽略空白），不代表对评审判断的认可；无引文需人工核对。")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("--env", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    scrub = Redactor(*(args.env or [p.parent / ".env" for p in repository_books()]))
    result = scrub(table(verify(args.book, args.review.read_text("utf-8"))))
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
