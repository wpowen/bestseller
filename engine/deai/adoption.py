"""Helpers for choosing and validating source-preserving de-AI edits."""

import re

from .rules import carry_hits, neutral_tags
from .text import rollback_invalid


def final_adoption(original, text, edits, before, after, ctx):
    model_changed = {edit["pid"] for edit in edits if edit.get("source") != "deterministic"}
    known = {(hit["pid"], hit["type"], hit["text"]) for hit in before}
    bad = {
        hit["pid"]
        for hit in after
        if hit["pid"] in model_changed and (hit["pid"], hit["type"], hit["text"]) not in known
    }
    text, edits, rolled = rollback_invalid(original, text, edits, ctx, bad)
    remaining = [hit for hit in after if hit["pid"] not in rolled]
    return text, edits, carry_hits(before, remaining, text, original), rolled


def entities(ctx, inventory):
    cards = ctx.get("characters", [])
    cards = list(cards.values()) if isinstance(cards, dict) else cards
    return (
        [card.get("name") for card in cards if isinstance(card, dict) and card.get("name")]
        + [
            entity
            for paragraph in inventory.get("paragraphs", [])
            for entity in paragraph.get("entities", [])
            if isinstance(entity, str)
        ]
        + [entity for entity in inventory.get("entities", []) if isinstance(entity, str)]
    )


def factcheck(session, stage, items, ctx):
    from .flow import PROMPTS

    items = [dict(item, before=neutral_tags(item["before"]), after=neutral_tags(item["after"])) for item in items]
    raw = session.ask(
        stage,
        PROMPTS["runtime"]["facts_preservation"],
        dict(
            items=items,
            characters=ctx.get("characters", []),
            must_keep=ctx.get("facts", []),
            ending=ctx.get("ending_image"),
        ),
        6000,
    )
    return {
        row["id"]: row
        for row in raw.get("checks", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str) and type(row.get("ok")) is bool
    }


def deterministic_choices(hit, rules):
    choices = []
    for item in rules.get("deterministic_replacements", []):
        if item.get("type") not in hit["types"]:
            continue
        changed = re.sub(item["regex"], item["replacement"], hit["text"])
        if changed != hit["text"]:
            choices.append(changed)
    return choices
