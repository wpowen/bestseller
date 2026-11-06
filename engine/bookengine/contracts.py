"""Pure growth/payoff contracts; caller loads profiles and supplies legal hooks."""

from collections import Counter
from copy import deepcopy
import re

ALIASES = dict(
    cost="代价",
    limit="限制",
    blocked="封禁",
    balance="余额",
    status="当前状态",
    realm="境界",
    strength="境界",
    costs="代价",
    limits="限制",
    balances="余额",
    current_status="当前状态",
    level="境界",
)
META = dict(value="值", last_updated="最后更新", amount="数量", unit="单位", life="寿命", **ALIASES)
TYPES = ("对抗", "谋划", "情感", "日常", "揭秘", "收获")


def _map(value):
    return value if isinstance(value, dict) else {}


def _quoted(value, text):
    return isinstance(value, str) and bool(value.strip()) and isinstance(text, str) and value in text


def _merge(old, patch):
    return (
        {**old, **{k: _merge(old.get(k), v) for k, v in patch.items() if v is not None}}
        if isinstance(old, dict) and isinstance(patch, dict)
        else old
        if patch is None
        else deepcopy(patch)
    )


def _ledger(key, value):
    return (
        key in ("gains", "costs", "solutions", "advances", "events", "r2_events")
        and isinstance(value, list)
        and all(isinstance(x, dict) and any(k in x for k in ("content", "event", "kind")) for x in value)
    )


def _cn(value):
    if isinstance(value, list):
        return [_cn(x) for x in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    known = {
        META.get(k, k): _cn(v)
        for k, v in value.items()
        if not _ledger(k, v)
        and k not in ("引文", "段号", "证据原句")
        and (k in META or not re.search("[A-Za-z]", str(k)))
    }
    extra = [
        dict(名称=k, 值=_cn(v))
        for k, v in value.items()
        if not _ledger(k, v)
        and k not in META
        and k not in ("quote", "paragraph", "paragraph_id", "evidence")
        and re.search("[A-Za-z]", str(k))
    ]
    return {
        **known,
        **({"扩展": [*(known.get("扩展") if isinstance(known.get("扩展"), list) else []), *extra]} if extra else {}),
    }


def dashboard_guard(old, patch, canon, text, demotion_event=None):
    old, patch = _map(old), {k: v for k, v in _map(patch).items() if v is not None}
    result, warnings = (
        {k: v for k, v in {**old, **patch}.items() if k in ("strength", "wealth", "status", "affiliation", "stage")},
        [],
    )
    ladder, proposed = canon["ladder"]["strength"], patch.get("strength", old.get("strength"))
    previous = old.get("strength")
    invalid = "strength" in patch and (
        proposed not in ladder
        or (
            previous in ladder
            and proposed in ladder
            and (
                ladder.index(proposed) > ladder.index(previous) + 1
                or ladder.index(proposed) < ladder.index(previous)
                and not _quoted(_map(demotion_event).get("quote"), text)
            )
        )
    )
    if invalid:
        result["strength"] = previous
        warnings.append(
            "strength_outside_canon"
            if proposed not in ladder
            else "unsupported_upgrade"
            if ladder.index(proposed) > ladder.index(previous)
            else "unsupported_demotion"
        )
    return result, warnings


def power_merge(old, patch, chapter, limit=6):
    result, library = {}, {}
    for source, fresh in ((old, False), (patch, True)):
        for key, value in _map(source).items():
            if (
                _ledger(key, value)
                or value is None
                or isinstance(value, dict)
                and ("值" in value and value["值"] is None or "value" in value and value["value"] is None)
            ):
                continue
            entry = _cn(value)
            entry = (
                entry
                if isinstance(entry, dict) and "值" in entry and "最后更新" in entry
                else dict(值=entry, 最后更新=0)
            )
            entry["最后更新"] = chapter if fresh else (entry["最后更新"] if type(entry["最后更新"]) is int else 0)
            name = ALIASES.get(str(key).lower(), key)
            if name not in ALIASES.values():
                library[key] = dict(名称=key, **entry)
                continue
            entry["值"] = _merge(result.get(name, {}).get("值"), entry["值"])
            if name == "当前状态":
                payload = _map(entry["值"])
                for item in payload.pop("扩展", []) or []:
                    if isinstance(item, dict) and isinstance(item.get("名称"), str):
                        library[item["名称"]] = item
            result[name] = entry
    if library:
        while len(library) > limit:
            del library[
                min(library, key=lambda k: library[k].get("最后更新") if type(library[k].get("最后更新")) is int else 0)
            ]
        status = result.setdefault("当前状态", dict(值={}, 最后更新=chapter))
        status["值"] = _map(status["值"]) or {"状态": status["值"]}
        status["值"]["扩展"] = list(library.values())
    return result


def valid_extract(raw, text, hooks, menu, ending=False):
    r, p = _map(raw), _map(_map(raw).get("payoff"))
    return (
        isinstance(r.get("state"), dict)
        and r.get("type") in TYPES
        and (
            r.get("hook_type") in hooks
            or ending
            and r.get("hook_type") is None
            and r.get("opens_new_mainline") is False
        )
        and type(p.get("fulfilled")) is bool
        and (p.get("type") in menu if p.get("fulfilled") else p.get("type") == "无兑现")
        and (
            _quoted(p.get("quote"), text)
            if p.get("fulfilled")
            else p.get("quote") in (None, "") or _quoted(p.get("quote"), text)
        )
    )


def observations(raw, text, number):
    return {
        key: [
            dict(
                chapter=x.get("chapter")
                if type(x.get("chapter")) is int and max(1, number - 1) <= x["chapter"] <= number
                else number,
                type=x.get("type") or ("代价" if key == "costs" else x.get("category")),
                content=x.get("content") or x.get("category"),
                **({"primary": True} if x.get("primary") is True else {}),
                **(
                    {k: x[k] for k in ("quote", "target_noun", "method_noun", "noun_status") if k in x}
                    if key == "solutions" and x.get("noun_status") == "Evidence"
                    else {}
                ),
            )
            for x in _map(raw).get(key, [])
            if isinstance(x, dict)
            and isinstance(x.get("type") or ("代价" if key == "costs" else x.get("category")), str)
            and isinstance(x.get("content") or x.get("category"), str)
            and _quoted(x.get("quote"), _map(_map(raw).get("_sources")).get(x.get("chapter", number), text))
        ]
        for key in ("gains", "costs", "solutions")
        if isinstance(_map(raw).get(key, []), list)
    }


def main_solutions(entries, canon):
    result, counts = {}, Counter(x["chapter"] for x in entries)
    for x in entries:
        row = dict(x, type=canon.get("solution_aliases", {}).get(x["type"], x["type"]))
        old = result.get(x["chapter"])
        if row["type"] == "集体认同" and re.search("齐声|作证|共同拒绝", row.get("content", "")):
            row = dict(row, type="群体表态", classification_basis="legacy_explicit_group_content")
        score = lambda r: (
            r.get("primary") is True,
            bool(re.search("使|导致|从而|迫使|解除|终止|阻断", r.get("content", ""))),
        )
        if old is None or score(row) > score(old):
            result[x["chapter"]] = row
    return [
        dict(
            x,
            primary_basis="explicit_primary"
            if x.get("primary")
            else "causal_content"
            if score(x)[1]
            else "first_record_requires_body_review",
        )
        if counts[x["chapter"]] > 1
        else x
        for x in result.values()
    ]


def growth_obligations(state, idx, canon):
    level = _map(_map(state).get("dashboard")).get("strength")
    p = _map(_map(state).get("realm_progress"))
    at = p.get("upgrade_at") if p.get("level") == level else None
    required = (
        level in canon["ladder"]["strength"]
        and type(at) is int
        and not (type(p.get("displayed_at")) is int and at <= p["displayed_at"] <= idx)
    )
    return dict(
        level=level,
        upgrade_at=at,
        display_by=at + 3 if type(at) is int else None,
        display_required=required,
        display_before_upgrade=required,
        required_action="先展示当前阶能力，再推进升级" if required else None,
    )


def reduce_growth(previous, raw, bible, text, number, base=None):
    state, canon = {**deepcopy(_map(previous)), **deepcopy(base or {})}, bible["canon"]
    patch = _map(_map(raw).get("state"))
    state["dashboard"], warnings = dashboard_guard(
        _map(_map(previous).get("dashboard")),
        _map(patch.get("dashboard")),
        canon,
        text,
        _map(raw).get("demotion_event"),
    )
    state["power_status"] = power_merge(_map(previous).get("power_status"), patch.get("power_status"), number)
    level = state["dashboard"].get("strength")
    realm = _map(state["power_status"].get("境界")).get("值")
    if level in canon["ladder"]["strength"] and realm != level:
        if realm is not None:
            warnings.append("power_realm_synced_to_dashboard")
        state["power_status"] = power_merge(state["power_status"], {"境界": level}, number)
    prior = _map(_map(previous).get("dashboard")).get("strength")
    p = deepcopy(_map(_map(previous).get("realm_progress")))
    if p.get("level") != level:
        p = dict(
            level=level,
            upgrade_at=number
            if level in canon["ladder"]["strength"]
            and prior in canon["ladder"]["strength"]
            and canon["ladder"]["strength"].index(level) > canon["ladder"]["strength"].index(prior)
            else None,
            displayed_at=None,
        )
    if _map(raw).get("realm_displayed") is True and _quoted(_map(raw).get("realm_display_quote"), text):
        p["displayed_at"] = number
    state["realm_progress"] = p
    for key, incoming in observations(raw, text, number).items():
        entries = state.setdefault(
            "solution_history" if key == "solutions" else key,
            deepcopy(state.get("solutions", [])) if key == "solutions" else [],
        )
        for row in incoming:
            if key == "solutions":
                row["type"] = canon.get("solution_aliases", {}).get(row["type"], row["type"])
            if key == "solutions" and row["type"] not in canon["solution_menu"]:
                warnings.append("solution_unknown:" + row["type"])
                continue
            if row not in entries:
                entries.append(row)
    state["solutions"] = main_solutions(state.get("solution_history", state.get("solutions", [])), canon)
    chosen = [x for x in state["solutions"] if x["chapter"] == number]
    older = [x for x in state["solutions"] if x["chapter"] < number]
    for row in chosen:
        same = [x for x in older if x["type"] == row["type"]]
        groups = [x for x in older if x["type"] in ("群体表态", "集体认同")]
        warnings += [
            w
            for w, bad in (
                ("solution_repeat_30", sum(0 < number - x["chapter"] < 30 for x in same) >= 2),
                ("solution_repeat_5", any(0 < number - x["chapter"] < 5 for x in same)),
                ("group_solution_book_cap", row["type"] == "群体表态" and len(groups) >= 2),
                (
                    "group_solution_final_cap",
                    row["type"] == "群体表态"
                    and canon.get("final_volume_start", number + 1) <= number
                    and any(x["chapter"] >= canon["final_volume_start"] for x in groups),
                ),
            )
            if bad
        ]
    declared = [
        x
        for x in _map(raw).get("injuries", []) or []
        if isinstance(x, dict)
        and x.get("type") not in ("旧伤复发", "旧伤加重")
        and x.get("new") is not False
        and isinstance(x.get("content"), str)
        and _quoted(x.get("quote"), text)
    ]
    physical = [
        x
        for x in _map(raw).get("costs", []) or []
        if isinstance(x, dict)
        and (x.get("type") in ("伤残", "身体伤残", "身体代价") or x.get("physical") is True)
        and not any(i["quote"] == x.get("quote") for i in declared)
    ]
    injuries = [
        dict(chapter=number, content=x["content"])
        for x in declared + physical
        if x.get("type") not in ("旧伤复发", "旧伤加重")
        and x.get("new") is not False
        and isinstance(x.get("content"), str)
        and _quoted(x.get("quote"), text)
    ]
    injuries = list({x["content"]: x for x in injuries}.values())
    state["injuries_count"] = len(injuries)
    state["injuries"] = [x for x in state.get("injuries", []) if x.get("chapter") != number] + injuries[:1]
    state["injury_facts"] = [x for x in state.get("injury_facts", []) if x.get("chapter") != number] + injuries
    if len(injuries) > 1:
        warnings.append("injuries_over_one")
    return state, warnings


def payoff_ok(payoff, contracted_type, text, menu):
    p = _map(payoff)
    return (
        p.get("type") == contracted_type
        and contracted_type in menu
        and p.get("fulfilled") is True
        and _quoted(p.get("quote"), text)
    )


def _rank(value, field, canon):
    order = canon["ladder"].get(field, [])
    if field != "wealth":
        return order.index(value) if value in order else None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*" + re.escape(canon["ladder"]["wealth_unit"]), str(value))
    return float(match[1]) if match else None


def growth_warnings(state, before, after, canon, start, end):
    if canon["genre_kind"] != "爽文":
        return []
    gains, costs = (sum(start <= x.get("chapter", 0) <= end for x in state.get(key, [])) for key in ("gains", "costs"))
    increased = any(
        a is not None and b is not None and b > a
        for a, b in (
            (_rank(before.get(k), k, canon), _rank(after.get(k), k, canon)) for k in ("strength", "status", "wealth")
        )
    )
    return [
        name
        for name, bad in (
            ("gains_below_two", gains < 2),
            ("dashboard_no_growth", not increased),
            ("cost_gain_ratio", costs > gains * 1.5),
        )
        if bad
    ]


def choose_contract(canon, rows, idx, pending=None, state=None, final_volume_start=None):
    from random import Random

    history, policy = list(rows), _map(canon.get("scheduling"))
    counts, n = Counter(r.get("hook_type") for r in history[-29:]), min(30, len(history) + 1)
    legal = [h for h in canon.get("hook_types", []) if n < 4 or (counts[h] + 1) * 4 <= n]
    fantasy = canon["genre_kind"] == "爽文"
    aliases = canon.get("payoff_subtype_to_type", {})
    rng, upgrades = Random(str(canon.get("seed", ""))), []
    for _ in canon["ladder"]["strength"][1:] if fantasy else []:
        upgrades.append((upgrades[-1] if upgrades else 0) + rng.randint(*policy.get("upgrade_gap", [4, 8])))
    next_upgrade = next((n for n in upgrades if n >= idx), None)
    tracks = [
        [
            _map(r.get("payoff")).get("type") if _map(r.get("payoff")).get("fulfilled") is True else None,
            r.get("reserved_payoff_type"),
        ]
        for r in history[-9:]
    ]
    tracks = [
        [aliases.get(a, a) or aliases.get(b, b) for a, b in (t if forward else t[::-1] for t in tracks)]
        for forward in (True, False)
    ]
    available = [p for p in canon["payoff_menu"] if not fantasy or p in ("升级", "打脸", "收获", "身份抬升", "碾压")]
    if fantasy and not available:
        raise ValueError("No canonical payoff type")
    menu = [
        p
        for p in available
        if not fantasy
        or (p == pending or (p == "升级") == (idx == next_upgrade))
        and all(t[-2:] != [p, p] and (len(t) < 9 or len(set(t + [p]) - {None}) >= 3) for t in tracks)
    ]
    selected = (
        pending
        if pending in menu
        else Random(f"{canon.get('seed', '')}:{idx}").choice(menu or available or [None])
        if not pending or fantasy
        else None
    )
    ledger = main_solutions(
        _map(state).get("solutions", [])
        if state is not None
        else [x for r in history for x in r.get("solutions", []) if isinstance(x, dict)],
        canon,
    )
    end = final_volume_start if final_volume_start is not None else canon.get("final_volume_start", idx + 1)
    groups = [x for x in ledger if x["type"] in ("群体表态", "集体认同") and x["chapter"] < idx]
    group_allowed = len(groups) < 2 and (idx < end or not any(x["chapter"] >= end for x in groups))
    used = Counter(x["type"] for x in ledger if 0 < idx - x["chapter"] < 30)
    avoid = (
        {x["type"] for x in ledger if 0 < idx - x["chapter"] < 5}
        | {k for k, v in used.items() if v >= 2}
        | ({"群体表态"} if not group_allowed else set())
    )
    solutions = [x for x in canon["solution_menu"] if canon.get("solution_aliases", {}).get(x, x) not in avoid]
    warnings = [
        key
        for key, bad in (
            ("payoff_menu_exhausted", not menu),
            ("pending_contract_blocked", pending and selected != pending),
            ("hook_quota_no_legal", not legal),
            ("solution_menu_exhausted", not solutions),
        )
        if bad
    ]
    return dict(
        number=idx,
        payoff_due=bool(due := (fantasy or pending or idx % 2 == 1) and selected),
        payoff_type=selected if due else "无兑现",
        hook_type=min(legal, key=lambda h: counts[h]) if legal else None,
        growth_setup=next_upgrade == idx + policy.get("setup_lead_chapters", 1),
        upgrade_due=fantasy and selected == "升级",
        next_upgrade=idx if fantasy and selected == "升级" else next_upgrade,
        deferred_payoff=pending if pending and selected != pending else None,
        solution_type=solutions[0] if solutions else None,
        solution_avoid=sorted(avoid),
        group_allowed=group_allowed,
        group_history_unknown=sum(x["type"] == "集体认同" for x in groups),
        growth_obligation=growth_obligations(state, idx, canon),
        warnings=warnings,
    )
