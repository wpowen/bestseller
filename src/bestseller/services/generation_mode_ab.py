"""Paired evaluation primitives for chapter generation modes.

The experiment keeps story inputs fixed while changing only the generation
unit: three scene calls assembled into a chapter, or one whole-chapter call.
All scoring helpers are pure so the live runner and unit tests share the same
decision contract.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
import json
import re
from statistics import mean
from typing import Any

from bestseller.services.ai_flavor import detect as detect_ai_flavor
from bestseller.services.ai_flavor.detector import _score as score_ai_flavor_spans

MODE_SCENE_BY_SCENE = "scene_by_scene"
MODE_CHAPTER_FIRST = "chapter_first"
MODES = (MODE_SCENE_BY_SCENE, MODE_CHAPTER_FIRST)
SCORE_DIMENSIONS = ("anti_ai", "logic", "story", "readability")
SCORE_WEIGHTS = {
    "anti_ai": 0.30,
    "logic": 0.30,
    "story": 0.20,
    "readability": 0.20,
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SCENE_HEADING_RE = re.compile(
    r"(?im)^\s{0,3}(?:#{1,6}\s*)?(?:第[一二三四五六七八九十\d]+场|场景[一二三四五六七八九十\d：:\s_-]*)\s*$"
)
_META_LEAK_RE = re.compile(
    r"(?i)\b(?:entry_state|exit_state|scene_type|chapter_contract|scene_card)\b|"
    r"(?:场景目的|写作说明|内部节拍|策划说明)"
)
_QUOTE_RE = re.compile(r"[“”「」『』\"]")


@dataclass(frozen=True)
class SceneBeat:
    number: int
    event: str
    goal: str
    obstacle: str
    turn: str


@dataclass(frozen=True)
class ChapterABCase:
    case_id: str
    title: str
    genre: str
    chapter_function: str
    participants: tuple[str, ...]
    previous_context: str
    chapter_goal: str
    required_events: tuple[tuple[str, ...], ...]
    scene_beats: tuple[SceneBeat, ...]
    target_min_chars: int = 2300
    target_max_chars: int = 3000
    ai_category_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeterministicScore:
    cjk_chars: int
    ai_flavor_score: float
    ai_pattern_counts: dict[str, int]
    required_event_coverage: float
    required_event_order: float
    visible_scene_heading_count: int
    meta_leak_count: int
    paragraph_count: int
    short_solo_paragraph_count: int
    length_passed: bool


@dataclass(frozen=True)
class GeneratedSample:
    case_id: str
    mode: str
    text: str
    deterministic: DeterministicScore
    writer_model: str
    provider: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    component_count: int = 1
    fallback_used: bool = False
    hard_integrity_failure_count: int = 0
    stitched_or_repeated_climax_count: int = 0

    @classmethod
    def fake(
        cls,
        *,
        case_id: str,
        mode: str,
        deterministic_coverage: float,
    ) -> GeneratedSample:
        return cls(
            case_id=case_id,
            mode=mode,
            text=f"{case_id}-{mode}",
            deterministic=DeterministicScore(
                cjk_chars=2500,
                ai_flavor_score=10.0,
                ai_pattern_counts={},
                required_event_coverage=deterministic_coverage,
                required_event_order=1.0,
                visible_scene_heading_count=0,
                meta_leak_count=0,
                paragraph_count=20,
                short_solo_paragraph_count=2,
                length_passed=True,
            ),
            writer_model="fake-writer",
        )


@dataclass(frozen=True)
class PairwiseJudgement:
    case_id: str
    judge_model: str
    swapped: bool
    mode_scores: dict[str, dict[str, float]]
    winner: str
    evidence: dict[str, str] = field(default_factory=dict)
    risk_notes: tuple[str, ...] = ()
    raw_text: str = ""

    @classmethod
    def fake(
        cls,
        *,
        case_id: str,
        judge_model: str,
        swapped: bool,
        winner: str,
        chapter_first_score: float,
        scene_by_scene_score: float,
    ) -> PairwiseJudgement:
        return cls(
            case_id=case_id,
            judge_model=judge_model,
            swapped=swapped,
            mode_scores={
                MODE_CHAPTER_FIRST: dict.fromkeys(SCORE_DIMENSIONS, chapter_first_score),
                MODE_SCENE_BY_SCENE: dict.fromkeys(SCORE_DIMENSIONS, scene_by_scene_score),
            },
            winner=winner,
        )


def build_default_cases() -> tuple[ChapterABCase, ...]:
    """Return three paired, non-suspense chapter briefs with distinct jobs."""

    return (
        ChapterABCase(
            case_id="bakery-opening-pressure",
            title="停电以前",
            genre="现实经营",
            chapter_function="opening_pressure",
            participants=("林见夏", "周琴", "老杜"),
            previous_context=(
                "林见夏辞掉省城工作，回到县城接手父亲留下的夏禾点心铺。母亲周琴想卖店还债，"
                "老杜是供电所催收员，也是父亲旧识。"
            ),
            chapter_goal=(
                "开门第一天遇到欠费停电。林见夏必须保住一批婚宴点心，并用一次具体选择证明她不是回来办完手续就走。"
            ),
            required_events=(
                ("停电通知", "断电", "拉闸"),
                ("黄油", "冰柜", "冷藏"),
                ("婚宴", "三批", "第一车"),
            ),
            scene_beats=(
                SceneBeat(
                    1,
                    "老杜带着停电通知上门，今天不补旧账就拉闸。",
                    "争取两小时",
                    "周琴当众同意卖店",
                    "电仍被切断",
                ),
                SceneBeat(
                    2,
                    "冷藏柜停转，黄油和奶油开始回温。",
                    "保住原料",
                    "邻店只肯借半格冰柜",
                    "林见夏放弃一款高利润点心",
                ),
                SceneBeat(
                    3,
                    "她重排婚宴订单，把能做的点心拆成三批发车。",
                    "完成首批交付",
                    "送货车只等二十分钟",
                    "第一车出门，欠款仍未解决",
                ),
            ),
            ai_category_allowlist=("debt_metaphor_leak",),
        ),
        ChapterABCase(
            case_id="bakery-relationship-conflict",
            title="账本归谁",
            genre="现实经营",
            chapter_function="relationship_conflict",
            participants=("林见夏", "林望", "周琴"),
            previous_context=(
                "点心铺暂时恢复营业。林见夏的哥哥林望十年没进后厨，这次带着房产中介回来，"
                "坚持卖掉店面；周琴把父亲生前的手写账本锁在抽屉里。"
            ),
            chapter_goal=(
                "兄妹围绕卖店正面冲突。争执不能靠互诉苦衷解决，必须通过账本、旧欠条和一次当场选择改变关系。"
            ),
            required_events=(
                ("中介", "合同", "卖店"),
                ("账本", "抽屉", "钥匙"),
                ("欠条", "撕", "签字"),
            ),
            scene_beats=(
                SceneBeat(
                    1,
                    "林望把中介和卖店合同带到铺里。",
                    "阻止当天签约",
                    "林望握有一半产权",
                    "林见夏拒绝替母亲做决定",
                ),
                SceneBeat(
                    2,
                    "周琴拿出锁了多年的手写账本。",
                    "弄清父亲债务",
                    "账目显示林望曾替家里垫款",
                    "林见夏发现哥哥并非只来分钱",
                ),
                SceneBeat(
                    3,
                    "林望要求她签一张新的家庭欠条。",
                    "保住经营权",
                    "签字等于承认店铺必卖",
                    "她撕掉空白合同，只在欠款金额下签名",
                ),
            ),
            ai_category_allowlist=("debt_metaphor_leak",),
        ),
        ChapterABCase(
            case_id="bakery-public-payoff",
            title="这一炉算谁的",
            genre="现实经营",
            chapter_function="public_payoff",
            participants=("林见夏", "赵师傅", "阿禾", "周琴"),
            previous_context=(
                "夏禾点心铺参加县里的老字号市集。赵师傅公开指责林见夏偷用他家的桂花酥配方，"
                "学徒阿禾知道父亲旧配方的揉面顺序，却害怕得罪同行。"
            ),
            chapter_goal=(
                "把配方争议变成一次现场制作对照。林见夏要靠工序和成品说服围观者，同时承担公开失败的代价。"
            ),
            required_events=(
                ("秤", "称重", "克"),
                ("揉面", "折", "醒面"),
                ("出炉", "掰开", "酥层"),
            ),
            scene_beats=(
                SceneBeat(
                    1,
                    "赵师傅要求当众称料，证明两家配方相同。",
                    "把争执落到可验证步骤",
                    "围观者默认名气更大的赵师傅可信",
                    "林见夏主动封存自己的配方纸",
                ),
                SceneBeat(
                    2,
                    "两边同时揉面，阿禾必须决定是否说出关键折叠次数。",
                    "完成父亲的旧工序",
                    "时间和面温都不利",
                    "阿禾不作证，只递来一盆冷水",
                ),
                SceneBeat(
                    3,
                    "两炉点心同时出炉，由周琴随机掰开给人看酥层。",
                    "让结果自行说话",
                    "林见夏这一炉颜色更差",
                    "断面层次胜出，但她承认火候输了一档",
                ),
            ),
        ),
    )


def _shared_contract(case: ChapterABCase) -> str:
    participants = "、".join(case.participants)
    required = "\n".join(
        f"- 事件 {index}: 至少自然出现一项：{' / '.join(group)}"
        for index, group in enumerate(case.required_events, start=1)
    )
    return (
        f"章节名：{case.title}\n"
        f"题材：{case.genre}\n"
        f"章节功能：{case.chapter_function}\n"
        f"参与人物：{participants}\n"
        f"前情：{case.previous_context}\n"
        f"本章目标：{case.chapter_goal}\n"
        f"目标长度：{case.target_min_chars}-{case.target_max_chars} 个中文字符。\n"
        f"必须兑现：\n{required}\n"
        "共同写作约束：\n"
        "- 过程在前，结果由动作和后果落地；不由旁白先下结论。\n"
        "- 禁止‘不是X而是Y’、顿悟宣告、命运升华、连续身体微动作和万能发烫信号。\n"
        "- 对话允许停顿、答非所问和省略，不替对方解释已知信息。\n"
        "- 只写连续正文，不写标题、提纲、总结或创作说明。"
    )


def _writer_system(mode_instruction: str) -> str:
    return (
        "你是中文现实题材长篇小说写手。正文要像人物在具体处境中做事，不像作者在解释主题。\n"
        f"{mode_instruction}\n"
        "不要输出场景标题、章节标题、分隔线、策划字段或写作说明。\n"
        "只输出可直接交给读者的连续正文。"
    )


def build_scene_prompts(
    case: ChapterABCase,
    beat: SceneBeat,
    *,
    previous_tail: str,
) -> tuple[str, str]:
    system = _writer_system(
        "本次只写当前生产单元；它将在幕后与同章其他单元连接，开头和结尾都要给自然承接点。"
    )
    tail = previous_tail.strip() or "（本章开头，没有上一单元正文。）"
    user = (
        f"{_shared_contract(case)}\n\n"
        "当前生产单元：\n"
        f"- 顺序：{beat.number}/3\n"
        f"- 事件：{beat.event}\n"
        f"- 人物目标：{beat.goal}\n"
        f"- 阻力：{beat.obstacle}\n"
        f"- 单元变化：{beat.turn}\n"
        f"- 本单元长度：760-980 个中文字符。\n\n"
        f"上一单元结尾（只用于承接，不得复述）：\n{tail[-600:]}\n\n"
        "写当前单元正文。不要提前写后续单元，不要输出场景标题。"
    )
    return system, user


def build_chapter_first_prompts(case: ChapterABCase) -> tuple[str, str]:
    system = _writer_system(
        "本次一次性写完整章。弱场景地图只约束顺序与状态变化，不是正文素材；"
        "不得照抄、逐句扩写或换词改写地图措辞。转场靠动作、时间和因果完成。"
    )
    beats = "\n".join(
        f"节点{beat.number}｜压力={beat.event}｜选择目标={beat.goal}｜"
        f"反制={beat.obstacle}｜必须变化={beat.turn}"
        for beat in case.scene_beats
    )
    user = (
        f"{_shared_contract(case)}\n\n"
        "弱场景逻辑地图（低优先级，只回答什么必须发生/改变；不提供句子、对白、描写或段落结构）：\n"
        f"{beats}\n\n"
        "优先级：前情与整章目标 > 必须兑现事件 > 弱场景地图。"
        "请先在内部建立全章因果，再一次性写完整章；用全新的动作、对白、失误和后果实现节点，"
        "不要按节点分段报账，不要输出场景标题。"
    )
    return system, user


def assemble_scene_texts(parts: Sequence[str]) -> str:
    cleaned: list[str] = []
    for part in parts:
        body = _SCENE_HEADING_RE.sub("", str(part or "")).strip()
        body = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", body, flags=re.I).strip()
        if body:
            cleaned.append(body)
    return "\n\n".join(cleaned).strip()


def score_generated_sample(text: str, case: ChapterABCase) -> DeterministicScore:
    report = detect_ai_flavor(text, language="zh", chapter_number=1)
    counted_spans = tuple(
        span for span in report.spans if span.category not in case.ai_category_allowlist
    )
    categories = Counter(span.category for span in counted_spans)
    marker_positions: list[int] = []
    covered = 0
    for group in case.required_events:
        positions = [text.find(marker) for marker in group if marker and marker in text]
        if positions:
            covered += 1
            marker_positions.append(min(positions))
    event_count = len(case.required_events)
    coverage = covered / event_count if event_count else 1.0
    if len(marker_positions) < 2:
        order = 1.0 if event_count <= 1 and marker_positions else 0.0
    else:
        comparisons = len(marker_positions) - 1
        ordered = sum(
            left < right
            for left, right in zip(marker_positions[:-1], marker_positions[1:], strict=True)
        )
        order = ordered / comparisons

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    short_solo = sum(
        len(_CJK_RE.findall(paragraph)) <= 18 and not _QUOTE_RE.search(paragraph)
        for paragraph in paragraphs
    )
    cjk_chars = len(_CJK_RE.findall(text))
    return DeterministicScore(
        cjk_chars=cjk_chars,
        ai_flavor_score=float(score_ai_flavor_spans(counted_spans)),
        ai_pattern_counts=dict(sorted(categories.items())),
        required_event_coverage=round(coverage, 4),
        required_event_order=round(order, 4),
        visible_scene_heading_count=len(_SCENE_HEADING_RE.findall(text)),
        meta_leak_count=len(_META_LEAK_RE.findall(text)),
        paragraph_count=len(paragraphs),
        short_solo_paragraph_count=short_solo,
        length_passed=case.target_min_chars <= cjk_chars <= case.target_max_chars,
    )


def build_pairwise_judge_prompts(
    case: ChapterABCase,
    *,
    left_text: str,
    right_text: str,
    judge_label: str,
    swapped: bool,
) -> tuple[str, str]:
    first, second = (right_text, left_text) if swapped else (left_text, right_text)
    system = (
        "你是中文商业长篇正文的配对盲评判官。你不知道两篇正文的生成方式，也不得猜测来源。"
        "只比较成品，不奖励更华丽、更工整或解释更充分的文本。"
        "分别按 0-10 分评估：anti_ai（越自然越高）、logic（因果/动机/空间时间越严谨越高）、"
        "story（冲突推进、人物选择、转折和余味）、readability（清楚、顺滑、愿意继续读）。"
        "每个维度必须给出至少一条可核对的正文证据。只输出 JSON。"
    )
    schema = {
        "judge_label": judge_label,
        "scores": {
            "A": dict.fromkeys(SCORE_DIMENSIONS, "0-10 number"),
            "B": dict.fromkeys(SCORE_DIMENSIONS, "0-10 number"),
        },
        "winner": "A|B|tie",
        "evidence": dict.fromkeys(SCORE_DIMENSIONS, "具体比较证据"),
        "risk_notes": ["评审不确定性"],
    }
    user = (
        f"题材：{case.genre}\n章节名：{case.title}\n"
        f"前情：{case.previous_context}\n章节目标：{case.chapter_goal}\n\n"
        f"文本 A：\n{first}\n\n"
        f"文本 B：\n{second}\n\n"
        f"请按此结构输出：{json.dumps(schema, ensure_ascii=False)}"
    )
    return system, user


def parse_pairwise_judgement(
    case_id: str,
    judge_model: str,
    raw_text: str,
    *,
    swapped: bool,
) -> PairwiseJudgement:
    try:
        payload = _parse_json_object(raw_text)
    except (json.JSONDecodeError, ValueError):
        payload = _recover_pairwise_payload(raw_text)
    raw_scores: Mapping[str, Any] = (
        payload["scores"] if isinstance(payload.get("scores"), Mapping) else {}
    )
    a_scores = _normalise_scores(raw_scores.get("A"))
    b_scores = _normalise_scores(raw_scores.get("B"))
    if swapped:
        mode_scores = {MODE_CHAPTER_FIRST: a_scores, MODE_SCENE_BY_SCENE: b_scores}
    else:
        mode_scores = {MODE_SCENE_BY_SCENE: a_scores, MODE_CHAPTER_FIRST: b_scores}
    raw_winner = str(payload.get("winner") or "tie").strip().upper()
    if raw_winner == "A":
        winner = MODE_CHAPTER_FIRST if swapped else MODE_SCENE_BY_SCENE
    elif raw_winner == "B":
        winner = MODE_SCENE_BY_SCENE if swapped else MODE_CHAPTER_FIRST
    else:
        winner = "tie"
    evidence_raw = payload.get("evidence")
    evidence = (
        {str(key): str(value) for key, value in evidence_raw.items()}
        if isinstance(evidence_raw, Mapping)
        else {}
    )
    risks = payload.get("risk_notes")
    risk_notes = tuple(str(item) for item in risks) if isinstance(risks, list) else ()
    return PairwiseJudgement(
        case_id=case_id,
        judge_model=judge_model,
        swapped=swapped,
        mode_scores=mode_scores,
        winner=winner,
        evidence=evidence,
        risk_notes=risk_notes,
        raw_text=raw_text,
    )


def weighted_score(scores: Mapping[str, float]) -> float:
    return round(
        sum(float(scores.get(key, 0.0)) * weight for key, weight in SCORE_WEIGHTS.items()),
        4,
    )


def summarize_experiment(
    cases: Sequence[ChapterABCase],
    samples: Sequence[GeneratedSample],
    judgements: Sequence[PairwiseJudgement],
) -> dict[str, Any]:
    expected_sample_keys = {(case.case_id, mode) for case in cases for mode in MODES}
    sample_by_key = {(sample.case_id, sample.mode): sample for sample in samples}
    missing_samples = sorted(expected_sample_keys - set(sample_by_key))

    scores_by_mode: dict[str, list[float]] = defaultdict(list)
    dimension_scores: dict[str, dict[str, list[float]]] = {
        mode: defaultdict(list) for mode in MODES
    }
    scores_by_case: dict[str, dict[str, list[float]]] = {
        case.case_id: defaultdict(list) for case in cases
    }
    for judgement in judgements:
        for mode in MODES:
            scores = judgement.mode_scores.get(mode, {})
            value = weighted_score(scores)
            scores_by_mode[mode].append(value)
            scores_by_case.setdefault(judgement.case_id, defaultdict(list))[mode].append(value)
            for dimension in SCORE_DIMENSIONS:
                dimension_scores[mode][dimension].append(float(scores.get(dimension, 0.0)))

    case_wins = dict.fromkeys(MODES, 0)
    case_results: dict[str, Any] = {}
    for case in cases:
        mode_means = {
            mode: round(mean(scores_by_case[case.case_id][mode]), 4)
            if scores_by_case[case.case_id][mode]
            else 0.0
            for mode in MODES
        }
        delta = mode_means[MODE_CHAPTER_FIRST] - mode_means[MODE_SCENE_BY_SCENE]
        winner = "tie"
        if abs(delta) >= 0.10:
            winner = MODE_CHAPTER_FIRST if delta > 0 else MODE_SCENE_BY_SCENE
            case_wins[winner] += 1
        case_results[case.case_id] = {"mode_scores": mode_means, "winner": winner}

    position_pairs: dict[tuple[str, str], dict[bool, str]] = defaultdict(dict)
    for judgement in judgements:
        position_pairs[(judgement.case_id, judgement.judge_model)][judgement.swapped] = (
            judgement.winner
        )
    stable = 0
    complete_pairs = 0
    for pair in position_pairs.values():
        if False in pair and True in pair:
            complete_pairs += 1
            stable += int(pair[False] == pair[True])
    position_agreement = stable / complete_pairs if complete_pairs else 0.0
    position_by_judge: dict[str, dict[str, float | int]] = {}
    for judge_model in sorted({item.judge_model for item in judgements}):
        judge_pairs = {
            key: pair for key, pair in position_pairs.items() if key[1] == judge_model
        }
        judge_complete = [pair for pair in judge_pairs.values() if False in pair and True in pair]
        judge_stable = sum(pair[False] == pair[True] for pair in judge_complete)
        position_by_judge[judge_model] = {
            "agreement_rate": round(
                judge_stable / len(judge_complete) if judge_complete else 0.0,
                4,
            ),
            "complete_pairs": len(judge_complete),
        }

    scores_by_judge: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for judgement in judgements:
        for mode in MODES:
            scores_by_judge[judgement.judge_model][mode].append(
                weighted_score(judgement.mode_scores.get(mode, {}))
            )
    judge_mode_scores = {
        judge: {
            mode: round(mean(values.get(mode, ())), 4) if values.get(mode) else 0.0
            for mode in MODES
        }
        for judge, values in scores_by_judge.items()
    }

    mode_means = {
        mode: round(mean(scores_by_mode[mode]), 4) if scores_by_mode[mode] else 0.0
        for mode in MODES
    }
    dimension_means = {
        mode: {
            dimension: round(mean(values), 4) if values else 0.0
            for dimension, values in dimension_scores[mode].items()
        }
        for mode in MODES
    }
    deterministic = {
        mode: {
            "mean_ai_flavor_score": round(
                mean(
                    sample_by_key[(case.case_id, mode)].deterministic.ai_flavor_score
                    for case in cases
                    if (case.case_id, mode) in sample_by_key
                ),
                4,
            )
            if any((case.case_id, mode) in sample_by_key for case in cases)
            else 0.0,
            "mean_required_event_coverage": round(
                mean(
                    sample_by_key[(case.case_id, mode)].deterministic.required_event_coverage
                    for case in cases
                    if (case.case_id, mode) in sample_by_key
                ),
                4,
            )
            if any((case.case_id, mode) in sample_by_key for case in cases)
            else 0.0,
            "length_pass_rate": round(
                mean(
                    float(sample_by_key[(case.case_id, mode)].deterministic.length_passed)
                    for case in cases
                    if (case.case_id, mode) in sample_by_key
                ),
                4,
            )
            if any((case.case_id, mode) in sample_by_key for case in cases)
            else 0.0,
            "hard_integrity_failure_count": sum(
                sample_by_key[(case.case_id, mode)].hard_integrity_failure_count
                for case in cases
                if (case.case_id, mode) in sample_by_key
            ),
            "stitched_or_repeated_climax_rate": round(
                mean(
                    float(
                        sample_by_key[
                            (case.case_id, mode)
                        ].stitched_or_repeated_climax_count
                        > 0
                    )
                    for case in cases
                    if (case.case_id, mode) in sample_by_key
                ),
                4,
            )
            if any((case.case_id, mode) in sample_by_key for case in cases)
            else 0.0,
        }
        for mode in MODES
    }

    score_delta = mode_means[MODE_CHAPTER_FIRST] - mode_means[MODE_SCENE_BY_SCENE]
    candidate = MODE_CHAPTER_FIRST if score_delta >= 0 else MODE_SCENE_BY_SCENE
    opponent = MODE_SCENE_BY_SCENE if candidate == MODE_CHAPTER_FIRST else MODE_CHAPTER_FIRST
    coverage_ok = (
        deterministic[candidate]["mean_required_event_coverage"] + 0.05
        >= deterministic[opponent]["mean_required_event_coverage"]
    )
    required_judgement_count = len(cases) * 4
    enough_judgements = len(judgements) >= required_judgement_count
    decision = "inconclusive"
    distinct_judge_paths = sorted({item.judge_model for item in judgements})
    if (
        not missing_samples
        and enough_judgements
        and len(distinct_judge_paths) >= 2
        and position_agreement >= 0.80
        and abs(score_delta) >= 0.30
        and case_wins[candidate] >= 2
        and coverage_ok
    ):
        decision = candidate

    e2_blockers: list[str] = []
    if missing_samples:
        e2_blockers.append("E2_PAIRED_SAMPLES_INCOMPLETE")
    if not enough_judgements or len(distinct_judge_paths) < 2:
        e2_blockers.append("E2_INDEPENDENT_REVIEW_PATHS_INSUFFICIENT")
    if position_agreement < 0.80:
        e2_blockers.append("E2_POSITION_SWAP_AGREEMENT_INSUFFICIENT")
    chapter_length_passed = (
        deterministic[MODE_CHAPTER_FIRST]["length_pass_rate"] >= 0.90
    )
    hard_failure_guard_passed = (
        deterministic[MODE_CHAPTER_FIRST]["hard_integrity_failure_count"]
        <= deterministic[MODE_SCENE_BY_SCENE]["hard_integrity_failure_count"]
    )
    stitched_delta = (
        deterministic[MODE_SCENE_BY_SCENE]["stitched_or_repeated_climax_rate"]
        - deterministic[MODE_CHAPTER_FIRST]["stitched_or_repeated_climax_rate"]
    )
    stitched_guard_passed = stitched_delta >= 0.10
    if not chapter_length_passed:
        e2_blockers.append("E2_CHAPTER_FIRST_LENGTH_RATE_INSUFFICIENT")
    if not hard_failure_guard_passed:
        e2_blockers.append("E2_CHAPTER_FIRST_HARD_FAILURE_REGRESSION")
    if not stitched_guard_passed:
        e2_blockers.append("E2_STITCHED_OR_REPEATED_CLIMAX_NOT_REDUCED")

    evidence_blockers = {
        "E2_PAIRED_SAMPLES_INCOMPLETE",
        "E2_INDEPENDENT_REVIEW_PATHS_INSUFFICIENT",
        "E2_POSITION_SWAP_AGREEMENT_INSUFFICIENT",
    }
    if any(code in evidence_blockers for code in e2_blockers) or decision == "inconclusive":
        e2_release_status = "INCONCLUSIVE_E2"
        e2_recommended_mode = "inconclusive"
    elif decision == MODE_CHAPTER_FIRST and not e2_blockers:
        e2_release_status = "PASS_E2_CHAPTER_FIRST"
        e2_recommended_mode = MODE_CHAPTER_FIRST
    else:
        e2_release_status = "PASS_E2_KEEP_SCENE"
        e2_recommended_mode = MODE_SCENE_BY_SCENE

    return {
        "decision": decision,
        "mode_weighted_scores": mode_means,
        "score_delta_chapter_first_minus_scene": round(score_delta, 4),
        "dimension_scores": dimension_means,
        "case_wins": case_wins,
        "case_results": case_results,
        "position_agreement_rate": round(position_agreement, 4),
        "position_complete_pairs": complete_pairs,
        "position_agreement_by_judge": position_by_judge,
        "mode_weighted_scores_by_judge": judge_mode_scores,
        "deterministic": deterministic,
        "missing_samples": [list(item) for item in missing_samples],
        "judgement_count": len(judgements),
        "required_judgement_count": required_judgement_count,
        "enough_judgements": enough_judgements,
        "coverage_guard_passed": coverage_ok,
        "distinct_judge_paths": distinct_judge_paths,
        "e2_gate": {
            "release_status": e2_release_status,
            "recommended_mode": e2_recommended_mode,
            "blocking_codes": list(dict.fromkeys(e2_blockers)),
            "chapter_first_length_passed": chapter_length_passed,
            "hard_failure_guard_passed": hard_failure_guard_passed,
            "stitched_or_repeated_climax_guard_passed": stitched_guard_passed,
            "stitched_or_repeated_climax_rate_delta": round(stitched_delta, 4),
            "position_swap_agreement_required": 0.80,
            "independent_review_paths_required": 2,
        },
    }


def sample_to_dict(sample: GeneratedSample) -> dict[str, Any]:
    payload = asdict(sample)
    return payload


def judgement_to_dict(judgement: PairwiseJudgement) -> dict[str, Any]:
    return asdict(judgement)


def _normalise_scores(raw: Any) -> dict[str, float]:
    mapping = raw if isinstance(raw, Mapping) else {}
    scores: dict[str, float] = {}
    for key in SCORE_DIMENSIONS:
        try:
            value = float(mapping.get(key, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        scores[key] = max(0.0, min(10.0, value))
    return scores


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(text[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("judge response did not contain a JSON object")


def _recover_pairwise_payload(raw_text: str) -> dict[str, Any]:
    """Recover score objects from a judge response with broken outer JSON.

    Some reasoning models emit valid ``A`` and ``B`` score objects but omit a
    comma later in the response. The experiment can safely retain the numeric
    vote while dropping malformed free-form evidence; it must never invent a
    score or winner.
    """

    text = str(raw_text or "")
    scores_anchor = text.find('"scores"')
    score_region = text[scores_anchor:] if scores_anchor >= 0 else text
    a_scores = _extract_object_after_key(score_region, "A")
    b_scores = _extract_object_after_key(score_region, "B")
    winner_match = re.search(r'"winner"\s*:\s*"(A|B|tie)"', text, flags=re.I)
    if a_scores is None or b_scores is None or winner_match is None:
        raise ValueError("could not recover required pairwise judge fields")
    return {
        "scores": {"A": a_scores, "B": b_scores},
        "winner": winner_match.group(1),
        "evidence": {},
        "risk_notes": ["outer JSON malformed; retained valid score objects only"],
    }


def _extract_object_after_key(text: str, key: str) -> dict[str, Any] | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', text)
    if match is None:
        return None
    start = text.find("{", match.start())
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


__all__ = [
    "MODE_CHAPTER_FIRST",
    "MODE_SCENE_BY_SCENE",
    "ChapterABCase",
    "DeterministicScore",
    "GeneratedSample",
    "PairwiseJudgement",
    "SceneBeat",
    "assemble_scene_texts",
    "build_chapter_first_prompts",
    "build_default_cases",
    "build_pairwise_judge_prompts",
    "build_scene_prompts",
    "judgement_to_dict",
    "parse_pairwise_judgement",
    "sample_to_dict",
    "score_generated_sample",
    "summarize_experiment",
    "weighted_score",
]
