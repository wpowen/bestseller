"""Q1 bounded context, prose helpers and grounded state extraction."""

import json
from pathlib import Path
import re
import yaml
from tools.runtime_support import QuotaExhausted, ProjectError
from .client import SYSTEM
from .contracts import TYPES, valid_extract
from .endings import ending_candidates, scene_context, english_hits
from .identity import name_hits
from .precision import locate, _sentences
from .polish_checks import scan
from .serial_checks import catch_hits
from .longutil import ask as raw_ask, recover_fields, long_context
from .q1_plan import beat_slice
from .storage import save
from .text import prose, number_paragraphs, _paragraph_spans
from .q1_cast import cast_hits, cast_findings
from .retired_opponents import stage_constraints
from .opening_promises import opening_contract
from .prose_repetition import clean_repetitions
from .q1_continuity import OPENING_RULE
from .solution_substance import observe_solutions
from tools.book_checkup import clean_prose

DATA = Path(__file__).resolve().parents[1] / "data/q1_style.yaml"
EXTRACT = yaml.safe_load((DATA.parent / "growth_extract.yaml").read_text())


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ask(client, *args, **kwargs):
    try:
        return raw_ask(client, *args, **kwargs)
    except (QuotaExhausted, ProjectError):
        raise
    except (ValueError, TypeError) as exc:
        return {}


def clean(text):
    return re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", text).strip()


def body_of(value, client, cleaning=None):
    if isinstance(value, dict):
        value = value.get("paragraphs", value.get("text", value.get("body", "")))
    if isinstance(value, list):
        value = "\n\n".join(
            p.get("text", "") if isinstance(p, dict) else p for p in value if isinstance(p, (str, dict))
        )
    if not isinstance(value, str):
        return ""
    value, counts = clean_prose(value)
    if cleaning is not None:
        cleaning.update(counts)
    return clean(value)


def context(bible, threads, vol, chapter, state, history, rows, previous, dropped=None):
    dropped = [] if dropped is None else dropped
    names = pack(chapter.get("scenes", []))
    people = sorted(
        [c for c in bible.get("characters", []) if c.get("role") == "protagonist" or c.get("name", "?") in names],
        key=lambda c: c.get("role") != "protagonist",
    )
    dash = {
        k: (str(v) if len(str(v)) <= 18 else str(v)[:13] + "…" + str(v)[-4:])
        for k, v in state.get("dashboard", {}).items()
    }
    dropped.extend("dashboard." + k + ":truncated" for k, v in state.get("dashboard", {}).items() if len(str(v)) > 18)
    cards = []
    for c in people:
        card = dict(
            name=c["name"],
            identity=c.get("identity", ""),
            voice=c.get("voice", "")[:100],
            gender=c.get("gender") or "未知",
            address=c.get("address") or c["name"],
            aliases=c.get("aliases", []),
        )
        card.update({k: c[k] for k in ("address_origin", "address_target") if c.get(k)})
        card.update(
            {k: c.get(k) for k in ("active_desire", "personality_tags", "never_do", "desire", "fear") if c.get(k)}
        )
        card["current"] = state.get("characters", {}).get(c["name"], {})
        if c.get("role") == "protagonist":
            card = dict(
                dashboard=dash,
                **card,
                continuity=state.get("continuity", {}),
                power_status=json.loads(
                    pack(
                        {
                            k: (v.get("值") if isinstance(v, dict) else v)
                            for k, v in state.get("power_status", {}).items()
                        }
                    ),
                    object_hook=lambda d: {k: v for k, v in d.items() if k != "最后更新"},
                ),
                power=bible.get("power", {}),
                realm_ability=next(
                    (
                        str(v)[:160]
                        for v in bible.get("world", {}).get("levels", [])
                        if str(state.get("dashboard", {}).get("strength", "?")) in str(v)
                    ),
                    "",
                ),
            )
        cards.append(card)
    dossier = dict(
        characters=cards or [dict(name=n, current=v) for n, v in state.get("characters", {}).items()],
        fixed_cast=[(c["name"], c.get("role")) for c in bible.get("characters", [])],
        **({"dashboard": dash} if not cards else {}),
    )
    hero = cards[0] if cards else {}
    candidates = (
        [(c, "current") for c in cards[1:]]
        + [(hero, k) for k in ("desire", "fear")]
        + [
            (c, k)
            for c in reversed(cards)
            for k in ("current", "desire", "fear", "active_desire", "personality_tags", "never_do")
        ]
    )
    for card, key in candidates:
        if len(pack(dossier)) - len(pack(dash)) <= 1500:
            break
        if key in card:
            dropped.append(card["name"] + "." + key)
            card.pop(key)
    outline = {
        k: chapter[k]
        for k in (
            "number",
            "transition",
            "introductions",
            "payoff_due",
            "payoff_type",
            "payoff_criterion",
            "solution_type",
            "solution_target",
            "solution_method",
            "solution_avoid",
            "group_allowed",
            "growth_obligation",
            "growth_setup",
            "upgrade_due",
            "next_upgrade",
            "injury_limit",
            "ending",
            "hook_type",
            "growth_target",
            "growth_setup",
            "upgrade_due",
            "next_upgrade",
            "title",
            "scenes",
            "hook",
            "threads",
            "must_respond",
            "closing",
            "callback_events",
            "opening_conflict",
            "structure",
        )
        if k in chapter
    }
    if chapter["number"] <= 3 and (bible.get("title") or bible.get("power")):
        requirements = [
            {k: item[k] for k in ("id", "requirement")}
            for item in opening_contract(bible)
            if item["chapter"] == chapter["number"]
        ]
        outline = dict({"本章承诺验收": requirements}, **outline)
    constraints = stage_constraints(bible, chapter["number"])
    constraints["omitted_retired"] = 0
    constraints["omitted_locations"] = 0
    while len(pack(constraints)) > 1200 and (constraints["retired"] or constraints["departed_locations"]):
        field = "retired" if constraints["retired"] else "departed_locations"
        constraints[field].pop(0)
        constraints["omitted_retired" if field == "retired" else "omitted_locations"] += 1
    if state.get("handoff_unknown"):
        outline["上章地点未知"] = "开头必须明确交代当前地点，并从上章尾可确认的处境交代移动或时间；不得臆造上章地点。"
    if state.get("end_location_candidate"):
        outline["上章结尾地点候选，仅供参考不当作事实"] = state["end_location_candidate"]
    if state.get("end_location") or state.get("present"):
        outline["开场必须承接"] = dict(
            end_location=state.get("end_location"),
            present=state.get("present", []),
            end_pov=state.get("end_pov"),
            pov_locations={
                name: {k: row.get(k) for k in ("location", "status", "chapter")}
                for name, row in state.get("pov_locations", {}).items()
                if name in {c.get("name") for c in people}
            },
            rule=OPENING_RULE,
        )
    handoff_keys = ("开场必须承接", "上章地点未知", "上章结尾地点候选，仅供参考不当作事实")
    outline = {**{k: outline[k] for k in handoff_keys if k in outline}, **outline}
    return [
        *([("用户选定事实，只可扩写不可替换", pack(bible["selected_card"]))] if bible.get("selected_card") else []),
        ("本章章纲", pack(outline)[:2500] + "\n阶段退场约束：" + pack(constraints)),
        ("出场人物档案、口吻卡与当前状态", pack(dossier)),
        ("上章结尾", previous[-900:]),
        ("本章推进线", pack(beat_slice(threads, vol, chapter, state))[:1000]),
    ]


def normalize_extract(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("state"), dict):
        return raw
    fields = recover_fields(pack(raw))
    result = dict(raw)
    result.update(
        {"type": EXTRACT.get("type_aliases", {}).get(raw["type"], raw["type"])}
        if isinstance(raw.get("type"), str)
        else {}
    )
    for key in (
        "gains",
        "costs",
        "solutions",
        "advances",
        "events",
        "r2_events",
        "demotion_event",
        "injuries",
        "previous_delta",
        "realm_displayed",
        "realm_display_pid",
        "realm_display_sid",
        "opens_new_mainline",
        "ending_reason",
        "body",
        "scene",
        "rename",
        "tail",
        "patch",
        "edits",
        "paragraph_edits",
    ):
        if result.get(key) is None and key in fields:
            result[key] = fields[key]
    return result


def ground(raw, text):
    raw = normalize_extract(raw)
    raw = raw if isinstance(raw, dict) else {}
    raw["state"] = raw.get("state") if isinstance(raw.get("state"), dict) else {}
    spans = _sentences(text)
    paragraphs = _paragraph_spans(text)
    for k in ("gains", "costs", "solutions", "injuries", "events", "advances"):
        raw[k] = raw[k] if isinstance(raw.get(k), list) else [raw[k]] if isinstance(raw.get(k), dict) else []
    values = [
        raw.get("payoff"),
        raw.get("demotion_event"),
        *[x for k in ("gains", "costs", "solutions", "injuries") for x in (raw.get(k) or []) if isinstance(x, dict)],
    ]
    for item, key in (
        (item, key)
        for item in [raw, *values]
        if isinstance(item, dict)
        for key in ("pid", "sid", "realm_display_pid", "realm_display_sid")
    ):
        if isinstance(item.get(key), str) and re.fullmatch(r"[PS]?\d+", item[key]):
            item[key] = int(item[key].lstrip("PS"))
    for item in values:
        if isinstance(item, dict) and type(item.get("pid")) is int and 1 <= item["pid"] <= len(paragraphs):
            a, b = paragraphs[item["pid"] - 1]
            item["quote"] = text[a:b]
        elif (
            isinstance(item, dict)
            and isinstance(item.get("pid"), str)
            and (m := re.fullmatch(r"P?(\d+)\s*[-–—~至到]\s*P?(\d+)", item["pid"], re.I))
            and 1 <= int(m[1]) <= int(m[2]) <= len(paragraphs)
        ):
            item["quote"] = text[paragraphs[int(m[1]) - 1][0] : paragraphs[int(m[2]) - 1][1]]
        elif isinstance(item, dict) and type(item.get("sid")) is int and 1 <= item["sid"] <= len(spans):
            a, b = spans[item["sid"] - 1]
            item["quote"] = text[a:b]
    if type(raw.get("realm_display_sid")) is int and 1 <= raw["realm_display_sid"] <= len(spans):
        a, b = spans[raw["realm_display_sid"] - 1]
        raw["realm_display_quote"] = text[a:b]
    if type(raw.get("realm_display_pid")) is int and 1 <= raw["realm_display_pid"] <= len(paragraphs):
        a, b = paragraphs[raw["realm_display_pid"] - 1]
        raw["realm_display_quote"] = text[a:b]
    return observe_solutions(raw, text)


def prose_hits(text, bible, state):
    hits = name_hits(text, bible)
    hits += cast_hits(text, bible, state)
    pending = clean_repetitions(text)["needs_repair"]
    hits += [
        dict(id=i, start=a, end=b, kind="prose_repetition", text=text[a:b])
        for i, (a, b) in enumerate(_sentences(text), 1)
        if any(a < r["end"] and b > r["start"] for r in pending)
    ]
    hits += [
        dict(id=i, start=a, end=b, kind="english_or_injury", text=text[a:b])
        for i, (a, b) in enumerate(_sentences(text), 1)
        if english_hits(text[a:b]) or re.search(EXTRACT["injury_pattern"], text[a:b])
    ]
    return (
        hits
        + locate(text)
        + scan(text, bible.get("characters", []), state)
        + catch_hits(text, [], bible.get("characters", []))
    )


def extract_state(client, name, text, state, bible, outline, rows, final=False):
    previous = rows[-1] if rows and rows[-1].get("state_failed") and not rows[-1].get("carry_attempted") else None
    prior = (client.out / f"chapter-{previous['number']:03d}.body.md").read_text() if previous else ""
    hits = prose_hits(text, bible, state) if not final else []
    contract = {
        k: outline.get(k)
        for k in (
            "number",
            "payoff_due",
            "payoff_type",
            "payoff_criterion",
            "solution_type",
            "solution_target",
            "solution_method",
            "solution_avoid",
            "group_allowed",
            "growth_obligation",
            "growth_setup",
            "upgrade_due",
            "next_upgrade",
            "injury_limit",
            "ending",
            "ending_review",
            "force_scene",
            "actual_chars",
            "target_chars",
            "word_range",
            "dialogue_ratio",
        )
    }
    contract["scene_start"] = scene_context(text)["start"] if outline.get("ending") else None
    findings = [
        {k: (str(row.get(k, ""))[:180]) for k in ("kind", "name", "status", "message")}
        for row in cast_findings(text, bible, state, outline)
        if row.get("status") != "Evidence"
    ][:12]
    sections = [
        ("编号正文", number_paragraphs(text)),
        (
            "合法值",
            pack(
                dict(
                    types=TYPES,
                    ladder=bible["canon"]["ladder"],
                    payoff_menu=bible["canon"]["payoff_menu"],
                    solution_menu=bible["canon"]["solution_menu"],
                    hook_types=bible["canon"]["hook_types"],
                )
            ),
        ),
        ("本章要求", pack(contract)),
        ("登场与称呼问题，合并本次定点修复", pack(findings)),
        ("旧状态", pack({k: state.get(k) for k in ("dashboard", "power_status", "continuity", "clock")})[:500]),
        ("候选句S编号", pack(hits)[:1800]),
        (
            "固定角色",
            pack(
                [
                    dict(name=c["name"], identity=c.get("identity", ""), role=c.get("role"))
                    for c in bible.get("characters", [])
                ]
            ),
        ),
        (
            "末场可改范围",
            pack({k: v for k, v in scene_context(text).items() if k != "paragraphs"}) if outline.get("ending") else "",
        ),
        ("末章候选", pack(ending_candidates(text, bible, outline) if outline.get("ending") else [])),
        ("上章失败顺带补提", pack(dict(number=previous["number"], text=prior)) if previous else ""),
    ]
    prompt = EXTRACT["final_prompt"] if final else EXTRACT["prompt"]
    prompt += (
        "\n只据正文抽取边界事实：opening_location/opening_quote限开头300字；end_location/end_quote从末尾1200字符内"
        "能证明最后所在地的叙述句提取，不要只看末尾对白。location必须是quote内连续原文，不拼接、不概括。"
        "quote逐字引用且包含地点原名；无法确定填null。present只列结尾实际在场人物，present_quotes逐人复制末场含其原名的叙述句，"
        "不能用不含人物名的纯对白，且引文必须在末尾1200字符内。"
        "提到但不在场者不列；不从旧状态或章纲推测。"
        "opening_pov/end_pov抽取开头/结尾的视角人物，opening_pov_quote/end_pov_quote复制对应含姓名的逐字叙述；"
        "不要把仅被提及者当视角，不确定填null。character_locations只列有逐字引文证明其当前地点的人物，"
        "每项name/location/quote，按正文时间顺序；回忆、计划和对白中的地点不算。"
    )
    prompt += "\n每条solutions附target_noun作用对象、method_noun手段核心名词和含两者及结果的逐字quote，primary标主要解法；不以类型标签代替名词。"
    if not final:
        prompt += (
            "\nprose_repetition是整句/邻段复述：仅改后一次，保留主体指代与衔接，不能删掉被前后句依赖的事实。"
            "\n人物首次出现需一句与卡片一致的身份或来历。missing_introduction/unregistered_alias与其他问题在本轮"
            "统一用edits修复，id用候选句的S编号；补身份只在该句自然交代，卡外外号或自称改为卡片name/address/aliases。"
            "未知身份不编造，不把英语role当身份。"
        )
    if (
        not final
        and not outline.get("word_range", [1870, 2700])[0]
        <= outline.get("actual_chars", 2200)
        <= outline.get("word_range", [1870, 2700])[1]
    ):
        prompt = EXTRACT["length_first"] + prompt
    schema = pack(
        dict(
            {
                k: v
                for k, v in EXTRACT["schema"].items()
                if not final
                or k not in ("edits", "paragraph_edits", "patch", "body", "scene", "rename", "tail", "previous_delta")
            },
            opening_location=None,
            opening_quote=None,
            end_location=None,
            end_quote=None,
            present=[],
            present_quotes={},
            opening_pov=None,
            opening_pov_quote=None,
            end_pov=None,
            end_pov_quote=None,
            character_locations=[],
        )
    )
    saved = client.prefix
    client.prefix = ""
    complete = not previous or sections[-1][1] in long_context(
        sections, max(0, 10000 - len(SYSTEM) - len(prompt) - len(schema) - 350)
    )
    try:
        raw = ground(
            ask(
                client,
                name,
                prompt,
                sections,
                schema,
                cap=6000 if final else 7000,
                effort="low",
                input_limit=10000,
                max_attempts=1,
            ),
            text,
        )
    finally:
        client.prefix = saved
    if outline.get("ending") and raw.get("hook_type") is None and raw.get("opens_new_mainline") is False:
        raw["hook_type"] = "完结收束"
    raw["_previous_complete"] = complete
    save(client.out / "steps" / f"{name}.json", raw)
    return (
        raw,
        not valid_extract(
            raw,
            text,
            bible["canon"]["hook_types"] + (["完结收束"] if outline.get("ending") else []),
            bible["canon"]["payoff_menu"],
            ending=outline.get("ending", False),
        ),
        0,
    )
