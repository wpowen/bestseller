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
from .structure_planning import stage_by_number, opponent_presence_issues
from .stage_transition import planning_transition_issues, complete_transitions
from .retired_opponents import stage_constraints
from .solution_substance import solution_context, solution_issues, chapter_nouns
from .ability_methods import primary_target
from .hook_momentum import apply_ending_contract, schedule_ending_types
from .core_ability import ability_slots, ability_issues, core_source, enforce_ability_plan, RULE as CORE_RULE
from .character_context import schedule_catchphrases
from .chapter_rhythm import RULE as RHYTHM_RULE, VERSION as RHYTHM_VERSION, apply_contract, chapter_type, rhythm_issues
from .rhythm_skeleton import stage_content_skeleton, window_stage_content
from .outline_diversity import (
    STRUCTURE_CHECKS_VERSION,
    historical_outlines,
    terminal_window,
    used_vocabulary,
    prompt_vocabulary,
    repair_batch,
    title_terms,
    conflict_type,
)
from .rhythm_replanning import model_review_issues, observed_rows, replan_context, semantic_review


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
    boundary = stage_by_number(bible, number, total)
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
            ("对手分级与退场：exit后只能背景提及，不得在场或继续主导冲突", bible.get("antagonists", [])),
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
            ("指定卷范围", {k: boundary[k] for k in ("number", "start", "end")}),
            ("反馈", feedback),
            ("已发生历史", events),
            ("实际状态", state),
            ("圣经", bible),
        ],
        redo,
    )
    value = defaults(dict(skeleton, **raw) if isinstance(raw, dict) else skeleton, DATA["schemas"]["volume"])
    value.update(number=number, start=boundary["start"], end=boundary["end"], beats=beats, feedback=feedback)
    if bible.get("structure_version"):
        value.update({k: skeleton.get(k) for k in ("location", "antagonist", "goal", "climax", "exit_chapter")})
        value["stage"] = skeleton.get("location", "")
    if target:
        value["expected_dashboard"] = dict(value.get("expected_dashboard") or {}, strength=target)
    value["callback_events"] = events
    return value


def schedule(start, end, profile, recent=(), state=None, *, stage_start=None, stage_end=None):
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
    ending_slots = {
        row["number"]: row
        for row in schedule_ending_types(
            start,
            end,
            stage_end=stage_end,
            recent=[r for r in previous if stage_start is None or integer(r.get("number")) >= stage_start],
        )
    }
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
            ending_type=ending_slots[n]["ending_type"],
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
    prior = historical_outlines(getattr(client, "out", None), start, recent)
    cached = terminal_window(getattr(client, "out", None), start, end) if not redo else None
    plot_history = [dict(number=r["number"], nouns=chapter_nouns(r, bible), target=primary_target(r)) for r in prior]
    plot_stale = bool(
        cached
        and (solution_issues(bible, prior, cached["chapters"]) + ability_issues(bible, prior, cached["chapters"]))
        and cached.get("plot_history") != plot_history
    )
    revision_suffix = ""
    if plot_stale and getattr(client, "out", None):
        revision_suffix = "-observed" + str(len(list((Path(client.out) / "outline-diversity").glob("*.json"))) + 1)
    superseded = bool(
        cached
        and (
            plot_stale
            or rhythm_issues(bible, prior, cached["chapters"])
            or (
                cached.get("structure_checks_version", 0) < STRUCTURE_CHECKS_VERSION
                and (
                    opponent_presence_issues(bible, cached["chapters"])
                    + planning_transition_issues(bible, cached["chapters"])
                    + ability_issues(bible, prior, cached["chapters"])
                    + solution_issues(bible, prior, cached["chapters"])
                )
            )
        )
    )
    stale = cached if superseded else None
    if superseded:
        end = cached["end"]
        cached = None
    if cached:
        if cached["normalized"]:
            return apply_ending_contract(
                complete_transitions(
                    cached["chapters"], historical_outlines(getattr(client, "out", None), start, recent)
                ),
                stage_end=integer(volume.get("end"), end),
            )
        end = cached["end"]
    used = prompt_vocabulary(used_vocabulary(prior))
    rhythm = schedule(
        start,
        end,
        profile,
        prior,
        state,
        stage_start=integer(volume.get("start"), start),
        stage_end=integer(volume.get("end"), end),
    )
    stage_skeleton, scene_slots = stage_content_skeleton(client, bible, volume, prior, rhythm)
    core_slots = {r["number"]: r for r in ability_slots(bible, start, end)}
    for slot, scene_slot in zip(rhythm, scene_slots):
        slot.update(scene_slot, type=scene_slot["rhythm_type"])
        slot.update(core_slots.get(slot["number"], {}))
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
    stage_contract = {
        k: volume.get(k)
        for k in ("number", "start", "end", "location", "stage", "antagonist", "goal", "climax", "exit_chapter", "cost")
    }
    opponents = [
        {k: opponent.get(k) for k in ("name", "tier", "stage", "exit")} for opponent in rows(bible.get("antagonists"))
    ]
    cards = [{k: c.get(k) for k in DATA["plan_card_fields"]} for c in bible.get("characters", [])]
    sections = [
        (CORE_RULE, dict(source=core_source(bible), required=core_slots)),
        (
            "主要解法必须给solution_target作用对象及solution_method手段核心名词；普通手段同阶段同名词最多3次；圣经核心能力及子能力另计，同阶段同能力组合对同类对象最多2次；超额换实际手段或作用对象，不只换标签",
            solution_context(bible, prior, start),
        ),
        (RHYTHM_RULE, window_stage_content(stage_skeleton, start, end)),
        ("本批完整范围", dict(start=start, end=end, count=end - start + 1)),
        ("用户选定事实，只可扩写不可替换", bible.get("selected_card", {})),
        ("阶段地点、对手、目标与收束固定约束", stage_contract),
        (
            "阶段首章transition必填：一两句交代从上一阶段实际终点到本阶段起点的移动、时间与原因；开头300字落实",
            dict(start=volume.get("start"), previous_end=state.get("end_location")),
        ),
        ("已退场对手不能在conflict/opponent中发起冲突；已离开地点不能再作章末舞台", stage_constraints(bible, start)),
        ("对手分级与退场：exit后只能背景提及，禁止列入scene.characters", opponents),
        ("固定人物卡，首次登场须交代identity，称呼仅限name/aliases", cards),
        ("已用核心冲突类型与章名关键词", used),
        (
            "前四章核心冲突，连续五章不得同型",
            [
                dict(number=r["number"], title=str(r.get("title", ""))[:80], conflict_type=conflict_type(r))
                for r in prior[-4:]
            ],
        ),
        (
            "稳定节奏排期，每章只执行指定两条线",
            [{k: r[k] for k in DATA["plan_slot_fields"] if k in r} for r in rhythm],
        ),
        ("固定成长阶梯与当前仪表盘", [profile.get("canon"), state.get("dashboard")]),
        ("本阶段固定节拍", _beats(threads, integer(volume.get("number")), state)),
        ("爽点落实标准", GROWTH.get("payoff_criteria", {})),
        ("尾章必须融入当下冲突的首卷事实与原文，不复述梗概", events),
        ("警报与已用解法", [alerts, state.get("solutions", [])[-29:]]),
        ("圣经摘要", bible.get("summary", "")[:1800]),
        ("能力", bible.get("power", {})),
        ("实际状态及近章", [state, {r.get("number"): r.get("summary") for r in recent[-5:]}]),
    ]
    raw = stale or cached or _ask(client, "outlines", f"chapters-{start}-{end}", sections, redo)
    source = rows(raw.get("chapters")) if isinstance(raw, dict) else rows(raw)
    mapped = {}
    for i, item in enumerate(source):
        mapped.setdefault(integer(item.get("number"), start + i), item)

    candidates = []
    for slot in rhythm:
        candidate = dict(mapped.get(slot["number"], {}), number=slot["number"])
        if chapter_type(candidate) not in DATA["profile"]["chapter_type_mix"]:
            candidate["type"] = slot["type"]
        candidate.setdefault("rhythm_type", candidate["type"])
        candidate.setdefault("rhythm_focus", slot["rhythm_focus"])
        candidate.setdefault("rhythm_contract_version", RHYTHM_VERSION)
        candidates.append(candidate)

    key = f"{start:03d}-{end:03d}" + (f"-redo{redo}" if redo else "")
    if superseded:
        key += f"-redo-structure35v{STRUCTURE_CHECKS_VERSION}" + revision_suffix
    deterministic_review = rhythm_issues(bible, prior, candidates)
    review = semantic_review(client, key, candidates, deterministic_review)
    review_issues = model_review_issues(candidates, review)
    observed = observed_rows(recent, history)

    def rewrite(current, issues, targets, vocabulary, attempt):
        context = replan_context(
            prior,
            current,
            targets,
            observed,
            scene_slots,
            stage_start=integer(volume.get("start"), start),
            stage_end=integer(volume.get("end"), end),
        )
        return _ask(
            client,
            "outlines",
            f"chapters-{start}-{end}-diversity"
            + (f"-structure35v{STRUCTURE_CHECKS_VERSION}" if superseded else "")
            + (f"-user{redo}" if redo else "")
            + revision_suffix,
            [
                (CORE_RULE, dict(source=core_source(bible), required=core_slots)),
                (
                    "普通手段名词阶段上限3次，核心能力组合对同类对象阶段上限2次；重写实际动作，解法附solution_target/solution_method",
                    solution_context(bible, prior, start),
                ),
                (RHYTHM_RULE, [r for r in scene_slots if r["number"] in targets]),
                ("只重写本批相关章，完整返回这些章号，禁止改其他章与历史", targets),
                ("每章重写两场场景，冲突、障碍、选择和行动后果必须与要求章型一致", targets),
                ("逐项错误清单，必须更改场景行动与后果，不能只换conflict_type标签", issues),
                ("目标章原内容、要求章型、前后章摘要与必须保留事实，只能改目标章", context),
                ("固定人物卡与阶段约束", [cards, stage_contract, bible.get("selected_card", {})]),
                (
                    "阶段首章必须带transition：从上阶段终点到新阶段的移动、时间与原因，落实于开头300字",
                    state.get("end_location"),
                ),
                ("退场后只作背景，禁止列在场人物", opponents),
                ("已退场对手与已离开地点约束", stage_constraints(bible, start)),
                ("已用核心冲突类型与章名关键词", prompt_vocabulary(vocabulary)),
                ("待重写章纲", [r for r in current if r.get("number") in targets]),
                ("相关节奏要求", [r for r in rhythm if r["number"] in targets]),
            ],
            attempt,
        )

    repaired = (
        cached["chapters"]
        if cached
        else repair_batch(
            getattr(client, "out", None),
            key,
            prior,
            candidates,
            rewrite,
            bible=bible,
            initial_issues=review_issues,
            model_review=review,
        )
    )
    mapped = {r["number"]: r for r in repaired}
    result = []
    for slot in rhythm:
        n = slot["number"]
        chapter = defaults(mapped.get(n), DATA["schemas"]["outlines"]["chapters"][0])
        chapter["title_keywords"] = title_terms(chapter.get("title"), chapter.get("title_keywords"))
        chapter.update(
            number=n,
            threads=slot["threads"],
            type=chapter_type(chapter)
            if chapter_type(chapter) in DATA["profile"]["chapter_type_mix"]
            else slot["type"],
            hook_type=chapter["hook_type"] if chapter["hook_type"] in DATA["hook_types"] else slot["hook_type"],
        )
        chapter.update(
            structure=slot["structure"],
            required_event=slot["required_event"],
            payoff_due=slot["payoff_due"],
            payoff_type=slot.get("payoff_type"),
            ending_type=slot["ending_type"],
            ending_content=chapter.get("ending_content") or chapter.get("hook"),
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
    result = enforce_ability_plan(bible, result)
    result = [schedule_catchphrases(chapter, bible) for chapter in result]
    result = [apply_contract(chapter) for chapter in result]
    result = complete_transitions(result, prior)
    result = apply_ending_contract(result, stage_end=integer(volume.get("end"), end))
    final_rhythm_issues = rhythm_issues(bible, prior, result)
    if getattr(client, "out", None) and (not cached or final_rhythm_issues):
        source_path = cached["path"] if cached else None
        if cached:
            key += f"-redo-rhythm{RHYTHM_VERSION}"
        path = Path(client.out) / "outline-diversity" / (key + ".json")
        report = json.loads(Path(source_path or path).read_text())
        report["rhythm_contract_version"] = RHYTHM_VERSION
        report["plot_history"] = plot_history
        report["plot_issues_after"] = solution_issues(bible, prior, result) + ability_issues(bible, prior, result)
        report["rhythm_issues_after"] = final_rhythm_issues
        if source_path:
            report.update(source_report=source_path, chapters=result)
        save(path, dict(report, planned_chapters=result))
    return result
