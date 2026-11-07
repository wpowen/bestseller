"""Verify structured external-review claims and render an evidence-separated report."""

import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile

try:
    from tools.review_pack import is_system_temp
    from tools.review_runner import review_book
    from tools.verify_review import verify_claims
except ModuleNotFoundError:
    from review_pack import is_system_temp
    from review_runner import review_book
    from verify_review import verify_claims


SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_LABEL = {"critical": "致命", "high": "高", "medium": "中", "low": "低"}


def _cell(value):
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _sorted(rows):
    return sorted(rows, key=lambda row: (SEVERITY.get(row.get("severity"), 9), row.get("chapter") or 10**9))


def _issue_table(rows):
    lines = [
        "| 严重度 | 章 | 结论 | 引文 | 核对说明 |",
        "|---|---:|---|---|---|",
    ]
    for row in _sorted(rows):
        values = (
            SEVERITY_LABEL.get(row.get("severity"), row.get("severity", "—")),
            row.get("chapter"),
            row.get("conclusion"),
            row.get("quote"),
            row.get("reason"),
        )
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    return "\n".join(lines) if rows else "无。"


def _inference(review):
    lines = []
    impressions = review.get("impressions", [])
    if impressions:
        lines.append("### 无引文印象")
        for item in impressions:
            segment = item.get("segment", [])
            scope = f"第{segment[0]}–{segment[-1]}章" if segment else "未标章段"
            lines.append(
                f"- [{_cell(item.get('perspective'))} / {scope}] {_cell(item.get('conclusion'))}"
                f"（{_cell(item.get('reason'))}）"
            )
    abandon = Counter(
        item.get("abandon_chapter")
        for item in review.get("reviews", [])
        if item.get("perspective") == "reader" and type(item.get("abandon_chapter")) is int
    )
    lines.extend(("### 读者弃书点分布", "| 章 | 次数 |", "|---:|---:|"))
    if abandon:
        lines.extend(f"| {chapter} | {count} |" for chapter, count in sorted(abandon.items()))
    else:
        lines.append("| — | 0 |")
    memories = []
    seen = set()
    for item in review.get("reviews", []):
        for character in item.get("remembered_characters", []):
            if not isinstance(character, dict):
                continue
            value = f"{character.get('name', '').strip()}：{character.get('description', '').strip()}".strip("：")
            if value and value not in seen:
                memories.append(value)
                seen.add(value)
    lines.append("### 人物记忆复述")
    lines.extend(f"- {value}" for value in memories) if memories else lines.append("无。")
    return "\n".join(lines)


def render_report(review, verified):
    reviewer = review.get("reviewer", {})
    source_note = "同源判官，可能高估" if reviewer.get("same_family") else "判官与写手不同家族"
    evidence = [row for row in verified if row.get("status") == "Evidence"]
    unknown = [row for row in verified if row.get("status") == "Unknown"]
    fabricated = [row for row in verified if row.get("status") == "可能捏造"]
    lines = [
        f"# 外部评审核对 · {review.get('book_title', '未命名书稿')}",
        "",
        f"判官：{_cell(reviewer.get('model'))}（{_cell(reviewer.get('family'))}；{source_note}）。",
        f"核对结果：Evidence {len(evidence)}；Unknown {len(unknown)}；可能捏造 {len(fabricated)}。",
        "本报告不修改书稿，也不自动转成开发需求；程序核对不代替读者验收。",
        "",
        "## Evidence（已核引文）",
        "",
        _issue_table(evidence),
        "",
        "## Inference（评审印象与分布）",
        "",
        _inference(review),
        "",
        "## Unknown（对不上或无法确认）",
        "",
        _issue_table(unknown),
        "",
        "## 可能捏造",
        "",
        _issue_table(fabricated),
    ]
    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(book, env, *, output=None, chapter_spec=None, segment_chapters=10, client_factory=None):
    output = Path(output or tempfile.mkdtemp(prefix="bestseller-external-review-")).resolve()
    if not is_system_temp(output):
        raise ValueError("评审输出必须位于系统临时目录")
    kwargs = {}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    review = review_book(
        book,
        env,
        output=output,
        chapter_spec=chapter_spec,
        segment_chapters=segment_chapters,
        **kwargs,
    )
    verified = verify_claims(book, review["claims"])
    (output / "verified-claims.json").write_text(
        json.dumps(verified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = render_report(review, verified)
    (output / "review-report.md").write_text(report, encoding="utf-8")
    return {"output": str(output), "review": review, "verified": verified, "report": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--chapters")
    parser.add_argument("--segment-chapters", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_pipeline(
        args.book,
        args.env,
        output=args.output,
        chapter_spec=args.chapters,
        segment_chapters=args.segment_chapters,
    )
    print(result["output"])


if __name__ == "__main__":
    main()
