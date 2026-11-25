"""Per-chapter story obligations derived from the story design, and the ledger of what was written.

Foreshadows and private choices retain grounded evidence from the adopted text.
"""

from pathlib import Path
import json
import re

from tools.chapter_files import chapter_files, chapter_number

from .arc_runs import planner_arc_section, planner_obstacle_section
from .chapter_rhythm import chapter_type
from .foreshadow_verification import VERIFIED_KINDS, plant_observation, verify_foreshadows
from .q1_schema import integer, rows
from .side_cast import load_side_cast, planner_section, record_side_names, writer_section
from .storage import save
from .story_design import SPEC, stage_list
from .support_stakes import KINDS, support_entry

LEDGER = "story_ledger.json"
CLOSED = ("done", "unverified")
MUST_CLOSE = ("payoff", "truth", "flaw_cost", "fear_faced", "growth_end")
VILLAIN_TYPES = ("对抗", "谋划", "揭秘")
SOUL_LIMIT = 600
PLANNER_LIMIT = 900
LEDGER_POLICY = (
    "伏笔及真相done=正文明示或逐字核对确认，锚点提及保留为unverified且可续发；early=提前写出；"
    "配角私愿done=本章有核实的行动与后果；unverified=已调度但兑现证据不足；"
    "missing=未写出，顺延。仅供参考，不代替读者验收"
)
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
            dict(
                _duty(
                    f"{row['id']}-payoff", "payoff", row["payoff"], row["payoff"], _fill("payoff", **fields), anchors
                ),
                proof_event=fields["truth"],
            )
        )
    for row in rows(design.get("truth_reveals")):
        fields = dict(
            layer=row["layer"],
            anchor=row.get("anchor") or "关键实物",
            overturns=row.get("overturns") or row.get("truth"),
        )
        duties.append(
            dict(
                _duty(
                    f"truth-{row['layer']}",
                    "truth",
                    row["chapter"],
                    row["chapter"],
                    _fill("truth", **fields),
                    [row.get("anchor")],
                ),
                proof_event=row.get("truth") or row.get("overturns"),
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
    for stake in rows(design.get("support_stakes")):
        name = stake["name"]
        for phase in ("choice", "consequence"):
            number = stake[phase + "_chapter"]
            stage = next((s for s in stages if s["start"] <= number <= s["end"]), None)
            if not stage:
                continue
            deadline = min(stage["end"], stake["consequence_chapter"] - 1) if phase == "choice" else stage["end"]
            kind = "support_" + phase
            text = f"{name}一直想{stake['desire']}。{stake[phase]}"
            duty = _duty(f"support-{name}-{phase}", kind, number, deadline, text, names=[name])
            duties.append(dict(duty, desire=stake["desire"], target=stake.get("target", "")))
    return duties + _villain_duties(bible, stages)


def load_ledger(out):
    path = Path(out) / LEDGER if out else None
    if not path or not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("duties", {}) if isinstance(value, dict) else {}


def _open(duty, entry, number):
    """A duty closed by the chapter being (re)written is open again for that chapter."""
    closed = ("done",) if duty["kind"] in VERIFIED_KINDS else CLOSED
    return not entry or entry.get("status") not in closed or integer(entry.get("chapter")) >= number


def _expired(duty, number, total):
    if duty["kind"] in KINDS:
        return number > duty["deadline"]
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
        if duty["kind"] == "support_consequence":
            prior = ledger.get(f"support-{duty['names'][0]}-choice") or {}
            if 0 < integer(prior.get("chapter")) < number and prior.get("goal_state") not in (None, "unknown"):
                label = {
                    "stated": "本人说明愿望",
                    "promised": "仅有承诺",
                    "progressed": "已有部分进展",
                    "chose": "已作选择",
                    "achieved": "愿望已经达成",
                    "lost": "机会已经失去",
                    "deferred_with_cost": "已因代价延期",
                }.get(prior["goal_state"])
                if label:
                    duty = dict(duty, text=f"{duty['text']}（第{prior['chapter']}章{label}；承接其后果。）")
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


def _saved_bodies(out, before):
    for path in chapter_files(out, "chapter-*.body.md"):
        number = chapter_number(path, "chapter-*.body.md")
        if number >= before:
            break
        yield number, path.read_text(encoding="utf-8")


def settle_early_plants(out, design, bible, number):
    """A plant already written before its scheduled chapter is closed with that chapter and quote.

    Without this the writer is asked to plant the same thing again and the ledger reports it missing.
    """
    if not out:
        return []
    ledger = load_ledger(out)
    waiting = [
        duty
        for duty in all_duties(design, bible)
        if duty["kind"] == "plant" and duty["chapter"] <= number and _open(duty, ledger.get(duty["id"]), number)
    ]
    settled = []
    for duty in waiting:
        for chapter, text in _saved_bodies(out, number):
            proof = plant_observation(duty, chapter, text)
            if proof["status"] == "done":
                entry = dict(
                    ledger.get(duty["id"], {}), kind=duty["kind"], scheduled=duty["chapter"], text=duty["text"]
                )
                entry.update(proof, early=True)
                ledger[duty["id"]] = entry
                settled.append(duty["id"])
                break
    if settled:
        save(Path(out) / LEDGER, dict(policy=LEDGER_POLICY, duties=ledger))
    return settled


def with_story_duties(outline, bible, out):
    """Attach this chapter's obligations to its writing outline."""
    design = bible.get("story_design") if isinstance(bible.get("story_design"), dict) else {}
    if not design:
        return outline
    settle_early_plants(out, design, bible, integer(outline.get("number")))
    outline = record_side_names(out, outline, bible)
    kind = chapter_type(outline) or outline.get("rhythm_type") or ""
    duties = due_duties(design, bible, integer(outline.get("number")), kind, load_ledger(out))
    return dict(outline, story_duties=duties, side_names=load_side_cast(out) if out else {})


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
    return sections + writer_section(outline.get("side_names"))


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
    duties = all_duties(design, bible)
    villain_chapters = {duty["chapter"] for duty in duties if duty["kind"] == "villain_move"}
    items = []
    # Reserve room for selected private stakes before a backlog fills the outline window.
    for duty in sorted(duties, key=lambda d: (d["kind"] not in KINDS, d["chapter"])):
        if duty["chapter"] > end or not _open(duty, ledger.get(duty["id"]), start) or _expired(duty, start, total):
            continue
        if duty["kind"] == "relationship":
            continue
        item = dict(
            chapter=max(duty["chapter"], start),
            duty=duty["text"] if duty["kind"] in KINDS else duty["text"].split("。")[0],
        )
        items.append(dict(item, overdue=True) if duty["chapter"] < start else item)
    packed = []
    for item in items:
        if len(json.dumps(packed + [item], ensure_ascii=False)) > PLANNER_LIMIT:
            break
        packed.append(item)
    packed.sort(key=lambda item: item["chapter"])
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
    sections.append((SPEC["planner"]["device_heading"], SPEC["planner"]["device_rule"]))
    return (
        sections
        + planner_section(out, bible, start)
        + planner_arc_section(out, start)
        + planner_obstacle_section(bible, out, start, end, villain_chapters)
    )


def _quote(text, word):
    for match in SENTENCE.finditer(text):
        if word in match.group(0):
            return match.group(0).strip()[:120]
    return ""


def record_story_duties(out, number, text, outline, *, payoff_review=None, client=None, prefix="", ask=None):
    """Close what the finished chapter actually contains; leave the rest open for later chapters."""
    duties = outline.get("story_duties") or []
    if not duties or not out:
        return []
    path = Path(out) / LEDGER
    ledger = load_ledger(out)
    verified = verify_foreshadows(client, prefix, ask, number, text, duties)
    results = []
    for duty in duties:
        entry = dict(ledger.get(duty["id"], {}), kind=duty["kind"], scheduled=duty["chapter"], text=duty["text"])
        tried = sorted({*entry.get("attempts", []), number})
        anchor = next((word for word in duty["anchors"] if word in text), "")
        if duty["kind"] in KINDS:
            entry.update(support_entry(duty, number, text, payoff_review or {}))
        elif duty["id"] in verified:
            proof = verified[duty["id"]]
            if proof["status"] != "missing" or entry.get("status") != "unverified":
                entry.update(proof)
        elif anchor:
            entry.update(status="done", chapter=number, quote=_quote(text, anchor))
        elif not duty["anchors"] and duty["names"] and all(name in text for name in duty["names"]):
            entry.update(status="unverified", chapter=number, quote="")
        else:
            entry.update(status="missing", chapter=None, quote="")
        entry["attempts"] = tried
        ledger[duty["id"]] = entry
        results.append(dict(id=duty["id"], kind=duty["kind"], status=entry["status"]))
    save(path, dict(policy=LEDGER_POLICY, duties=ledger))
    return results
