"""Recurring people the writer only ever calls by a role (药童, 老矿工) get a name once.

The planner may only use names from the character cards, so a side character the writer invents
keeps a bare role label for the whole book, and one label can end up meaning several people.
Each chapter's extracted ``present`` list shows who was on the page. A label that is no card name,
looks like no personal name and recurs in several chapters is handed to the planner, which either
says which card it is or gives it a name written as ``名字（称呼）``. The chosen name is kept in
``side_cast.json`` and the writer gets the label-to-name list. Nothing here rewrites saved prose.
"""

import json
import re
from pathlib import Path

from .identity import RULES
from .q1_schema import integer, rows
from .storage import save

SIDE_CAST = "side_cast.json"
MIN_CHAPTERS = 3
LIST_LIMIT = 6
_COUNTED = re.compile(r"[0-9一二两三四五六七八九十几数众群们批些各诸]")
_SURNAMES = set("".join(RULES["surname"]))
_NAMED = re.compile(r"^\s*([一-鿿·]{2,5})\s*[（(]\s*([一-鿿]{1,6})\s*[)）]\s*$")
PLANNER_HEADING = (
    "反复出场却没有名字的人：下面每个称呼已在多章出场，读者分不清是不是同一个人。"
    "本批章纲若再安排他出场，scene.characters里要么写卡上的原名（他就是那位），"
    "要么给他起一个合本书世界的名字，写成“名字（称呼）”；起名后全书固定用这个名字。这是固定人物卡之外唯一允许新增的名字"
)
WRITER_HEADING = "配角名字：下面这些人已经有名字，正文里用名字称呼，身份最多由旁人带一句，不再只用称呼指代"


def _known(bible):
    labels = set()
    for card in [*rows(bible.get("characters")), *rows(bible.get("antagonists"))]:
        for value in [card.get("name"), card.get("address"), *(card.get("aliases") or [])]:
            if isinstance(value, str) and value:
                labels.add(value)
    return labels


def load_side_cast(out):
    path = Path(out) / SIDE_CAST if out else None
    if not path or not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    names = value.get("names", {}) if isinstance(value, dict) else {}
    return {label: name for label, name in names.items() if isinstance(label, str) and isinstance(name, str)}


def _role_label(label, known, named):
    if not isinstance(label, str) or not 2 <= len(label) <= 6 or label in known or label in named.values():
        return False
    if _COUNTED.search(label) or label[0] in _SURNAMES or label.startswith("阿"):
        return False
    return not any(name in label or label in name for name in known)


def recurring_unnamed(out, bible, before):
    """Role labels seen in at least MIN_CHAPTERS saved chapters before ``before``, most frequent first."""
    if not out:
        return []
    known, named = _known(bible), load_side_cast(out)
    seen = {}
    for path in sorted((Path(out) / "chapters").glob("*.json")):
        number = integer(path.stem)
        if not 0 < number < before:
            continue
        present = json.loads(path.read_text(encoding="utf-8")).get("present") or []
        for label in {p for p in present if _role_label(p, known, named)}:
            seen.setdefault(label, []).append(number)
    found = [dict(label=k, chapters=len(v), last=max(v)) for k, v in seen.items() if len(v) >= MIN_CHAPTERS]
    found = [row for row in found if row["label"] not in named]
    return sorted(found, key=lambda row: (-row["chapters"], row["label"]))[:LIST_LIMIT]


def planner_section(out, bible, start):
    found = recurring_unnamed(out, bible, start)
    return [(PLANNER_HEADING, [dict(称呼=row["label"], 出场章数=row["chapters"]) for row in found])] if found else []


def record_side_names(out, outline, bible):
    """Keep names the planner gave to role labels and return the outline with plain names in its scenes."""
    known, named = _known(bible), load_side_cast(out) if out else {}
    added = {}
    scenes = []
    for scene in rows(outline.get("scenes")):
        people = scene.get("characters")
        if not isinstance(people, list):
            scenes.append(scene)
            continue
        plain = []
        for person in people:
            match = _NAMED.match(person) if isinstance(person, str) else None
            if match and match[1] not in known and _role_label(match[2], known, {}):
                name, label = match[1], match[2]
                if label not in named and label not in added:
                    added[label] = name
                plain.append(named.get(label) or added[label])
            else:
                plain.append(person)
        scenes.append(dict(scene, characters=plain))
    if added and out:
        save(
            Path(out) / SIDE_CAST,
            dict(policy="章纲给反复出场的称呼起的名字；起名后固定，不改写已存正文", names={**named, **added}),
        )
    return dict(outline, scenes=scenes) if scenes else outline


def writer_section(named):
    named = named if isinstance(named, dict) else {}
    lines = "；".join(f"{label}={name}" for label, name in list(named.items())[:LIST_LIMIT])
    return [(WRITER_HEADING, lines)] if lines else []
