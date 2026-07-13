"""Batch-test project-card-first logline generation across Chinese web-fiction genres."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from json_repair import loads as repair_json

from bestseller.infra.db.session import session_scope
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import load_settings


ROOT = Path(__file__).resolve().parents[1]
GENRES = (
    ("xianxia", "仙侠升级"),
    ("urban_career", "都市职业"),
    ("mystery", "悬疑推理"),
    ("folk_horror", "民俗诡事"),
    ("science_fiction", "近未来科幻"),
    ("historical", "历史权谋"),
    ("apocalypse", "末日生存"),
    ("female_growth", "女性成长与成年人关系"),
    ("fantasy", "东方奇幻冒险"),
    ("campus", "校园青春"),
    ("crime", "犯罪与社会派悬疑"),
    ("ancient_romance", "古言家宅与女性谋略"),
)

GENERATOR_SYSTEM = """你是一位克制、聪明的中文类型小说策划人。先证明项目成立，再写一句话。
禁止先想华丽钩子再倒填故事；禁止把失忆、折寿、扣命、命债当成默认深度；没有超能力使用费完全合法。
每个候选必须是同一条因果链：特定人物在具体压力下，为可观察目标行动，遭遇有利益和能力的阻力，
失败与成功分别产生不同后果，选择造成不可逆变化，并能生成不重复的场景。人物和对手都按正常智力行动。
只输出合法 JSON。"""

GENERATOR_USER = """为【{genre}】生成 {count} 个彼此显著不同的长篇/连载候选项目。
每项必须包含：
id, protagonist_situation, observable_goal, obstacle, failure_stakes, success_tradeoff,
irreversible_change, reader_promise, differentiation, rational_choice_reason,
scene_prototypes（3个不同场景）, escalation_steps（3级）, logline。

硬要求：
1. logline 45至90个汉字，写清谁、要做什么、谁/什么阻止他、最尖锐的两难或未知；不得用抽象宣传语。
2. failure_stakes 是失败/退出会失去什么；success_tradeoff 是成功会伤害、暴露或放弃什么。
3. 如果有超自然能力，能力费必须能从机制因果推出且无法靠停用、代劳、记录规避；不需要时不要设置能力费。
4. 三个场景不能只是换地点重复触发同一种能力；升级必须来自对手反应、证据变化、关系变化或选择后果。
5. 不得漂移出【{genre}】。不要输出解释。

JSON：{{"genre":"{genre}","candidates":[{{...}}]}}"""

JUDGE_SYSTEM = """你是敌对性小说选题委员会。只判断项目是否真的成立，不奖励句子华丽、惨痛代价、
格式齐全或术语复杂。一次正常沟通、停止使用能力、让别人代劳、做备忘录即可解决的问题必须淘汰。
一句话中的每个卖点必须能在项目卡找到因果支撑。只输出合法 JSON。"""

JUDGE_USER = """审查以下【{genre}】候选。先逐项硬淘汰，再评分，不能用总分掩盖短板。

硬淘汰条件：
- 说不清谁在做什么；阻力可被一次正常沟通轻易解除；人物可无损退出；
- 没有具体失败后果或成功取舍；能力费是随机系统收税；明显存在更安全低成本方案却无理由不用；
- 三个场景本质重复；冲突只能靠新增设定续命；题材漂移；logline 的关键信息在项目卡无支撑。

对每项输出：id, hard_pass, rejection_reasons, unsupported_logline_claims，及1至5分 scores：
action_conflict, reader_promise, character_change, scene_generation, decision_rationality,
causal_coherence, genre_fidelity。action_conflict、reader_promise、scene_generation 任一低于3不得通过。
最后给 verdict（PASS/REJECT）和 concise_reason。

JSON：{{"genre":"{genre}","reviews":[{{...}}]}}

候选：
{payload}"""


def _json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("response does not contain a JSON object")
    payload = text[start : end + 1]
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = repair_json(payload)
    if not isinstance(parsed, dict):
        raise ValueError("response JSON root must be an object")
    return parsed


async def _call(session: Any, settings: Any, *, role: str, system: str, user: str) -> str:
    fallback = json.dumps({"error": "MODEL_FALLBACK"}, ensure_ascii=False)
    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role=role,
            model_tier="strong",
            system_prompt=system,
            user_prompt=user,
            fallback_response=fallback,
            prompt_template="project_card_logline_batch",
            prompt_version="v1",
            max_tokens_override=6000,
        ),
    )
    text = str(getattr(result, "content", None) or "").strip()
    if not text or text == fallback:
        raise RuntimeError("model returned fallback")
    return text


async def _run_genre(
    settings: Any,
    semaphore: asyncio.Semaphore,
    key: str,
    genre: str,
    count: int,
) -> dict[str, Any]:
    async with semaphore:
        async with session_scope(settings) as session:
            generated = _json_object(
                await _call(
                    session,
                    settings,
                    role="planner",
                    system=GENERATOR_SYSTEM,
                    user=GENERATOR_USER.format(genre=genre, count=count),
                )
            )
            candidates = generated.get("candidates") or []
            judged = _json_object(
                await _call(
                    session,
                    settings,
                    role="critic",
                    system=JUDGE_SYSTEM,
                    user=JUDGE_USER.format(
                        genre=genre,
                        payload=json.dumps(candidates, ensure_ascii=False),
                    ),
                )
            )
    reviews = {
        str(item.get("id")): item for item in judged.get("reviews") or []
    }
    return {
        "key": key,
        "genre": genre,
        "candidates": [
            {**candidate, "review": reviews.get(str(candidate.get("id")), {})}
            for candidate in candidates
        ],
    }


def _render_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# 项目卡优先的一句话钩子批量验证",
        "",
        f"生成时间：{datetime.now(UTC).isoformat()}",
        "",
    ]
    total = passed = 0
    for group in results:
        candidates = group.get("candidates") or []
        group_passed = sum(
            1 for item in candidates if (item.get("review") or {}).get("hard_pass") is True
        )
        total += len(candidates)
        passed += group_passed
        lines.extend(
            [
                f"## {group['genre']}",
                "",
                f"通过：{group_passed}/{len(candidates)}",
                "",
            ]
        )
        for index, item in enumerate(candidates, 1):
            review = item.get("review") or {}
            status = "PASS" if review.get("hard_pass") is True else "REJECT"
            lines.extend(
                [
                    f"### {index}. [{status}] {item.get('logline', '')}",
                    "",
                    f"- 主角与处境：{item.get('protagonist_situation', '')}",
                    f"- 当前目标：{item.get('observable_goal', '')}",
                    f"- 主要阻力：{item.get('obstacle', '')}",
                    f"- 失败代价：{item.get('failure_stakes', '')}",
                    f"- 成功代价：{item.get('success_tradeoff', '')}",
                    f"- 不可逆变化：{item.get('irreversible_change', '')}",
                    f"- 读者承诺：{item.get('reader_promise', '')}",
                    f"- 裁判理由：{review.get('concise_reason', '')}",
                    f"- 淘汰项：{'；'.join(review.get('rejection_reasons') or []) or '无'}",
                    f"- 分数：{json.dumps(review.get('scores') or {}, ensure_ascii=False)}",
                    "",
                ]
            )
    lines[3:3] = [f"总计：{passed}/{total} 通过硬门", ""]
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> Path:
    settings = load_settings()
    semaphore = asyncio.Semaphore(args.concurrency)
    requested_keys = {
        key.strip() for key in args.genre_keys.split(",") if key.strip()
    }
    selected_genres = tuple(
        item for item in GENRES if not requested_keys or item[0] in requested_keys
    )
    unknown_keys = requested_keys - {key for key, _ in GENRES}
    if unknown_keys:
        raise ValueError(f"unknown genre keys: {', '.join(sorted(unknown_keys))}")
    tasks = [
        _run_genre(settings, semaphore, key, genre, args.per_genre)
        for key, genre in selected_genres
    ]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for (key, genre), item in zip(selected_genres, raw, strict=True):
        if isinstance(item, Exception):
            errors.append({"key": key, "genre": genre, "error": str(item)})
        else:
            results.append(item)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "output" / "project-card-logline-batch" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_render_markdown(results), encoding="utf-8")
    return out_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-genre", type=int, default=6)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--genre-keys",
        default="",
        help="comma-separated genre keys; empty means all genres",
    )
    print(asyncio.run(_main(parser.parse_args())))
