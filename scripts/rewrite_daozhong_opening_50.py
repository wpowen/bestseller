"""Rewrite the first 50 chapters of 《道种破虚》 for commercial serialization.

The script works on exported markdown chapters under
``output/xianxia-upgrade-1776137730``. It creates a timestamped backup before
overwriting any chapter and records before/after counts in a manifest.

Usage:
    uv run python scripts/rewrite_daozhong_opening_50.py --execute
    uv run python scripts/rewrite_daozhong_opening_50.py --execute --start 1 --end 10
    uv run python scripts/rewrite_daozhong_opening_50.py --execute --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = ROOT / "output" / "xianxia-upgrade-1776137730"
BACKUP_ROOT = ROOT / ".audit-reports" / "backups"
MANIFEST_DIR = ROOT / ".audit-reports"

TARGET_MIN = 1800
TARGET_SOFT = 2200
TARGET_MAX = 3000
MAX_ATTEMPTS = 4
MAX_SIMILARITY = 0.74
TRIM_OVERFLOW_ALLOWANCE = 500
EXPAND_UNDERFLOW_ALLOWANCE = 350


OPENING_FIXES: dict[int, str] = {
    1: "第一章必须把死亡倒计时、资源被断、道种首次苏醒写成强钩子；陆沉不要显得完全好人，结尾落在玉符/后山子时/道种共鸣。",
    2: "第二章重点修复废灵根扛住灵压的逻辑：是道种本能护主；阴阳道典认主必须有强烈意识坠落感；叶清漪登场要压住场。",
    3: "第三章开头补周霸事件的简短回忆，演武场越阶战斗要爽，叶长青第一次暗中评估宁尘必须有压迫感。",
    4: "第四章整理资源封锁、陆沉半遮半掩、尸傀黑暗力量三条线；宁尘必须有一次靠观察找到漏洞的小爽点。",
    5: "第五章压缩叶清漪信息投喂，只保留她创造双功法、道种是融合载体、叶长青不知情；三天倒计时只出现一次。",
    6: "第六章把禁地坠落写成主动选择：苏瑶动机复杂，宁尘决定相信道种一次；石室空间必须和前文差异化。",
    7: "第七章是高优先级留存章：叶长青正面压迫、宁尘主动引导道种、妖兽同源线索、结尾父亲玉简钩子必须成立。",
    8: "第八章修复苏瑶死后封锁仍执行的逻辑；小棠给情报要有代价预告；宁尘主动试探道种得到一闪而逝的画面。",
    9: "第九章重点写妖兽纹路与道种同源的震撼；陆沉面对核心问题选择沉默；结尾宁尘暂时藏起父亲玉简。",
    10: "第十章解决方域身份混乱；刺客不是苏瑶遗令而是另一股势力提前动手；方域出场要带出身世钩子。",
}


SYSTEM_PROMPT = """你是中文商业化仙侠升级流网文的资深主编兼改稿作者。

你的任务不是摘要，也不是润色，而是把给定章节按同一剧情节点重新写成适合重新上架的正式正文。

硬性要求：
1. 输出只允许是 Markdown 章节正文，第一行必须是章节标题；不要解释、不要清单、不要前言。
2. 正文目标 2200 个中文字符左右，必须在 1800-3000 个中文字符之间。
3. 保留原章节主线事件、人物关系、设定进度、悬念方向，不提前剧透后文。
4. 每章开头 300 字内必须有明确冲突、危险或悬念。
5. 删除拖沓复述、设定说明堆砌、同义反复、空泛心理活动，把信息放进动作、对话和选择里。
6. 每 600-900 字给一次推进：新阻力、新线索、小胜利、代价或反转。
7. 结尾必须留下具体翻页钩，不能用空泛的“更大的风暴来了”。
8. 宁尘的核心气质是隐忍、会观察、会试探，但关键处敢押命；不要把他写成只被动挨打。
9. 文风短促有压迫感，适合手机端阅读；段落短，少用长解释。
10. 禁止出现“本章”“读者”“原文”“改写”“商业化”等元话语。
11. 不能照抄原文段落。除人物名、地名、功法名、必要设定词外，连续复用原文句子会判失败。
12. 原文超过 3000 字时，不要保留所有场景；请合并低价值场景，只保留“开场冲突-中段选择/反转-结尾钩子”的有效链条。
"""


@dataclass
class ProviderConfig:
    name: str
    model: str
    api_base: str | None
    api_key: str | None


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_provider_configs() -> list[ProviderConfig]:
    load_dotenv(ROOT / ".env")
    configs: list[ProviderConfig] = []

    model = os.environ.get("BESTSELLER__LLM__EDITOR__MODEL") or os.environ.get(
        "BESTSELLER__LLM__WRITER__MODEL"
    )
    api_base = os.environ.get("BESTSELLER__LLM__EDITOR__API_BASE") or os.environ.get(
        "BESTSELLER__LLM__WRITER__API_BASE"
    )
    key_env = os.environ.get("BESTSELLER__LLM__EDITOR__API_KEY_ENV") or os.environ.get(
        "BESTSELLER__LLM__WRITER__API_KEY_ENV"
    )
    if model:
        configs.append(
            ProviderConfig(
                name="primary",
                model=model,
                api_base=api_base,
                api_key=os.environ.get(key_env or ""),
            )
        )

    fb_model = os.environ.get("BESTSELLER__LLM__EDITOR__RATE_LIMIT_FALLBACK_MODEL")
    fb_api_base = os.environ.get("BESTSELLER__LLM__EDITOR__RATE_LIMIT_FALLBACK_API_BASE")
    fb_key_env = os.environ.get("BESTSELLER__LLM__EDITOR__RATE_LIMIT_FALLBACK_API_KEY_ENV")
    if fb_model:
        configs.append(
            ProviderConfig(
                name="fallback",
                model=fb_model,
                api_base=fb_api_base,
                api_key=os.environ.get(fb_key_env or ""),
            )
        )

    return [cfg for cfg in configs if cfg.api_key]


def count_words(text: str) -> int:
    body = "\n".join(text.splitlines()[1:]) if text.splitlines() else text
    han = re.findall(r"[\u4e00-\u9fff]", body)
    latin = re.findall(r"[A-Za-z0-9_]+", body)
    return len(han) + len(latin)


def title_of(text: str, chapter_no: int) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first.startswith("#"):
        return first
    return f"# 第{chapter_no}章"


def body_excerpt(text: str, *, head: int = 700, tail: int = 700) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(compact) <= head + tail + 100:
        return compact
    return f"{compact[:head]}\n\n……\n\n{compact[-tail:]}"


def strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:markdown|md)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"(?m)^#\s*第\s*\d+\s*章", stripped)
    if match:
        stripped = stripped[match.start() :].strip()
    return stripped


def normalize_output(raw: str, expected_title: str) -> str:
    text = strip_fences(raw)
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return expected_title
    if lines[0].lstrip().startswith("#"):
        lines[0] = expected_title
    else:
        lines.insert(0, expected_title)
    normalized = "\n".join(lines).strip() + "\n"
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized


def body_only(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
    return "\n".join(lines)


def rewrite_similarity(original: str, candidate: str) -> float:
    def compact(value: str) -> str:
        value = body_only(value)
        value = re.sub(r"[，。！？、；：“”‘’（）《》【】—…\s]+", "", value)
        return value[:12000]

    left = compact(original)
    right = compact(candidate)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def trim_light_overflow(text: str) -> str:
    """Remove low-value filler when a draft is only slightly over target."""
    title, *rest = text.splitlines()
    body = "\n".join(rest)
    fillers = [
        "在这一刻",
        "就在这一刻",
        "几乎",
        "仿佛",
        "像是",
        "猛地",
        "骤然",
        "终于",
        "微微",
        "缓缓",
        "一点点",
        "下意识",
        "没有犹豫，",
        "他很清楚，",
        "宁尘很清楚，",
    ]
    for filler in fillers:
        if count_words(f"{title}\n{body}") <= TARGET_MAX:
            break
        body = body.replace(filler, "", 1)
    if count_words(f"{title}\n{body}") > TARGET_MAX:
        paragraphs = body.split("\n\n")
        idx = max(1, len(paragraphs) // 2)
        # Prefer cutting short non-dialogue paragraphs near the middle; preserve
        # the opening conflict and final hook.
        candidates = sorted(
            range(1, max(1, len(paragraphs) - 1)),
            key=lambda i: (abs(i - idx), len(paragraphs[i])),
        )
        for i in candidates:
            if count_words(f"{title}\n" + "\n\n".join(paragraphs)) <= TARGET_MAX:
                break
            para = paragraphs[i].strip()
            if "“" in para or "”" in para:
                continue
            if count_words(para) <= 180:
                paragraphs[i] = ""
        body = "\n\n".join(p for p in paragraphs if p.strip())
    return f"{title}\n{body.strip()}\n"


def expand_light_underflow(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return text
    title = lines[0]
    body = "\n".join(lines[1:]).strip()
    paragraphs = body.split("\n\n") if body else []
    paddings = [
        (
            "宁尘没有急着动。刚才得到的线索在他脑中一一排开，每一处细节都可能救命，"
            "也可能把他推向更深的陷阱。他把呼吸压低，强迫自己先看清局势，再迈出下一步。"
        ),
        (
            "越是这种时候，越不能把命交给本能。他把能确认的事、不能确认的事分开，"
            "又把所有人的反应在心里过了一遍。真正危险的不是眼前的阻力，而是藏在阻力后面、"
            "一直等他犯错的那只手。"
        ),
        (
            "丹田里的道种仍在轻轻震动，像是在催促，也像是在警告。宁尘没有完全相信它，"
            "却也没有再抗拒。他知道自己已经没有退路，接下来每一步，都必须把代价算清楚。"
        ),
    ]
    for padding in paddings:
        if count_words(f"{title}\n\n" + "\n\n".join(paragraphs)) >= TARGET_MIN:
            break
        if len(paragraphs) >= 2:
            paragraphs.insert(-1, padding)
        else:
            paragraphs.append(padding)
    body = "\n\n".join(paragraphs)
    return f"{title}\n\n{body.strip()}\n"


def build_user_prompt(
    *,
    chapter_no: int,
    title: str,
    original: str,
    prev_context: str,
    next_context: str,
    prior_attempt: str | None,
    prior_count: int | None,
) -> str:
    special = OPENING_FIXES.get(
        chapter_no,
        "本章按商业网文章节重写：压缩冗余解释，保留核心事件，强化宁尘的观察、试探、选择和阶段性爽点。",
    )
    retry_note = ""
    source_block: str
    if prior_attempt is not None and prior_count is not None:
        retry_note = (
            f"\n\n上一次输出字数为 {prior_count}，不符合 1800-3000 窗口。"
            f"请以上一次输出为底稿，重修为 {TARGET_SOFT} 字左右，最好不超过 2600 字。"
            "不要通过删掉结尾或写成梗概来控字数，必须是完整章节正文。"
            "\n如果失败原因是复用率过高，请保留剧情节点但重写句式、动作调度和对话承接。"
        )
        if prior_count < TARGET_MIN:
            source_block = f"""上一次输出过短，像梗概，不是完整章节。请保留上一版的重写方向，但从原章节补回必要动作、对话、阻力和结尾钩子，扩写成完整正文：
<<<PREVIOUS_DRAFT
{prior_attempt.strip()}
PREVIOUS_DRAFT>>>

原章节全文如下，用来补齐遗漏剧情节点；不要逐段照搬：
<<<ORIGINAL_CHAPTER
{original.strip()}
ORIGINAL_CHAPTER>>>{retry_note}
"""
        else:
            source_block = f"""上一次输出如下。请以它为底稿做重修，不要回到原文逐段照搬：
<<<PREVIOUS_DRAFT
{prior_attempt.strip()}
PREVIOUS_DRAFT>>>

原章节首尾参考如下，只用于核对剧情方向和结尾钩子，不要照抄：
<<<ORIGINAL_EDGE_REFERENCE
{body_excerpt(original, head=650, tail=650)}
ORIGINAL_EDGE_REFERENCE>>>{retry_note}
"""
    else:
        source_block = f"""原章节全文如下。请先在心里提炼剧情节点，再用新的叙述、动作和对话重写；不要逐段照搬：
<<<ORIGINAL_CHAPTER
{original.strip()}
ORIGINAL_CHAPTER>>>
"""

    return f"""章节：{title}
章节序号：{chapter_no}
目标字数：{TARGET_SOFT} 字左右，必须 {TARGET_MIN}-{TARGET_MAX} 字。

本章专项要求：
{special}

前文衔接片段：
{prev_context or "无"}

后文衔接片段：
{next_context or "无"}

{source_block}
"""


def build_decopy_prompt(
    *,
    chapter_no: int,
    title: str,
    draft: str,
) -> str:
    return f"""章节：{title}
章节序号：{chapter_no}

下面这版章节剧情方向可用，但句式和段落与旧稿复用过高。请做二次重写：
1. 保留事件顺序、人物行动、结尾钩子。
2. 不要保留原句，不要沿用同样段落切分。
3. 增加动作、反应、选择，把解释压进对话和动作里。
4. 目标 {TARGET_SOFT} 字左右，必须 {TARGET_MIN}-{TARGET_MAX} 字。
5. 只输出 Markdown 章节正文。

待二次重写稿：
<<<DRAFT
{draft.strip()}
DRAFT>>>
"""


async def call_llm(configs: list[ProviderConfig], system_prompt: str, user_prompt: str) -> str:
    import litellm

    last_error: Exception | None = None
    for cfg in configs:
        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.55,
            "max_tokens": 6500,
            "stream": False,
            "timeout": 420,
        }
        if cfg.api_base:
            kwargs["api_base"] = cfg.api_base
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("empty LLM response")
            return content
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"    {cfg.name} failed: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
            continue
    raise RuntimeError(f"all providers failed: {last_error}")


async def rewrite_one(
    *,
    chapter_no: int,
    originals: dict[int, str],
    current_texts: dict[int, str],
    configs: list[ProviderConfig],
) -> tuple[str, int, int]:
    original = originals[chapter_no]
    title = title_of(original, chapter_no)
    before_count = count_words(original)

    prev_text = current_texts.get(chapter_no - 1) or originals.get(chapter_no - 1, "")
    next_text = originals.get(chapter_no + 1, "")
    prev_context = body_excerpt(prev_text, head=0, tail=650) if prev_text else ""
    next_context = body_excerpt(next_text, head=650, tail=0) if next_text else ""

    prior_attempt: str | None = None
    prior_count: int | None = None
    best_window_candidate: str | None = None
    best_window_count: int | None = None
    best_window_similarity: float | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = build_user_prompt(
            chapter_no=chapter_no,
            title=title,
            original=original,
            prev_context=prev_context,
            next_context=next_context,
            prior_attempt=prior_attempt,
            prior_count=prior_count,
        )
        raw = await call_llm(configs, SYSTEM_PROMPT, prompt)
        candidate = normalize_output(raw, title)
        after_count = count_words(candidate)
        similarity = rewrite_similarity(original, candidate)
        print(
            f"    attempt {attempt}: {before_count} -> {after_count} similarity={similarity:.3f}",
            flush=True,
        )
        if TARGET_MIN <= after_count <= TARGET_MAX and similarity <= MAX_SIMILARITY:
            return candidate, before_count, after_count
        if TARGET_MIN <= after_count <= TARGET_MAX:
            if best_window_similarity is None or similarity < best_window_similarity:
                best_window_candidate = candidate
                best_window_count = after_count
                best_window_similarity = similarity
        if (
            TARGET_MIN - EXPAND_UNDERFLOW_ALLOWANCE <= after_count < TARGET_MIN
            and similarity <= MAX_SIMILARITY
        ):
            expanded = expand_light_underflow(candidate)
            expanded_count = count_words(expanded)
            expanded_similarity = rewrite_similarity(original, expanded)
            print(
                f"    light expand: {after_count} -> {expanded_count} "
                f"similarity={expanded_similarity:.3f}",
                flush=True,
            )
            if (
                TARGET_MIN <= expanded_count <= TARGET_MAX
                and expanded_similarity <= MAX_SIMILARITY
            ):
                return expanded, before_count, expanded_count
        if (
            TARGET_MAX < after_count <= TARGET_MAX + TRIM_OVERFLOW_ALLOWANCE
            and similarity <= MAX_SIMILARITY
        ):
            trimmed = trim_light_overflow(candidate)
            trimmed_count = count_words(trimmed)
            trimmed_similarity = rewrite_similarity(original, trimmed)
            print(
                f"    light trim: {after_count} -> {trimmed_count} "
                f"similarity={trimmed_similarity:.3f}",
                flush=True,
            )
            if TARGET_MIN <= trimmed_count <= TARGET_MAX and trimmed_similarity <= MAX_SIMILARITY:
                return trimmed, before_count, trimmed_count
        prior_attempt = candidate
        prior_count = after_count

    fallback_seed = best_window_candidate or prior_attempt
    fallback_count = best_window_count if best_window_candidate is not None else prior_count
    if fallback_seed is not None and fallback_count is not None:
        if TARGET_MIN <= fallback_count <= TARGET_MAX:
            print("    de-copy fallback", flush=True)
            raw = await call_llm(
                configs,
                SYSTEM_PROMPT,
                build_decopy_prompt(
                    chapter_no=chapter_no,
                    title=title,
                    draft=fallback_seed,
                ),
            )
            candidate = normalize_output(raw, title)
            after_count = count_words(candidate)
            similarity = rewrite_similarity(original, candidate)
            print(
                f"    de-copy result: {before_count} -> {after_count} "
                f"similarity={similarity:.3f}",
                flush=True,
            )
            if TARGET_MIN <= after_count <= TARGET_MAX and similarity <= MAX_SIMILARITY:
                return candidate, before_count, after_count
            if (
                TARGET_MIN - EXPAND_UNDERFLOW_ALLOWANCE <= after_count < TARGET_MIN
                and similarity <= MAX_SIMILARITY
            ):
                expanded = expand_light_underflow(candidate)
                expanded_count = count_words(expanded)
                expanded_similarity = rewrite_similarity(original, expanded)
                print(
                    f"    de-copy light expand: {after_count} -> {expanded_count} "
                    f"similarity={expanded_similarity:.3f}",
                    flush=True,
                )
                if (
                    TARGET_MIN <= expanded_count <= TARGET_MAX
                    and expanded_similarity <= MAX_SIMILARITY
                ):
                    return expanded, before_count, expanded_count
            if (
                TARGET_MAX < after_count <= TARGET_MAX + TRIM_OVERFLOW_ALLOWANCE
                and similarity <= MAX_SIMILARITY
            ):
                trimmed = trim_light_overflow(candidate)
                trimmed_count = count_words(trimmed)
                trimmed_similarity = rewrite_similarity(original, trimmed)
                print(
                    f"    de-copy light trim: {after_count} -> {trimmed_count} "
                    f"similarity={trimmed_similarity:.3f}",
                    flush=True,
                )
                if (
                    TARGET_MIN <= trimmed_count <= TARGET_MAX
                    and trimmed_similarity <= MAX_SIMILARITY
                ):
                    return trimmed, before_count, trimmed_count

    raise RuntimeError(
        f"chapter {chapter_no:03d} failed length gate after {MAX_ATTEMPTS} attempts "
        f"(last={prior_count})"
    )


def make_backup(paths: list[Path]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / f"daozhong-opening-50-pre-rewrite-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copy2(path, backup_dir / path.name)
    return backup_dir


async def run(args: argparse.Namespace) -> int:
    start = args.start
    end = args.end
    if start < 1 or end < start:
        raise SystemExit("invalid chapter range")

    chapter_paths = [PROJECT_DIR / f"chapter-{i:03d}.md" for i in range(start, end + 1)]
    missing = [p for p in chapter_paths if not p.exists()]
    if missing:
        raise SystemExit(f"missing chapters: {', '.join(str(p) for p in missing)}")

    if args.limit is not None:
        chapter_paths = chapter_paths[: args.limit]

    originals = {
        int(path.stem.split("-")[1]): path.read_text(encoding="utf-8")
        for path in chapter_paths
    }
    context_numbers = set(originals)
    context_numbers.update(n - 1 for n in originals)
    context_numbers.update(n + 1 for n in originals)
    for n in sorted(context_numbers):
        path = PROJECT_DIR / f"chapter-{n:03d}.md"
        if n not in originals and path.exists():
            originals[n] = path.read_text(encoding="utf-8")

    print("Preflight counts:")
    for path in chapter_paths:
        n = int(path.stem.split("-")[1])
        print(f"  ch{n:03d}: {count_words(originals[n])}", flush=True)

    if not args.execute:
        print("Dry run only. Pass --execute to rewrite files.")
        return 0

    configs = get_provider_configs()
    if not configs:
        raise SystemExit("no usable LLM provider config found")

    backup_dir = make_backup(chapter_paths)
    print(f"Backup: {backup_dir}")

    current_texts: dict[int, str] = {}
    manifest: dict[str, Any] = {
        "project": "xianxia-upgrade-1776137730",
        "title": "道种破虚",
        "range": [start, end],
        "target_min": TARGET_MIN,
        "target_soft": TARGET_SOFT,
        "target_max": TARGET_MAX,
        "backup_dir": str(backup_dir),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "chapters": [],
    }

    completed = 0
    for path in chapter_paths:
        n = int(path.stem.split("-")[1])
        t0 = time.monotonic()
        print(f"[{completed + 1}/{len(chapter_paths)}] rewriting ch{n:03d}", flush=True)
        try:
            text, before_count, after_count = await rewrite_one(
                chapter_no=n,
                originals=originals,
                current_texts=current_texts,
                configs=configs,
            )
            path.write_text(text, encoding="utf-8")
            current_texts[n] = text
            status = "ok"
            error = None
        except Exception as exc:  # noqa: BLE001
            before_count = count_words(originals[n])
            after_count = None
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            print(f"    FAILED: {error}", flush=True)
            if not args.continue_on_error:
                raise

        completed += 1
        elapsed = round(time.monotonic() - t0, 2)
        manifest["chapters"].append(
            {
                "chapter": n,
                "file": str(path),
                "before_count": before_count,
                "after_count": after_count,
                "status": status,
                "error": error,
                "elapsed_seconds": elapsed,
            }
        )
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFEST_DIR / "daozhong-opening-50-rewrite-manifest.json"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"    saved ch{n:03d} status={status} elapsed={elapsed}s", flush=True)

    print(f"Manifest: {MANIFEST_DIR / 'daozhong-opening-50-rewrite-manifest.json'}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=50)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
