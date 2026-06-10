"""判官校准跑批：把真榜单书章节喂进自家判官，校验判官刻度。

榜单对标闭环 P0.3（docs/榜单对标闭环-总体方案与修改计划-20260610.md）。

三个测试臂：
  - t1   — 23 本头部榜单校对版全本（《亵渎》《大奉打更人》《将夜》…）
  - t2   — 17 本题材补充完本
  - fw   — 框架近期生成的章节（output/<book>/chapter-NNN.md）

每章跑两类评估：
  - 确定性（免费）：AI 味检测 ``ai_flavor.detect``（overall_score，分高=AI味重）
  - LLM 判官（可选 --no-llm 跳过）：LitStyle 文采判官 final_score（分高=文采好）

校准判定：如果 T1 真书的判官中位分不显著高于框架产出，判官刻度失真，
进入 P1.0 修刻度；所有后续 Arena win-rate 才有意义。

用法：
  python scripts/benchmark_judge_calibration.py --no-llm           # 先跑免费部分
  python scripts/benchmark_judge_calibration.py --chapters 1,10,50 # 全量
  python scripts/benchmark_judge_calibration.py --limit 5          # 每臂限5本试跑
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.services.ai_flavor import detect as detect_ai_flavor  # noqa: E402
from bestseller.services.benchmark_corpus import (  # noqa: E402
    BenchmarkBook,
    load_benchmark_chapter,
    load_benchmark_corpus,
)
from bestseller.services.judge_genre_context import resolve_judge_genre_context  # noqa: E402
from bestseller.services.litstyle_prose import (  # noqa: E402
    detect_ai_tone,
    load_litstyle_config,
)
from bestseller.services.litstyle_prose_judge import (  # noqa: E402
    build_litstyle_system_prompt,
    build_litstyle_user_prompt,
    litstyle_result_from_mapping,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
CRITIC = SETTINGS.llm.critic
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")
LITSTYLE_CONFIG = load_litstyle_config()

# 题材 → judge genre context 的粗映射（真书只有 canonical_category）
_CATEGORY_GENRE = {
    "action-progression": ("玄幻", None),
    "strategy-worldbuilding": ("历史", None),
    "base-building": ("科幻", None),
    "esports-competition": ("都市", None),
    "suspense-mystery": ("悬疑", None),
    "otherworld-cross-system": ("玄幻", None),
    "relationship-driven": ("都市", None),
    "eastern-aesthetic": ("仙侠", None),
}


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


async def _litstyle_judge(text: str, category: str) -> dict[str, Any] | None:
    genre, sub = _CATEGORY_GENRE.get(category, (None, None))
    genre_context = resolve_judge_genre_context(genre=genre, sub_genre=sub)
    ai_tone = detect_ai_tone(text, LITSTYLE_CONFIG)
    system = build_litstyle_system_prompt(config=LITSTYLE_CONFIG, genre_context=genre_context)
    user = build_litstyle_user_prompt(chapter_number=1, content_md=text, ai_tone=ai_tone)
    try:
        response = await litellm.acompletion(
            model=CRITIC.model,
            api_base=CRITIC.api_base,
            api_key=CRITIC_KEY,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            # reasoning 模型的思考计入 max_tokens，给足余量防 content 截断
            max_tokens=6144,
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 — batch job must survive single failures
        print(f"    litstyle judge failed: {type(exc).__name__}: {exc}")
        return None
    message = response.choices[0].message
    raw = (message.content or "").strip() or (
        getattr(message, "reasoning_content", None) or ""
    ).strip()
    result = litstyle_result_from_mapping(
        _parse_json_object(raw),
        config=LITSTYLE_CONFIG,
        ai_tone_prior=ai_tone.deterministic_penalty,
        ai_tone_flagged=ai_tone.flagged,
    )
    if "LITSTYLE_JUDGE_UNAVAILABLE" in result.top_issues:
        return None
    return {
        "final_score": result.final_score,
        "dimension_scores": dict(result.dimension_scores),
        "ai_tone_penalty": result.ai_tone_penalty,
    }


def _evaluate_deterministic(text: str) -> dict[str, Any]:
    report = detect_ai_flavor(text, language="zh-CN")
    tone = detect_ai_tone(text, LITSTYLE_CONFIG)
    return {
        "chars": len(text),
        "ai_flavor_score": report.overall_score,
        "ai_flavor_block_spans": len(report.block_spans),
        "ai_flavor_warn_spans": len(report.warn_spans),
        "litstyle_ai_tone_penalty": tone.deterministic_penalty,
        "litstyle_ai_tone_flagged": tone.flagged,
    }


def _framework_chapters(framework_dirs: list[str], chapter_numbers: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for book_dir in framework_dirs:
        base = Path(book_dir)
        if not base.is_dir():
            continue
        for chapter_no in chapter_numbers:
            path = base / f"chapter-{chapter_no:03d}.md"
            if not path.is_file():
                continue
            rows.append(
                {
                    "arm": "fw",
                    "book": base.name,
                    "category": "framework",
                    "chapter": chapter_no,
                    "text": path.read_text(encoding="utf-8"),
                }
            )
    return rows


def _real_chapters(
    books: tuple[BenchmarkBook, ...], arm: str, chapter_numbers: list[int], limit: int | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for book in books[: limit or len(books)]:
        for chapter_no in chapter_numbers:
            body = load_benchmark_chapter(book, chapter_no)
            if not body or len(body) < 800:
                continue
            rows.append(
                {
                    "arm": arm,
                    "book": book.title_key,
                    "category": book.category,
                    "chapter": chapter_no,
                    "text": body,
                }
            )
    return rows


def _summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.fmean(values), 2),
        "p25": round(statistics.quantiles(values, n=4)[0], 2) if len(values) >= 4 else None,
        "p75": round(statistics.quantiles(values, n=4)[2], 2) if len(values) >= 4 else None,
    }


def _render_report(results: list[dict[str, Any]], out_dir: Path, llm_enabled: bool) -> str:
    lines = ["# 判官校准报告（P0.3）", ""]
    lines.append(f"- 样本数：{len(results)}（按臂：" + "、".join(
        f"{arm}={sum(1 for r in results if r['arm'] == arm)}" for arm in ("t1", "t2", "fw")
    ) + "）")
    lines.append("")
    metric_keys = ["ai_flavor_score", "litstyle_ai_tone_penalty"]
    if llm_enabled:
        metric_keys.append("litstyle_final_score")
    for metric in metric_keys:
        lines.append(f"## {metric}")
        direction = "分高=AI味重（真书应更低）" if "ai" in metric else "分高=文采好（真书应更高）"
        lines.append(f"_{direction}_")
        lines.append("")
        lines.append("| 臂 | n | 中位 | 均值 | p25 | p75 |")
        lines.append("|---|---|---|---|---|---|")
        for arm in ("t1", "t2", "fw"):
            arm_rows = [r for r in results if r["arm"] == arm]
            s = _summary(arm_rows, metric)
            if s["n"] == 0:
                lines.append(f"| {arm} | 0 | - | - | - | - |")
            else:
                lines.append(
                    f"| {arm} | {s['n']} | {s['median']} | {s['mean']} | {s.get('p25')} | {s.get('p75')} |"
                )
        lines.append("")
    # 校准判定
    lines.append("## 校准判定")
    if llm_enabled:
        t1 = _summary([r for r in results if r["arm"] == "t1"], "litstyle_final_score")
        fw = _summary([r for r in results if r["arm"] == "fw"], "litstyle_final_score")
        if t1.get("n") and fw.get("n"):
            verdict = (
                "✅ T1 真书中位分高于框架产出 — 判官方向正确"
                if t1["median"] > fw["median"]
                else "❌ T1 真书中位分未高于框架产出 — 判官刻度失真，进入 P1.0 修刻度"
            )
            lines.append(f"- LitStyle: T1 中位 {t1['median']} vs FW 中位 {fw['median']} → {verdict}")
    t1_flavor = _summary([r for r in results if r["arm"] == "t1"], "ai_flavor_score")
    fw_flavor = _summary([r for r in results if r["arm"] == "fw"], "ai_flavor_score")
    if t1_flavor.get("n") and fw_flavor.get("n"):
        verdict = (
            "✅ 真书 AI 味低于框架产出 — 检测器方向正确"
            if t1_flavor["median"] < fw_flavor["median"]
            else "⚠️ 真书 AI 味分不低于框架产出 — AI 味检测器误杀真人文风，需要调规则"
        )
        lines.append(
            f"- AI味: T1 中位 {t1_flavor['median']} vs FW 中位 {fw_flavor['median']} → {verdict}"
        )
    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapters", default="1,10,50", help="逗号分隔章位")
    parser.add_argument("--limit", type=int, default=None, help="每臂最多书数")
    parser.add_argument("--no-llm", action="store_true", help="只跑确定性检测")
    parser.add_argument(
        "--framework-dirs",
        default="output/xianxia-upgrade-1781078378,output/exorcist-detective-1778051012,output/romantasy-1776330993",
        help="框架产出书目录，逗号分隔",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    chapter_numbers = [int(x) for x in args.chapters.split(",") if x.strip()]
    corpus = load_benchmark_corpus()
    if not corpus.books:
        print("FATAL: benchmark corpus registry empty — is .distillation_private present?")
        sys.exit(1)
    available = corpus.available()
    print(f"corpus: {len(corpus.books)} registered / {len(available)} available on volume")
    if not available:
        print("FATAL: /Volumes/书籍 unavailable — mount the volume first")
        sys.exit(1)

    rows = (
        _real_chapters(corpus.by_tier("t1"), "t1", chapter_numbers, args.limit)
        + _real_chapters(corpus.by_tier("t2"), "t2", chapter_numbers, args.limit)
        + _framework_chapters(args.framework_dirs.split(","), chapter_numbers)
    )
    print(f"evaluating {len(rows)} chapters (llm={'off' if args.no_llm else CRITIC.model})")

    for row in rows:
        row.update(_evaluate_deterministic(row["text"]))

    if not args.no_llm:
        semaphore = asyncio.Semaphore(args.concurrency)

        async def _judge_row(row: dict[str, Any]) -> None:
            async with semaphore:
                result = await _litstyle_judge(row["text"], row["category"])
            if result:
                row["litstyle_final_score"] = result["final_score"]
                row["litstyle_dimensions"] = result["dimension_scores"]
            print(
                f"  [{row['arm']}] {row['book'][:24]} ch{row['chapter']}: "
                f"litstyle={row.get('litstyle_final_score')} ai_flavor={row['ai_flavor_score']}"
            )

        await asyncio.gather(*(_judge_row(row) for row in rows))

    out_dir = Path("output/benchmark_calibration") / time.strftime("%Y%m%dT%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    slim = [{k: v for k, v in row.items() if k != "text"} for row in rows]
    (out_dir / "results.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    report = _render_report(slim, out_dir, llm_enabled=not args.no_llm)
    print("\n" + report)
    print(f"\nartifacts: {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
