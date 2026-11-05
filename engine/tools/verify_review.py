"""Verify chapter-scoped quotations literally; editorial claims need human review."""

import argparse
import json
from pathlib import Path
import re
import sys

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
