"""Offline deterministic quantity audit; deliberately unregistered.

No IO, database, settings, pipeline or model imports. Rules are deliberately
small: absent findings do not establish consistency. Entity aliases and menu
families are explicit caller input, never inferred with a model. Decimal values
use yuan, jin (10 liang), minutes, or minute-of-day. No date rollover is guessed.
"""

# Chinese prose and punctuation are intentional rule input/output.
# ruff: noqa: RUF001, RUF003
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import re

from bestseller.services.checker_schema import CheckerIssue, CheckerReport

_DIGITS = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9), strict=True))
_SMALL = {"十": 10, "百": 100, "千": 1000}
_NUM = (
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|"
    r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二三四五六七八九]+)?|半)"
)
_Q = re.compile(
    rf"(?P<num>{_NUM})(?P<unit>块钱|公斤|千克|小时|钟头|分钟|元|块|桌|斤|两|克|道|轮|只|条|个|张|沓|分)"
)
_INT = r"(?:\d+|[零〇一二两三四五六七八九十百]+)"
_CLOCK = re.compile(
    rf"(?P<period>凌晨|早上|上午|中午|下午|晚上|今晚)?(?P<hour>{_INT})点(?:(?P<minute>{_INT})分?|(?P<half>半)|(?P<whole>整))?"
)
_HOURS = re.compile(rf"(?P<num>{_NUM})(?:个)?(?P<half>半)?(?:小时|钟头)")
_LIST = re.compile(rf"(?P<num>{_NUM})道(?P<category>冷菜|冷盘|热荤|热素|菜品|菜)[：:]([^。\n]+)")
_CHANGE = re.compile(r"变成|变为|改成|改为|增至|减至|增到|减到|又买|已付|付了|花了|追加|剩下|用掉")
_UNCERTAIN = re.compile(
    r"大约|大概|约|至少|至多|最多|最少|不超过|不到|超过|如果|假如|原计划|原来|旧|预计|打算|可能|还需|需要|差"
)
_DEFAULT_ALIASES = {"宴会厅": "婚宴", "宴席": "婚宴", "喜宴": "婚宴", "宾客": "婚宴"}


def parse_number(raw: str) -> Decimal:
    """Parse explicit decimal or Chinese positional numerals; reject guesses."""
    if raw == "半":
        return Decimal(".5")
    if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", raw):
        return Decimal(raw.replace(",", ""))
    if not raw or not re.fullmatch(
        r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二三四五六七八九]+)?", raw
    ):
        raise ValueError("not an explicit numeral")
    if "点" in raw:
        whole, fraction = raw.split("点")
        return parse_number(whole) + Decimal("0." + "".join(str(_DIGITS[c]) for c in fraction))
    for symbol, magnitude in (("亿", 100000000), ("万", 10000)):
        if symbol in raw:
            if raw.count(symbol) != 1:
                raise ValueError("repeated large numeral unit")
            left, right = raw.split(symbol)
            if not left:
                raise ValueError("large unit without coefficient")
            return parse_number(left) * magnitude + (parse_number(right) if right else 0)
    if all(c in _DIGITS for c in raw):
        return Decimal("".join(str(_DIGITS[c]) for c in raw))
    total = pending = 0
    last_unit = 10000
    for c in raw:
        if c in _DIGITS:
            if pending and _DIGITS[c]:
                raise ValueError("adjacent coefficients")
            pending = _DIGITS[c]
        else:
            unit = _SMALL[c]
            if unit >= last_unit:
                raise ValueError("non-descending numeral units")
            total += (pending or 1) * unit
            pending = 0
            last_unit = unit
    return Decimal(total + pending)


@dataclass(frozen=True)
class QuantityFact:
    chapter: int
    paragraph: int
    start: int
    end: int
    surface: str
    kind: str
    entity: str
    value: Decimal
    unit: str
    role: str = "assertion"
    items: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"ch{self.chapter}:p{self.paragraph}@{self.start}:{self.end}"


def _canonical(entity: str, aliases: Mapping[str, str]) -> str:
    # One hop only: cycles/chains cannot accidentally merge unrelated entities.
    return aliases.get(entity, entity)


def _entity(before: str, after: str, unit: str, aliases: Mapping[str, str]) -> str:
    if unit == "桌":
        match = re.search(r"([\u4e00-\u9fff]{1,8}?)(?:里的|里|共有|共|有|为|是)?$", before)
        name = match.group(1) if match else ""
        for known in ("东厅", "西厅", "婚宴", "宴会厅", "宴席", "喜宴"):
            if known in before:
                return _canonical(known, aliases)
        return _canonical(name, aliases) if name.endswith(("厅", "宴", "席")) else ""
    for label in ("预算余额", "总余额", "总预算", "每桌上限", "桌面上限", "总计", "合计"):
        if re.search(re.escape(label) + r"(?:为|是|：|:)?$", before):
            return {"总余额": "预算余额", "总计": "总成本", "合计": "总成本"}.get(label, label)
    # Require a nearby explicit noun; never equate all amounts sharing a unit.
    tail = re.split(
        r"[，,：:“”\s]|(?:每桌|每斤|每条|每只|共有|共|还有|还剩|还差|余额为|为|是|有)", before
    )[-1]
    tail = re.sub(r"^(?:现有|库存|采购|准备|需要|补货|订购)", "", tail)
    tail = re.sub(r"(?:还需|需要|需)$", "", tail)
    if tail in ("第", "一笔", "另一笔"):
        return ""
    if 0 < len(tail) <= 8 and re.fullmatch(r"[\u4e00-\u9fff]+", tail):
        return _canonical(tail, aliases)
    if not tail and unit in ("斤", "两", "公斤", "千克", "克"):
        match = re.match(r"([\u4e00-\u9fff]{2,6})(?:。|，|够用)", after)
        if match:
            return _canonical(match.group(1), aliases)
    return ""


def extract_facts(
    text: str, *, chapter: int = 1, aliases: Mapping[str, str] | None = None
) -> tuple[QuantityFact, ...]:
    """Extract source-addressable observations, including uncertain quantities."""
    names = {**_DEFAULT_ALIASES, **(aliases or {})}
    facts: list[QuantityFact] = []
    clock_spans: list[tuple[int, int]] = []

    def add(
        match: re.Match[str],
        kind: str,
        entity: str,
        value: Decimal,
        unit: str,
        role: str = "assertion",
        items: tuple[str, ...] = (),
    ) -> None:
        facts.append(
            QuantityFact(
                chapter,
                text[: match.start()].count("\n\n") + 1,
                match.start(),
                match.end(),
                match.group(),
                kind,
                entity,
                value,
                unit,
                role,
                items,
            )
        )

    for match in _CLOCK.finditer(text):
        if re.match(r"斤|两|公斤|千克|克|元|块", text[match.end() :]):
            continue
        # Decimal 点 is numeric syntax, not a clock. Require a time context.
        if (
            not match.group("period")
            and not match.group("minute")
            and not match.group("half")
            and not match.group("whole")
            and not re.search(
                r"开席|到|收铺|交|时|钟|现在|今天|明天",
                text[max(0, match.start() - 8) : match.end() + 12],
            )
        ):
            continue
        if (
            match.group("minute")
            and not match.group().endswith("分")
            and not match.group("period")
            and not re.search(r"十|二十|三十|四十|五十", match.group("minute"))
            and not re.search(
                r"开席|到|收铺|交|钟|现在|今天|明天",
                text[max(0, match.start() - 8) : match.end() + 12],
            )
        ):
            continue
        hour = parse_number(match.group("hour"))
        minute = (
            Decimal(30)
            if match.group("minute") == "半" or match.group("half")
            else parse_number(match.group("minute"))
            if match.group("minute")
            else Decimal(0)
        )
        if hour > 23 or minute >= 60:
            continue
        if match.group("period") in ("下午", "晚上", "今晚") and hour < 12:
            hour += 12
        add(match, "clock", "", hour * 60 + minute, "minute_of_day")
        clock_spans.append(match.span())
    for match in _HOURS.finditer(text):
        before = re.split(r"[。！？\n；;]", text[: match.start()])[-1][-28:]
        value = parse_number(match.group("num")) + (Decimal(".5") if match.group("half") else 0)
        add(match, "duration", _entity(before, "", "小时", names), value * 60, "minute")
        clock_spans.append(match.span())
    for match in _Q.finditer(text):
        if any(start <= match.start() < end for start, end in clock_spans):
            continue
        value = parse_number(match.group("num"))
        unit = match.group("unit")
        before = re.split(r"[。！？\n；;]", text[: match.start()])[-1][-28:]
        after = text[match.end() : match.end() + 16]
        if unit == "分" and after.startswith("之"):
            continue
        entity = _entity(before, after, unit, names)
        role = "assertion"
        if (
            _UNCERTAIN.search(before[-10:])
            or re.search(r"多|余|左右", after[:3])
            or re.search(r"[至到-]$", before)
        ):
            role = "uncertain"
        elif _CHANGE.search(before[-16:]):
            role = "change"
        if before.endswith("第"):
            role = "ordinal"
        if "每桌" in before[-4:]:
            role = "per_table"
            prefix = before.rsplit("每桌", 1)[0]
            entity = _entity(prefix, after, unit, names)
        if unit in ("元", "块钱") or (
            unit == "块" and re.search(r"钱|费|付|花|买|余额|收|价", before[-10:])
        ):
            kind, unit = "money", "yuan"
        elif unit in ("小时", "钟头", "分钟", "分"):
            kind = "duration"
            value *= 60 if unit in ("小时", "钟头") else 1
            unit = "minute"
        else:
            kind = "quantity"
            if unit in ("公斤", "千克", "克", "两"):
                value *= {
                    "公斤": Decimal(2),
                    "千克": Decimal(2),
                    "克": Decimal(".002"),
                    "两": Decimal(".1"),
                }[unit]
                unit = "斤"
        add(match, kind, entity, value, unit, role)
    for match in _LIST.finditer(text):
        raw_items = match.group(3)
        items = tuple(
            s.strip()
            for s in re.split(r"[；;]" if "每桌" in raw_items else r"[、，,和与]", raw_items)
            if s.strip()
        )
        add(
            match,
            "list",
            match.group("category"),
            parse_number(match.group("num")),
            "item",
            items=items,
        )
    return tuple(sorted(facts, key=lambda fact: (fact.start, fact.kind)))


def probe_chapters(
    chapters: Mapping[int, str], *, aliases: Mapping[str, str] | None = None
) -> CheckerReport:
    """Return soft audit findings only. `passed` never implies factual acceptance."""
    texts = dict(sorted(chapters.items()))
    if any(n < 1 or not isinstance(t, str) for n, t in texts.items()):
        raise ValueError("chapters require positive numbers and text")
    facts = tuple(
        f for n, text in texts.items() for f in extract_facts(text, chapter=n, aliases=aliases)
    )
    # An implicit table count is aligned only if its chapter names one event.
    table_events = {
        n: {f.entity for f in facts if f.chapter == n and f.unit == "桌" and f.entity}
        for n in texts
    }
    facts = tuple(
        replace(f, entity=next(iter(table_events[f.chapter])))
        if f.unit == "桌"
        and not f.entity
        and len(table_events[f.chapter]) == 1
        and re.match(r"的菜|宾客|分成", texts[f.chapter][f.end : f.end + 4])
        else f
        for f in facts
    )
    issues: list[CheckerIssue] = []
    evidence: list[dict] = []

    def emit(code: str, refs: list[str], description: str) -> None:
        key = f"{code}:{'|'.join(refs)}"
        identity = "QP_" + sha256(key.encode()).hexdigest()[:16]
        if any(i.id == identity for i in issues):
            return
        issues.append(
            CheckerIssue(
                identity,
                code,
                "medium",
                " ↔ ".join(refs),
                description,
                "核对实体、时间范围和实际变更；仅供编辑审计。",
                True,
            )
        )
        evidence.append({"id": identity, "fact_refs": refs})

    previous: dict[tuple, QuantityFact] = {}
    for fact in facts:
        if (
            fact.kind not in ("money", "quantity")
            or not fact.entity
            or fact.role not in ("assertion", "change")
        ):
            continue
        key = (fact.entity, fact.unit)
        old = previous.get(key)
        if old and fact.role != "change" and old.value != fact.value:
            emit(
                "quantity_mismatch",
                [old.ref, fact.ref],
                f"同名实体 {fact.entity} 的 {fact.unit} 数值 {old.value} / {fact.value} 不同，"
                "需确认状态是否变化。",
            )
        previous[key] = fact
    for n, text in texts.items():
        current = [f for f in facts if f.chapter == n]

        def ref_at(pos: int, number: int = n, body: str = text) -> str:
            return f"ch{number}:p{body[:pos].count(chr(10) + chr(10)) + 1}@{pos}"

        for fact in current:
            if fact.kind == "list" and fact.value != len(fact.items):
                emit(
                    "list_count_mismatch",
                    [fact.ref],
                    f"{fact.entity} 声明 {fact.value} 项，分隔符可识别 {len(fact.items)} 项。",
                )
        # Arithmetic only for explicitly total-scoped budget and cost claims.
        caps = list(re.finditer(rf"(?:总预算|总上限|预算上限)(?:为|是)?(?P<num>{_NUM})元", text))
        if not caps:
            caps = list(re.finditer(rf"(?:压到|砍到)(?P<num>{_NUM})(?:元)?", text))
        costs = list(re.finditer(rf"(?:总计|合计)(?P<num>{_NUM})元", text))
        if not costs:
            costs = list(re.finditer(rf"(?P<num>{_NUM})元(?=[，,]离)", text))
        margins = list(
            re.finditer(rf"(?:总余额|离[^。\n]{{0,12}}上限还[差剩有])(?P<num>{_NUM})元", text)
        )
        if caps and costs:
            cap = parse_number(caps[-1]["num"])
            # Colloquial budget 三千六 is only accepted with an explicit 压到/砍到 cue.
            if re.search(r"[千百][一二三四五六七八九]$", caps[-1]["num"]) and re.match(
                "压到|砍到", caps[-1].group()
            ):
                cap += parse_number(caps[-1]["num"][-1]) * (
                    100 if "千" in caps[-1]["num"] else 10
                ) - parse_number(caps[-1]["num"][-1])
            cost = parse_number(costs[-1]["num"])
            for margin in margins:
                prefix = re.split(r"[。\n]", text[: margin.start()])[-1]
                if re.search(r"每桌|桌面", prefix + margin.group()):
                    continue
                stated = parse_number(margin["num"])
                if cap - cost != stated:
                    emit(
                        "arithmetic_mismatch",
                        [
                            ref_at(caps[-1].start()),
                            ref_at(costs[-1].start()),
                            ref_at(margin.start()),
                        ],
                        f"总额余额：{cap} - {cost} = {cap - cost} 元，与 {stated} 元不符。",
                    )
        for total in re.finditer(rf"合计(?P<num>{_NUM})元[：:](?P<items>[^。\n]+)", text):
            parts = list(re.finditer(rf"({_NUM})元", total["items"]))
            value = sum((parse_number(p[1]) for p in parts), Decimal(0))
            if len(parts) >= 2 and value != parse_number(total["num"]):
                emit(
                    "sum_mismatch",
                    [ref_at(total.start())],
                    f"分项之和 {value} 元，与声明合计 {parse_number(total['num'])} 元不同。",
                )
        # Dependency suspicion requires an arrival claim and an explicit first-round item.
        arrivals = [
            f
            for f in current
            if f.kind == "duration"
            and re.match(r"(?:后|内)?(?:才|能|就)?(?:送|到)", text[f.end : f.end + 10])
        ]
        deadlines = list(re.finditer(rf"(?:还有|限时)(?P<num>{_NUM})分钟", text))
        for arrival in arrivals:
            for deadline in deadlines:
                context = text[max(0, deadline.start() - 45) : deadline.end()]
                item = arrival.entity
                first_round = re.search(r"第一轮[^。\n]*", context)
                if not first_round or re.search(r"不等|不用|现货|已经备好|只出冷盘", context):
                    continue
                if item and item not in first_round.group():
                    continue
                if arrival.value > parse_number(deadline["num"]):
                    emit(
                        "time_dependency",
                        [arrival.ref, ref_at(deadline.start())],
                        f"首轮期限 {parse_number(deadline['num'])} 分钟，"
                        f"所需补货 {arrival.value} 分钟才到。",
                    )
        # An unbound arrival + first-round countdown is deliberately weaker, audit only.
        if re.search(r"采购|补货", text) and re.search(r"第一轮.*还有", text):
            candidates = list(
                re.finditer(
                    rf"(?:全部到齐[^。\n]{{0,8}}?|最近[^。\n]{{0,8}}?)"
                    rf"(半|{_NUM})(?:个)?(?:小时|钟头)",
                    text,
                )
            )
            arrival = max(candidates, key=lambda m: parse_number(m[1]), default=None)
            if arrival and deadlines and not re.search(r"第一轮[^。\n]*(?:不等|只出冷盘)", text):
                duration = parse_number(arrival[1]) * 60
                if duration > parse_number(deadlines[-1]["num"]):
                    emit(
                        "time_dependency",
                        [ref_at(arrival.start()), ref_at(deadlines[-1].start())],
                        f"补货最长 {duration} 分钟，"
                        f"首轮 {parse_number(deadlines[-1]['num'])} 分钟；"
                        "依赖关系未明，需人工确认。",
                    )
        clocks = [f for f in current if f.kind == "clock"]
        for opening in clocks:
            opening_tail = text[opening.end : opening.end + 18]
            if not re.search(r"开席[^。]*必须用", opening_tail):
                continue
            for arrival in clocks:
                if arrival.start <= opening.start or not re.search(
                    r"才到货", text[arrival.end : arrival.end + 12]
                ):
                    continue
                section = text[opening.start : arrival.end]
                if "明天" in section or "次日" in section:
                    continue
                if arrival.value > opening.value:
                    emit(
                        "time_dependency",
                        [opening.ref, arrival.ref],
                        "同日开席依赖的货物在开席时刻之后才到。",
                    )
        for rounds in re.finditer(
            rf"(?P<num>{_NUM})道热荤分成(?P<rounds>{_NUM})轮[：:](?P<items>[^。\n]+)", text
        ):
            groups = re.split(r"[，,]", rounds["items"])
            last = groups[-1]
            if (
                re.search(r"最后", last)
                and re.search(r"包菜|娃娃菜|素菜|汤", last)
                and not re.search(r"肉|排骨|鱼|虾|鸡|鸭", last)
            ):
                emit(
                    "round_category_mismatch",
                    [ref_at(rounds.start())],
                    f"热荤称分 {parse_number(rounds['rounds'])} 轮，末轮列出的却是素菜或汤。",
                )
    # Inventory balance across chapters: total tables × per-table vs available stock.
    table_counts = [
        f for f in facts if f.unit == "桌" and f.entity == "婚宴" and f.role == "assertion"
    ]
    for portion in facts:
        if portion.role != "per_table" or portion.unit != "斤" or not portion.entity:
            continue
        tables = [
            f for f in table_counts if (f.chapter, f.start) <= (portion.chapter, portion.start)
        ]
        if not tables:
            continue
        required = tables[-1].value * portion.value
        stocks = [
            f
            for f in facts
            if f.entity == portion.entity
            and f.unit == "斤"
            and f.role == "assertion"
            and (f.chapter, f.start) > (portion.chapter, portion.start)
        ]
        for stock in stocks[:1]:
            if stock.value < required:
                emit(
                    "stock_shortfall",
                    [tables[-1].ref, portion.ref, stock.ref],
                    f"{portion.entity} 需 {required} 斤，后述存量仅 {stock.value} 斤。",
                )
    # Optional explicit dish-family alignment; transformations require human context.
    for family in sorted(set((aliases or {}).values())):
        variants = [name for name, canonical in (aliases or {}).items() if canonical == family]
        if len(variants) < 2:
            continue
        old_name = old_ref = ""
        for n, text in texts.items():
            for match in re.finditer(
                "|".join(re.escape(name) for name in sorted(variants, key=len, reverse=True)), text
            ):
                name = match.group()
                if (
                    old_name
                    and name != old_name
                    and not _CHANGE.search(text[max(0, match.start() - 12) : match.start()])
                ):
                    emit(
                        "menu_item_mismatch",
                        [old_ref, f"ch{n}@{match.start()}"],
                        f"显式同族菜品 {old_name} / {name} 形式变化，需核对是否换菜。",
                    )
                old_name, old_ref = name, f"ch{n}@{match.start()}"
    return CheckerReport(
        agent="quantity-consistency-probe",
        chapter=max(texts, default=1),
        overall_score=0 if not facts else max(0, 100 - 5 * len(issues)),
        passed=bool(facts) and not issues,
        issues=tuple(issues),
        metrics={
            "mode": "audit_only",
            "coverage": 0,
            "fact_count": len(facts),
            "source_sha256": {str(n): sha256(t.encode()).hexdigest() for n, t in texts.items()},
            "findings": evidence,
            "factual_acceptance": "unknown",
            "coverage_note": "规则覆盖率未知；无告警不等于正文一致。",
        },
        summary=f"离线规则观察 {len(facts)} 项；{len(issues)} 条待编辑复核；不授予事实验收。",
    )
