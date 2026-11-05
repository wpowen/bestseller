"""Q1 immutable design and lazy planning; defaults are plans, never story facts."""

from pathlib import Path
import json
import re
import yaml
from .client import SYSTEM
from tools.runtime_support import ProjectError, QuotaExhausted
from .contracts import choose_contract
from .identity import validate_bible
from .longmemory import long_context
from .longutil import loose_object
from .storage import save
from .text import pack
from .q1_schema import DATA, FIX, GROWTH, BORROWED, parse, rows, model_value, integer, defaults, normalize_threads
from .q1_prepare import _ask, prepare


def _beats(threads, number, state=None):
    return [
        dict(b, thread_id=t.get("id"), kind=t.get("kind"), overdue=integer(b.get("volume")) < number)
        for t in rows(threads.get("threads"))
        for b in rows(t.get("beats"))
        if integer(b.get("volume")) > integer(t.get("resume_after_volume"))
        and (
            integer(b.get("volume")) == number
            or (
                state is not None
                and integer(b.get("volume")) < number
                and b.get("id") not in state.get("ledger", {}).get(t.get("id"), {}).get("done", [])
            )
        )
    ]


def callbacks(history):
    """Only observed first-volume events, never future plans."""
    if isinstance(history, dict):
        history = history.get("events", history.get("chapters", list(history.values())))
    result = []
    for row in rows(history):
        if isinstance(row.get("events"), list):
            items = callbacks(row["events"])
        else:
            n = integer(row.get("chapter", row.get("number")))
            event = row.get("event") or row.get("summary")
            items = (
                [dict(chapter=n, event=event)]
                if not {"start", "end"} & row.keys() and 1 <= n <= 25 and isinstance(event, str) and event.strip()
                else []
            )
        result.extend(x for x in items if x not in result)
    return result[:3]


def volume(client, bible, threads, number, total, state, history, feedback, redo=False):
    skeleton = next((x for x in rows(bible.get("volumes")) if integer(x.get("number")) == number), {})
    beats, events = _beats(threads, number, state), callbacks(history)
    ladder = bible.get("canon", {}).get("ladder", {}).get("strength", [])
    old = state.get("dashboard", {}).get("strength")
    target = ladder[min(ladder.index(old) + 1, len(ladder) - 1)] if old in ladder else None
    raw = _ask(
        client,
        "volume",
        f"volume-{number}",
        [
            ("用户选定事实，只可扩写不可替换", bible.get("selected_card", {})),
            ("固定人物卡", bible.get("characters", [])),
            (
                "成长阶梯/当前仪表盘/收获与解法账本",
                [
                    bible.get("canon"),
                    state.get("dashboard"),
                    state.get("gains", [])[-25:],
                    state.get("solutions", [])[-30:],
                ],
            ),
            ("本卷固定走向与节拍", [skeleton, beats]),
            ("指定卷范围", dict(number=number, start=(number - 1) * 25 + 1, end=min(number * 25, total))),
            ("反馈", feedback),
            ("已发生历史", events),
            ("实际状态", state),
            ("圣经", bible),
        ],
        redo,
    )
    value = defaults(dict(skeleton, **raw) if isinstance(raw, dict) else skeleton, DATA["schemas"]["volume"])
    value.update(
        number=number, start=(number - 1) * 25 + 1, end=min(number * 25, total), beats=beats, feedback=feedback
    )
    if target:
        value["expected_dashboard"] = dict(value.get("expected_dashboard") or {}, strength=target)
    value["callback_events"] = events
    return value


def schedule(start, end, profile, recent=(), state=None):
    """Return future rhythm requirements, never an observed payoff/progress."""
    previous = rows(list(recent)) if isinstance(recent, (list, tuple)) else []
    mix = defaults(profile.get("chapter_type_mix"), DATA["profile"]["chapter_type_mix"])
    weights = {k: max(1, integer(v, 1)) for k, v in mix.items() if k in DATA["profile"]["chapter_type_mix"]}
    gap = max(1, min(5, integer(profile.get("payoff_max_gap"), 4)))
    since = next((i for i, r in enumerate(reversed(previous)) if r.get("payoff")), len(previous))
    meeting = max(
        [
            integer(r.get("number"), start - len(previous) + i)
            for i, r in enumerate(previous)
            if integer(r.get("meeting_scenes")) or any(s.get("kind") == "meeting" for s in rows(r.get("scenes")))
        ]
        or [start - 5]
    )
    result = []
    reservation = dict(state or {}, solutions=list((state or {}).get("solutions", [])))
    for n in range(start, end + 1):
        choices = [k for k in weights if len(previous) < 2 or not all(r.get("type") == k for r in previous[-2:])]
        kind = max(
            choices,
            key=lambda k: (
                (len(previous) + 1) * weights[k] / sum(weights.values()) - sum(r.get("type") == k for r in previous)
            ),
        )
        hooks = DATA["hook_types"]
        ordered = hooks[(n - 1) % len(hooks) :] + hooks[: (n - 1) % len(hooks)]
        hook = next(
            h
            for h in ordered
            if sum(r.get("hook_type") == h for r in previous[-9:]) < 2
            and (h != "clue" or all(r.get("hook_type") != "clue" for r in previous[-4:]))
        )
        structures = DATA["structures"]
        structure = structures[(n - 1) % len(structures)]
        if previous and previous[-1].get("structure") == structure:
            structure = structures[n % len(structures)]
        slot = dict(
            number=n,
            type=kind,
            hook_type=hook,
            structure=structure,
            payoff_due=n == 3 or since + 1 >= gap,
            meeting_allowed=n - meeting >= 5,
            required_event="",
            action_required=n % 5 != 0,
        )
        if profile.get("canon"):
            slot.update(choose_contract(profile["canon"], previous, n, state=reservation))
            slot["reserved_payoff_type"] = slot["payoff_type"] if slot["payoff_due"] else None
        since = 0 if slot["payoff_due"] else since + 1
        if slot["meeting_allowed"]:
            meeting = n
        result.append(slot)
        previous.append(slot)
        if slot.get("solution_type"):
            reservation["solutions"].append(dict(chapter=n, type=slot["solution_type"], content="计划未兑现"))
    return result


def beat_slice(threads, vol, chapter, state):
    lines = rows(threads.get("threads"))
    idx = chapter["number"]
    ledger = state.get("ledger", {})
    main = next((t for t in lines if t.get("kind") == "main"), None)
    others = sorted(
        (t for t in lines if t is not main),
        key=lambda t: (t["id"] not in chapter.get("threads", []), ledger.get(t["id"], {}).get("last", 0)),
    )
    chosen = [t for t in [main, *others[:1]] if t]
    result = []
    for line in chosen:
        record = ledger.get(line["id"], {})
        done = record.get("done", [])
        beat = next(
            (
                b
                for b in rows(line.get("beats"))
                if integer(line.get("resume_after_volume")) < b.get("volume", 0) <= vol["number"]
                and b.get("id") not in done
            ),
            {},
        )
        if not beat and idx - record.get("last", 0) < 8:
            continue
        result.append(
            dict(
                thread=line["id"],
                beat=beat.get("id", ""),
                event=beat.get("description", "已有矛盾产生行动后果，不完成后卷节拍")[:100],
                must_appear=idx - record.get("last", 0) >= 8,
                reveal_not_before=beat.get("reveal_not_before", 0),
            )
        )
    return result


def outlines(client, bible, threads, profile, volume, start, end, state, recent, history, alerts, redo=False):
    end = min(end, start + 9, integer(volume.get("end"), end))
    if end < start:
        return []
    rhythm = schedule(start, end, profile, recent, state)
    planned_state = dict(state, ledger={k: dict(v) for k, v in state.get("ledger", {}).items()})
    for slot in rhythm:
        selected = beat_slice(threads, volume, slot, planned_state)
        slot["threads"] = [x["thread"] for x in selected]
        for item in selected:
            planned_state["ledger"].setdefault(item["thread"], {})["last"] = slot["number"]
    from .craft import recall_events

    total = integer(
        bible.get("chapters", bible.get("total_chapters")),
        max(v.get("end", 0) for v in bible.get("volumes", []) or [{}]),
    )
    saved = (
        [json.loads(p.read_text()) for p in sorted((client.out / "chapters").glob("*.json"))][:25]
        if getattr(client, "out", None)
        else []
    )
    events = (
        recall_events(client.out, history or saved, end, total, volume["end"])
        if getattr(client, "out", None)
        else callbacks(history)
        if start <= 100 <= end
        else []
    )
    raw = _ask(
        client,
        "outlines",
        f"chapters-{start}-{end}",
        [
            ("本批完整范围", dict(start=start, end=end, count=end - start + 1)),
            ("用户选定事实，只可扩写不可替换", bible.get("selected_card", {})),
            ("固定人物卡", [{k: c.get(k) for k in DATA["plan_card_fields"]} for c in bible.get("characters", [])]),
            ("固定成长阶梯与当前仪表盘", [profile.get("canon"), state.get("dashboard")]),
            (
                "稳定节奏排期，每章只执行指定两条线",
                [{k: r[k] for k in DATA["plan_slot_fields"] if k in r} for r in rhythm],
            ),
            ("爽点落实标准", GROWTH.get("payoff_criteria", {})),
            ("尾章必须融入当下冲突的首卷事实与原文，不复述梗概", events),
            ("本卷与固定节拍", [volume, _beats(threads, integer(volume.get("number")), state)]),
            ("警报与已用解法", [alerts, state.get("solutions", [])[-29:]]),
            ("圣经摘要", bible.get("summary", "")[:1800]),
            ("人物与能力", [bible.get("characters", []), bible.get("power", {})]),
            ("实际状态及近章", [state, {r.get("number"): r.get("summary") for r in recent[-5:]}]),
        ],
        redo,
    )
    source = rows(raw.get("chapters")) if isinstance(raw, dict) else rows(raw)
    mapped = {}
    for i, item in enumerate(source):
        mapped.setdefault(integer(item.get("number"), start + i), item)
    result = []
    for slot in rhythm:
        n = slot["number"]
        chapter = defaults(mapped.get(n), DATA["schemas"]["outlines"]["chapters"][0])
        chapter.update(
            number=n,
            threads=slot["threads"],
            type=chapter["type"] if chapter["type"] in DATA["profile"]["chapter_type_mix"] else slot["type"],
            hook_type=chapter["hook_type"] if chapter["hook_type"] in DATA["hook_types"] else slot["hook_type"],
        )
        chapter.update(
            structure=slot["structure"],
            required_event=slot["required_event"],
            payoff_due=slot["payoff_due"],
            payoff_type=slot.get("payoff_type"),
            hook=chapter.get("hook")
            if chapter["hook_type"] == slot["hook_type"]
            else "末钩目标" + slot["hook_type"] + "，用本场面既有冲突重构，不沿用旧结尾。",
            hook_type=slot["hook_type"],
            structure_rewrite="指定结构" + slot["structure"] + "优先覆盖冲突旧场景，必须围绕它重新设计行动后果。",
        )
        chapter["solution_type"] = chapter.get("solution_type") or slot.get("solution_type")
        chapter.update(
            solution_avoid=slot.get("solution_avoid", []),
            group_allowed=slot.get("group_allowed"),
            growth_obligation=slot.get("growth_obligation"),
            **{k: slot.get(k) for k in ("growth_setup", "upgrade_due", "next_upgrade")},
        )
        chapter["scenes"] = [
            defaults(x, DATA["schemas"]["outlines"]["chapters"][0]["scenes"][0]) for x in rows(chapter["scenes"])
        ]
        chapter["callback_events"] = events if n == total else events[:1] if n == volume["end"] else []
        result.append(chapter)
    return result
