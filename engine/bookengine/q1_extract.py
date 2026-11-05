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
from .serial_checks import adjacent, catch_hits
from .longutil import ask as raw_ask, recover_fields, long_context
from .q1_plan import beat_slice
from .storage import save
from .text import prose, number_paragraphs, _paragraph_spans

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
    return re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", adjacent(prose(text))).strip()


def body_of(value, client):
    if isinstance(value, dict):
        value = value.get("paragraphs", value.get("text", value.get("body", "")))
    if isinstance(value, list):
        value = "\n\n".join(
            p.get("text", "") if isinstance(p, dict) else p for p in value if isinstance(p, (str, dict))
        )
    return clean(value) if isinstance(value, str) else ""


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
        )
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
            "payoff_due",
            "payoff_type",
            "payoff_criterion",
            "solution_type",
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
    return [
        ("用户选定事实，只可扩写不可替换", pack(bible["selected_card"]))
        if bible.get("selected_card")
        else ("故事", ""),
        ("本章章纲", pack(outline)[:2500]),
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
    return raw


def prose_hits(text, bible, state):
    hits = name_hits(text, bible)
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
    sections = [
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
        ("编号正文", number_paragraphs(text)),
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
    if (
        not final
        and not outline.get("word_range", [1870, 2700])[0]
        <= outline.get("actual_chars", 2200)
        <= outline.get("word_range", [1870, 2700])[1]
    ):
        prompt = EXTRACT["length_first"] + prompt
    schema = pack(
        {
            k: v
            for k, v in EXTRACT["schema"].items()
            if not final
            or k not in ("edits", "paragraph_edits", "patch", "body", "scene", "rename", "tail", "previous_delta")
        }
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
