"""Story design layer: scheduled foreshadows, truth reveals, character interior and relationships.

The bible already names foreshadows, truth layers and character arcs. This module turns
them into chapter-numbered obligations once, before chapter 1, and saves them next to the
bible. Chapter numbers are always clamped by code; text the model did not supply stays empty.
"""

from pathlib import Path
import yaml
from .q1_schema import DATA, integer, rows
from .storage import save

SPEC = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/story_design.yaml").read_text(encoding="utf-8"))
DATA["schemas"]["story_design"] = SPEC["schema"]
DATA["instructions"]["story_design"] = SPEC["instruction"]
FILE = "story_design.yaml"
VERSION = 1
TEXT_LIMIT = 80
ANCHOR_RANGE = (2, 6)
MODEL_ATTEMPTS = 2


def _text(value, limit=TEXT_LIMIT):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _clamp(value, low, high, default):
    """Keep a model-chosen chapter inside its allowed window; fall back to the default outside it."""
    if high < low:
        high = low
    number = integer(value)
    return number if low <= number <= high else min(max(default, low), high)


def _anchor(value):
    text = _text(value, 12)
    return text if ANCHOR_RANGE[0] <= len(text) <= ANCHOR_RANGE[1] else ""


def stage_list(bible, total):
    stages = [
        dict(number=integer(row.get("number"), index), start=integer(row.get("start")), end=integer(row.get("end")))
        for index, row in enumerate(rows(bible.get("volumes")), 1)
    ]
    stages = [row for row in stages if 1 <= row["start"] <= row["end"]]
    return stages or [dict(number=1, start=1, end=total)]


def _stage(stages, number):
    return next((row for row in stages if row["number"] == integer(number)), stages[-1])


def _by(items, key):
    return {row.get(key): row for row in rows(items) if row.get(key) not in (None, "")}


def _characters(raw, bible):
    supplied = _by(raw.get("characters"), "name")
    result = []
    for card in rows(bible.get("characters")):
        row = supplied.get(card.get("name"), {})
        habits = [_text(item, 24) for item in row.get("habits", []) if _text(item, 24)] if row else []
        entry = dict(name=card.get("name"), habits=list(dict.fromkeys(habits))[:3])
        entry.update({key: _text(row.get(key)) for key in ("wound", "cares_for", "contrast")})
        result.append(entry)
    return result


def _foreshadows(raw, bible, stages, total):
    supplied = _by(raw.get("foreshadows"), "id")
    sources = [row for row in rows(bible.get("main_foreshadows")) if row.get("id")]
    result = []
    used_anchors = set()
    for index, source in enumerate(sources):
        row = supplied.get(source["id"], {})
        close = _stage(stages, source.get("close_volume"))
        last_allowed = max(1, min(close["end"], total - 1))
        payoff = _clamp(row.get("payoff"), min(close["start"] + 1, last_allowed), last_allowed, last_allowed - 1)
        plant = _clamp(row.get("plant"), 1, max(1, payoff - 1), min(2 + index, max(1, payoff - 1)))
        between = list(range(plant + 1, payoff))
        wanted = [integer(x) for x in row.get("reminders", [])] if isinstance(row.get("reminders"), list) else []
        reminders = sorted({x for x in wanted if x in between})[:2]
        for share in (1, 2):
            if len(reminders) >= min(2, len(between)):
                break
            candidate = plant + (payoff - plant) * share // 3
            if candidate in between and candidate not in reminders:
                reminders = sorted([*reminders, candidate])
        anchor = _anchor(row.get("anchor"))
        if anchor in used_anchors:
            anchor = ""
        used_anchors.add(anchor)
        result.append(
            dict(
                id=source["id"],
                description=_text(source.get("description")),
                anchor=anchor,
                surface=_text(row.get("surface")),
                truth=_text(row.get("truth")),
                plant=plant,
                reminders=reminders,
                payoff=payoff,
            )
        )
    return result


def _truths(raw, bible, stages, total):
    layers = rows((bible.get("world") or {}).get("truth_layers")) if isinstance(bible.get("world"), dict) else []
    supplied = _by(raw.get("truth_reveals"), "layer")
    result = []
    previous = 1
    for index, layer in enumerate(layers, 1):
        number = integer(layer.get("layer"), index)
        row = supplied.get(number, supplied.get(str(number), {}))
        target_stage = stages[min(len(stages) - 1, max(0, (index * len(stages) + len(layers) - 1) // len(layers) - 1))]
        high = max(1, total - 1)
        low = min(max(integer(layer.get("reveal_not_before")), previous + 1, 2), high)
        chapter = _clamp(row.get("chapter"), low, high, max(low, target_stage["end"] - 1))
        previous = chapter
        result.append(
            dict(
                layer=number,
                truth=_text(layer.get("truth")),
                chapter=chapter,
                overturns=_text(row.get("overturns")),
                anchor=_anchor(row.get("anchor")),
            )
        )
    return result


def _arc(raw, stages, total):
    supplied = _by(raw.get("arc"), "kind")
    early = _stage(stages, min(2, len(stages)))
    last = stages[-1]
    windows = dict(
        flaw_cost=(min(3, total), early["end"], (early["start"] + early["end"]) // 2),
        fear_faced=(last["start"], max(last["start"], total - 1), max(last["start"], total - 2)),
        growth_end=(max(1, total - 2), total, total),
    )
    return [
        dict(
            kind=kind,
            chapter=_clamp(supplied.get(kind, {}).get("chapter"), low, high, default),
            event=_text(supplied.get(kind, {}).get("event")),
        )
        for kind, (low, high, default) in windows.items()
    ]


def _relationships(raw, bible, stages):
    names = {card.get("name") for card in rows(bible.get("characters"))}
    numbers = {row["number"] for row in stages}
    result = []
    for row in rows(raw.get("relationships")):
        people = row.get("people") if isinstance(row.get("people"), list) else []
        people = [name for name in people if name in names]
        if len(people) != 2 or people[0] == people[1]:
            continue
        steps = {}
        for step in rows(row.get("steps")):
            stage = integer(step.get("stage"))
            event, before, after = (_text(step.get(key)) for key in ("event", "before", "after"))
            if stage in numbers and stage not in steps and event and before and after and before != after:
                steps[stage] = dict(stage=stage, event=event, before=before, after=after)
        if steps:
            result.append(dict(people=people, steps=[steps[key] for key in sorted(steps)]))
    return result


def _appearances(raw, bible, stages):
    supplied = _by(raw.get("appearances"), "name")
    opponents = _by(bible.get("antagonists"), "name")
    first_stage = stages[0]
    result = []
    order = 0
    for card in rows(bible.get("characters")):
        name = card.get("name")
        if card.get("role") == "protagonist" or not name:
            continue
        wanted = supplied.get(name, {}).get("first_chapter")
        if card.get("role") == "antagonist":
            home = _stage(stages, opponents.get(name, {}).get("stage") or card.get("ladder_volume") or 1)
            first = _clamp(wanted, home["start"], min(home["start"] + 2, home["end"]), home["start"])
        else:
            limit = _stage(stages, card.get("exit_volume") or len(stages))
            order += 1
            first = _clamp(wanted, 1, limit["end"], min(order, first_stage["end"]))
        result.append(dict(name=name, first_chapter=first))
    return result


def _progress(raw, stages):
    supplied = _by(raw.get("stage_progress"), "stage")
    return [
        dict(
            stage=row["number"],
            **{key: _text(supplied.get(row["number"], {}).get(key)) for key in ("goal_line", "marker")},
        )
        for row in stages
    ]


def normalize(raw, bible, total):
    """Bind a model design to the fixed bible; code owns every chapter number."""
    raw = raw if isinstance(raw, dict) else {}
    stages = stage_list(bible, total)
    return dict(
        version=VERSION,
        total=total,
        characters=_characters(raw, bible),
        foreshadows=_foreshadows(raw, bible, stages, total),
        truth_reveals=_truths(raw, bible, stages, total),
        arc=_arc(raw, stages, total),
        relationships=_relationships(raw, bible, stages),
        appearances=_appearances(raw, bible, stages),
        stage_progress=_progress(raw, stages),
    )


def design_gaps(design):
    """Text the model still owes. Chapter numbers are never listed: code always supplies them."""
    gaps = []
    for row in design["characters"]:
        missing = [key for key in ("wound", "cares_for", "contrast") if not row.get(key)]
        if len(row.get("habits", [])) < 3:
            missing.append("habits")
        if missing:
            gaps.append(f"characters.{row['name']}：缺{'/'.join(missing)}")
    for row in design["foreshadows"]:
        missing = [key for key in ("anchor", "surface", "truth") if not row.get(key)]
        if missing:
            gaps.append(f"foreshadows.{row['id']}：缺{'/'.join(missing)}（anchor须2—6字且全书唯一）")
    for row in design["truth_reveals"]:
        if not row.get("overturns") or not row.get("anchor"):
            gaps.append(f"truth_reveals.{row['layer']}：缺overturns或anchor")
    for row in design["arc"]:
        if not row.get("event"):
            gaps.append(f"arc.{row['kind']}：缺event")
    if not design["relationships"]:
        gaps.append("relationships：至少一对人物的关系台阶")
    for row in design["stage_progress"]:
        if not row.get("goal_line") or not row.get("marker"):
            gaps.append(f"stage_progress.{row['stage']}：缺goal_line或marker")
    return gaps


def _sources(bible, threads):
    world = bible.get("world") if isinstance(bible.get("world"), dict) else {}
    card_keys = ("name", "role", "gender", "identity", "desire", "fear", "flaw", "growth_end", "exit_volume")
    stage_keys = ("number", "start", "end", "location", "antagonist", "goal")
    beats = {
        line.get("kind"): [beat.get("description") for beat in rows(line.get("beats")) if beat.get("description")][:6]
        for line in rows(threads.get("threads") if isinstance(threads, dict) else threads)
    }
    card = bible.get("selected_card") if isinstance(bible.get("selected_card"), dict) else {}
    return [
        ("人物卡，name照抄", [{k: c.get(k) for k in card_keys if c.get(k)} for c in rows(bible.get("characters"))]),
        ("主伏笔，id照抄", rows(bible.get("main_foreshadows"))),
        ("三层真相", rows(world.get("truth_layers"))),
        ("阶段边界、对手与目标", [{k: v.get(k) for k in stage_keys} for v in rows(bible.get("volumes"))]),
        ("对手所属阶段与退场章", rows(bible.get("antagonists"))),
        ("能力规则", bible.get("power", {})),
        ("书名与卖点", dict(title=bible.get("title"), premise=card.get("premise") or bible.get("premise"))),
        ("各线节拍，供安排伏笔与关系时参照", beats),
    ]


def load_story_design(out):
    path = Path(out) / FILE
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def ensure_story_design(ask, bible, threads, total, out):
    """Create the design once for a newly prepared book. Books prepared or started earlier run without it."""
    out = Path(out)
    saved = load_story_design(out)
    if saved:
        return saved
    if not bible.get("story_layer_version") or any((out / "chapters").glob("*.json")):
        return {}
    sections = _sources(bible, threads)
    design = normalize({}, bible, total)
    attempts = []
    for attempt in range(MODEL_ATTEMPTS):
        extra = [("上次缺失项，逐项补齐，其余照原样返回", attempts[-1]["gaps"])] if attempts else []
        raw = ask("story_design", "story-design", sections + extra, attempt)
        candidate = normalize(raw, bible, total)
        gaps = design_gaps(candidate)
        attempts.append(dict(attempt=attempt, returned=bool(raw), gaps=gaps))
        if not attempts[:-1] or len(gaps) < len(design_gaps(design)):
            design = candidate
        if not gaps:
            break
    design["generation"] = dict(
        attempts=attempts,
        remaining_gaps=design_gaps(design),
        policy="章号由程序按阶段边界校正；模型没给出的文字保持为空，不由程序编造",
    )
    save(out / FILE, yaml.safe_dump(design, allow_unicode=True, sort_keys=False))
    return design
