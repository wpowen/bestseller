"""Small deterministic location vocabulary and explicit containment relationships."""

import re


MOVEMENT_WORDS = (
    "离开",
    "走出",
    "走进",
    "抵达",
    "赶到",
    "来到",
    "到达",
    "穿过",
    "绕过",
    "翻过",
    "潜入",
    "追",
    "赶",
    "转入",
    "钻进",
    "回到",
    "折返",
    "跟着",
    "拐进",
    "爬出",
    "逃出",
    "跑出",
    "冲出",
)
PLACE_NOUNS = ("通道", "钟铺", "器室", "剑阁", "剑廊", "客栈", "驿站", "码头", "渡口", "矿道", "洞窟", "城楼", "巷")


def _strings(value):
    return (
        [value]
        if isinstance(value, str) and value.strip()
        else [s for s in value if isinstance(s, str) and s.strip()]
        if isinstance(value, list)
        else []
    )


def location_table(bible, known=()):
    """Only explicit parent/child/alias metadata or literal containment; no guessed geography."""
    names, links = set(_strings(list(known))), set()

    def read(value):
        rows = value if isinstance(value, list) else []
        for row in rows:
            if isinstance(row, str):
                names.add(row)
            elif isinstance(row, dict):
                name = row.get("name", row.get("location"))
                if not isinstance(name, str) or not name:
                    continue
                names.add(name)
                for child in _strings(row.get("children", row.get("sublocations", []))):
                    names.add(child)
                    links.add((name, child))
                for alias in _strings(row.get("aliases", [])):
                    names.add(alias)
                    links.update(((name, alias), (alias, name)))
                if isinstance(row.get("parent"), str) and row["parent"]:
                    names.add(row["parent"])
                    links.add((row["parent"], name))

    read(bible.get("locations", []))
    world = bible.get("world", {})
    read(world.get("locations", []) if isinstance(world, dict) else [])
    for row in bible.get("volumes", bible.get("stages", [])) or []:
        if isinstance(row, dict):
            names.update(re.split(r"[、，,与和]", str(row.get("location") or "")))
            read(row.get("locations", []))
    for parent in names:
        if len(parent) >= 2:
            links.update((parent, child) for child in names if parent != child and parent in child)
    return dict(names=sorted(n for n in names if n), links=sorted(links))


def same_place(old, new, bible=None, known=()):
    if not old or not new:
        return False
    if old == new:
        return True
    left, right = (re.sub(r"(?:里面|之中|中|内|里|旁|处|上)$", "", s) for s in (old, new))
    if left == right:
        return True
    for noun in PLACE_NOUNS:
        if left.endswith(noun) and right.endswith(noun):
            a, b = left[: -len(noun)], right[: -len(noun)]
            if not a.strip("狭窄宽长旧") or not b.strip("狭窄宽长旧"):
                return True
    table = location_table(bible or {}, [*known, old, new])

    # An explicit ancestor can stand for its sublocation, siblings cannot.
    def contains(parent, child):
        visited, pending = set(), [parent]
        while pending:
            current = pending.pop()
            if current == child:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(b for a, b in table["links"] if a == current)
        return False

    return contains(old, new) or contains(new, old)
