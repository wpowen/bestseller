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
    "挪去",
    "挪到",
    "退入",
    "退出来",
    "跨过",
    "钻过",
    "驶向",
)
PLACE_NOUNS = ("通道", "钟铺", "器室", "剑阁", "剑廊", "客栈", "驿站", "码头", "渡口", "矿道", "洞窟", "城楼", "巷")

_LOCAL = re.compile(r"(?:上方|下方|更深处|最深处|里面|之中|中|内|里|旁|处|上|下|外|后)$")
_DEICTIC = re.compile(r"(?:一|这|那)处")


def _local_name(value):
    name = _DEICTIC.sub("", value)
    root = _LOCAL.sub("", name)
    return name, root if len(root) >= 2 or root in PLACE_NOUNS else name


def mentioned_place(location, passage):
    """Match bounded spatial wording in a movement quote, retaining proper names."""
    name, root = _local_name(location)
    passage = _DEICTIC.sub("", passage)
    # Deeper/deepest describe the same local area; don't equate opposite sides.
    if name.endswith(("更深处", "最深处")):
        return bool(re.search(re.escape(root) + r"(?:更|最)深处", passage))
    return name in passage or (
        name != root and bool(re.search(re.escape(root) + r"(?!上方|下方|上|下|外|内|里|中|后|旁)", passage))
    )


def quoted_departure(previous, movement):
    """A concrete source in the grounded end quote can anchor rising to continue."""
    quote, old = previous.get("end_quote"), previous.get("end_location")
    if not quote or not old:
        return False
    narrative = re.sub(r'“[^”]*(?:”|$)|‘[^’]*(?:’|$)|「[^」]*(?:」|$)|"[^"]*(?:"|$)', "", quote)
    source = re.search(r"从([^。！？！，,；;\n]{2,20}?)(?:起身|站起|动身|出发)", movement)
    return bool(source) and any(
        source[1] in sentence
        and mentioned_place(old, sentence)
        and not re.search(r"想起|记得|回忆|打算|计划|如果|假如|没有|并未|未曾|不曾|没能", sentence)
        for sentence in re.split(r"[。！？!?\n]", narrative)
    )


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
    interiors = ("前堂", "后堂", "大堂", "正堂", "后院", "前院", "内院", "外院", "门口", "内室", "东厢", "西厢")
    for parent in names:
        if len(parent) >= 2:
            links.update(
                (parent, child) for child in names if child.startswith(parent) and child[len(parent) :] in interiors
            )
    return dict(names=sorted(n for n in names if n), links=sorted(links))


def same_place(old, new, bible=None, known=()):
    if not old or not new:
        return False
    if old == new:
        return True
    old_name, left = _local_name(old)
    new_name, right = _local_name(new)
    if old_name == new_name or old_name == right or new_name == left:
        return True
    if left == right:
        inside = ("里面", "之中", "中", "内", "里")
        return old_name[len(left) :] in inside and new_name[len(right) :] in inside
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
