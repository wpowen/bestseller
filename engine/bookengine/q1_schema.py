"""Q1 schema data, tolerant parsing and planning defaults."""

from pathlib import Path
import re
import yaml
from .longutil import loose_object
from .cost_design import FORMAT as COST_FORMAT

DATA = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/q1_schemas.yaml").read_text(encoding="utf-8"))
FIX = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/fix100.yaml").read_text())
GROWTH = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/growth_payoff.yaml").read_text())
BORROWED = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/borrowed_style.yaml").read_text())
DATA["hook_types"] = list(dict.fromkeys([*DATA["hook_types"], *BORROWED["hook_types"]]))
DATA["schemas"]["address_design"] = {
    "characters": [{"name": "", "address": "", "address_origin": "", "address_target": ""}]
}
DATA["instructions"]["address_design"] = (
    "只修改错误清单中人物的address/address_origin/address_target；name只作定位，不准改名。"
    "address是该人物如何称呼目标人物，address_target填写目标真名。优先用姓名派生称呼；"
    "确需无关称呼，给出具体address_origin，供首次使用时交代。不能删空称呼绕过校验。"
)
DATA["schemas"]["cost_design"] = {"cost": ""}
DATA["instructions"]["cost_design"] = "只返回当前指定阶段的新cost字符串，其他字段只读。" + COST_FORMAT
DATA["instructions"]["prepare"] += "\n阶段cost约束：" + COST_FORMAT


def parse(value):
    if isinstance(value, (dict, list)):
        return value
    cleaned = re.sub(r"^```(?:json|yaml)?\s*|\s*```$", "", str(value).strip())
    if cleaned.startswith(("[", "- ")):
        try:
            parsed = yaml.safe_load(cleaned)
            if isinstance(parsed, list):
                return parsed
        except yaml.YAMLError:
            pass
    try:
        return loose_object(value)
    except (ValueError, TypeError, AttributeError):
        return {}


def rows(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def model_value(raw):
    value = parse(raw)
    cleaned = re.sub(r"^```(?:json|yaml)?\s*|\s*```$", "", str(raw).strip())
    if not value and cleaned not in ("{}", "[]"):
        raise ValueError("Unparseable planning response")
    return value


def integer(value, default=0):
    try:
        return int(value) if not isinstance(value, bool) else default
    except (ValueError, TypeError, OverflowError):
        return default


def defaults(value, template):
    if template is None:
        return value
    if isinstance(template, dict):
        value = value if isinstance(value, dict) else {}
        return {**value, **{k: defaults(value.get(k), v) for k, v in template.items()}}
    if isinstance(template, list):
        return value if isinstance(value, list) else []
    if isinstance(template, int):
        return integer(value, template)
    return value if isinstance(value, type(template)) else template


def normalize_threads(value, count, total, profile, stages=None):
    boundaries = stages or [dict(number=n, start=(n - 1) * 25 + 1, end=min(n * 25, total)) for n in range(1, count + 1)]
    count = len(boundaries)
    items = rows(value.get("threads")) if isinstance(value, dict) else rows(value)
    aliases = dict(
        主线="main",
        暗线="dark",
        隐藏线="hidden",
        感情线="romance",
        成长线="growth",
        配角线="support",
        power="growth",
        foreshadow="hidden",
        companion="support",
        love="romance",
        subplot="dark",
        consequence="main",
        aftermath="main",
    )
    merged = {}
    for source in items:
        item = dict(source)
        kind = str(item.get("kind", item.get("type", "main"))).strip()
        item["kind"] = aliases.get(kind, kind)
        if item["kind"] in merged:
            merged[item["kind"]]["beats"] = rows(merged[item["kind"]].get("beats")) + rows(item.get("beats"))
        else:
            merged[item["kind"]] = item
    items = list(merged.values())
    items += [dict(id=k, kind=k) for k in DATA["thread_kinds"] if not any(x.get("kind") == k for x in items)]
    used, ids = set(), set()
    for idx, item in enumerate(items, 1):
        kind = item.get("kind", "main")
        key = str(item.get("id") or f"{kind}-{idx}")
        while key in used:
            key += "-x"
        used.add(key)
        gap = max(1, integer(item.get("max_gap"), integer(profile.get(kind + "_max_gap"), 10)))
        if kind in ("dark", "romance"):
            gap = max(1, min(gap, integer(profile.get(kind + "_max_gap"), 10), 10 if kind == "dark" else gap))
        beats = []
        for n in range(1, count + 1):
            selected = [x for x in rows(item.get("beats")) if integer(x.get("volume")) == n]
            if any(x.get("description") for x in selected):
                selected = [x for x in selected if x.get("description")]
            length = boundaries[n - 1]["end"] - boundaries[n - 1]["start"] + 1
            minimum = max(2, (length + gap - 1) // gap) if kind in ("dark", "romance") else 1
            selected += [{} for _ in range(max(0, minimum - len(selected)))]
            for j, beat in enumerate(selected, 1):
                beat = defaults(beat, dict(id="", volume=n, description="", reveal_not_before=0))
                ident = beat["id"] or f"{key}-v{n}-{j}"
                while ident in ids:
                    ident += "-x"
                ids.add(ident)
                beat.update(id=ident, volume=n)
                if kind == "hidden" and not beat["reveal_not_before"]:
                    beat["reveal_not_before"] = boundaries[-1]["start"]
                beats.append(beat)
        item.update(id=key, kind=kind, max_gap=gap, beats=beats)
    return dict(threads=items)
