"""Read completed body evidence and locate future planning fields affected by deaths."""

from pathlib import Path
import re

from .storage import load


REVIVAL = re.compile(r"复活|重生|假死|装死|诈死|未死|没死|尚未死|并未死|死而复生|借尸还魂|替身|冒充|顶替")
PASSIVE_AFTER = re.compile(
    r"^(?:的)?(?:生前|死前|死后|已死|之死|死讯|遗物|遗体|尸体|尸首|尸身|留下的|所留|残留|余党|旧阵)"
)
# A clause about what the dead person left behind or did before dying, e.g. “裴照夜遗留的缺页”,
# “裴照夜夺册时留下的刻痕” (3f3950ba chapter 31, rejected twice although nothing had him act).
PAST_CLAUSE = re.compile(
    r"^[^，,：:]{0,8}?(?:留下的|遗留的?|遗弃的?|遗下的?|所留|生前|死前|死亡|昔日|当年|曾经?|早先|旧日)"
)
PRESENT_AFTER_DEATH = re.compile(
    r"^(?:的)?(?:死后|遗体|尸体|尸首|尸身)"
    r"(?:[，, ]|仍然|依然|竟然|正在|仍|还|又|却|就|便)*"
    r"(?:下令|说|问|答|走|出手|行动|发令|开口|站|挥|拿|抬|率|带|主持|命令|冲|笑|喊)"
)
READ_ONLY = {
    "callback_events",
    "life_constraints",
    "life_planning",
    "observed_history",
    "id",
    "quote",
    "source",
    "status",
    "fact_source",
    "evidence",
}


def completed_record(out, number):
    if out is None:
        return None
    path = Path(out) / f"chapters/{number:03d}.json"
    if not path.exists():
        return None
    row = load(path)
    return row if row.get("fact_source") == "final_body" and row.get("status") == "complete" else None


def confirmed_deaths(out, state=None, *, chapter):
    """Earliest death with a quote in its completed final body; never trust run progress."""
    if out is None:
        return {}
    out = Path(out)
    records = {}
    for path in sorted((out / "chapters").glob("[0-9]*.json")):
        if not path.stem.isdigit() or int(path.stem) >= chapter:
            continue
        number = int(path.stem)
        row = completed_record(out, number)
        body = out / f"chapter-{number:03d}.body.md"
        if row and body.exists():
            records[number] = (row, body.read_text(encoding="utf-8"))
    result = {}

    def add(name, fact, default):
        if not isinstance(name, str) or not name or not isinstance(fact, dict):
            return
        number, quote = fact.get("chapter", default), fact.get("quote")
        if type(number) is not int or number not in records or not isinstance(quote, str) or not quote.strip():
            return
        quote = quote.strip()
        if quote not in records[number][1]:
            return
        if name not in result or number < result[name]["chapter"]:
            result[name] = {"chapter": number, "quote": quote, "status": "Evidence"}

    snapshots = [(number, row.get("state", {})) for number, (row, _) in records.items()]
    snapshots.append((chapter, state or {}))
    for number, snapshot in snapshots:
        people = snapshot.get("continuity_ledger", {}).get("characters", {})
        for name, person in people.items():
            if not isinstance(person, dict):
                continue
            life, location = person.get("life_status", {}), person.get("location", {})
            if isinstance(life, dict) and life.get("value") in ("dead", "死亡"):
                add(name, life, number)
            if isinstance(location, dict) and location.get("travel_status") == "死亡":
                add(name, location, number)
    return dict(sorted(result.items()))


def leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in READ_ONLY and not key.endswith(("_id", "_ids", "_quote")):
                yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))
    elif isinstance(value, str):
        if not (path and (path[-1] in {"thread", "threads"} or len(path) > 1 and path[-2] == "threads")):
            yield path, value


def validate_life_constraints(value, deaths):
    """Conservative future-plan check; bare cast names also require a directed revision.

    Historical callbacks are read only. Passive consequences and explicit recollection
    remain usable, but a revival explanation cannot make an acting dead person valid.
    """
    found = []
    for path, text in leaves(value):
        for name in deaths:
            for sentence in re.split(r"[。！？；\n]", text):
                if name not in sentence:
                    continue
                invalid = bool(REVIVAL.search(sentence))
                for match in re.finditer(re.escape(name), sentence):
                    before = re.split(r"[，,：:]", sentence[: match.start()])[-1]
                    after = sentence[match.end() :]
                    passive = (
                        PASSIVE_AFTER.search(after)
                        or PAST_CLAUSE.search(after)
                        or re.search(r"回忆|想起|追忆|谈及", before)
                    )
                    if not passive or PRESENT_AFTER_DEATH.search(after):
                        invalid = True
                if invalid:
                    found.append({"path": list(path), "name": name, "text": text, "kind": "dead_in_future_plan"})
                    break
    return found
