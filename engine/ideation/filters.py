"""Deterministic validity, novelty and diversity; model scores never delete cards."""

import json
from pathlib import Path
import re
import yaml

DATA = Path(__file__).resolve().parents[1] / "data/ideation"
FIELDS = ("title", "hook", "tags", "premise", "golden_finger_rule", "opening", "engine")


def load(name):
    return yaml.safe_load((DATA / (name + ".yaml")).read_text("utf-8")) or {}


def grams(text):
    text = "".join(re.findall(r"[\u4e00-\u9fff]", str(text)))
    return {text[i : i + 2] for i in range(len(text) - 1)}


def similarity(a, b):
    a, b = grams(a), grams(b)
    return len(a & b) / len(a | b) if a and b else 0


def validate(card, chapters):
    if not isinstance(card, dict):
        return ["字段"]
    errors = []
    for key in FIELDS:
        value = card.get(key)
        if key == "tags":
            if (
                not isinstance(value, list)
                or not 3 <= len(value) <= 5
                or any(not isinstance(v, str) or not v.strip() or len(v) > 16 for v in value)
            ):
                errors.append("卖点")
        elif not isinstance(value, str) or (not value.strip() and (key != "engine" or chapters >= 30)):
            errors.append("字段:" + key)
    for key, cap in dict(title=40, hook=30, premise=120, opening=100, golden_finger_rule=120, engine=240).items():
        if len(str(card.get(key, ""))) > cap:
            errors.append("字数:" + key)
    text = json.dumps({k: card.get(k) for k in FIELDS}, ensure_ascii=False)
    names = load("names")
    if re.search(
        r"[A-Za-z\u00c0-\u024f\uff21-\uff3a\uff41-\uff5a\ud800-\udfff\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]|[锟烫]{2,}",
        "".join(str(card.get(k, "")) for k in FIELDS),
    ):
        errors.append("语言")
    if any(c in text for c in names.get("traditional", "")):
        errors.append("繁体")
    if any(n in text for n in names.get("blacklist", [])):
        errors.append("名字")
    return errors


def shape(title):
    if re.search("[，,:：]", title):
        return "两段式"
    if re.search("[？?！!]", title):
        return "反问感叹"
    return "短名"


def screen(raw, chapters, genre, history, market):
    accepted, rejected = [], []
    combos = load("cliche")
    combos = combos.get(genre, combos.get("default", []))
    for item in raw:
        errors = validate(item, chapters)
        if not errors:
            for old in history + accepted:
                if any(similarity(item[k], old.get(k, "")) >= 0.5 for k in ("title", "hook")):
                    errors.append("历史或批内重复")
                    break
            collision = next((b["title"] for b in market if similarity(item["title"], b["title"]) >= 0.5), None)
            if collision:
                errors.append("撞车:" + collision)
        if errors:
            rejected.append(dict(title=item.get("title", "") if isinstance(item, dict) else "", reasons=errors))
            continue
        item = dict(item)
        text = "".join(str(item.get(k, "")) for k in FIELDS)
        item["cliche"] = [pair for pair in combos if len(pair) >= 2 and all(token in text for token in pair)]
        accepted.append(item)
    return accepted, rejected


def rotate(cards, limit=12, ordered=False, shape_counts=None, shape_cap=None):
    remaining = list(cards)
    chosen = []
    shells = {}
    shapes = dict(shape_counts or {})
    cap = max(1, int(limit * 0.6)) if shape_cap is None else shape_cap
    while remaining and len(chosen) < limit:
        eligible = [c for c in remaining if shapes.get(shape(c["title"]), 0) < cap]
        if not eligible:
            break
        card = min(
            eligible,
            key=lambda c: (
                bool(c.get("cliche")),
                0 if ordered else shells.get(c.get("axes", {}).get("shell", ""), 0),
                0 if ordered else shapes.get(shape(c["title"]), 0),
            ),
        )
        remaining.remove(card)
        chosen.append(card)
        shell = card.get("axes", {}).get("shell", "")
        shells[shell] = shells.get(shell, 0) + 1
        form = shape(card["title"])
        shapes[form] = shapes.get(form, 0) + 1
    return chosen
