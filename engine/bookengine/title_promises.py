"""Reader-title promise contracts shared by planning, writing and checkup."""

from copy import deepcopy
import re


RESOLUTION_TYPES = {"被杀", "被擒", "被废", "当众击败后失势"}
GROWTH_DIMENSIONS = {"力量", "身份", "关系"}
_VIOLENT = re.compile(r"杀|复仇|报仇|击败|清算|镇压|审判")
FIXED_STAGE_FIELDS = (
    "location",
    "antagonist",
    "goal",
    "climax",
    "exit_chapter",
    "promise_payoffs",
    "opponent_resolution",
    "ending_contract",
)
PROMPT_STAGE_FIELDS = ("number", "start", "end", "stage", "cost", *FIXED_STAGE_FIELDS)


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _integer(value, default=0):
    try:
        return int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _title_clauses(title):
    title = re.sub(r"^[《〈\[【]|[》〉\]】]$", "", _text(title))
    return [part.strip() for part in re.split(r"[，,；;：:。！？!?]", title) if part.strip()]


def _promise_text(clause):
    match = re.fullmatch(r"(?:我)?拿(.{1,10})喂([兽宠灵妖])", clause)
    if match:
        return f"喂{match.group(1)}换{match.group(2)}力"
    return re.sub(r"^(?:我靠|我用|我拿)", "", clause).strip()


def extract_reader_promises(bible):
    """Return one to three concise promises without treating them as observed facts."""
    supplied = bible.get("reader_promises") if isinstance(bible, dict) else None
    candidates = []
    for row in supplied if isinstance(supplied, list) else []:
        text = _text(row.get("text")) if isinstance(row, dict) else _text(row)
        if text:
            candidates.append((text, _text(row.get("source")) if isinstance(row, dict) else "saved"))
    if not candidates:
        candidates.extend((_promise_text(part), "title") for part in _title_clauses(bible.get("title")))
        if len(candidates) < 2:
            extras = [bible.get("premise"), bible.get("reader_expectation"), *bible.get("selling_points", [])]
            candidates.extend((_text(value), "selling_point") for value in extras if _text(value))
    unique = []
    for text, source in candidates:
        text = re.sub(r"\s+", "", text)[:40]
        if len(text) >= 2 and text not in {row[0] for row in unique}:
            unique.append((text, source))
        if len(unique) == 3:
            break
    if not unique:
        unique = [("兑现书名与核心卖点", "fallback")]
    primary = next((index for index, row in reversed(list(enumerate(unique))) if _VIOLENT.search(row[0])), 0)
    return [
        {"id": f"reader-promise-{index}", "text": text, "source": source, "primary": index - 1 == primary}
        for index, (text, source) in enumerate(unique, 1)
    ]


def ensure_reader_promise_contract(bible, total):
    """Attach immutable planning grounds; model-authored schedules remain empty until validated."""
    current = deepcopy(bible) if isinstance(bible, dict) else {}
    current["reader_promises"] = extract_reader_promises(current)
    current["reader_promise_contract_version"] = 1
    current["reader_promise_total"] = _integer(total)
    return current


def normalize_promise_references(bible):
    """Migrate model-facing labels back to stable IDs without changing planned events."""
    current = deepcopy(bible) if isinstance(bible, dict) else {}
    promises = _rows(current.get("reader_promises"))
    ids = {row.get("id") for row in promises}
    ordered = sorted(promises, key=lambda row: not row.get("primary"))
    primary = next((row for row in ordered if row.get("primary")), ordered[0] if ordered else None)
    for stage in _rows(current.get("volumes")):
        for payoff in _rows(stage.get("promise_payoffs")):
            reference = _text(payoff.get("promise_id"))
            if reference in ids:
                continue
            alias = re.sub(r"[\s_-]+", "", reference).casefold()
            match = primary if alias in {"mainpromise", "primarypromise", "primary", "主承诺"} else None
            match = match or next((row for row in ordered if _text(row.get("text")) in reference), None)
            if match:
                payoff["promise_id"] = match["id"]
    return current


def _primary_id(bible):
    promises = _rows(bible.get("reader_promises"))
    return next((row.get("id") for row in promises if row.get("primary")), promises[0].get("id") if promises else "")


def structure_promise_issues(bible, total):
    """Validate stage payoffs, named-opponent exits and final closure."""
    if not bible.get("reader_promise_contract_version"):
        return []
    promises = _rows(bible.get("reader_promises"))
    issues = []
    if not 1 <= len(promises) <= 3 or any(not row.get("id") or not row.get("text") for row in promises):
        issues.append("reader promises: 必须从书名和卖点保留1–3条非空承诺")
    primary = _primary_id(bible)
    stages = _rows(bible.get("volumes"))
    opponents = {row.get("name"): row for row in _rows(bible.get("antagonists"))}
    for stage in stages:
        number, start, end = (_integer(stage.get(key)) for key in ("number", "start", "end"))
        payoffs = _rows(stage.get("promise_payoffs"))
        if not payoffs or not any(row.get("promise_id") == primary for row in payoffs):
            issues.append(f"stage {number}: reader promise正面兑现缺失，必须安排主承诺")
        for payoff in payoffs:
            chapter = _integer(payoff.get("chapter"))
            fields = ("promise_id", "action", "target", "result", "escalation")
            if not start <= chapter <= end or any(not _text(payoff.get(key)) for key in fields):
                issues.append(f"stage {number}: reader promise payoff字段或章号无效")
        resolution = stage.get("opponent_resolution") if isinstance(stage.get("opponent_resolution"), dict) else {}
        chapter = _integer(resolution.get("chapter"))
        if (
            resolution.get("name") != stage.get("antagonist")
            or resolution.get("type") not in RESOLUTION_TYPES
            or not start <= chapter <= end
            or not _text(resolution.get("outcome"))
        ):
            issues.append(f"stage {number}: opponent_resolution须给具名对手、明确结局类型、章号和结果")
        opponent = opponents.get(stage.get("antagonist"), {})
        if resolution and _integer(opponent.get("exit")) != chapter:
            issues.append(f"stage {number}: antagonist.exit必须等于opponent_resolution.chapter")
        if end == total and chapter >= total:
            issues.append(
                f"stage {number}: 终局反派必须在末章前正面清算；exit_chapter仍为{total}，"
                "只把opponent_resolution.chapter与antagonist.exit改到末章前"
            )
    final = next((stage for stage in stages if _integer(stage.get("end")) == total), {})
    ending = final.get("ending_contract") if isinstance(final.get("ending_contract"), dict) else {}
    if (
        not _text(ending.get("promise_response"))
        or ending.get("growth_dimension") not in GROWTH_DIMENSIONS
        or not _text(ending.get("before"))
        or not _text(ending.get("after"))
        or ending.get("before") == ending.get("after")
    ):
        issues.append("ending_contract: 末章须回应书名承诺，并让力量、身份或关系至少一项实质变化")
    if _text(ending.get("new_burden_or_mystery")):
        issues.append("ending_contract: 末章不能留下新污名或新谜团")
    return issues


def _same_fields(actual, expected, fields):
    return isinstance(actual, dict) and all(_text(actual.get(key)) == _text(expected.get(key)) for key in fields)


def planning_promise_issues(bible, batch):
    """Check exact scheduled chapters so the existing bounded outline rewrite can repair them."""
    if not bible.get("reader_promise_contract_version"):
        return []
    mapped = {_integer(row.get("number")): row for row in batch if isinstance(row, dict)}
    issues = []
    for stage in _rows(bible.get("volumes")):
        for payoff in _rows(stage.get("promise_payoffs")):
            number, promise_id = _integer(payoff.get("chapter")), payoff.get("promise_id")
            if number not in mapped:
                continue
            row = mapped[number]
            valid = row.get("reader_promise_id") == promise_id and _same_fields(
                row.get("promise_payoff"), payoff, ("action", "target", "result", "escalation")
            )
            if not valid:
                issues.append(
                    {
                        "kind": "reader_promise_payoff",
                        "promise_id": promise_id,
                        "required": payoff,
                        "chapters": [number],
                        "targets": [number],
                    }
                )
        resolution = stage.get("opponent_resolution") if isinstance(stage.get("opponent_resolution"), dict) else {}
        number = _integer(resolution.get("chapter"))
        if number in mapped and not _same_fields(
            mapped[number].get("opponent_resolution"), resolution, ("name", "type", "outcome")
        ):
            issues.append(
                {"kind": "opponent_resolution", "required": resolution, "chapters": [number], "targets": [number]}
            )
    total = _integer(
        bible.get("reader_promise_total"),
        max([_integer(stage.get("end")) for stage in _rows(bible.get("volumes"))] or [0]),
    )
    final = next((stage for stage in _rows(bible.get("volumes")) if _integer(stage.get("end")) == total), {})
    number = _integer(final.get("end"))
    expected = final.get("ending_contract") if isinstance(final.get("ending_contract"), dict) else {}
    if number in mapped and not _same_fields(
        mapped[number].get("ending_promise_response"),
        expected,
        ("promise_response", "growth_dimension", "before", "after"),
    ):
        issues.append(
            {"kind": "ending_promise_growth", "required": expected, "chapters": [number], "targets": [number]}
        )
    return issues


def bind_title_contracts(bible, batch):
    """Project validated stage schedules onto exact chapters after bounded rewrite fallback."""
    current = deepcopy(batch)
    mapped = {_integer(row.get("number")): row for row in current if isinstance(row, dict)}
    for stage in _rows(bible.get("volumes")):
        for payoff in _rows(stage.get("promise_payoffs")):
            row = mapped.get(_integer(payoff.get("chapter")))
            if row is None:
                continue
            row["reader_promise_id"] = payoff.get("promise_id")
            row["promise_payoff"] = {key: payoff.get(key) for key in ("action", "target", "result", "escalation")}
        resolution = stage.get("opponent_resolution") if isinstance(stage.get("opponent_resolution"), dict) else {}
        row = mapped.get(_integer(resolution.get("chapter")))
        if row is not None:
            row["opponent_resolution"] = {key: resolution.get(key) for key in ("name", "type", "outcome")}
    total = _integer(
        bible.get("reader_promise_total"),
        max([_integer(stage.get("end")) for stage in _rows(bible.get("volumes"))] or [0]),
    )
    final = next((stage for stage in _rows(bible.get("volumes")) if _integer(stage.get("end")) == total), {})
    ending = final.get("ending_contract") if isinstance(final.get("ending_contract"), dict) else {}
    if ending and total in mapped:
        mapped[total]["ending_promise_response"] = deepcopy(ending)
    return current


def writer_title_contract(outline):
    """Route validated metadata through the existing minimal-B writer context."""
    current = dict(outline)
    requirements = []
    payoff = current.get("promise_payoff")
    if isinstance(payoff, dict) and current.get("reader_promise_id"):
        requirements.append(
            f"本章必须让{payoff.get('action')}真实发生：面对{payoff.get('target')}，"
            f"用人物行动、现场反应和直接后果写出{payoff.get('result')}，不能只回顾计划。"
        )
    resolution = current.get("opponent_resolution")
    if isinstance(resolution, dict) and resolution.get("name"):
        resolution_hook = (
            f"{resolution.get('name')}已{resolution.get('type')}且本章结局不可逆；章末只写清算后的直接余波或下一阶段行动动因，"
            "不得让其脱身、放狠话离开、重掌资源或留下翻盘悬念。"
        )
        current.update(hook=resolution_hook, ending_content=resolution_hook)
        requirements.append(
            f"本章必须写出{resolution.get('name')}{resolution.get('type')}，"
            f"并让{resolution.get('outcome')}成为不可逆的现场结果。"
        )
    ending = current.get("ending_promise_response")
    if isinstance(ending, dict) and ending.get("promise_response"):
        requirements.append(
            f"末章回应书名：{ending.get('promise_response')}；{ending.get('growth_dimension')}从{ending.get('before')}变为{ending.get('after')}，不添新污名、新谜团或追查任务。"
        )
    if requirements:
        prior = current.get("must_respond")
        current["must_respond"] = [*prior, *requirements] if isinstance(prior, list) else requirements
    return current


def title_promise_checkup(bible, chapters=()):
    """Expose saved planning only; absent contracts remain Unknown."""
    promises = extract_reader_promises(bible)
    scheduled, resolutions = [], []
    for stage in _rows(bible.get("volumes")):
        for payoff in _rows(stage.get("promise_payoffs")):
            scheduled.append(dict(stage=stage.get("number"), **payoff))
        resolution = stage.get("opponent_resolution") if isinstance(stage.get("opponent_resolution"), dict) else {}
        resolutions.append(
            {
                "stage": stage.get("number"),
                "opponent": stage.get("antagonist"),
                "chapter": resolution.get("chapter"),
                "resolution_type": resolution.get("type") or "Unknown",
                "outcome": resolution.get("outcome") or "Unknown",
            }
        )
    final = max(_rows(bible.get("volumes")), key=lambda row: _integer(row.get("end")), default={})
    ending = final.get("ending_contract") if isinstance(final.get("ending_contract"), dict) else {}
    complete = bool(
        scheduled and resolutions and all(row["resolution_type"] != "Unknown" for row in resolutions) and ending
    )
    from .story_payoffs import story_payoff_checkup

    return {
        "status": "planned" if complete else "Unknown",
        "basis": "saved_planning_fields_only; reference_not_reader_acceptance",
        "promises": promises,
        "scheduled_payoffs": scheduled,
        "opponent_resolutions": resolutions,
        "ending_contract": ending or "Unknown",
        "prose_contracts": story_payoff_checkup(bible, chapters),
    }
