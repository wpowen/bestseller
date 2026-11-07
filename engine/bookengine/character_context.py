"""Deterministic character-context scheduling shared by planning and writing."""

import re


def identity_keywords(identity, occupation=""):
    """Reduce appositive identity prose to short non-copyable role keywords."""
    source = occupation or identity
    if not isinstance(source, str):
        return []
    result = []
    for clause in re.split(r"[，,；;、。]+", source):
        pieces = [piece.strip() for piece in clause.split("的") if piece.strip()]
        if not pieces:
            continue
        keyword = pieces[-1]
        if len(pieces) > 1 and len(pieces[-2]) <= 4 and len(pieces[-2] + keyword) <= 7:
            keyword = pieces[-2] + keyword
        keyword = re.sub(r"^(?:被|曾|现任|原是|本是)", "", keyword).strip()
        keyword = keyword[-7:]
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def catchphrase_due(name, chapter_number, interval=5):
    """Spread each character's optional catchphrase slot across a stable interval."""
    if not isinstance(name, str) or not name or type(chapter_number) is not int or chapter_number < 1:
        return False
    offset = sum(ord(char) for char in name) % interval
    return (chapter_number - 1) % interval == offset


def schedule_catchphrases(chapter, bible):
    """Return an outline with only scene characters whose five-chapter slot is due."""
    chapter = dict(chapter)
    scene_names = {
        name
        for scene in chapter.get("scenes", [])
        if isinstance(scene, dict)
        for name in scene.get("characters", [])
        if isinstance(name, str)
    }
    number = chapter.get("number")
    chapter["catchphrase_due"] = [
        card["name"]
        for card in bible.get("characters", [])
        if card.get("name") in scene_names and card.get("catchphrase") and catchphrase_due(card["name"], number)
    ]
    return chapter


def catchphrase_context(card, chapter):
    """Expose a catchphrase only in its planned slot, explicitly as optional."""
    due = chapter.get("catchphrase_due")
    selected = (
        card.get("name") in due if isinstance(due, list) else catchphrase_due(card.get("name"), chapter.get("number"))
    )
    if not selected or not card.get("catchphrase"):
        return {}
    return {
        "catchphrase": card["catchphrase"],
        "catchphrase_guidance": "本章可在合适情境用一次，不强求。",
    }


def _scene_names(chapter):
    return {
        name
        for scene in chapter.get("scenes", [])
        if isinstance(scene, dict)
        for name in scene.get("characters", [])
        if isinstance(name, str)
    }


def _compress(value, limit=48):
    if isinstance(value, dict):
        return {
            key: _compress(item.get("值", item) if isinstance(item, dict) else item, limit)
            for key, item in value.items()
        }
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def trimming_candidates(cards, chapter):
    """Order expendable fields while keeping scene characterization until last."""
    hero = cards[0] if cards else {}
    scenes = _scene_names(chapter)
    absent = [card for card in cards if card.get("name") not in scenes]
    present = [card for card in cards if card.get("name") in scenes]
    supporting = [card for card in present if card is not hero]
    return (
        [(card, key) for card in absent for key in tuple(card) if key != "name"]
        + [
            (card, key)
            for card in reversed(present)
            for key in ("address_origin", "address_target", "aliases", "gender", "identity_keywords")
        ]
        + [(card, key) for card in reversed(supporting) for key in ("desire", "fear")]
        + [
            (card, key)
            for card in reversed(present)
            for key in ("active_desire", "personality_tags", "never_do", "catchphrase", "catchphrase_guidance")
        ]
        + [(hero, key) for key in ("desire", "fear")]
        + [(card, key) for card in reversed(present) for key in ("flaw", "bottom_line", "current")]
    )


def trim_dossier(dossier, cards, chapter, dropped, pack, limit=1500):
    """Fit the dossier by compressing system/history data before scene characters."""
    if len(pack(dossier)) <= limit:
        return
    hero = cards[0] if cards else {}
    if hero.get("dashboard"):
        hero["dashboard"] = {key: value for key, value in hero["dashboard"].items() if key in ("strength", "stage")}
        dropped.append(hero["name"] + ".dashboard:compressed")
    if len(pack(dossier)) > limit and hero.get("continuity"):
        hero["continuity"] = _compress(hero["continuity"])
        dropped.append(hero["name"] + ".continuity:compressed")
    if len(pack(dossier)) > limit and hero.get("power"):
        hero["power"] = _compress(hero["power"], 80)
        dropped.append(hero["name"] + ".power:compressed")
    if len(pack(dossier)) > limit:
        scenes = _scene_names(chapter)
        omitted = [name for name, _ in dossier.get("fixed_cast", []) if name not in scenes]
        dossier["fixed_cast"] = [row for row in dossier.get("fixed_cast", []) if row[0] in scenes]
        dropped.extend("fixed_cast." + name for name in omitted)
    for card, key in trimming_candidates(cards, chapter):
        if len(pack(dossier)) <= limit:
            break
        if key in card:
            dropped.append(card["name"] + "." + key)
            card.pop(key)
