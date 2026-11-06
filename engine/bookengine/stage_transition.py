"""Pure planning/body checks for stage starts; no extraction or filesystem access."""

import re
from .handoff_places import MOVEMENT_WORDS, same_place


def _label(value):
    return value.strip() if isinstance(value, str) else ""


def _number(value):
    try:
        return int(value) if not isinstance(value, bool) else 0
    except (ValueError, TypeError):
        return 0


def _boundary(bible, number, outline):
    """Saved ranges take precedence; an explicit outline contract is a fallback."""
    number = _number(number)
    if number <= 1:
        return None
    stages = bible.get("volumes", bible.get("stages", []))
    stages = [s for s in stages if isinstance(s, dict)] if isinstance(stages, list) else []
    current = next((s for s in stages if _number(s.get("start")) <= number <= _number(s.get("end"))), None)
    if current is not None:
        if _number(current.get("start")) != number:
            return None
        previous = next((s for s in stages if _number(s.get("end")) == number - 1), {})
        return dict(
            chapter=number,
            stage=current.get("number"),
            previous=_label(previous.get("location", previous.get("stage"))),
            opening=_label(current.get("location", current.get("stage"))),
        )
    stage = outline.get("stage")
    stage = stage if isinstance(stage, dict) else dict(location=stage)
    start = _number(stage.get("start", outline.get("stage_start")))
    if start != number and (start or not _label(outline.get("transition"))):
        return None
    return dict(
        chapter=number,
        stage=stage.get("number"),
        previous=_label(outline.get("previous_location")),
        opening=_label(stage.get("location", outline.get("location"))),
    )


_MOVEMENT = re.compile(
    "|".join(map(re.escape, MOVEMENT_WORDS)) + r"|从[^。！？\n]{1,40}(?:到|往|去)"
    r"|沿[^。！？\n]{1,20}(?:走|行|赶)|(?:走|行|赶)了[^。！？\n]{1,12}(?:时辰|小时|天|日|里)"
)
_DURATION = r"(?:[一二两三四五六七八九十百数几半\d]+(?:个)?(?:时辰|小时|分钟|日|天|夜|月|年|刻钟)|一炷香|一盏茶)"
_ELAPSED = re.compile(
    _DURATION + r"(?:之)?后|" + _DURATION + r"(?:已(?:经)?)?过去(?:了)?"
    r"|翌日|次日|第二天|天亮(?:时|后)|入夜(?:时|后)|转眼(?:已|便|就|过)|转眼间"
)
_SCENE = re.compile(r"与此同时|同一时刻|另一边|镜头转(?:到|向)|场景转(?:到|向)|再看|此时的|回到")
_RECOLLECTION = re.compile(r"想起|记得|回忆|曾经|当年|往年|旧事|打算|计划|准备|如果|假如")


def _transition_quotes(opening):
    # Mask dialogue without shifting offsets, so every returned quote is verbatim.
    narrative = re.sub(
        r'“[^”]*(?:”|$)|‘[^’]*(?:’|$)|「[^」]*(?:」|$)|"[^"]*(?:"|$)', lambda m: " " * len(m[0]), opening
    )
    quotes = []
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", narrative):
        sentence = match[0]
        if _RECOLLECTION.search(sentence):
            continue
        movement = _MOVEMENT.search(sentence)
        elapsed = _ELAPSED.search(sentence)
        scene = _SCENE.search(sentence)
        moved = bool(movement)
        if moved and re.search(r"(?:尚未|没有|没能|并未|未曾|不曾).{0,6}$", sentence[: movement.start()]):
            moved = False
        if elapsed and re.search(r"^(?:再|将|要)", sentence[elapsed.end() :].lstrip("，, ")):
            continue
        if moved or elapsed or scene:
            quotes.append(opening[match.start() : match.end()].strip())
    return quotes


def stage_transition(bible, number, text, previous_end=None, *, outline=None):
    """Return None outside a changed-location stage start, else issue/status/quotes.

    ``previous_end`` is a grounded previous end-location string, if available.
    ``outline`` optionally supplies stage={start, location} or stage/transition
    when saved bible ranges are unavailable. Planning is never an observed fact.
    Status is present/missing/unknown; quotes always come from text[:300].
    Missing is a deterministic warning, not a reader's semantic acceptance.
    """
    boundary = _boundary(bible, number, outline or {})
    if boundary is None:
        return None
    old, new = _label(previous_end) or boundary["previous"], boundary["opening"]
    if same_place(old, new, bible):
        return None
    opening = text[:300]
    result = dict(boundary, previous=old or None, opening=new or None, issue=None, status="unknown", quotes=[])
    if not old or not new or not opening.strip():
        return result
    quotes = _transition_quotes(opening)
    result.update(
        status="present" if quotes else "missing",
        issue=None if quotes else "missing_stage_transition",
        quotes=quotes or [opening],
    )
    return result


def planning_transition_issues(bible, batch):
    """Require nonblank transition text on later stage starts; never mutate plans.

    Issue chapters/targets identify only existing batch rows to regenerate.
    This checks the field's presence; its meaning still needs model/reader review.
    """
    issues = []
    for outline in batch:
        number = _number(outline.get("number"))
        boundary = _boundary(bible, number, outline)
        if boundary is not None and not _label(outline.get("transition")):
            issues.append(
                dict(
                    kind="missing_stage_transition_plan",
                    chapters=[number],
                    targets=[number],
                    previous=boundary["previous"] or None,
                    opening=boundary["opening"] or None,
                    suggestion="阶段首章transition须用一两句说明从上一阶段终点到本阶段起点的移动、时间或原因。",
                )
            )
    return issues


def planned_location(outline):
    for value in [
        outline.get("location"),
        *[s.get("location") for s in outline.get("scenes", []) if isinstance(s, dict)],
    ]:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def complete_transitions(batch, history=()):
    """Add executable transfer beats when consecutive planned locations change."""
    from copy import deepcopy

    result = deepcopy(batch)
    prior = {row.get("number"): row for row in [*history, *result]}
    for row in result:
        previous = prior.get(_number(row.get("number")) - 1, {})
        old, new = planned_location(previous), planned_location(row)
        if old and new and old != new and not _label(row.get("transition")):
            actions = " ".join(str(s.get("action", "")) for s in row.get("scenes", []) if isinstance(s, dict))
            if not (old in actions and new in actions and _transition_quotes(actions)):
                row["transition"] = f"交代离开{old}、抵达{new}的移动过程、耗时和原因，落实在开头300字。"
    return result
