"""Executable stage-cost contracts, preserving plain strings and all locked facts.

Each cost names its loss, psychological change, level and concrete escalation.
Levels and comparisons validate the stated design; they do not establish reader
acceptance of its emotional weight. Existing saved bibles are never rewritten.
"""

from copy import deepcopy
import re

FORMAT = (
    "cost保持字符串，格式：损失：具体失去什么；心理：性格/记忆/情感/身份的具体改变及行为后果；"
    "等级：正整数；升级：上一阶段心理字段原句→本阶段心理字段原句（第一阶段写首次代价）。"
    "等级逐级增加，心理改变不能原地重复。不能只写更痛苦/侵蚀加深。"
    "若限制说话、行走、视力或手部，增加恢复章：本阶段内章号；恢复办法：具体医治或可实际使用的替代办法。"
    "永久损失只能用有效替代，不能宣称恢复原能力。音色损失必须写成失去某种音色、仍能正常说话，不能写嗓音永逝。"
    "保留所有用户锁定的能力规则/永久代价事实，不能为了过检查换故事或把永久记忆损失改成临时伤痛。"
)


def _fields(cost):
    result = {}
    if isinstance(cost, str):
        labels = list(re.finditer(r"(?:^|[；;。\n])\s*(损失|心理|等级|升级|恢复章|恢复办法)\s*[：:]", cost))
        for index, match in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(cost)
            result[match[1]] = cost[match.end() : end].strip(" ；;。\n")
    return result


def _level(value):
    return int(value) if isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value) else 0


def _specific(value):
    return (
        isinstance(value, str)
        and len(value.strip()) >= 8
        and not re.search(r"待定|未知|稍后|某种|更加痛苦|进一步侵蚀", value)
    )


def _specific_psychology(value):
    return _specific(value) and bool(
        re.search(
            r"不再|不敢|开始|转而|主动|拒绝|选择|决定|改为|宁可|从此|再也|变得|习惯|反复|每次|总是|"
            r"只(?:相信|接受|依赖)",
            value,
        )
    )


def _ability_risks(cost):
    if not isinstance(cost, str):
        return []
    risks = []
    voice = re.search(
        r"失声|无法(?:正常)?(?:说话|发声)|不能(?:说话|开口)|失去.{0,8}(?:嗓音|声音)|嗓音.{0,5}(?:永逝|消失)|声音.{0,5}(?:永逝|消失)",
        cost,
    )
    tone_only = (
        "音色" in cost
        and re.search(r"仍(?:然)?(?:能|可)(?:以)?正常说话", cost)
        and not re.search(r"失声|无法说话|无法发声", cost)
    )
    if voice and not tone_only:
        risks.append("说话")
    for ability, pattern in (
        ("行走", r"无法行走|不能行走|失去.{0,6}(?:行走|双腿)|双腿.{0,5}(?:残废|瘫痪)|瘫痪"),
        ("视力", r"失明|失去.{0,6}(?:视力|视觉|双目)|看不见|目不能视"),
        ("双手", r"失去.{0,6}(?:双手|双臂|左手|右手)|双手.{0,5}(?:残废|废掉)|不能使用双手"),
    ):
        if re.search(pattern, cost):
            risks.append(ability)
    return risks


def _canonical_escalation(cost, previous_cost):
    """Use the model's two psychology fields as the comparison's exact source."""
    fields, previous = _fields(cost), _fields(previous_cost)
    comparison = fields.get("升级", "")
    if not all(fields.get(key) for key in ("损失", "心理", "等级", "升级")):
        return cost
    if not previous.get("心理") or not re.search(r"→|->", comparison):
        return cost
    exact = f"{previous['心理']}→{fields['心理']}"
    if exact in comparison:
        return cost
    fields["升级"] = exact
    order = ("损失", "心理", "等级", "升级", "恢复章", "恢复办法")
    return "；".join(f"{label}：{fields[label]}" for label in order if fields.get(label))


def has_cost_source(bible):
    power = bible.get("power", {})
    return any(v.get("cost") for v in bible.get("volumes", [])) or bool(
        any(power.values()) if isinstance(power, dict) else power
    )


def cost_issues(bible):
    """Pure validation including legacy free-text failures; no model judgments."""
    issues, previous = [], None
    for position, stage in enumerate(bible.get("volumes", []), 1):
        number, cost = stage.get("number", position), stage.get("cost", "")
        fields = _fields(cost)

        def issue(code, message):
            issues.append(dict(stage=number, code=code, message=message))

        if not isinstance(cost, str) or not cost.strip():
            issue("missing_cost", "阶段缺少具体cost")
        if not _specific(fields.get("损失")):
            issue("specific_wording", "损失字段须说明具体失去什么，避免音色/失声歧义")
        mind, level = fields.get("心理", ""), _level(fields.get("等级"))
        if not _specific_psychology(mind):
            issue("psychological_change", "心理字段须具体改变性格、记忆、情感或身份认知并说明行为后果")
        if not level:
            issue("escalation", "等级缺少可检查的正整数")
        if previous is not None:
            old_mind, old_level = previous.get("心理", ""), _level(previous.get("等级"))
            comparison = fields.get("升级", "")
            if (
                not old_level
                or level <= old_level
                or not old_mind
                or mind == old_mind
                or f"{old_mind}→{mind}" not in comparison
            ):
                issue("escalation", "代价须逐级加重，升级字段用上一阶段心理原句→本阶段新心理原句具体比较")
        elif not fields.get("升级", "").strip():
            issue("escalation", "首阶段也须注明首次代价")
        risks = _ability_risks(cost)
        if risks:
            recovery = fields.get("恢复办法", "")
            chapter = _level(fields.get("恢复章"))
            start, end = stage.get("start", 0), stage.get("end", 0)
            within = type(start) is int and type(end) is int and start <= chapter <= end and chapter > 0
            permanent = bool(re.search(r"永久|永远|终身|永逝|不可恢复", fields.get("损失", cost)))
            alternative = bool(re.search(r"替代|辅助|传音|手语|义肢", recovery))
            if not within or not _specific(recovery) or permanent and not alternative:
                issue(
                    "primary_ability",
                    "、".join(risks) + "受限，须在同阶段具体恢复或给出有效替代；永久损失不能恢复原能力",
                )
        previous = fields
    return issues


def repair_costs(bible, ask, editable=None):
    """At most three proposals per stage. Adopt only a rechecked cost field."""
    current = deepcopy(bible)
    attempts = []
    stages = current.get("volumes", [])
    allowed = set(editable) if editable is not None else {s.get("number", n) for n, s in enumerate(stages, 1)}
    for position, stage in enumerate(stages):
        number = stage.get("number", position + 1)
        if number not in allowed:
            continue
        last_candidate, last_issues = None, []
        for retry in range(1, 4):
            before = cost_issues(current)
            issues = [i for i in before if i["stage"] == number]
            if not issues:
                break
            sections = [
                ("用户锁定事实，只读", dict(card=current.get("selected_card", {}), power=current.get("power", {}))),
                ("仅本阶段cost允许重写", stage),
                ("逐项错误", issues),
                ("前一阶段cost，只读", stages[position - 1].get("cost") if position else None),
                ("后一阶段cost，只读", stages[position + 1].get("cost") if position + 1 < len(stages) else None),
            ]
            if last_candidate is not None:
                sections.extend([("上次候选，只读", last_candidate), ("上次候选仍有错误", last_issues)])
            raw = ask(
                "cost_design",
                f"cost-stage-{number}",
                sections,
                retry,
            )
            candidate = deepcopy(current)
            proposed = raw.get("cost") if isinstance(raw, dict) else None
            normalized = (
                _canonical_escalation(proposed, stages[position - 1].get("cost"))
                if isinstance(proposed, str) and position
                else proposed
            )
            if isinstance(normalized, str) and normalized.strip():
                candidate["volumes"][position]["cost"] = normalized
            after = cost_issues(candidate)
            last_candidate = normalized
            last_issues = [issue for issue in after if issue["stage"] == number]
            before_stages = {i["stage"] for i in before}
            adopted = (
                isinstance(normalized, str)
                and normalized != stage.get("cost")
                and not any(i["stage"] == number or i["stage"] not in before_stages for i in after)
            )
            attempts.append(
                dict(
                    stage=number,
                    retry=retry,
                    before=stage.get("cost"),
                    candidate=proposed,
                    normalized_candidate=normalized,
                    issues_before=issues,
                    issues_after=after,
                    adopted=adopted,
                )
            )
            if adopted:
                # Assign only this string; ignore every other model-returned field.
                stage["cost"] = normalized
                break
    remaining = cost_issues(current)
    return current, dict(
        status="failed_continued" if remaining else "passed",
        issues=remaining,
        attempts=attempts,
        authority="design_contract_not_reader_acceptance",
    )
