"""Turn planned turns of dialogue into compact directions for the writer."""

from pathlib import Path

from .nonhuman_speech import _can_speak, _from_chapter, is_nonhuman
from .q1_schema import integer, rows
from .storage import load


HEADING = "本章开口人物：声口与当场目的"
GUIDANCE = (
    "人物开口是为了问出、瞒住、拒绝或争取眼前的一件事。按声口和当场目的写来回反应；"
    "不要把卡里的说法、人物小传或解释说给对方听。未在这场在场的人不得开口。"
    "若临时加一位开口人物，先明确此人此刻要对方做什么，和其他人怎么说不同。"
)


def _text(value, limit=72):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _names(value):
    return [name.strip() for name in value if isinstance(name, str) and name.strip()] if isinstance(value, list) else []


def _can_talk(card, number):
    return not is_nonhuman(card) or (_can_speak(card) and number >= _from_chapter(card))


def speaker_briefs(outline, bible, previous_present=None, *, out=None):
    """Supply an intent for each named scene speaker and scheduled entrant."""
    opponents = {card["name"]: card for card in rows(bible.get("antagonists")) if _text(card.get("name"))}
    cast = dict(opponents)
    cast.update({card["name"]: card for card in rows(bible.get("characters")) if _text(card.get("name"))})
    hero = next((name for name, card in cast.items() if card.get("role") == "protagonist"), "")
    number = integer(outline.get("number"))
    briefs = []
    covered = set()

    for index, scene in enumerate(rows(outline.get("scenes")), 1):
        names = _names(scene.get("characters"))
        intents = {
            intent["name"].strip(): intent
            for intent in rows(scene.get("speaker_intents"))
            if _text(intent.get("name")) and (intent["name"].strip() in cast or intent["name"].strip() in names)
        }
        speakers = []
        for name in dict.fromkeys([*names, *intents]):
            card = cast.get(name, {})
            if not _can_talk(card, number):
                continue
            intent = intents.get(name, {})
            opponent = name in opponents or name == outline.get("opponent")
            if opponent and 0 < integer(opponents.get(name, {}).get("exit")) < number:
                continue
            goal = _text(intent.get("goal"))
            if not goal and (name == hero or opponent):
                goal = _text(scene.get("goal" if name == hero else "obstacle"))
            speakers.append(_brief(name, card, intent, goal, opponent))
            covered.add(name)
        if speakers:
            briefs.append(dict(scene=index, speakers=speakers))

    required = [
        name
        for duty in rows(outline.get("story_duties"))
        if duty.get("kind") in ("appearance", "relationship")
        for name in _names(duty.get("names"))
    ]
    required.extend(row["name"] for row in rows(outline.get("introductions")) if _text(row.get("name")))
    entrances = [
        _brief(name, cast[name], {}, "", name in opponents)
        for name in dict.fromkeys(required)
        if name in cast and name not in covered and _can_talk(cast[name], number)
    ]
    if entrances:
        briefs.append(dict(scene="本章安排出场或参与关系戏，仅在实际登场时采用", speakers=entrances))
        covered.update(speaker["name"] for speaker in entrances)
    returning = [
        name
        for name in dict.fromkeys(_names(previous_present))
        if name in cast
        and name not in covered
        and _can_talk(cast[name], number)
        and not 0 < integer(opponents.get(name, {}).get("exit")) < number
    ]
    if returning:
        previous = _previous_intents(out, number)
        speakers = [
            _brief(
                name,
                cast[name],
                previous.get(name, {}),
                _text(previous.get(name, {}).get("goal")) or "回应上一幕尚未解决的眼前问题",
                name in opponents,
            )
            for name in returning
        ]
        briefs.append(dict(scene="上章末在场，仅在本章承接同一场时采用，接着结果往下走", speakers=speakers))
    return briefs


def _previous_intents(out, number):
    if out is None or number < 2:
        return {}
    path = Path(out) / "writing_outlines" / f"{number - 1:03}.json"
    previous = load(path) if path.is_file() else {}
    return {
        speaker["name"]: speaker
        for scene in rows(previous.get("speaker_briefs"))
        if isinstance(scene.get("scene"), int)
        for speaker in rows(scene.get("speakers"))
        if _text(speaker.get("name"))
    }


def _brief(name, card, intent, goal, opponent):
    voice = _text(card.get("voice")) or _text(intent.get("voice"))
    if not voice:
        voice = "对眼前的人提具体要求，条件只说一遍，藏住私下盘算" if opponent else "按身份与对方关系说当下能说的话"
    desire = _text(card.get("desire"))
    if not goal:
        goal = f"从在意的事（{desire}）出发，先向在场的人争取一件具体小事" if desire else "只为眼前阻碍问、答或表态"
    return dict(name=name, voice=voice, goal=goal)
