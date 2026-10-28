"""Deterministic prose signals, deliberately unregistered and audit-only.

No DB, model, project vocabulary, gate decision or text rewriting. Counts are
observations, not quality scores. Rates use non-whitespace Unicode characters
(including punctuation); all spans are half-open offsets in the original input.
Chinese/Arabic quantities and explicit Chinese speaker attributions are supported.
Semantic redundancy, implicit speakers and irony remain outside this probe.
"""

# ruff: noqa: RUF001 -- Full-width punctuation and Chinese numerals are intentional.

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import unicodedata

__all__ = ["AuditConfig", "DialogueTurn", "audit_prose"]

_CN = "零〇一二两三四五六七八九十百千万亿"
_NUM = rf"(?:-?\d+(?:\.\d+)?[万亿]?|负?[{_CN}]+(?:点[{_CN}]+)?[万亿]?)"
_TIME_NUM = rf"(?:\d{{1,2}}|[{_CN}]+)"
_CLOCK = re.compile(
    rf"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])|"
    rf"(?<![\d{_CN}])(?:凌晨|早上|上午|中午|午后|下午|傍晚|晚上|晚间)?"
    rf"(?P<hour>{_TIME_NUM})(?:点|时)"
    rf"(?:(?P<minute>{_TIME_NUM})分|(?P<part>半|一刻|三刻))?"
    rf"(?![\d{_CN}]|钟头|小时|意见|建议|看法)"
)
_QUANTITY = re.compile(
    rf"(?<![\d.\-])(?P<number>{_NUM})(?P<unit>块钱|元钱|人民币|公斤|千克|公里|"
    r"分钟|小时|万元|亿元|元|块|斤|克|米|桌|人|份|个|张|次|秒|天|两|枚|艘|辆|件|本)"
)
_CLAUSE = re.compile(r"[^，,。！？!?；;\n]+")
_QUOTE = re.compile(r'“([^”\n]*)”|「([^」\n]*)」|"([^"\n]*)"')
_SAY = r"(?:说道|问道|答道|喊道|说|问|答|喊)"
_NAME = r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{0,7}"
_CONDITION = (
    r"完好无损|原封不动|没有破损|没有移动|没有变化|未变|未动|"
    r"关闭|紧闭|关着|没有打开|未开启|开启|打开着"
)
_STATE = re.compile(
    r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_]{1,16}?)"
    r"(?:再次确认|确认|保持|依旧|依然|仍然|仍旧|还是|仍|还)"
    rf"(?P<state>在[^，,。！？!?；;\n]{{1,12}}|{_CONDITION})"
    rf"|(?P<intact>[\u4e00-\u9fffA-Za-z0-9_]{{1,16}}?)(?P<condition>{_CONDITION})"
)
_NORMAL_STATES = {
    "完好无损": "intact",
    "原封不动": "intact",
    "没有破损": "intact",
    "没有移动": "unmoved",
    "未动": "unmoved",
    "没有变化": "unchanged",
    "未变": "unchanged",
    "关闭": "closed",
    "紧闭": "closed",
    "关着": "closed",
    "没有打开": "closed",
    "未开启": "closed",
    "开启": "open",
    "打开着": "open",
}


@dataclass(frozen=True)
class AuditConfig:
    """One public rule set; callers can replace the lexicon, not enable a gate."""

    process_terms: tuple[str, ...] = (
        "封存",
        "封条",
        "保管",
        "见证",
        "核验",
        "核对",
        "确认",
        "登记",
        "记录",
        "交接",
        "签收",
        "验收",
        "凭证",
        "编号",
        "复核",
        "归档",
        "流程",
        "手续",
        "原件",
        "副本",
        "签字",
        "公证",
        "对账",
        "结算",
    )
    repetition_window: int = 1000
    negation_chain_min: int = 3
    dialogue_min_turns: int = 2
    dialogue_min_chars: int = 40

    def __post_init__(self) -> None:
        for value in (self.repetition_window, self.dialogue_min_turns, self.dialogue_min_chars):
            if type(value) is not int or value < 1:
                raise ValueError("Windows and sample sizes must be positive integers")
        if type(self.negation_chain_min) is not int or self.negation_chain_min < 2:
            raise ValueError("A negation chain needs at least two clauses")
        if not isinstance(self.process_terms, tuple) or any(
            not isinstance(term, str) or not term.strip() for term in self.process_terms
        ):
            raise ValueError("process_terms must be a tuple of nonempty literal strings")


@dataclass(frozen=True)
class DialogueTurn:
    speaker: str
    text: str


def _number(raw: str) -> Decimal | None:
    raw = raw.replace("两", "二").replace("〇", "零")
    sign = -1 if raw.startswith(("负", "-")) else 1
    raw = raw.lstrip("负-")
    multiplier = Decimal(1)
    if raw.endswith(("万", "亿")) and (raw[0].isdigit() or "点" in raw):
        multiplier = Decimal(10000 if raw[-1] == "万" else 100000000)
        raw = raw[:-1]
    try:
        return sign * Decimal(raw) * multiplier
    except InvalidOperation:
        pass
    integer, _, fraction = raw.partition("点")
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    total, section, digit = 0, 0, 0
    if not integer or any(c not in _CN for c in integer):
        return None
    if all(c in digits for c in integer):
        total = int("".join(str(digits[c]) for c in integer))
    else:
        for char in integer:
            if char in digits:
                digit = digits[char]
            elif char in "十百千":
                section += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                digit = 0
            elif char in "万亿":
                section = (section + digit) * (10000 if char == "万" else 100000000)
                total += section
                section, digit = 0, 0
        total += section + digit
    value = Decimal(total)
    if fraction:
        if any(c not in digits for c in fraction):
            return None
        value += Decimal("0." + "".join(str(digits[c]) for c in fraction))
    return sign * value * multiplier


def _normal_text(text: str) -> str:
    # Only one-codepoint compatibility substitutions keep offsets exact.
    return "".join(n if len(n := unicodedata.normalize("NFKC", c)) == 1 else c for c in text)


def _span(start: int, end: int) -> dict:
    return {"start": start, "end": end}


def _repetitions(hits: list[tuple[str, int, int]], window: int) -> dict:
    """Cluster identical normalized keys with bounded total span, per chapter."""
    clusters: dict[str, list[list[dict]]] = defaultdict(list)
    for key, start, end in hits:
        groups = clusters[key]
        if not groups or start - groups[-1][0]["start"] > window:
            groups.append([])
        groups[-1].append(_span(start, end))
    groups = [
        {"key": key, "mentions": len(spans), "spans": spans}
        for key, parts in clusters.items()
        for spans in parts
        if len(spans) > 1
    ]
    return {"repeated_mentions": sum(g["mentions"] - 1 for g in groups), "groups": groups}


def _extract_dialogue(text: str) -> tuple[list[DialogueTurn], int]:
    turns: list[DialogueTurn] = []
    transcript_ranges = []
    for match in re.finditer(rf"(?m)^\s*({_NAME})[：:]([^\n]+)", text):
        # Transcript syntax excludes clocks and full prose sentences.
        speaker = re.sub(rf"{_SAY}$", "", match[1])
        if speaker in {"他", "她", "它", "他们", "她们", "有人", "对方"}:
            continue
        turns.append(DialogueTurn(speaker, match[2]))
        transcript_ranges.append((match.start(), match.end()))
    unattributed = 0
    for quote in _QUOTE.finditer(text):
        if any(start <= quote.start() < end for start, end in transcript_ranges):
            continue
        prefix = text[max(0, quote.start() - 20) : quote.start()]
        suffix = text[quote.end() : quote.end() + 20]
        before = re.search(rf"(?:^|[。！？\n，,])({_NAME}){_SAY}[：:,，]?\s*$", prefix)
        after = re.match(rf"[，,]?({_NAME}){_SAY}", suffix)
        if before or after:
            speaker = (before or after)[1]
            if speaker in {"他", "她", "它", "他们", "她们", "有人", "对方"}:
                unattributed += 1
                continue
            turns.append(DialogueTurn(speaker, next(x for x in quote.groups() if x is not None)))
        else:
            unattributed += 1
    return turns, unattributed


def _dialogue(text: str, config: AuditConfig, supplied: Sequence[DialogueTurn] | None) -> dict:
    parsed, unknown = _extract_dialogue(text)
    turns = list(supplied) if supplied is not None else parsed
    buckets: dict[str, list[str]] = defaultdict(list)
    for turn in turns:
        if not turn.speaker.strip():
            raise ValueError("DialogueTurn requires an explicit speaker")
        cleaned = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", turn.text)).lower()
        if cleaned:
            buckets[turn.speaker].append(cleaned)
    speakers = []
    grams = {}
    for who, utterances in sorted(buckets.items()):
        chars = sum(map(len, utterances))
        eligible = (
            len(utterances) >= config.dialogue_min_turns and chars >= config.dialogue_min_chars
        )
        speakers.append(
            {"speaker": who, "turns": len(utterances), "characters": chars, "eligible": eligible}
        )
        if eligible:
            grams[who] = {u[i : i + 2] for u in utterances for i in range(len(u) - 1)}
    pairs = []
    names = sorted(grams)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            union = grams[a] | grams[b]
            pairs.append(
                {
                    "speakers": [a, b],
                    "bigram_jaccard": len(grams[a] & grams[b]) / len(union) if union else 0.0,
                }
            )
    return {
        "status": "measured" if pairs else "insufficient_sample",
        "source": "explicit_turns" if supplied is not None else "conservative_attribution",
        "attributed_turns": len(turns),
        "unattributed_quotes": unknown,
        "speakers": speakers,
        "pairs": pairs,
    }


def audit_prose(
    text: str,
    *,
    config: AuditConfig | None = None,
    dialogue_turns: Sequence[DialogueTurn] | None = None,
    entity_aliases: Mapping[str, str] | None = None,
) -> dict:
    """Measure one chapter. Never merges chapters or infers missing attribution.

    Aliases/turns must come from the caller's explicit local annotations. They
    are hashed into the receipt; no fuzzy pronoun resolution is attempted.
    State repetition is literal subject + normalized state, number repetition
    is value + unit (not a claim of redundant meaning or inconsistent accounts).
    """
    cfg = config or AuditConfig()
    normalized = _normal_text(text)
    aliases = dict(entity_aliases or {})
    size = sum(not c.isspace() for c in text)

    def rate(n: int) -> float:
        return n * 1000 / size if size else 0.0

    clocks = []
    for match in _CLOCK.finditer(normalized):
        if match["hour"] is not None:
            hour = _number(match["hour"])
            minute = _number(match["minute"]) if match["minute"] else Decimal(0)
            if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            # Bare 点 in an ordinary phrase is ambiguous: accept at clause end,
            # a pause, or followed by temporal/action syntax, not e.g. 点意见.
        clocks.append(_span(match.start(), match.end()))
    state_hits = []
    for match in _STATE.finditer(normalized):
        subject = match["subject"] or match["intact"]
        state = match["state"] or match["condition"]
        subject = re.sub(r"^(?:再次确认|确认|经确认|那个|这个|那只|这只)", "", subject)
        subject = aliases.get(subject, aliases.get(match["subject"] or match["intact"], subject))
        state_hits.append(
            (subject + ":" + _NORMAL_STATES.get(state, state), match.start(), match.end())
        )
    state_result = _repetitions(state_hits, cfg.repetition_window)
    state_result.update(
        {
            "candidate_mentions": len(state_hits),
            "per_1000_characters": rate(state_result["repeated_mentions"]),
        }
    )
    number_hits = []
    for match in _QUANTITY.finditer(normalized):
        if any(s["start"] < match.end() and match.start() < s["end"] for s in clocks):
            continue
        number = _number(match["number"])
        unit = match["unit"]
        if number is None:
            continue
        if unit in {"万元", "亿元"}:
            number *= 10000 if unit == "万元" else 100000000
            unit = "元"
        unit = {"块钱": "元", "元钱": "元", "人民币": "元", "块": "元", "公斤": "千克"}.get(
            unit, unit
        )
        number_hits.append((f"{number.normalize():f}:{unit}", match.start(), match.end()))
    number_result = _repetitions(number_hits, cfg.repetition_window)
    number_result.update(
        {
            "candidate_mentions": len(number_hits),
            "per_1000_characters": rate(number_result["repeated_mentions"]),
        }
    )
    counts = Counter()
    process_spans = []
    if cfg.process_terms:
        pattern = re.compile(
            "|".join(
                re.escape(t) for t in sorted(set(cfg.process_terms), key=lambda s: (-len(s), s))
            )
        )
        for match in pattern.finditer(normalized):
            counts[match[0]] += 1
            process_spans.append(_span(match.start(), match.end()))
    runs, current = [], []
    for clause in _CLAUSE.finditer(normalized):
        value = re.sub(r"不仅|不但|不错|不由自主|不得不|差不多|不小心|未时|未央", "", clause[0])
        negative = bool(
            re.search(r"没有|不曾|从未|并非|不是|未曾|不肯|不能|不会|不再|不必|不可|未|不", value)
        )
        if negative:
            current.append(_span(clause.start(), clause.end()))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    chains = [r for r in runs if len(r) >= cfg.negation_chain_min]
    config_data = asdict(cfg)
    config_json = json.dumps(config_data, ensure_ascii=False, sort_keys=True)
    annotations = {
        "aliases": aliases,
        "turns": [asdict(t) for t in dialogue_turns] if dialogue_turns is not None else None,
    }
    return {
        "schema": "prose-procedural-density.v1",
        "rule_version": "reader-first-signals.v1",
        "mode": "audit_only",
        "body_sha256": sha256(text.encode()).hexdigest(),
        "config": config_data,
        "config_sha256": sha256(config_json.encode()).hexdigest(),
        "annotations_sha256": sha256(
            json.dumps(annotations, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "metrics": {
            "characters": size,
            "clock": {
                "count": len(clocks),
                "per_1000_characters": rate(len(clocks)),
                "spans": clocks,
            },
            "state_repetition": state_result,
            "number_repetition": number_result,
            "process_terms": {
                "count": sum(counts.values()),
                "per_1000_characters": rate(sum(counts.values())),
                "term_counts": dict(counts),
                "spans": process_spans,
            },
            "negation_chains": {
                "chain_count": len(chains),
                "longest_chain": max(map(len, runs), default=0),
                "spans": chains,
            },
            "dialogue": _dialogue(text, cfg, dialogue_turns),
        },
        "limitations": [
            "Lexical signals do not establish readability or a literary defect.",
            "Aliases, paraphrases, units and implicit speakers are not semantically resolved.",
            "Repeated quantities can be necessary; overlap can reflect shared topics or quotation.",
        ],
    }
