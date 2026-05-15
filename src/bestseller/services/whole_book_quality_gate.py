# ruff: noqa: RUF001

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.services.emotion_driven_kernel import (
    EmotionDrivenKernel,
    emotion_driven_kernel_from_dict,
    evaluate_emotion_contracts,
)


@dataclass(frozen=True)
class WholeBookQualityFinding:
    code: str
    severity: str
    scope: str
    message: str
    evidence: str
    chapter_number: int | None = None
    volume_number: int | None = None


@dataclass(frozen=True)
class ChapterEngagementRecord:
    chapter_number: int
    has_conflict: bool
    has_action: bool
    has_payoff: bool
    has_hook: bool
    has_reveal: bool
    has_decision: bool
    has_emotional_turn: bool
    loop_score: int
    momentum_score: int
    functional_shape: str
    opening_signature: str
    ending_signature: str


@dataclass(frozen=True)
class WholeBookQualityReport:
    passed: bool
    findings: tuple[WholeBookQualityFinding, ...]
    ledger: tuple[ChapterEngagementRecord, ...]
    metrics: dict[str, object]


_CONFLICT_TERMS = (
    "逼", "夺", "抢", "杀", "逃", "威胁", "否则", "必须", "不能", "冲突", "证据", "危险", "逼迫",
    "追", "债", "欠", "封印", "松动", "反噬",
    "threat", "forced", "must", "cannot", "or else", "danger", "pressure", "conflict", "evidence",
)
_ACTION_TERMS = (
    "抓", "按", "推", "摔", "砸", "冲", "躲", "拔", "抬", "扯", "扣", "夺", "撕", "盯",
    "拦", "挡", "挖", "取", "攥", "滴", "提", "挂", "转身", "起身", "伸手", "行动", "反制",
    "grab", "push", "pull", "run", "strike", "dodge", "block", "move", "act", "counter",
)
_PAYOFF_TERMS = (
    "拿到", "夺回", "抢回", "反制", "翻盘", "小胜", "救下", "证据", "筹码", "突破",
    "升级", "奖励", "解锁", "发现", "撬开", "关键", "取出", "拿出", "确认", "映入",
    "gain", "win", "save", "evidence", "leverage", "breakthrough", "reward", "unlock",
    "discover", "reversal",
)
_PAYOFF_NEGATIONS = (
    "没有拿到", "没拿到", "没有任何筹码", "没有得到", "毫无收获",
    "no reward", "gained nothing", "no leverage",
)
_COST_TERMS = (
    "代价", "暴露", "失去", "受伤", "疼", "牺牲", "风险", "罚", "断裂",
    "cost", "exposed", "lost", "wound", "sacrifice", "risk", "punished",
)
_REVEAL_TERMS = (
    "真相", "发现", "原来", "秘密", "线索", "暗号", "证实", "看出", "明白",
    "reveal", "realize", "learn", "discover", "clue", "secret", "proof",
)
_DECISION_TERMS = (
    "决定", "选择", "发誓", "打算", "下一步", "只好", "必须", "不能再", "转身去",
    "decide", "choose", "vow", "plan", "next", "must", "will", "cannot",
)
_EMOTIONAL_TURN_TERMS = (
    "心口", "发冷", "颤", "恐惧", "愤怒", "后悔", "羞耻", "疼", "沉默", "眼眶",
    "fear", "grief", "anger", "regret", "shame", "ache", "tremble", "silent",
)
_HOOK_TERMS = (
    "？", "?", "否则", "就在这时", "突然", "门外", "真相", "谁", "不能", "必须",
    "只剩", "却", "响起", "下一刻", "不是", "而是", "等你", "送回来", "第七",
    "or else", "suddenly", "truth", "who", "must", "cannot", "only", "but", "then", "next",
)

_STRATEGY_BY_CODE = {
    "early_retention_hook_density_low": "early_retention_hook_rebuild",
    "early_retention_turn_density_low": "early_retention_turn_reseed",
    "chapter_function_missing": "chapter_function_rewrite",
    "chapter_loop_missing": "chapter_serial_loop_rewrite",
    "chapter_hook_missing": "chapter_hook_rebuild",
    "chapter_payoff_missing": "chapter_payoff_rebuild",
    "rolling_payoff_gap": "rolling_payoff_reseed",
    "rolling_repetition": "rolling_freshness_rewrite",
    "arc_payoff_missing": "arc_closure_rewrite",
    "volume_momentum_drop": "volume_momentum_rebuild",
    "emotion_visible_desire_gap": "emotion_empathy_chain_rebuild",
    "emotion_irreversible_cost_gap": "emotion_cost_reseed",
    "emotion_bomb_payoff_stale": "emotion_bomb_payoff_reseed",
    "emotion_flat_chain": "emotion_chain_reseed",
    "emotion_cost_free_win": "emotion_cost_reseed",
    "emotion_ending_texture_not_ready": "emotion_ending_texture_rewrite",
    "emotion_kernel_invalid": "emotion_contract_rebuild",
}
_SIGNING_ZONE_END = 50
_EXTENDED_ENTRY_ZONE_END = 100
_SIGNING_ZONE_MIN_SAMPLE = 5
_EXTENDED_ZONE_MIN_SAMPLE = 8
_SIGNING_HOOK_DENSITY_MIN = 0.75
_SIGNING_PULL_DENSITY_MIN = 0.9
_SIGNING_TURN_DENSITY_MIN = 0.6
_EXTENDED_HOOK_DENSITY_MIN = 0.6
_EXTENDED_PULL_DENSITY_MIN = 0.8
_EXTENDED_TURN_DENSITY_MIN = 0.5
_EMOTION_COVERAGE_MIN = 0.5
_EMOTION_FLAT_STREAK_MIN = 3
_EMOTION_COST_TERMS = (
    *_COST_TERMS,
    "不可逆",
    "回不去",
    "错过",
    "破裂",
    "债",
    "伤痕",
    "代价不能",
    "不能复原",
    "cannot be restored",
    "irreversible",
    "debt",
)
_EMOTION_WIN_TERMS = (
    "赢",
    "胜",
    "成功",
    "幸福",
    "圆满",
    "反杀",
    "翻盘",
    "拿回",
    "得到",
    "win",
    "wins",
    "victory",
    "happy",
    "fulfilled",
)


def _normalize_chapter_texts(
    chapter_texts: Mapping[int, str] | Sequence[str],
) -> list[tuple[int, str]]:
    if isinstance(chapter_texts, Mapping):
        return [(int(number), str(text or "")) for number, text in sorted(chapter_texts.items())]
    return [(idx, str(text or "")) for idx, text in enumerate(chapter_texts, start=1)]


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _has_payoff(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in _PAYOFF_NEGATIONS):
        return False
    return _contains_any(text, _PAYOFF_TERMS) or _contains_any(text, _COST_TERMS)


def _tail(text: str, chars: int = 260) -> str:
    stripped = text.strip()
    return stripped[-chars:] if len(stripped) > chars else stripped


def _signature(text: str, chars: int = 28) -> str:
    compact = "".join(str(text or "").split())
    for mark in ("。", ".", "！", "!", "？", "?"):
        if mark in compact:
            return compact.split(mark, 1)[0][:80]
    return compact[:chars]


def _ending_signature(text: str, chars: int = 32) -> str:
    compact = "".join(str(text or "").split())
    return compact[-chars:] if len(compact) > chars else compact


def _functional_shape(
    *,
    has_conflict: bool,
    has_action: bool,
    has_payoff: bool,
    has_hook: bool,
    has_reveal: bool,
    has_decision: bool,
    has_emotional_turn: bool,
) -> str:
    if has_conflict and has_action and (has_payoff or has_reveal or has_hook):
        return "proactive_scene"
    if has_emotional_turn and has_decision and (has_conflict or has_reveal or has_hook):
        return "reactive_sequel"
    if has_reveal and (has_conflict or has_decision or has_hook):
        return "reveal_turn"
    if has_payoff and (has_hook or has_decision or has_emotional_turn):
        return "payoff_resolution"
    if has_conflict and (has_decision or has_hook):
        return "pressure_setup"
    return "flat"


def _record_for_chapter(chapter_number: int, text: str) -> ChapterEngagementRecord:
    tail = _tail(text)
    has_conflict = _contains_any(text, _CONFLICT_TERMS)
    has_action = _contains_any(text, _ACTION_TERMS)
    has_payoff = _has_payoff(text)
    has_hook = _contains_any(tail, _HOOK_TERMS) or text.rstrip().endswith(("？", "?", "！", "!"))
    has_reveal = _contains_any(text, _REVEAL_TERMS)
    has_decision = _contains_any(text, _DECISION_TERMS)
    has_emotional_turn = _contains_any(text, _EMOTIONAL_TURN_TERMS)
    loop_score = sum((has_conflict, has_action, has_payoff, has_hook))
    momentum_score = sum((
        has_conflict,
        has_action,
        has_payoff,
        has_hook,
        has_reveal,
        has_decision,
        has_emotional_turn,
    ))
    functional_shape = _functional_shape(
        has_conflict=has_conflict,
        has_action=has_action,
        has_payoff=has_payoff,
        has_hook=has_hook,
        has_reveal=has_reveal,
        has_decision=has_decision,
        has_emotional_turn=has_emotional_turn,
    )
    return ChapterEngagementRecord(
        chapter_number=chapter_number,
        has_conflict=has_conflict,
        has_action=has_action,
        has_payoff=has_payoff,
        has_hook=has_hook,
        has_reveal=has_reveal,
        has_decision=has_decision,
        has_emotional_turn=has_emotional_turn,
        loop_score=loop_score,
        momentum_score=momentum_score,
        functional_shape=functional_shape,
        opening_signature=_signature(text),
        ending_signature=_ending_signature(text),
    )


def _volume_ranges(volume_plan: object, chapter_numbers: list[int]) -> list[tuple[int, int, int]]:
    if not chapter_numbers:
        return []
    if not isinstance(volume_plan, list):
        return [(1, min(chapter_numbers), max(chapter_numbers))]

    ranges: list[tuple[int, int, int]] = []
    cursor = min(chapter_numbers)
    for idx, entry in enumerate(volume_plan, start=1):
        if not isinstance(entry, dict):
            continue
        volume_number = int(entry.get("volume_number") or idx)
        arc_ranges = entry.get("arc_ranges")
        if isinstance(arc_ranges, list):
            starts: list[int] = []
            ends: list[int] = []
            for item in arc_ranges:
                if isinstance(item, list) and len(item) == 2:
                    starts.append(int(item[0]))
                    ends.append(int(item[1]))
            if starts and ends:
                ranges.append((volume_number, min(starts), max(ends)))
                continue
        count = int(entry.get("chapter_count_target") or 0)
        if count > 0:
            ranges.append((volume_number, cursor, cursor + count - 1))
            cursor += count
    return ranges or [(1, min(chapter_numbers), max(chapter_numbers))]


def _density(records: Sequence[ChapterEngagementRecord], attr: str) -> float:
    if not records:
        return 0.0
    return sum(1 for record in records if bool(getattr(record, attr))) / len(records)


def _turn_density(records: Sequence[ChapterEngagementRecord]) -> float:
    if not records:
        return 0.0
    return sum(
        1
        for record in records
        if record.has_payoff or record.has_reveal or record.has_emotional_turn
    ) / len(records)


def _pull_density(records: Sequence[ChapterEngagementRecord]) -> float:
    if not records:
        return 0.0
    return sum(1 for record in records if record.has_hook or record.has_decision) / len(records)


def _max_no_hook_streak(records: Sequence[ChapterEngagementRecord]) -> int:
    longest = 0
    current = 0
    for record in records:
        if record.has_hook:
            current = 0
            continue
        current += 1
        longest = max(longest, current)
    return longest


def _early_retention_findings(
    ledger: Sequence[ChapterEngagementRecord],
) -> list[WholeBookQualityFinding]:
    findings: list[WholeBookQualityFinding] = []
    signing_records = [
        record for record in ledger if 1 <= record.chapter_number <= _SIGNING_ZONE_END
    ]
    if len(signing_records) >= _SIGNING_ZONE_MIN_SAMPLE:
        hook_density = _density(signing_records, "has_hook")
        pull_density = _pull_density(signing_records)
        turn_density = _turn_density(signing_records)
        no_hook_streak = _max_no_hook_streak(signing_records)
        if (
            hook_density < _SIGNING_HOOK_DENSITY_MIN
            or pull_density < _SIGNING_PULL_DENSITY_MIN
            or no_hook_streak >= 3
        ):
            findings.append(
                WholeBookQualityFinding(
                    code="early_retention_hook_density_low",
                    severity="critical",
                    scope="early_retention_zone",
                    message="前50章是签约/付费决策区，钩子密度或连续牵引不足。",
                    evidence=(
                        f"chapters=1-{_SIGNING_ZONE_END}, "
                        f"hook_density={hook_density:.2f}, "
                        f"pull_density={pull_density:.2f}, "
                        f"max_no_hook_streak={no_hook_streak}"
                    ),
                )
            )
        if turn_density < _SIGNING_TURN_DENSITY_MIN:
            findings.append(
                WholeBookQualityFinding(
                    code="early_retention_turn_density_low",
                    severity="critical",
                    scope="early_retention_zone",
                    message="前50章缺少足够高频的收益、揭露或情绪转折，签约吸引力不足。",
                    evidence=(
                        f"chapters=1-{_SIGNING_ZONE_END}, "
                        f"turn_density={turn_density:.2f}"
                    ),
                )
            )

    extended_records = [
        record
        for record in ledger
        if _SIGNING_ZONE_END < record.chapter_number <= _EXTENDED_ENTRY_ZONE_END
    ]
    if len(extended_records) >= _EXTENDED_ZONE_MIN_SAMPLE:
        hook_density = _density(extended_records, "has_hook")
        pull_density = _pull_density(extended_records)
        turn_density = _turn_density(extended_records)
        no_hook_streak = _max_no_hook_streak(extended_records)
        if (
            hook_density < _EXTENDED_HOOK_DENSITY_MIN
            or pull_density < _EXTENDED_PULL_DENSITY_MIN
            or no_hook_streak >= 4
        ):
            findings.append(
                WholeBookQualityFinding(
                    code="early_retention_hook_density_low",
                    severity="high",
                    scope="extended_entry_zone",
                    message="第51-100章仍属于早期留存区，钩子或下一步牵引密度不足。",
                    evidence=(
                        f"chapters={_SIGNING_ZONE_END + 1}-{_EXTENDED_ENTRY_ZONE_END}, "
                        f"hook_density={hook_density:.2f}, "
                        f"pull_density={pull_density:.2f}, "
                        f"max_no_hook_streak={no_hook_streak}"
                    ),
                )
            )
        if turn_density < _EXTENDED_TURN_DENSITY_MIN:
            findings.append(
                WholeBookQualityFinding(
                    code="early_retention_turn_density_low",
                    severity="high",
                    scope="extended_entry_zone",
                    message="第51-100章收益、揭露或情绪转折密度不足，早期留存会走低。",
                    evidence=(
                        f"chapters={_SIGNING_ZONE_END + 1}-{_EXTENDED_ENTRY_ZONE_END}, "
                        f"turn_density={turn_density:.2f}"
                    ),
                )
            )
    return findings


def _retention_metrics(ledger: Sequence[ChapterEngagementRecord]) -> dict[str, object]:
    signing_records = [
        record for record in ledger if 1 <= record.chapter_number <= _SIGNING_ZONE_END
    ]
    extended_records = [
        record
        for record in ledger
        if _SIGNING_ZONE_END < record.chapter_number <= _EXTENDED_ENTRY_ZONE_END
    ]
    return {
        "signing_zone": {
            "chapter_count": len(signing_records),
            "hook_density": round(_density(signing_records, "has_hook"), 2),
            "pull_density": round(_pull_density(signing_records), 2),
            "turn_density": round(_turn_density(signing_records), 2),
            "max_no_hook_streak": _max_no_hook_streak(signing_records),
        },
        "extended_entry_zone": {
            "chapter_count": len(extended_records),
            "hook_density": round(_density(extended_records, "has_hook"), 2),
            "pull_density": round(_pull_density(extended_records), 2),
            "turn_density": round(_turn_density(extended_records), 2),
            "max_no_hook_streak": _max_no_hook_streak(extended_records),
        },
    }


def _coerce_emotion_kernel(
    payload: EmotionDrivenKernel | Mapping[str, Any] | None,
) -> tuple[EmotionDrivenKernel | None, str | None]:
    if payload is None:
        return None, None
    if isinstance(payload, EmotionDrivenKernel):
        return payload, None
    if isinstance(payload, Mapping):
        try:
            return emotion_driven_kernel_from_dict(dict(payload)), None
        except Exception as exc:  # pragma: no cover - exact pydantic message is unstable
            return None, str(exc)
    return None, f"unsupported payload type: {type(payload).__name__}"


def _chapter_range_bounds(chapter_range: str) -> tuple[int, int] | None:
    numbers = [int(value) for value in re.findall(r"\d+", str(chapter_range or ""))]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    start, end = numbers[0], numbers[1]
    return min(start, end), max(start, end)


def _chapters_for_range(chapter_range: str, chapter_numbers: Sequence[int]) -> set[int]:
    if not chapter_numbers:
        return set()
    bounds = _chapter_range_bounds(chapter_range)
    if bounds is None:
        return set(chapter_numbers)
    start, end = bounds
    return {number for number in chapter_numbers if start <= number <= end}


def _records_for_bounds(
    ledger: Sequence[ChapterEngagementRecord],
    bounds: tuple[int, int],
) -> list[ChapterEngagementRecord]:
    start, end = bounds
    return [record for record in ledger if start <= record.chapter_number <= end]


def _emotion_text_has_cost(*values: str) -> bool:
    return _contains_any(" ".join(str(value or "") for value in values), _EMOTION_COST_TERMS)


def _emotion_text_has_win(*values: str) -> bool:
    return _contains_any(" ".join(str(value or "") for value in values), _EMOTION_WIN_TERMS)


def _repeated_emotion_modes(kernel: EmotionDrivenKernel) -> list[dict[str, object]]:
    streaks: list[dict[str, object]] = []
    current_mode = ""
    current_ranges: list[str] = []
    current_count = 0

    def flush() -> None:
        if current_mode and current_count >= _EMOTION_FLAT_STREAK_MIN:
            streaks.append(
                {
                    "emotion": current_mode,
                    "streak": current_count,
                    "chapter_ranges": list(current_ranges),
                }
            )

    for beat in kernel.emotion_chain:
        mode = " ".join(str(beat.target_reader_emotion or "").lower().split())
        if not mode:
            flush()
            current_mode = ""
            current_ranges = []
            current_count = 0
            continue
        if mode == current_mode:
            current_count += 1
            current_ranges.append(beat.chapter_range)
            continue
        flush()
        current_mode = mode
        current_ranges = [beat.chapter_range]
        current_count = 1
    flush()
    return streaks


def _emotion_quality_metrics_and_findings(
    emotion_driven_kernel: EmotionDrivenKernel | Mapping[str, Any] | None,
    ledger: Sequence[ChapterEngagementRecord],
) -> tuple[dict[str, object], list[WholeBookQualityFinding]]:
    chapter_numbers = [record.chapter_number for record in ledger]
    chapter_count = len(chapter_numbers)
    metrics: dict[str, object] = {"available": emotion_driven_kernel is not None}
    findings: list[WholeBookQualityFinding] = []

    kernel, kernel_error = _coerce_emotion_kernel(emotion_driven_kernel)
    if kernel is None:
        if kernel_error:
            metrics.update({"valid": False, "error": kernel_error})
            findings.append(
                WholeBookQualityFinding(
                    code="emotion_kernel_invalid",
                    severity="high",
                    scope="emotion_driven_kernel",
                    message="情绪驱动核心无法解析，全书门禁无法读取代入、炸弹和结局纹理合同。",
                    evidence=kernel_error[:240],
                )
            )
        return metrics, findings

    contract_report = evaluate_emotion_contracts(kernel)
    visible_desire_chapters: set[int] = set()
    for item in kernel.empathy_contracts:
        if item.current_desire.strip():
            visible_desire_chapters.update(
                _chapters_for_range(item.chapter_range, chapter_numbers)
            )

    cost_chapters: set[int] = set()
    for item in kernel.empathy_contracts:
        if _emotion_text_has_cost(item.fear_or_loss, item.flaw_pressure, item.consequence):
            cost_chapters.update(_chapters_for_range(item.chapter_range, chapter_numbers))
    for item in kernel.emotion_chain:
        if _emotion_text_has_cost(
            item.reader_worry,
            item.pressure_source,
            item.payoff_or_aftereffect,
            item.callback,
        ):
            cost_chapters.update(_chapters_for_range(item.chapter_range, chapter_numbers))
    for item in kernel.bomb_contracts:
        if _emotion_text_has_cost(item.danger, item.consequence):
            chapter_range = item.payoff_window or item.chapter_range
            cost_chapters.update(_chapters_for_range(chapter_range, chapter_numbers))

    bomb_distances: list[dict[str, object]] = []
    stale_bomb_ids: list[str] = []
    current_chapter = max(chapter_numbers) if chapter_numbers else 0
    for index, item in enumerate(kernel.bomb_contracts, start=1):
        bomb_id = item.bomb_id or f"bomb-{index}"
        setup_bounds = _chapter_range_bounds(item.chapter_range)
        payoff_bounds = _chapter_range_bounds(item.payoff_window)
        if setup_bounds is not None and payoff_bounds is not None:
            distance = max(0, payoff_bounds[0] - setup_bounds[1])
            bomb_distances.append(
                {
                    "bomb_id": bomb_id,
                    "setup_start": setup_bounds[0],
                    "setup_end": setup_bounds[1],
                    "payoff_start": payoff_bounds[0],
                    "payoff_end": payoff_bounds[1],
                    "distance": distance,
                }
            )
            if current_chapter >= payoff_bounds[1]:
                payoff_records = _records_for_bounds(ledger, payoff_bounds)
                if not any(
                    record.has_payoff or record.has_reveal or record.has_emotional_turn
                    for record in payoff_records
                ):
                    stale_bomb_ids.append(bomb_id)

    repeated_modes = _repeated_emotion_modes(kernel)
    cost_free_sources: list[str] = []
    for index, item in enumerate(kernel.emotion_chain, start=1):
        if _emotion_text_has_win(item.payoff_or_aftereffect) and not _emotion_text_has_cost(
            item.payoff_or_aftereffect,
            item.callback,
        ):
            cost_free_sources.append(item.chapter_range or f"emotion_chain[{index}]")
    ending = kernel.ending_texture_contract
    if (
        ending is not None
        and ending.ending_type == "HE"
        and ending.core_wish_fulfilled.strip()
        and not ending.irreversible_cost_retained.strip()
    ):
        cost_free_sources.append("ending_texture_contract")

    ending_issue_codes = [
        issue.code
        for issue in contract_report.issues
        if issue.path.startswith("ending_texture_contract")
    ]
    ending_texture_ready = not ending_issue_codes
    visible_coverage = (
        len(visible_desire_chapters) / chapter_count if chapter_count else 0.0
    )
    cost_coverage = len(cost_chapters) / chapter_count if chapter_count else 0.0

    metrics.update(
        {
            "valid": contract_report.passed,
            "visible_desire_chapter_count": len(visible_desire_chapters),
            "visible_desire_coverage": round(visible_coverage, 2),
            "irreversible_cost_chapter_count": len(cost_chapters),
            "irreversible_cost_coverage": round(cost_coverage, 2),
            "bomb_count": len(kernel.bomb_contracts),
            "bomb_setup_payoff_distances": bomb_distances,
            "stale_bomb_ids": stale_bomb_ids,
            "repeated_emotion_modes": repeated_modes,
            "cost_free_win_sources": cost_free_sources,
            "ending_texture_ready": ending_texture_ready,
            "ending_issue_codes": ending_issue_codes,
        }
    )

    if chapter_count >= 3 and visible_coverage < _EMOTION_COVERAGE_MIN:
        findings.append(
            WholeBookQualityFinding(
                code="emotion_visible_desire_gap",
                severity="high" if not visible_desire_chapters else "medium",
                scope="emotion_chain",
                message="全书情绪合同覆盖的可见人物欲望不足，读者难以持续知道该期待什么。",
                evidence=(
                    f"visible_desire_coverage={visible_coverage:.2f}, "
                    f"chapters={chapter_count}"
                ),
            )
        )
    if (
        chapter_count >= 3
        and cost_coverage < _EMOTION_COVERAGE_MIN
        and any(record.has_payoff for record in ledger)
    ):
        findings.append(
            WholeBookQualityFinding(
                code="emotion_irreversible_cost_gap",
                severity="medium",
                scope="emotion_chain",
                message="收益或转折已有推进，但情绪合同里的不可逆代价覆盖不足。",
                evidence=(
                    f"irreversible_cost_coverage={cost_coverage:.2f}, "
                    f"payoff_chapters={sum(1 for record in ledger if record.has_payoff)}"
                ),
            )
        )
    if stale_bomb_ids:
        findings.append(
            WholeBookQualityFinding(
                code="emotion_bomb_payoff_stale",
                severity="high",
                scope="emotion_bomb",
                message="桌下炸弹的承诺兑现窗口已过，但窗口内没有可识别收益、揭露或情绪转折。",
                evidence=", ".join(stale_bomb_ids),
            )
        )
    if (chapter_count >= 3 and not kernel.emotion_chain) or repeated_modes:
        findings.append(
            WholeBookQualityFinding(
                code="emotion_flat_chain",
                severity="high",
                scope="emotion_chain",
                message="情绪链缺失或连续重复同一种目标情绪，读者情绪推进会变平。",
                evidence=str(repeated_modes[:3] or "emotion_chain_missing"),
            )
        )
    if cost_free_sources:
        findings.append(
            WholeBookQualityFinding(
                code="emotion_cost_free_win",
                severity=(
                    "high" if "ending_texture_contract" in cost_free_sources else "medium"
                ),
                scope="emotion_chain",
                message="胜利、翻盘或幸福兑现缺少对应代价，容易变成一键清零。",
                evidence=", ".join(cost_free_sources[:5]),
            )
        )
    if not ending_texture_ready:
        critical_ending = {
            issue.code
            for issue in contract_report.issues
            if issue.path.startswith("ending_texture_contract")
            and issue.severity == "critical"
        }
        findings.append(
            WholeBookQualityFinding(
                code="emotion_ending_texture_not_ready",
                severity="high" if critical_ending else "medium",
                scope="emotion_ending",
                message="结局纹理合同尚未能同时回答幸福/悲剧、不可逆代价与主题回收。",
                evidence=", ".join(ending_issue_codes),
            )
        )

    return metrics, findings


def evaluate_whole_book_quality(
    chapter_texts: Mapping[int, str] | Sequence[str],
    *,
    volume_plan: object = None,
    rolling_window: int = 10,
    emotion_driven_kernel: EmotionDrivenKernel | Mapping[str, Any] | None = None,
) -> WholeBookQualityReport:
    chapters = _normalize_chapter_texts(chapter_texts)
    ledger = tuple(
        _record_for_chapter(number, text)
        for number, text in chapters
        if str(text).strip()
    )
    findings: list[WholeBookQualityFinding] = []

    for record in ledger:
        if record.functional_shape == "flat" or record.momentum_score < 2:
            findings.append(
                WholeBookQualityFinding(
                    code="chapter_function_missing",
                    severity="high",
                    scope="chapter",
                    message="本章缺少可识别章节功能：既不像主动场景，也不像反应、揭露、兑现或蓄压章节。",
                    evidence=(
                        f"shape={record.functional_shape}, "
                        f"momentum_score={record.momentum_score}"
                    ),
                    chapter_number=record.chapter_number,
                )
            )
        elif (
            record.functional_shape in {"proactive_scene", "reveal_turn", "payoff_resolution"}
            and not (record.has_hook or record.has_decision)
        ):
            findings.append(
                WholeBookQualityFinding(
                    code="chapter_hook_missing",
                    severity="high",
                    scope="chapter",
                    message="本章功能需要向后牵引，但结尾缺少钩子或下一步决定。",
                    evidence=record.ending_signature,
                    chapter_number=record.chapter_number,
                )
            )
        elif (
            record.functional_shape == "proactive_scene"
            and not (record.has_payoff or record.has_reveal or record.has_emotional_turn)
        ):
            findings.append(
                WholeBookQualityFinding(
                    code="chapter_payoff_missing",
                    severity="medium",
                    scope="chapter",
                    message="本章有主动推进，但缺少收益、代价、揭露或情绪转折。",
                    evidence=f"chapter={record.chapter_number}",
                    chapter_number=record.chapter_number,
                )
            )

    if len(ledger) >= 3:
        window = ledger[-max(3, rolling_window):]
        no_turn = [
            record.chapter_number
            for record in window
            if not (record.has_payoff or record.has_reveal or record.has_emotional_turn)
        ]
        if len(no_turn) == len(window) and len(window) >= 5:
            findings.append(
                WholeBookQualityFinding(
                    code="rolling_payoff_gap",
                    severity="high",
                    scope="rolling_window",
                    message="滚动窗口内没有任何可识别回报、揭露或情绪转折，读者追读会断档。",
                    evidence=", ".join(str(item) for item in no_turn),
                )
            )
        repeated = [
            signature
            for signature, count in Counter(record.opening_signature for record in window).items()
            if signature and count >= 3
        ]
        if repeated:
            findings.append(
                WholeBookQualityFinding(
                    code="rolling_repetition",
                    severity="high",
                    scope="rolling_window",
                    message="滚动窗口内多章使用重复开场，阅读新鲜感下降。",
                    evidence=" | ".join(repeated[:3]),
                )
            )

    findings.extend(_early_retention_findings(ledger))

    by_number = {record.chapter_number: record for record in ledger}
    chapter_numbers = [number for number, _ in chapters]
    for volume_number, start, end in _volume_ranges(volume_plan, chapter_numbers):
        records = [by_number[number] for number in range(start, end + 1) if number in by_number]
        if len(records) < 3:
            continue
        last_two = records[-2:]
        if not any(
            record.has_payoff or record.has_reveal or record.has_emotional_turn
            for record in last_two
        ):
            findings.append(
                WholeBookQualityFinding(
                    code="arc_payoff_missing",
                    severity="high",
                    scope="arc",
                    message="剧情单元末端没有阶段兑现、揭露或情绪转折，单元闭环不足。",
                    evidence=f"chapters={last_two[0].chapter_number}-{last_two[-1].chapter_number}",
                    volume_number=volume_number,
                )
            )
        last = records[-1]
        if last.functional_shape == "flat" or not (last.has_hook or last.has_decision):
            findings.append(
                WholeBookQualityFinding(
                    code="volume_momentum_drop",
                    severity="high",
                    scope="volume",
                    message="卷/单元末章缺少继续追读的动力或下一步决定。",
                    evidence=(
                        f"chapter={last.chapter_number}, "
                        f"shape={last.functional_shape}, "
                        f"momentum_score={last.momentum_score}"
                    ),
                    chapter_number=last.chapter_number,
                    volume_number=volume_number,
                )
            )

    emotion_metrics, emotion_findings = _emotion_quality_metrics_and_findings(
        emotion_driven_kernel,
        ledger,
    )
    findings.extend(emotion_findings)

    high_or_critical = any(finding.severity in {"critical", "high"} for finding in findings)
    metrics = {
        "chapter_count": len(ledger),
        "flat_chapter_count": sum(1 for record in ledger if record.functional_shape == "flat"),
        "payoff_or_cost_chapter_count": sum(1 for record in ledger if record.has_payoff),
        "reveal_chapter_count": sum(1 for record in ledger if record.has_reveal),
        "decision_chapter_count": sum(1 for record in ledger if record.has_decision),
        "hook_or_decision_chapter_count": sum(
            1 for record in ledger if record.has_hook or record.has_decision
        ),
        "average_momentum_score": (
            round(sum(record.momentum_score for record in ledger) / len(ledger), 2)
            if ledger else 0.0
        ),
        "average_loop_score": (
            round(sum(record.loop_score for record in ledger) / len(ledger), 2)
            if ledger else 0.0
        ),
        "retention_zones": _retention_metrics(ledger),
        "emotion_driven": emotion_metrics,
    }
    return WholeBookQualityReport(
        passed=not high_or_critical,
        findings=tuple(findings),
        ledger=ledger,
        metrics=metrics,
    )


def whole_book_quality_report_to_dict(report: WholeBookQualityReport) -> dict[str, object]:
    return {
        "passed": report.passed,
        "metrics": dict(report.metrics),
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "scope": finding.scope,
                "message": finding.message,
                "evidence": finding.evidence,
                "chapter_number": finding.chapter_number,
                "volume_number": finding.volume_number,
            }
            for finding in report.findings
        ],
        "ledger": [
            {
                "chapter_number": record.chapter_number,
                "has_conflict": record.has_conflict,
                "has_action": record.has_action,
                "has_payoff": record.has_payoff,
                "has_hook": record.has_hook,
                "has_reveal": record.has_reveal,
                "has_decision": record.has_decision,
                "has_emotional_turn": record.has_emotional_turn,
                "loop_score": record.loop_score,
                "momentum_score": record.momentum_score,
                "functional_shape": record.functional_shape,
                "opening_signature": record.opening_signature,
                "ending_signature": record.ending_signature,
            }
            for record in report.ledger
        ],
    }


def whole_book_quality_strategy_for_findings(
    findings: tuple[WholeBookQualityFinding, ...] | list[WholeBookQualityFinding],
) -> str:
    for finding in findings:
        if finding.severity in {"critical", "high"}:
            return _STRATEGY_BY_CODE.get(finding.code, "chapter_serial_loop_rewrite")
    if findings:
        return _STRATEGY_BY_CODE.get(findings[0].code, "chapter_serial_loop_rewrite")
    return "chapter_serial_loop_rewrite"


def build_whole_book_quality_rewrite_instructions(
    findings: tuple[WholeBookQualityFinding, ...] | list[WholeBookQualityFinding],
    *,
    chapter_number: int,
    opening_quality_contract: dict[str, object] | None = None,
) -> str:
    strategy = whole_book_quality_strategy_for_findings(findings)
    loop_contract = ""
    if isinstance(opening_quality_contract, dict):
        loop_contract = str(opening_quality_contract.get("first_10000_loop") or "").strip()
    lines = [
        "【全书质量门禁重写任务】",
        f"- rewrite_strategy: {strategy}",
        "- 这不是润色任务；必须先明确本章功能，再重建对应的读者推进力。",
        "- 不要求每章同构；可选择主动场景、反应场景、揭露转折、兑现章或蓄压章。",
        "- 前50章按签约/付费决策区处理，前100章按早期留存区处理；"
        "这些章节必须保持更高的钩子密度和转折密度。",
        "- 但无论哪种功能，都必须给读者至少一种有效推进："
        "压力、选择、发现、代价、情绪转折或下一步牵引。",
        "- 若失败项涉及 emotion_*，重写时必须回到情绪链："
        "处境/欲望/感官/情绪/行动/后果，信息差/倒计时/严重后果/兑现，"
        "以及幸福或胜利后的不可逆代价。",
        f"- 章节：第{chapter_number}章",
    ]
    if loop_contract:
        lines.append(f"- 全书追读循环合同：{loop_contract}")
    if findings:
        lines.append("- 门禁失败项：")
        for finding in findings:
            mapped = _STRATEGY_BY_CODE.get(finding.code, "chapter_serial_loop_rewrite")
            lines.append(f"  - {finding.code} [{finding.severity}] -> {mapped}：{finding.message}")
    lines.append("- 输出要求：直接重写正文，不输出分析、计划、修改说明。")
    return "\n".join(lines)
