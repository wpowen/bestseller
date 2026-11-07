"""Deterministic character-context scheduling shared by planning and writing."""


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
        if card.get("name") in scene_names
        and card.get("catchphrase")
        and catchphrase_due(card["name"], number)
    ]
    return chapter


def catchphrase_context(card, chapter):
    """Expose a catchphrase only in its planned slot, explicitly as optional."""
    due = chapter.get("catchphrase_due")
    selected = card.get("name") in due if isinstance(due, list) else catchphrase_due(card.get("name"), chapter.get("number"))
    if not selected or not card.get("catchphrase"):
        return {}
    return {
        "catchphrase": card["catchphrase"],
        "catchphrase_guidance": "本章可在合适情境用一次，不强求。",
    }


def trimming_candidates(cards):
    """Order expendable fields while keeping scene characterization until last."""
    hero = cards[0] if cards else {}
    return (
        [(card, "current") for card in cards[1:]]
        + [(hero, key) for key in ("desire", "fear")]
        + [
            (card, key)
            for card in reversed(cards)
            for key in (
                "current",
                "desire",
                "fear",
                "active_desire",
                "personality_tags",
                "never_do",
                "flaw",
                "bottom_line",
            )
        ]
    )
