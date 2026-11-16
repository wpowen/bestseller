"""Per-chapter story obligations derived from the story design, and the ledger of what was written.

A duty is closed only by what the chapter text contains. An anchor word found in the text
closes it as ``done`` with the quoted sentence; a duty without an anchor can only be closed
as ``unverified``. Nothing here blocks or rewrites a chapter: an open duty is carried forward.
"""

from pathlib import Path
import json
import re
from .chapter_rhythm import chapter_type
from .q1_schema import integer, rows
from .storage import save
from .story_design import SPEC, stage_list

LEDGER = "story_ledger.json"
CLOSED = ("done", "unverified")
MUST_CLOSE = ("payoff", "truth", "flaw_cost", "fear_faced", "growth_end")
VILLAIN_TYPES = ("对抗", "谋划", "揭秘")
SOUL_LIMIT = 600
PLANNER_LIMIT = 900
SENTENCE = re.compile(r"[^。！？\n]*[。！？]?")


def _fill(kind, **fields):
    text = SPEC["duty_text"][kind]
    for key, value in fields.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _duty(ident, kind, chapter, deadline, text, anchors=(), names=(), types=()):
    return dict(
        id=ident,
        kind=kind,
        chapter=chapter,
        deadline=max(chapter, deadline),
        text=text,
        anchors=[a for a in anchors if a],
        names=[n for n in names if n],
        types=list(types),
    )


def _villain_duties(bible, stages):
    duties = []
    opponents = {row.get("name"): row for row in rows(bible.get("antagonists"))}
    for stage in stages:
        source = next((v for v in rows(bible.get("volumes")) if integer(v.get("number")) == stage["number"]), {})
        name = source.get("antagonist")
        if not isinstance(name, str) or not name:
            continue
        leave = integer(opponents.get(name, {}).get("exit"), stage["end"])
        length = stage["end"] - stage["start"] + 1
        moves = SPEC["villain_moves_per_stage"]
        for index in range(moves):
            chapter = stage["start"] + 1 + index * max(1, length // (moves + 1))
            if chapter >= leave:
                continue
            duties.append(
                _duty(
                    f"villain-{stage['number']}-{index + 1}",
                    "villain_move",
                    chapter,
                    min(chapter + 3, leave - 1),
                    _fill("villain_move", name=name),
                    names=[name],
                    types=VILLAIN_TYPES,
                )
            )
    return duties


def all_duties(design, bible):
    """Every scheduled obligation of the book, in no particular order."""
    if not design:
        return []
    total = integer(design.get("total"))
    stages = stage_list(bible, total)
    hero = next((c.get("name") for c in rows(bible.get("characters")) if c.get("role") == "protagonist"), "")
    cards = {c.get("name"): c for c in rows(bible.get("characters"))}
    duties = []
    for row in rows(design.get("foreshadows")):
        label = row.get("anchor") or row.get("description") or row["id"]
        anchors = [row.get("anchor"), *row.get("aliases", [])]
        fields = dict(
            anchor=label,
            surface=row.get("surface") or "一件不起眼的东西",
            truth=row.get("truth") or row.get("description"),
        )
        duties.append(
            _duty(f"{row['id']}-plant", "plant", row["plant"], row["plant"] + 2, _fill("plant", **fields), anchors)
        )
        for index, chapter in enumerate(row.get("reminders", []), 1):
            duties.append(
                _duty(f"{row['id']}-remind{index}", "remind", chapter, chapter + 2, _fill("remind", **fields), anchors)
            )
        duties.append(
            _duty(f"{row['id']}-payoff", "payoff", row["payoff"], row["payoff"], _fill("payoff", **fields), anchors)
        )
    for row in rows(design.get("truth_reveals")):
        fields = dict(
            layer=row["layer"],
            anchor=row.get("anchor") or "关键实物",
            overturns=row.get("overturns") or row.get("truth"),
        )
        duties.append(
            _duty(
                f"truth-{row['layer']}",
                "truth",
                row["chapter"],
                row["chapter"],
                _fill("truth", **fields),
                [row.get("anchor")],
            )
        )
    for row in rows(design.get("arc")):
        if row.get("event"):
            text = _fill(row["kind"], event=row["event"])
            duties.append(
                _duty(f"arc-{row['kind']}", row["kind"], row["chapter"], row["chapter"] + 1, text, names=[hero])
            )
    for row in rows(design.get("relationships")):
        people = row["people"]
        for step in rows(row.get("steps")):
            stage = next((s for s in stages if s["number"] == step["stage"]), None)
            if stage:
                text = _fill(
                    "relationship", people="与".join(people), **{k: step[k] for k in ("event", "before", "after")}
                )
                ident = f"rel-{people[0]}-{people[1]}-s{stage['number']}"
                duties.append(
                    _duty(ident, "relationship", stage["start"], stage["end"], text, names=people, types=["情感"])
                )
    for row in rows(design.get("appearances")):
        name = row["name"]
        desire = str(cards.get(name, {}).get("desire", ""))[:40]
        text = _fill("appearance", name=name, desire=desire)
        duties.append(
            _duty(f"app-{name}", "appearance", row["first_chapter"], row["first_chapter"] + 2, text, [name], [name])
        )
    return duties + _villain_duties(bible, stages)


def load_ledger(out):
    path = Path(out) / LEDGER if out else None
    if not path or not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("duties", {}) if isinstance(value, dict) else {}


def _open(duty, entry, number):
    """A duty closed by the chapter being (re)written is open again for that chapter."""
    return not entry or entry.get("status") not in CLOSED or integer(entry.get("chapter")) >= number


def _expired(duty, number, total):
    limit = total if duty["kind"] in MUST_CLOSE else duty["deadline"] + SPEC["carry_chapters"]
    return number > limit


def due_duties(design, bible, number, kind, ledger):
    """The few obligations handed to the writer of one chapter."""
    total = integer(design.get("total")) if design else 0
    order = {name: index for index, name in enumerate(SPEC["priority"])}
    waiting = []
    for duty in all_duties(design, bible):
        if duty["chapter"] > number or not _open(duty, ledger.get(duty["id"]), number) or _expired(duty, number, total):
            continue
        if duty["types"] and kind not in duty["types"] and number < duty["deadline"]:
            continue
        waiting.append(duty)
    # A relationship step is the point of an emotional chapter, so it goes first there.
    waiting.sort(
        key=lambda d: (
            not (d["kind"] == "relationship" and kind == "情感"),
            order.get(_rank(d), len(order)),
            d["chapter"],
        )
    )
    return waiting[: SPEC["max_duties_per_chapter"]]


def _rank(duty):
    return "arc" if duty["kind"] in ("flaw_cost", "fear_faced", "growth_end") else duty["kind"]


def with_story_duties(outline, bible, out):
    """Attach this chapter's obligations to its writing outline."""
    design = bible.get("story_design") if isinstance(bible.get("story_design"), dict) else {}
    if not design:
        return outline
    kind = chapter_type(outline) or outline.get("rhythm_type") or ""
    duties = due_duties(design, bible, integer(outline.get("number")), kind, load_ledger(out))
    return dict(outline, story_duties=duties)


def _present_names(outline):
    names = []
    for scene in rows(outline.get("scenes")):
        people = scene.get("characters")
        if isinstance(people, list):
            names.extend(n for n in people if isinstance(n, str))
    return list(dict.fromkeys(names))


def writer_sections(outline, bible):
    """Short prompt sections: who these people are inside, and what this chapter owes the story."""
    design = bible.get("story_design") if isinstance(bible.get("story_design"), dict) else {}
    if not design:
        return []
    hero = next((c.get("name") for c in rows(bible.get("characters")) if c.get("role") == "protagonist"), "")
    wanted = list(dict.fromkeys([hero, *_present_names(outline)]))
    souls = {row.get("name"): row for row in rows(design.get("characters"))}
    cards = []
    for name in wanted:
        row = {k: v for k, v in souls.get(name, {}).items() if v}
        if len(row) > 1 and len(json.dumps(cards + [row], ensure_ascii=False)) <= SOUL_LIMIT:
            cards.append(row)
    sections = []
    if cards:
        sections.append((SPEC["writer"]["soul_heading"], json.dumps(cards, ensure_ascii=False, separators=(",", ":"))))
    duties = outline.get("story_duties") or []
    if duties:
        lines = "\n".join(f"{index}. {duty['text']}" for index, duty in enumerate(duties, 1))
        sections.append((SPEC["writer"]["duty_heading"], lines))
    return sections


def is_payoff_chapter(outline):
    promise = outline.get("promise_payoff") if isinstance(outline.get("promise_payoff"), dict) else {}
    resolution = outline.get("opponent_resolution") if isinstance(outline.get("opponent_resolution"), dict) else {}
    return bool(promise.get("action") or resolution.get("name"))


def writer_instruction(outline, bible):
    if not isinstance(bible.get("story_design"), dict) or not bible["story_design"]:
        return ""
    rules = SPEC["writer"]
    text = rules["general"]
    if integer(outline.get("number")) <= 3:
        text += rules["opening"]
    if is_payoff_chapter(outline):
        text += rules["payoff"]
    if outline.get("ending"):
        text += rules["ending"]
    return text


def planner_sections(bible, out, start, end, stage_number):
    """What the outline planner must place in this window."""
    design = bible.get("story_design") if isinstance(bible.get("story_design"), dict) else {}
    if not design:
        return []
    ledger = load_ledger(out)
    total = integer(design.get("total"))
    items = []
    for duty in sorted(all_duties(design, bible), key=lambda d: d["chapter"]):
        if duty["chapter"] > end or not _open(duty, ledger.get(duty["id"]), start) or _expired(duty, start, total):
            continue
        if duty["kind"] == "relationship":
            continue
        item = dict(chapter=max(duty["chapter"], start), duty=duty["text"].split("。")[0])
        items.append(dict(item, overdue=True) if duty["chapter"] < start else item)
    packed = []
    for item in items:
        if len(json.dumps(packed + [item], ensure_ascii=False)) > PLANNER_LIMIT:
            break
        packed.append(item)
    sections = [(SPEC["planner"]["duty_heading"], packed)] if packed else []
    progress = next((row for row in rows(design.get("stage_progress")) if row.get("stage") == stage_number), {})
    if progress.get("goal_line") or progress.get("marker"):
        sections.append((SPEC["planner"]["stage_heading"], progress))
    steps = [
        dict(people=row["people"], **step)
        for row in rows(design.get("relationships"))
        for step in rows(row.get("steps"))
        if step.get("stage") == stage_number
    ]
    if steps:
        sections.append((SPEC["planner"]["relationship_heading"], steps))
    return sections


def _quote(text, word):
    for match in SENTENCE.finditer(text):
        if word in match.group(0):
            return match.group(0).strip()[:120]
    return ""


def record_story_duties(out, number, text, outline):
    """Close what the finished chapter actually contains; leave the rest open for later chapters."""
    duties = outline.get("story_duties") or []
    if not duties or not out:
        return []
    path = Path(out) / LEDGER
    ledger = load_ledger(out)
    results = []
    for duty in duties:
        entry = dict(ledger.get(duty["id"], {}), kind=duty["kind"], scheduled=duty["chapter"], text=duty["text"])
        tried = sorted({*entry.get("attempts", []), number})
        anchor = next((word for word in duty["anchors"] if word in text), "")
        if anchor:
            entry.update(status="done", chapter=number, quote=_quote(text, anchor))
        elif not duty["anchors"] and duty["names"] and all(name in text for name in duty["names"]):
            entry.update(status="unverified", chapter=number, quote="")
        else:
            entry.update(status="missing", chapter=None, quote="")
        entry["attempts"] = tried
        ledger[duty["id"]] = entry
        results.append(dict(id=duty["id"], kind=duty["kind"], status=entry["status"]))
    save(
        path,
        dict(
            policy="done=锚点词出现在正文并附原句；unverified=相关人物在场但无法用锚点确认；missing=未写出，顺延。仅供参考，不代替读者验收",
            duties=ledger,
        ),
    )
    return results
