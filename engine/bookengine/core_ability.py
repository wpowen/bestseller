"""Stage ability obligations and quote-bound, reference-only outcome evidence."""

from copy import deepcopy
import json
import re

from .solution_substance import rows

DEVELOPMENT = ("new_use", "new_cost", "new_limit")
RULE = (
    "每阶段至少50%章节由核心能力直接解决主要冲突，core_application写操作及决定结果的因果，不能只提能力。"
    "core_development给new_use/new_cost/new_limit，逐阶段至少一项发生具体升级，不能仅换措辞。"
)


def core_source(bible):
    power = bible.get("power")
    if not isinstance(power, dict) or not any(
        v and str(v).strip() not in ("无", "无金手指", "无超能力") for v in power.values()
    ):
        return {}
    return dict(
        name=power.get("name") or power.get("object") or power.get("rule") or bible.get("title", ""),
        title=bible.get("title", ""),
        power=power,
    )


def ability_slots(bible, start, end):
    result = []
    if not core_source(bible):
        return result
    for stage in rows(bible.get("volumes")):
        a, b = stage.get("start"), stage.get("end")
        if type(a) is not int or type(b) is not int:
            continue
        for n in range(max(a, start), min(b, end) + 1):
            result.append(
                dict(
                    number=n,
                    core_required=n <= 3 or (n - a) % 2 == 0,
                    core_development=stage.get("core_development", {}),
                )
            )
    return result


def development_issues(bible):
    if not core_source(bible):
        return []
    issues, previous = [], None
    for index, stage in enumerate(rows(bible.get("volumes")), 1):
        development = stage.get("core_development")
        development = development if isinstance(development, dict) else {}
        valid = all(isinstance(development.get(k), str) and len(development[k].strip()) >= 5 for k in DEVELOPMENT)
        repeated = previous is not None and all(development.get(k) == previous.get(k) for k in DEVELOPMENT)
        if not valid or repeated:
            issues.append(
                dict(
                    stage=stage.get("number", index), kind="core_development", reason="missing_or_repeated_development"
                )
            )
        previous = development
    return issues


def repair_development(bible, ask):
    current, attempts = deepcopy(bible), []
    for retry in range(1, 4):
        issues = development_issues(current)
        if not issues:
            break
        response = ask(
            "core_development",
            "core-development",
            [
                ("核心能力原始依据", core_source(current)),
                ("只补阶段core_development，不改任何已有事实", current.get("volumes", [])),
                ("错误清单", issues),
            ],
            retry,
        )
        proposals = (
            {r.get("number"): r.get("core_development") for r in rows(response.get("volumes"))}
            if isinstance(response, dict)
            else {}
        )
        before = deepcopy(current)
        for stage in current.get("volumes", []):
            proposed = proposals.get(stage.get("number"))
            if isinstance(proposed, dict):
                stage["core_development"] = {k: proposed.get(k) for k in DEVELOPMENT}
        attempts.append(dict(retry=retry, issues=issues, proposals=proposals))
        # Keep valid stage fields only; failed candidates remain in the audit.
        for issue in development_issues(current):
            i = next(i for i, s in enumerate(current["volumes"]) if s.get("number") == issue["stage"])
            current["volumes"][i] = before["volumes"][i]
    remaining = development_issues(current)
    return current, dict(status="failed_continued" if remaining else "passed", issues=remaining, attempts=attempts)


def ability_issues(bible, history, batch):
    if not core_source(bible):
        return []
    combined = {r["number"]: r for r in [*history, *batch] if type(r.get("number")) is int}
    targets = {r["number"] for r in batch}
    issues = []
    slots = ability_slots(bible, min(targets, default=1), max(targets, default=0))
    for slot in slots:
        row = combined.get(slot["number"], {})
        if slot["core_required"] and (
            row.get("core_required") is not True
            or not isinstance(row.get("core_application"), str)
            or len(row["core_application"].strip()) < 8
        ):
            issues.append(dict(kind="core_application", targets=[slot["number"]], requirement=RULE))
    return issues


def verify_core_use(raw, text, bible):
    """Literal evidence plus explicit causal result; semantic authority remains agent review."""
    source = core_source(bible)
    raw = raw if isinstance(raw, dict) else {}
    result = dict(status="unknown", source=source, authority="agent_editorial_not_human", raw=raw)
    if not source:
        return dict(result, reason="no_core_source")
    action, outcome, mechanism = (raw.get(k) for k in ("action_quote", "result_quote", "mechanism"))
    grounded = all(isinstance(q, str) and len(q.strip()) >= 8 and q in text for q in (action, outcome))
    named = (
        isinstance(mechanism, str)
        and len(mechanism) >= 2
        and mechanism in json.dumps(source, ensure_ascii=False)
        and grounded
        and mechanism in action
    )
    causal = grounded and bool(re.search(r"使|令|因此|从而|终于|迫使|才|随之|于是", outcome))
    negative = grounded and bool(re.search(r"没有|并未|不曾|未曾|尚未|如果|假如|想起|打算|准备", action))
    if raw.get("decisive") is False and grounded:
        return dict(result, status="not_decisive", action_quote=action, result_quote=outcome)
    if raw.get("decisive") is True and named and causal and not negative:
        return dict(result, status="verified", action_quote=action, result_quote=outcome, evidence_status="Evidence")
    return dict(result, reason="missing_or_noncausal_verbatim_evidence")


def enforce_ability_plan(bible, batch):
    """Repair missing planning obligations without claiming an observed action/result."""
    result = deepcopy(batch)
    slots = {
        r["number"]: r
        for r in ability_slots(
            bible, min((r["number"] for r in batch), default=1), max((r["number"] for r in batch), default=0)
        )
    }
    for row in result:
        slot = slots.get(row["number"], {})
        if not slot.get("core_required"):
            continue
        row["core_required"] = True
        row["core_development"] = slot.get("core_development", {})
        if isinstance(row.get("core_application"), str) and len(row["core_application"].strip()) >= 8:
            continue
        development = slot.get("core_development", {})
        application = development.get("new_use") or str(core_source(bible).get("name", "核心能力"))
        scenes = rows(row.get("scenes")) or [dict(goal=row.get("opening_conflict") or "解决本章主要阻碍")]
        scene = scenes[0]
        goal = scene.get("goal") or row.get("opening_conflict") or "解决本章主要阻碍"
        causal = f"实际运用{application}，使{goal}得以实现；受{development.get('new_limit') or '圣经既有限制'}约束，并付出{development.get('new_cost') or '圣经既定代价'}。"
        row["core_application"] = causal
        scene["choice"] = causal
        row["scenes"] = scenes
        row["core_plan_repair"] = dict(status="deterministic_plan_completion", authority="plan_not_observation")
    return result
