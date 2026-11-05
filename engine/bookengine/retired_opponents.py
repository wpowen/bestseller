"""Shared, conservative surface checks for retired opponents and old stages."""

import json
import re

from .precision import _sentences, apply as sentence_edits
from .q1_schema import integer, rows
from .structure_planning import stage_for_chapter


def retired_opponents(bible, number):
    canonical = {c.get("name") for c in rows(bible.get("characters"))}
    result = []
    for opponent in rows(bible.get("antagonists")):
        name, exit_at = opponent.get("name"), integer(opponent.get("exit"))
        if not isinstance(name, str) or not name or not 0 < exit_at < number:
            continue
        card = next((c for c in rows(bible.get("characters")) if c.get("name") == name), {})
        aliases = card.get("aliases", [])
        aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
        labels = [name, *[a for a in aliases if isinstance(a, str) and a and a not in canonical]]
        result.append(dict(opponent, labels=labels))
    return result


_PAST = re.compile(r"回忆|想起|忆起|当年|那年|往年|昔日|从前|过去|此前|曾经|曾|早已|旧事")
_NOW = re.compile(r"如今|现在|此刻|眼下|正在|仍在|还在|又(?:派|来|要|封|截)")
_ACTION = re.compile(
    r"^(?:[，,：:\s]*)(?:(?:正|仍|还|又|已|竟|却|则|也|便|就|竟然|已经|突然|终于|亲自|立刻)*)(?:"
    r"要|派|截|封|追|杀|打|抢|夺|攻|逼|拦|堵|命令|下令|调|抬|举|挥|走|冲|站|立|怒吼|喝|说|道|喊|看|笑|伸|拔|按|扣|指|出手|现身|出现|复返|在)"
)
_AGENTS = re.compile(r"^的(?:人|手下|部下|兵|军|队|门徒|弟子|爪牙)|^麾下")


def active_opponent_hits(text, bible, number):
    """Return sentence targets with explicit agents/actions; all results need reader review."""
    hits = []
    for sid, (start, end) in enumerate(_sentences(text), 1):
        sentence = text[start:end]
        for opponent in retired_opponents(bible, number):
            for label in opponent["labels"]:
                active = False
                for match in re.finditer(re.escape(label), sentence):
                    before, after = sentence[: match.start()], sentence[match.end() :]
                    # A new current clause cannot be hidden by an earlier reminiscence.
                    clause = re.split(r"[，,；;：:]", before)[-1] + label + re.split(r"[，,；;。！？]", after)[0]
                    if _PAST.search(clause) and not _NOW.search(clause):
                        continue
                    direct = bool(_ACTION.search(after) or _AGENTS.search(after))
                    retribution = bool(
                        re.search(r"(?:找|追|讨|算)[^。！？]{0,30}$", before)
                        and re.match(r"的?(?:账|仇)(?!册|本|簿)", after)
                    )
                    if direct or retribution:
                        active = True
                        break
                if active:
                    hits.append(
                        dict(
                            id=sid,
                            start=start,
                            end=end,
                            text=sentence,
                            kind="retired_opponent",
                            name=opponent["name"],
                            exit=opponent["exit"],
                            status="Inference",
                        )
                    )
                    break
    return hits


def initiator_issues(bible, batch):
    issues = []
    for chapter in batch:
        number = integer(chapter.get("number"))
        for scene in [chapter, *rows(chapter.get("scenes"))]:
            for field in ("conflict", "opponent"):
                value = scene.get(field)
                value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if value else ""
                for opponent in retired_opponents(bible, number):
                    if any(label in value for label in opponent["labels"]) and not (
                        _PAST.search(value) and not _NOW.search(value)
                    ):
                        issues.append(
                            dict(
                                kind="retired_initiator",
                                name=opponent["name"],
                                field=field,
                                chapters=[number],
                                targets=[number],
                                quote=value,
                            )
                        )
    return issues


def stage_constraints(bible, number):
    stage = stage_for_chapter(bible, number)
    return dict(
        retired=[{k: r[k] for k in ("name", "exit", "labels")} for r in retired_opponents(bible, number)],
        current_opponent=stage.get("antagonist"),
        current_location=stage.get("location", stage.get("stage")),
        departed_locations=[
            s.get("location", s.get("stage"))
            for s in rows(bible.get("volumes"))
            if 0 < integer(s.get("end")) < integer(stage.get("start"))
        ],
        rule="已退场对手只可回忆旧事，不得施动、派人或主导冲突；禁止退回已离开的主地点作为章末舞台。",
    )


def location_regression(bible, number, location, quote):
    if not location or not quote or location not in quote:
        return None
    constraints = stage_constraints(bible, number)
    for old in constraints["departed_locations"]:
        if isinstance(old, str) and old and old in location and old != constraints["current_location"]:
            return dict(chapter=number, location=location, departed_location=old, quote=quote, status="Inference")
    return None


def repair_retired(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    hits = active_opponent_hits(text, bible, integer(outline.get("number")))
    note = dict(status="checked", before=hits, after=hits)
    if not hits:
        return text, raw, failed, note
    patch = ask(
        client,
        prefix + "-opponent-repair",
        "只改候选句的S编号，返回edits。退场对手不能再次主动行动；按当前阶段真实关系改为本阶段对手，"
        "或自然改为旧事背景，不能机械换名，不得添加新事实。其他句逐字保留。",
        [
            ("阶段约束", json.dumps(stage_constraints(bible, outline["number"]), ensure_ascii=False)),
            ("待修句", json.dumps(hits, ensure_ascii=False)),
            ("只读上下文", text),
        ],
        '{"edits":[{"id":1,"text":"替换句"}]}',
        cap=2500,
        input_limit=10000,
    )
    candidate, edits = sentence_edits(
        text, patch.get("edits", []) if isinstance(patch, dict) else [], hits, allow_empty=True, allow_style=True
    )
    if edits and candidate != text and not active_opponent_hits(candidate, bible, outline["number"]):
        checked, invalid, _ = extract(
            client, prefix + "-opponent-recheck", candidate, state, bible, outline, [], final=True
        )
        from .checks import high_confidence

        if not invalid and not high_confidence(state, checked.get("events", []), candidate):
            return candidate, checked, False, dict(note, status="repaired", after=[], edits=edits)
    return text, raw, failed, dict(note, status="rolled_back")
