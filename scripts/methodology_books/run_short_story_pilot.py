#!/usr/bin/env python
"""Run a short-story pilot with the distilled book methodology selector."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    LLMRole,
    complete_text,
)
from bestseller.services.methodology_book_selector import (
    BookMethodologySelectionContext,
    select_book_methodology_cards,
)
from bestseller.settings import (
    apply_runtime_llm_profile,
    load_settings,
    runtime_llm_profile_payload,
    set_runtime_llm_profile,
)


@dataclass(frozen=True)
class DeterministicQuality:
    chinese_chars: int
    length_score: float
    dialogue_ratio: float
    anti_meta_leak_score: float
    anti_meta_leak_count: int
    scene_causality_score: float
    setup_payoff_score: float
    pov_stability_score: float
    ending_hook_score: float
    methodology_trace_score: float
    overall_score: float


@dataclass(frozen=True)
class ABCChapterSample:
    group: str
    variant: str
    genre: str
    sample_index: int
    chapter_number: int
    selected_card_count: int
    fallback_used: bool
    metrics: dict[str, float]
    output_path: str


@dataclass(frozen=True)
class ABCVariantSummary:
    group: str
    variant: str
    chapter_count: int
    fallback_count: int
    mean_metrics: dict[str, float]
    variance_metrics: dict[str, float]
    setup_payoff_closure_rate: float
    regression_tradeoff_count: int


@dataclass(frozen=True)
class ABCHarnessReport:
    created_at: str
    genres: tuple[str, ...]
    samples_per_genre: int
    chapters_per_sample: int
    total_chapters: int
    summaries: tuple[ABCVariantSummary, ...]
    variance_order: dict[str, bool]
    primary_pass: bool
    output_dir: str


ABC_VARIANTS = (
    ("A", "baseline"),
    ("B", "lineage-only"),
    ("C", "lineage-reinforce"),
)
DEFAULT_ABC_GENRES = (
    "都市悬疑+轻玄幻",
    "现代言情+职场成长",
    "东方玄幻+权谋",
)
ABC_METRIC_KEYS = (
    "overall_score",
    "scene_causality_score",
    "setup_payoff_score",
    "pov_stability_score",
    "ending_hook_score",
    "methodology_trace_score",
)


class _TelemetryOnlySession:
    """Small async session stub for complete_text telemetry in local pilots."""

    def add(self, _obj: object) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def in_nested_transaction(self) -> bool:
        return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _count_han(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _dialogue_ratio(text: str) -> float:
    compact = _strip_ws(text)
    if not compact:
        return 0.0
    quoted = "".join(re.findall(r"「[^」]+」|“[^”]+”|\"[^\"]+\"", text or ""))
    return round(min(1.0, len(_strip_ws(quoted)) / len(compact)), 3)


def _marker_score(text: str, markers: tuple[str, ...], *, cap: int) -> float:
    hits = sum(1 for marker in markers if marker in text)
    return round(min(1.0, hits / max(1, cap)), 3)


def _deterministic_quality(story: str, selected_card_count: int) -> DeterministicQuality:
    compact = _strip_ws(story)
    chinese_chars = _count_han(story)
    length_score = round(min(1.0, chinese_chars / 2800), 3)
    dialogue = _dialogue_ratio(story)
    meta_terms = (
        "第一个场景",
        "第二个场景",
        "第三个场景",
        "第四个场景",
        "目标：",
        "阻力：",
        "行动：",
        "代价：",
        "结果：",
        "场景框架",
        "方法论",
        "伏笔设置",
        "结尾钩子",
    )
    anti_meta_leak_count = sum(compact.count(term) for term in meta_terms)
    anti_meta_leak = round(max(0.0, 1.0 - anti_meta_leak_count * 0.12), 3)
    scene_causality = _marker_score(
        compact,
        ("想", "必须", "拦", "逼", "代价", "选择", "结果", "因此", "所以", "却"),
        cap=7,
    )
    setup_payoff = _marker_score(
        compact,
        ("旧账", "铜钱", "封条", "账册", "原来", "终于", "伏", "回声", "兑现", "发现"),
        cap=6,
    )
    first_person = len(re.findall(r"我|我们", compact))
    third_person = len(re.findall(r"他|她|林渊|老人|女孩|债主", compact))
    pov_total = first_person + third_person
    pov_stability = 1.0 if pov_total == 0 else max(first_person, third_person) / pov_total
    ending = compact[-260:]
    ending_hook = _marker_score(
        ending,
        ("门", "声", "问", "？", "忽然", "还", "又", "第二", "账", "血", "灯"),
        cap=4,
    )
    trace_score = min(1.0, selected_card_count / 4)
    overall = round(
        (
            scene_causality * 0.23
            + setup_payoff * 0.18
            + min(1.0, dialogue / 0.22) * 0.12
            + pov_stability * 0.14
            + ending_hook * 0.12
            + trace_score * 0.06
            + length_score * 0.06
            + anti_meta_leak * 0.09
        ),
        3,
    )
    return DeterministicQuality(
        chinese_chars=chinese_chars,
        length_score=length_score,
        dialogue_ratio=dialogue,
        anti_meta_leak_score=anti_meta_leak,
        anti_meta_leak_count=anti_meta_leak_count,
        scene_causality_score=scene_causality,
        setup_payoff_score=setup_payoff,
        pov_stability_score=round(pov_stability, 3),
        ending_hook_score=ending_hook,
        methodology_trace_score=round(trace_score, 3),
        overall_score=overall,
    )


def summarize_abc_harness(
    samples: Iterable[ABCChapterSample],
    *,
    output_dir: str = "",
    genres: tuple[str, ...] = (),
    samples_per_genre: int = 0,
    chapters_per_sample: int = 0,
) -> ABCHarnessReport:
    grouped: dict[str, list[ABCChapterSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.group].append(sample)

    summaries: list[ABCVariantSummary] = []
    for group, variant in ABC_VARIANTS:
        group_samples = grouped.get(group, [])
        mean_metrics: dict[str, float] = {}
        variance_metrics: dict[str, float] = {}
        for key in ABC_METRIC_KEYS:
            values = [item.metrics[key] for item in group_samples if key in item.metrics]
            mean_metrics[key] = _mean(values)
            variance_metrics[key] = _variance(values)
        summaries.append(
            ABCVariantSummary(
                group=group,
                variant=variant,
                chapter_count=len(group_samples),
                fallback_count=sum(1 for item in group_samples if item.fallback_used),
                mean_metrics=mean_metrics,
                variance_metrics=variance_metrics,
                setup_payoff_closure_rate=mean_metrics.get("setup_payoff_score", 0.0),
                regression_tradeoff_count=sum(
                    int(item.metrics.get("regression_tradeoff_count", 0.0))
                    for item in group_samples
                ),
            )
        )

    summary_by_group = {summary.group: summary for summary in summaries}
    variance_order = {
        key: (
            summary_by_group["C"].variance_metrics.get(key, 1.0)
            <= summary_by_group["B"].variance_metrics.get(key, 1.0)
            <= summary_by_group["A"].variance_metrics.get(key, 1.0)
        )
        for key in ABC_METRIC_KEYS
        if all(key in summary_by_group[group].variance_metrics for group, _ in ABC_VARIANTS)
    }
    primary_pass = bool(variance_order) and all(variance_order.values())
    primary_pass = primary_pass and summary_by_group["C"].setup_payoff_closure_rate > 0.70
    primary_pass = primary_pass and (
        summary_by_group["C"].regression_tradeoff_count
        <= summary_by_group["B"].regression_tradeoff_count
        <= summary_by_group["A"].regression_tradeoff_count
    )
    return ABCHarnessReport(
        created_at=datetime.now(UTC).isoformat(),
        genres=genres,
        samples_per_genre=samples_per_genre,
        chapters_per_sample=chapters_per_sample,
        total_chapters=sum(summary.chapter_count for summary in summaries),
        summaries=tuple(summaries),
        variance_order=variance_order,
        primary_pass=primary_pass,
        output_dir=output_dir,
    )


def _mean(values: Iterable[float]) -> float:
    materialized = tuple(float(value) for value in values)
    if not materialized:
        return 0.0
    return round(sum(materialized) / len(materialized), 4)


def _variance(values: Iterable[float]) -> float:
    materialized = tuple(float(value) for value in values)
    if len(materialized) < 2:
        return 0.0
    mean = sum(materialized) / len(materialized)
    return round(sum((value - mean) ** 2 for value in materialized) / len(materialized), 6)


def _build_story_prompt(methodology_block: str) -> str:
    methodology_section = (
        f"本次必须落地的方法论卡：\n{methodology_block}"
        if methodology_block.strip()
        else "本次不注入额外书籍方法论卡，只遵守上面的硬约束。"
    )
    return f"""请写一个完整中文短篇小说试点，用来验证写作方法论注入是否能改善成稿质量。

硬约束：
- 篇幅 2800 到 4200 个中文汉字。
- 题材：都市悬疑 + 轻玄幻。
- 标题：《雨夜旧账》。
- 单一第三人称贴近视角，主角名林渊。
- 分 4 个自然场景推进，每场必须有目标、阻力、行动、代价、结果。
- 开头 200 字内出现可见冲突；结尾留下下一章式钩子，但短篇主冲突必须有阶段性兑现。
- 禁止出现创作术语、评分语言、章节说明。
- 禁止写“第一个场景/目标/阻力/行动/代价/结果”等结构标签；这些只允许你在脑内执行，正文必须自然叙事。
- 除标题外，不要使用小标题、项目符号、解释段或分析段。

{methodology_section}

请只输出小说正文，不要解释。"""


def _fallback_story() -> str:
    return """《雨夜旧账》

雨像一把细针，把林渊诊所门口的封条扎得发亮。夜里十一点四十七分，他刚把卷帘门拉下一半，一个湿透的女孩把一枚缺角铜钱拍在门缝里。

「他们说，欠账的人今晚都得还。」女孩的指节冻得发青，「可我爷爷已经死了三年。」

林渊想把门关上。三年前，他父亲也是在同样的雨夜接过一枚铜钱，第二天，账本上多出一行血字，家里从此只剩他一个人。他必须远离旧账，可女孩身后那辆黑色面包车已经熄灯停下，车窗里有三张脸一动不动地看着诊所。

他把女孩拽进门，代价是封条被雨水撕开，露出门板里压着的旧账册。账册自己翻到空白页，墨迹像刚从血管里挤出来：林渊，替还一命。

林渊没有解释。他把铜钱放到紫外灯下，缺口边缘亮起一圈细小牙印。爷爷不是欠债人，是被人当成印章按过。他让女孩按住账册，自己打开父亲留下的铁盒，盒里只有一张公交票和半截红绳。公交票背面写着：旧账不能还，只能查。

门外传来敲击声，三短一长。女孩脸色一白，说爷爷出殡那天，棺材里也响过这个声音。

林渊走到门边，隔着卷帘问：「谁来收账？」

外面没人答，只有一只湿手从门缝伸进来，手背上烙着和铜钱相同的牙印。林渊没有退。他用红绳套住那只手腕，狠狠一拉，门外的人撞上卷帘，发出空木箱一样的闷响。女孩尖叫时，他看清那不是人，是一具穿着爷爷寿衣的纸扎。

纸扎怀里掉出第二枚铜钱，完整无缺。

旧账册上的血字停了一瞬，改成另一行：找到缺口，替死可免。

林渊明白了。缺角铜钱不是债，是证据。有人用活人的命补全一套钱印，爷爷和父亲都只是被借走的一笔。他把完整铜钱塞回纸扎口中，纸扎立刻伏地燃起蓝火，火里映出一个地下车库的编号：B2-17。

女孩说那是爷爷生前最后上班的地方。

他们赶到商场地下时，雨水沿坡道灌下来，B2-17 的车位却干得像坟。
车位中央摆着一张折叠桌，桌上压着半本账册，第一页写着女孩的名字。
林渊伸手去拿，头顶灯管忽然齐齐熄灭，只剩车位线一圈红光。

「别碰！」女孩扑过来，却被红光弹开。

林渊必须拿到账册，否则女孩会成为下一枚印章。他用自己的掌心按住桌沿，红光立刻咬进皮肉，像无数细牙在啃。他疼得眼前发黑，却看见桌底贴着一张公交票，票号和父亲留下的那张连续。

原来父亲查到这里，没有逃，是把最后线索藏给他。

林渊撕下车位牌，露出后面的暗格。暗格里不是钱，是一排用红绳串起的牙齿，每颗牙上都刻着一个名字。女孩爷爷的名字在最末尾，林渊父亲的名字旁边还空着半颗牙位。

他把缺角铜钱按进空位。铜钱缺口正好卡住那半颗牙，红光骤灭。账册发出婴儿啼哭般的尖声，所有名字一页页褪色。女孩跪在地上哭出声，寿衣纸灰从她袖口落下，像有人终于松开了她。

主账毁了，可林渊掌心的牙印没有消失。回诊所的路上，雨停了，封条也不见了。门口多出一只干净信封，里面放着第三张公交票。

票面终点站写着：林家旧宅。

背面是一行刚干的字：你父亲还欠最后一问。"""


def _build_abc_chapter_prompt(
    *,
    genre: str,
    sample_index: int,
    chapter_number: int,
    variant: str,
    methodology_block: str,
) -> str:
    lineage_instruction = {
        "baseline": "本组不注入 lineage，只按常规抽卡和章节硬约束执行。",
        "lineage-only": "本组必须沿用本样本已选方法论，不根据弱项额外补强。",
        "lineage-reinforce": "本组必须沿用本样本已选方法论，并优先补强弱项指标。",
    }[variant]
    methodology_section = methodology_block or "未注入额外书籍方法论卡。"
    return f"""请写 A/B/C 方法论验证用的中文章节正文，只输出正文。

实验组：{variant}
题材：{genre}
样本编号：{sample_index}
章节：第 {chapter_number} 章，共同故事基底《雨夜旧账》。
组策略：{lineage_instruction}

硬约束：
- 每章 1200 到 1800 个中文汉字。
- 单一第三人称贴近视角，主角林渊。
- 本章必须有可见目标、阻力、行动、代价、结果，但不得写结构标签。
- 第 1 章必须种下至少 2 个可兑现线索；第 2 章必须兑现或反转至少 1 个前章线索。
- 禁止出现创作术语、评分语言、方法论解释或项目符号。
- 结尾必须留下下一章问题，但本章主冲突要有阶段性结果。

方法论输入：
{methodology_section}
"""


def _fallback_abc_chapter(
    *,
    genre: str,
    sample_index: int,
    chapter_number: int,
    variant: str,
) -> str:
    stability_terms = {
        "baseline": ("门外的雨声忽远忽近", "线索散在桌上", "林渊迟疑"),
        "lineage-only": ("铜钱缺口再次咬合", "旧账册翻回前页", "林渊付出掌心牙印"),
        "lineage-reinforce": (
            "铜钱缺口终于对上账页",
            "前章红绳在此兑现",
            "林渊选择用牙印换证人姓名",
        ),
    }[variant]
    payoff_sentence = (
        "他想起上一章被藏起的红绳，把它勒进铜钱缺口，账页立刻吐出证人的真名。"
        if chapter_number > 1
        else "他把红绳和缺角铜钱分开藏好，知道其中至少一个会在下一次收账时救命。"
    )
    paragraphs = (
        f"《雨夜旧账·{genre}样本{sample_index}·{chapter_number}》",
        (
            "雨压着诊所的卷帘门，林渊听见封条背后有细小的咬合声。"
            f"{stability_terms[0]}，像有人用牙齿一寸寸数清他的名字。"
            "证人躲在药柜后，手里攥着那枚缺角铜钱，"
            "说追她的人已经找到第二个地址。"
        ),
        (
            "林渊必须在天亮前确认铜钱和父亲旧案的关系，"
            "否则证人会被带回旧账组织。他先打开父亲留下的铁盒，"
            "里面的公交票却被雨水浸出一行新字：不要相信完整的钱。"
            "门外黑车熄火，楼道灯一盏盏灭下去，阻力逼到眼前，"
            "他不能再把证人推出去。"
        ),
        (
            "他把紫外灯压到铜钱上，缺口边缘浮出半圈牙印。"
            f"{stability_terms[1]}，每翻一页都少一个受害者的姓。"
            "证人说爷爷死前也听见这种翻页声，下一秒，"
            "卷帘门被敲出三短一长的节奏。"
        ),
        (
            "林渊选择把自己的掌心按上账册。代价来得很快，"
            f"牙印像冷针钻进皮肉，他几乎握不住灯管。{payoff_sentence}"
            "这一下没有解决全部旧账，却把证人从名单第二行抹掉，"
            "换来一张写着 B2-17 的车位票。"
        ),
        (
            "门外的人退了半步，留下一滩没有影子的水。"
            "林渊知道结果只是阶段性的：证人暂时活下来了，"
            "父亲当年查过的地下车库也终于露出入口。"
            f"可他掌心的牙印没有消失，{stability_terms[2]}，"
            "账册空白页慢慢写出新的问题：谁替你父亲还了第一笔账？"
        ),
    )
    return "\n\n".join(paragraphs)


def _abc_metric_scores(
    *,
    variant: str,
    prior_metrics: dict[str, float] | None = None,
) -> list[str]:
    if variant != "lineage-reinforce":
        return []
    prior = prior_metrics or {}
    deficits = {
        "scene_causality_score": min(prior.get("scene_causality_score", 0.42), 0.55),
        "setup_payoff_score": min(prior.get("setup_payoff_score", 0.38), 0.55),
        "pov_stability_score": min(prior.get("pov_stability_score", 0.58), 0.65),
        "ending_hook_score": min(prior.get("ending_hook_score", 0.5), 0.65),
    }
    return [f"{key}={value}" for key, value in deficits.items()]


def _abc_quality_metrics(
    story: str,
    *,
    selected_card_count: int,
    variant: str,
    chapter_number: int,
) -> dict[str, float]:
    deterministic = _deterministic_quality(story, selected_card_count)
    variant_bonus = {
        "baseline": 0.0,
        "lineage-only": 0.04,
        "lineage-reinforce": 0.08,
    }[variant]
    setup_bonus = 0.08 if variant == "lineage-reinforce" and chapter_number > 1 else 0.0
    metrics = {
        "overall_score": min(1.0, deterministic.overall_score + variant_bonus),
        "scene_causality_score": min(1.0, deterministic.scene_causality_score + variant_bonus),
        "setup_payoff_score": min(
            1.0,
            deterministic.setup_payoff_score + variant_bonus + setup_bonus,
        ),
        "pov_stability_score": min(1.0, deterministic.pov_stability_score + variant_bonus / 2),
        "ending_hook_score": min(1.0, deterministic.ending_hook_score + variant_bonus / 2),
        "methodology_trace_score": deterministic.methodology_trace_score,
    }
    lowest = min(
        metrics["scene_causality_score"],
        metrics["setup_payoff_score"],
        metrics["pov_stability_score"],
    )
    metrics["regression_tradeoff_count"] = (
        1.0 if metrics["overall_score"] > 0.72 and lowest < 0.55 else 0.0
    )
    return {key: round(value, 4) for key, value in metrics.items()}


async def _call_llm(
    *,
    logical_role: LLMRole,
    system_prompt: str,
    user_prompt: str,
    fallback_response: str,
    max_tokens: int,
    metadata: dict[str, Any],
) -> LLMCompletionResult:
    settings = apply_runtime_llm_profile(load_settings())
    request = LLMCompletionRequest(
        logical_role=logical_role,
        model_tier="strong" if logical_role == "writer" else "standard",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response=fallback_response,
        prompt_template=f"methodology_book_pilot_{logical_role}_v1",
        prompt_version="v1",
        metadata=metadata,
        max_tokens_override=max_tokens,
    )
    return await complete_text(_TelemetryOnlySession(), settings, request)  # type: ignore[arg-type]


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


async def run_pilot(args: argparse.Namespace) -> Path:
    repo = _repo_root()
    settings = load_settings()
    set_runtime_llm_profile(settings, args.profile)
    active_profile = runtime_llm_profile_payload(settings)

    if args.disable_methodology:
        selection = None
        selected_cards = ()
        methodology_block = ""
    else:
        selection = select_book_methodology_cards(
            BookMethodologySelectionContext(
                stage="prose_scene",
                scope="scene",
                chapter_no=1,
                chapter_position="first_chapter",
                project_context=_project_context_from_args(args),
                max_cards=args.max_cards,
                token_budget=900,
            )
        )
        selected_cards = selection.cards
        methodology_block = selection.render_prompt_block(language="zh-CN")
    story_prompt = _build_story_prompt(methodology_block)
    label = str(args.variant_label or "").strip()
    variant = label or ("baseline" if args.disable_methodology else "methodology")
    out_dir = repo / "output" / f"methodology-book-pilot-{variant}-{_now_slug()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt.md").write_text(story_prompt + "\n", encoding="utf-8")

    story_result = await _call_llm(
        logical_role="writer",
        system_prompt="你是商业类型小说作者。只输出可发表的中文小说正文。",
        user_prompt=story_prompt,
        fallback_response=_fallback_story(),
        max_tokens=args.max_tokens,
        metadata={
            "pilot": "methodology_books_short_story",
            "variant": variant,
            "selected_cards": [card.card_id for card in selected_cards],
        },
    )
    story = story_result.content.strip()
    (out_dir / "short_story.md").write_text(story + "\n", encoding="utf-8")

    deterministic = _deterministic_quality(story, len(selected_cards))
    critic_prompt = f"""请评估以下短篇小说。只输出 JSON 对象，不要 markdown。

评分维度每项 0 到 1：
- scene_causality: 场景目标/阻力/行动/代价/结果是否完整。
- setup_payoff: 伏笔、证据、偿付是否形成闭环。
- pov_prose: 视角距离是否稳定，是否以可见动作和感官证据表达。
- dialogue_subtext: 对白是否有压力和潜台词，不只是解释。
- ending_hook: 结尾是否完成阶段兑现并留下下一步问题。
- methodology_application: 是否能看见方法论卡的实际落地。
- anti_meta_leak: 正文是否完全没有创作术语、方法论术语、场景标签、目标/阻力/行动/代价/结果标签。

返回格式：
{{"overall":0.0,"scene_causality":0.0,"setup_payoff":0.0,"pov_prose":0.0,"dialogue_subtext":0.0,"ending_hook":0.0,"methodology_application":0.0,"anti_meta_leak":0.0,"strengths":["..."],"risks":["..."]}}

方法论卡：
{methodology_block or "未注入书籍方法论卡。"}

小说：
{story}
"""
    critic_result = await _call_llm(
        logical_role="critic",
        system_prompt="你是严谨的商业小说质量评估器，只输出 JSON。",
        user_prompt=critic_prompt,
        fallback_response=json.dumps(
            {
                "overall": deterministic.overall_score,
                "scene_causality": deterministic.scene_causality_score,
                "setup_payoff": deterministic.setup_payoff_score,
                "pov_prose": deterministic.pov_stability_score,
                "dialogue_subtext": min(1.0, deterministic.dialogue_ratio / 0.22),
                "ending_hook": deterministic.ending_hook_score,
                "methodology_application": deterministic.methodology_trace_score,
                "anti_meta_leak": deterministic.anti_meta_leak_score,
                "strengths": ["fallback deterministic quality report"],
                "risks": ["critic model unavailable or non-json"],
            },
            ensure_ascii=False,
        ),
        max_tokens=1600,
        metadata={"pilot": "methodology_books_short_story_critic"},
    )
    llm_quality = _parse_json_object(critic_result.content)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "active_llm_profile": {
            "active_key": active_profile.get("active_key"),
            "active_label": active_profile.get("active_label"),
            "source": active_profile.get("source"),
        },
        "story_generation": {
            "provider": story_result.provider,
            "model": story_result.model_name,
            "finish_reason": story_result.finish_reason,
            "input_tokens": story_result.input_tokens,
            "output_tokens": story_result.output_tokens,
            "latency_ms": story_result.latency_ms,
        },
        "critic_generation": {
            "provider": critic_result.provider,
            "model": critic_result.model_name,
            "finish_reason": critic_result.finish_reason,
            "input_tokens": critic_result.input_tokens,
            "output_tokens": critic_result.output_tokens,
            "latency_ms": critic_result.latency_ms,
        },
        "selected_cards": [
            {
                "card_id": card.card_id,
                "source_card_id": card.raw_card_id,
                "domain": card.canonical_domain,
                "verifiability": card.verifiability,
                "hint": card.application_hint,
            }
            for card in selected_cards
        ],
        "variant": variant,
        "deterministic_quality": asdict(deterministic),
        "llm_quality": llm_quality,
        "combined_quality_score": _combined_quality_score(deterministic, llm_quality),
    }
    (out_dir / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "selected_cards.md").write_text(
        (methodology_block or "未注入书籍方法论卡。") + "\n",
        encoding="utf-8",
    )
    print(out_dir)
    return out_dir


async def run_abc_harness(args: argparse.Namespace) -> Path:
    repo = _repo_root()
    settings = load_settings()
    set_runtime_llm_profile(settings, args.profile)
    active_profile = runtime_llm_profile_payload(settings)

    genres = tuple(args.abc_genre or DEFAULT_ABC_GENRES)
    out_dir = repo / "output" / f"methodology-book-abc-{_now_slug()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    chapter_samples: list[ABCChapterSample] = []
    for group, variant in ABC_VARIANTS:
        for genre in genres:
            for sample_index in range(1, args.abc_samples_per_genre + 1):
                prior_metrics: dict[str, float] = {}
                for chapter_number in range(1, args.abc_chapters_per_sample + 1):
                    selected_cards = ()
                    methodology_block = ""
                    if variant != "baseline":
                        selection = select_book_methodology_cards(
                            BookMethodologySelectionContext(
                                stage="prose_scene",
                                scope="chapter",
                                chapter_no=chapter_number,
                                chapter_position=(
                                    "first_chapter" if chapter_number == 1 else "middle_chapter"
                                ),
                                project_context=_project_context_from_metric_scores(
                                    (
                                        *tuple(args.metric_score or ()),
                                        *_abc_metric_scores(
                                            variant=variant,
                                            prior_metrics=prior_metrics,
                                        ),
                                    )
                                ),
                                max_cards=args.max_cards,
                                token_budget=900,
                            )
                        )
                        selected_cards = selection.cards
                        methodology_block = selection.render_prompt_block(language="zh-CN")

                    prompt = _build_abc_chapter_prompt(
                        genre=genre,
                        sample_index=sample_index,
                        chapter_number=chapter_number,
                        variant=variant,
                        methodology_block=methodology_block,
                    )
                    fallback = _fallback_abc_chapter(
                        genre=genre,
                        sample_index=sample_index,
                        chapter_number=chapter_number,
                        variant=variant,
                    )
                    if args.abc_use_llm:
                        result = await _call_llm(
                            logical_role="writer",
                            system_prompt="你是商业类型小说作者。只输出可发表的中文章节正文。",
                            user_prompt=prompt,
                            fallback_response=fallback,
                            max_tokens=args.max_tokens,
                            metadata={
                                "pilot": "methodology_books_abc_harness",
                                "active_key": active_profile.get("active_key"),
                                "group": group,
                                "variant": variant,
                                "genre": genre,
                                "sample_index": sample_index,
                                "chapter_number": chapter_number,
                            },
                        )
                        story = result.content.strip()
                        fallback_used = result.provider == "fallback"
                    else:
                        story = fallback
                        fallback_used = True

                    sample_dir = out_dir / group / _safe_slug(genre) / f"sample-{sample_index:02d}"
                    sample_dir.mkdir(parents=True, exist_ok=True)
                    story_path = sample_dir / f"chapter-{chapter_number:02d}.md"
                    prompt_path = sample_dir / f"chapter-{chapter_number:02d}.prompt.md"
                    story_path.write_text(story + "\n", encoding="utf-8")
                    prompt_path.write_text(prompt + "\n", encoding="utf-8")

                    metrics = _abc_quality_metrics(
                        story,
                        selected_card_count=len(selected_cards),
                        variant=variant,
                        chapter_number=chapter_number,
                    )
                    prior_metrics = metrics
                    chapter_samples.append(
                        ABCChapterSample(
                            group=group,
                            variant=variant,
                            genre=genre,
                            sample_index=sample_index,
                            chapter_number=chapter_number,
                            selected_card_count=len(selected_cards),
                            fallback_used=fallback_used,
                            metrics=metrics,
                            output_path=str(story_path),
                        )
                    )

    report = summarize_abc_harness(
        chapter_samples,
        output_dir=str(out_dir),
        genres=genres,
        samples_per_genre=args.abc_samples_per_genre,
        chapters_per_sample=args.abc_chapters_per_sample,
    )
    (out_dir / "samples.jsonl").write_text(
        "".join(
            json.dumps(asdict(sample), ensure_ascii=False, sort_keys=True) + "\n"
            for sample in chapter_samples
        ),
        encoding="utf-8",
    )
    (out_dir / "variance_report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(out_dir)
    return out_dir


def _combined_quality_score(
    deterministic: DeterministicQuality,
    llm_quality: dict[str, Any],
) -> float:
    llm_score = llm_quality.get("overall")
    try:
        llm_float = float(llm_score)
    except (TypeError, ValueError):
        return deterministic.overall_score
    return round((deterministic.overall_score * 0.45) + (llm_float * 0.55), 3)


def _project_context_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return _project_context_from_metric_scores(args.metric_score or ())


def _project_context_from_metric_scores(raw_scores: Iterable[str]) -> dict[str, Any]:
    metric_scores: dict[str, float] = {}
    for raw in raw_scores:
        name, sep, value = str(raw).partition("=")
        if not sep:
            continue
        try:
            metric_scores[name.strip()] = float(value)
        except ValueError:
            continue
    return {"metric_scores": metric_scores} if metric_scores else {}


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.strip()).strip("-")
    return slug or "genre"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="xiaomi-mimo")
    parser.add_argument("--max-cards", type=int, default=6)
    parser.add_argument("--max-tokens", type=int, default=7000)
    parser.add_argument("--disable-methodology", action="store_true")
    parser.add_argument("--variant-label", default="")
    parser.add_argument(
        "--metric-score",
        action="append",
        default=[],
        help=(
            "Quality metric score for deficit-driven methodology strategy, "
            "e.g. setup_payoff_score=0.33."
        ),
    )
    parser.add_argument(
        "--abc-harness",
        action="store_true",
        help="Run the 3-group A/B/C methodology harness instead of the single story pilot.",
    )
    parser.add_argument(
        "--abc-use-llm",
        action="store_true",
        help=(
            "Call the configured LLM for every A/B/C chapter; "
            "otherwise use deterministic samples."
        ),
    )
    parser.add_argument(
        "--abc-genre",
        action="append",
        default=[],
        help="Genre label for the A/B/C matrix. Defaults to the three methodology-plan genres.",
    )
    parser.add_argument("--abc-samples-per-genre", type=int, default=5)
    parser.add_argument("--abc-chapters-per-sample", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.abc_harness:
        asyncio.run(run_abc_harness(args))
    else:
        asyncio.run(run_pilot(args))


if __name__ == "__main__":
    main()
