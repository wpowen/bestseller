"""Review a written opening against the preceding chapter's actual conflict."""

from pathlib import Path

from . import adjacent_choice as choice
from .client import SYSTEM
from .longutil import normalize_sections
from .storage import load
from .text import _paragraph_spans, chinese_count


_PREVIOUS_LIMIT = 3500
_OPENING_LIMIT = 1900
_EDIT_LIMIT = 1400
_AFTERMATH_LIMIT = 1800
_REVIEW_LIMIT = 10000
_REVIEW_RULE = (
    "对照上一章已落盘的正文和本章开场。判断本章是否把上一章已经演过的冲突重新起头，"
    "包括换了台词、道具叫法或站位，仍让同一方提出同类要求、主角用同一种行动应对。"
    "同一对手依约回来、同一件东西仍重要、沿伤势和承诺处理后果都可以保留。"
    "只有同一套要求和应对被重新演一遍、缺乏新的当场目的或行动机制才报replay。"
    "有新目的或新动作真实改变了当前冲突才判断clear。后半章才有的新进展不能遮住开场重演；"
    "新借口、新物件名也不能给相同应对换名。只在有证据时报告。"
    "prior_action、prior_outcome和current_quote各只取一段内的一句连续原文，不能跨段、拼接、改标点或加省略号。"
    "分别引用上章实际的应对、已发生的结果，以及本章重演的动作。first_id与last_id只圈最短的连续重演过程，"
    "含重复动作和它的直接落点，不超过1400字符。若同一场连续两次沿用旧解法，范围要从第一次覆盖到第二次落点，"
    "夹在中间的新发现要留在改写中。若盘问也像上章，优先圈真正重打一遍的段落；"
    "保留独有的新争夺对象和后面新发展的段落。判断不了用unknown。"
    "另外检查skipped_choice：上章末明确提出有取舍的两条路且收笔时仍未决，本章开头却已经享有某一选择"
    "的结果，通读本章也找不到如何脱身、谁作了决定或局面怎样改变。逐字摘出上章末1200字符内的两处不同原文："
    "prior_choice为选择或排他代价，prior_pending为收笔仍未解决的表现。current_quote须能证明本章将结果视为已发生。"
    "只圈需要补上承接的最小开场范围。已在上章解决、换视角而原事待续、本章稍后揭示经过、"
    "或有意保留谜底时均不修。只缺解释、没有新处境证据时用unknown。"
)
_REVIEW_SCHEMA = (
    '{"status":"clear / replay / skipped_choice / unknown","finding":{"first_id":1,"last_id":2,'
    '"prior_action":"仅replay的上章行动","prior_outcome":"仅replay的上章结果",'
    '"prior_choice":"仅skipped_choice的两难","prior_pending":"仅skipped_choice的未决状况",'
    '"current_quote":"本章引文","reason":"具体的重演或被跳过的选择"}}'
)


def _previous_body(out, chapter):
    if out is None or chapter < 2:
        return ""
    number = chapter - 1
    root = Path(out)
    record = root / f"chapters/{number:03d}.json"
    body = root / f"chapter-{number:03d}.body.md"
    if not record.is_file() or not body.is_file():
        return ""
    value = load(record)
    if value.get("status") != "complete" or value.get("fact_source") != "final_body":
        return ""
    previous = body.read_text(encoding="utf-8").strip()
    if len(previous) <= _PREVIOUS_LIMIT:
        return previous
    cutoff = len(previous) - _PREVIOUS_LIMIT
    return previous[next((start for start, _ in _paragraph_spans(previous) if start >= cutoff), cutoff) :]


def _opening_spans(text):
    return [(start, end) for start, end in _paragraph_spans(text) if end <= _OPENING_LIMIT]


def _review_sections(previous, chapter, text, spans):
    return [
        (f"第{chapter - 1}章已落盘正文，仅作证据", previous),
        ("本章开场编号段", "\n".join(f"{n} {text[a:b]}" for n, (a, b) in enumerate(spans, 1))),
        ("本章其余正文，只供核实稍后是否揭晓，不能圈作改写范围", text[spans[-1][1] :]),
    ]


def _choice_input_fits(client, instruction, sections, schema):
    """A missing resolution can only be judged when the entire review survives Q1 packing."""
    # longutil.ask reserves 350 chars and long_context counts headings and separators.
    # Client.stage also includes its own system message and can clip at context_limit.
    prefix = getattr(client, "prefix", "")
    packed = "\n\n".join(f"## {heading}\n{body}" for heading, body in normalize_sections(sections))
    overhead = len(prefix) + len(SYSTEM) + len(instruction) + len(schema) + 350
    request = f"{prefix}\n\n{instruction}\n{packed}\n只返回完整JSON，结构：\n{schema}"
    context_limit = getattr(client, "context_limit", None) or _REVIEW_LIMIT
    return (
        len(packed) + overhead <= _REVIEW_LIMIT
        and len(request) + len(getattr(client, "system", SYSTEM)) + 256 <= context_limit
    )


def _finding(response, previous, text, spans):
    finding = response.get("finding")
    if not isinstance(finding, dict):
        return None
    first, last = finding.get("first_id"), finding.get("last_id")
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(spans):
        return None
    start, end = spans[first - 1][0], spans[last - 1][1]
    skipped_choice = response["status"] == "skipped_choice"
    prior_keys = ("prior_choice", "prior_pending") if skipped_choice else ("prior_action", "prior_outcome")
    quotes = [finding.get(key) for key in (*prior_keys, "current_quote")]
    evidence = previous[-1200:] if skipped_choice else previous
    if (
        end - start > _EDIT_LIMIT
        or not all(isinstance(quote, str) and len(quote.strip()) >= 8 and "\n" not in quote for quote in quotes)
        or any(quote not in evidence for quote in quotes[:2])
        or quotes[-1] not in text[start:end]
    ):
        return None
    if skipped_choice and (quotes[0] in quotes[1] or quotes[1] in quotes[0]):
        return None
    return dict(finding, kind=response["status"], start=start, end=end, authority="agent_editorial_not_human")


def _contract(outline):
    contract = {
        key: outline[key]
        for key in ("title", "required_event", "payoff", "ending_content", "opponent_resolution")
        if outline.get(key)
    }
    contract["scenes_results_to_keep"] = [
        scene["result"] for scene in (outline.get("scenes") or []) if isinstance(scene, dict) and scene.get("result")
    ]
    return contract


def _review(ask, client, prefix, previous, chapter, text, spans, *, focus_choice=False):
    sections = _review_sections(previous, chapter, text, spans)
    schema = _REVIEW_SCHEMA
    if focus_choice:
        schema = schema[:-1] + (
            ',"choice_trace":{"kind":"shown / already_shown / still_open / no_choice","action_quote":"","effect_quote":"",'
            '"first_state_quote":"","bridge_quote":""}}'
        )
    for suffix, feedback in (
        ("review", ""),
        (
            "review-grounded",
            "上次有引文未逐字匹配。每项只从单段复制一句原文，重新标注。若先出现脱险结果才能读到所谓解围动作，还须核实这是回叙还是下一次危机。",
        ),
    ):
        instruction = (choice.REVIEW if focus_choice else "") + _REVIEW_RULE + feedback
        response = ask(
            client,
            prefix + "-adjacent-replay-" + suffix,
            instruction,
            sections,
            schema,
            cap=1800,
            effort="low",
            input_limit=_REVIEW_LIMIT,
            max_attempts=1,
        )
        if not isinstance(response, dict) or response.get("status") not in ("clear", "replay", "skipped_choice"):
            break
        if response["status"] != "replay":
            fits = _choice_input_fits(client, instruction, sections, schema)
            if response["status"] == "clear":
                if fits and focus_choice and not choice.clear_trace(response.get("choice_trace"), previous, text):
                    continue
                return ("checked" if fits else "checked_opening_only"), None
            if not fits:
                return "choice_context_incomplete", None
        finding = _finding(response, previous, text, spans)
        if finding:
            return response["status"], finding
    return "unknown", None


def _valid_replacement(old, new, before_count, after_count, bible, repeated_quote, band):
    if not new or new == old or len(_paragraph_spans(new)) > len(_paragraph_spans(old)) + 2:
        return False
    if repeated_quote and repeated_quote in new:
        return False
    bounds = band if isinstance(band, (list, tuple)) and len(band) == 2 else (0.85 * before_count, 1.1 * before_count)
    if not bounds[0] <= after_count <= bounds[1]:
        return False
    return not _missing_names(old, new, bible)


def _missing_names(old, new, bible):
    cards = [*(bible.get("characters") or []), *(bible.get("antagonists") or [])]
    return [
        name
        for card in cards
        if isinstance(card, dict)
        and isinstance(name := card.get("name"), str)
        and name
        and name in old
        and name not in new
    ]


def _shares_participants(previous, opening, bible):
    cast = [*(bible.get("characters") or []), *(bible.get("antagonists") or [])]
    names = {row["name"] for row in cast if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]}
    return sum(name in previous and name in opening for name in names) >= 2


def _without_boundary_echoes(text, replacement, start, end):
    """A model may echo the unchanged context included for joining the scene."""
    proposed = _paragraph_spans(replacement)
    before = [text[a:b] for a, b in _paragraph_spans(text[:start])]
    after = [text[end + a : end + b] for a, b in _paragraph_spans(text[end:])]
    paragraphs = [replacement[a:b] for a, b in proposed]
    left = next((n for n in range(min(len(before), len(paragraphs)), 0, -1) if paragraphs[:n] == before[-n:]), 0)
    right = next((n for n in range(min(len(after), len(paragraphs) - left), 0, -1) if paragraphs[-n:] == after[:n]), 0)
    kept = proposed[left : len(paragraphs) - right]
    return replacement[kept[0][0] : kept[-1][1]] if kept else ""


def _repair_aftermath(suffix, supplied):
    if not isinstance(supplied, list) or len(supplied) > 3:
        return None
    spans = _aftermath_spans(suffix)
    edits = {}
    for edit in supplied:
        number = edit.get("id") if isinstance(edit, dict) else None
        if type(number) is not int or not 1 <= number <= len(spans) or number in edits:
            return None
        start, end = spans[number - 1]
        original = suffix[start:end]
        replacement = edit.get("text")
        if (
            not isinstance(replacement, str)
            or not replacement.strip()
            or "\n" in replacement
            or any(mark in original + replacement for mark in '“”「」『』"')
        ):
            return None
        edits[number] = replacement.strip()
    for number, replacement in sorted(edits.items(), reverse=True):
        start, end = spans[number - 1]
        suffix = suffix[:start] + replacement + suffix[end:]
    return suffix


def _aftermath_spans(suffix):
    return [(start, end) for start, end in _paragraph_spans(suffix) if end <= _AFTERMATH_LIMIT]


def repair_adjacent_replay(client, prefix, text, raw, failed, state, bible, outline, extract, ask, *, out):
    chapter = outline.get("number", 0)
    previous, spans = _previous_body(out, chapter), _opening_spans(text)
    if not previous or not spans or not _shares_participants(previous, text[: spans[-1][1]], bible):
        return text, raw, failed, {"status": "not_reviewed"}
    prior_plan = Path(out) / "writing_outlines" / f"{chapter - 1:03d}.json"
    try:
        plan = load(prior_plan)
    except (OSError, ValueError):
        plan = {}
    focus_choice = isinstance(plan, dict) and plan.get("ending_type") == "两难选择"
    status, finding = _review(ask, client, prefix, previous, chapter, text, spans, focus_choice=focus_choice)
    if not finding:
        note = {"status": status}
        if status == "unknown":
            note["reason"] = "no_grounded_verdict"
        elif status == "choice_context_incomplete":
            note = {"status": "unknown", "reason": status}
        elif status == "checked_opening_only":
            note = {"status": "checked", "choice_status": "unknown", "reason": "choice_context_incomplete"}
        return text, raw, failed, note
    args = (client, text, raw, failed, state, bible, outline, extract, ask, previous, finding, status)
    result = _repair_finding(prefix + "-adjacent-replay", *args)
    note = result[-1]
    if (
        focus_choice
        and status == "skipped_choice"
        and (
            note.get("reason") == "invalid_bounded_replacement"
            and note.get("proposed")
            or note.get("reason") == "adjacent_replay_recheck_failed"
            and note["after"].get("preserves_facts") is False
        )
    ):
        feedback = {k: note[k] for k in ("reason", "missing_names", "proposed", "after") if k in note}
        result = _repair_finding(prefix + "-adjacent-replay-retry", *args, feedback=feedback)
        result[-1]["first_rejection"] = feedback
    return result


def _repair_finding(
    prefix, client, text, raw, failed, state, bible, outline, extract, ask, previous, finding, status, *, feedback=None
):
    chapter = outline["number"]
    start, end = finding["start"], finding["end"]
    original = text[start:end]
    skipped_choice = status == "skipped_choice"
    before_count = chinese_count(text)
    band = outline.get("word_range")
    if not isinstance(band, (list, tuple)) or len(band) != 2:
        band = (round(0.85 * before_count), round(1.1 * before_count))
    outside = before_count - chinese_count(original)
    note = {"status": "rolled_back", "before": finding, "previous_chapter": chapter - 1}
    response = ask(
        client,
        prefix + "-repair",
        choice.REPAIR
        if skipped_choice
        else "只替换命中段落。第一段就承接上一章已经发生的结果，直接让在场的人提出或执行本章的新条件、新决定。"
        "若他们再次见面，有关上次案由、是否交物的反复问答最多一轮。删掉旧的逼问、旧交手和旧解法，"
        "不能在冲突外另写新台词又把所圈的开场照抄下来。"
        "命中引文中的具体旧动作必须从这段移除，包括同机制的改述；仍可通过现有伤势、物件和未了约定承接。"
        "先从上一章的应对原句识别解法依赖什么。若上次靠地形、借力或卸力，这次不能再借地形、"
        "把兵刃向同一方向引偏或逼对手再退一步。必须保留一次挡招或试探时，可用一次短而直接的护物动作，"
        "把篇幅给人物承担的代价和对手随后作的选择。"
        "保留出场人物、伤势、物件归属、施行中的条件、已经到手的成果及本章新事实。"
        "先读仍要接上的后半章：其中已发生的伤势、物件破损和新局面必须在改写后成立。"
        "若任务要求仍有一次挡招或试探，可以缩成一次新的应对，换掉旧招的起因、手段或争夺目标；不要删去相应后果。"
        "对手可以依约出现。禁止新增设定、人物、伤势或改变既定结局。"
        "写成完整场面，留足新选择、动作和对手反应的篇幅，别缩成概述。新增分段最多两处，"
        "末段的局面要直接接得上未改的下一段。"
        "只交替换区正文，不要重抄未改的前后文。",
        [
            *([("上次未采用的原因，从原稿重写。保留在场人的姓名和接缝的问答", feedback)] if feedback else []),
            ("命中原文，只替换这里", original),
            ("承接证据" if skipped_choice else "重复证据", finding),
            *([("选择发生前的现场，只读", previous[-850:])] if skipped_choice else []),
            ("未改的前后文", [text[max(0, start - 280) : start], text[end : end + _AFTERMATH_LIMIT]]),
            ("本章原有合同", _contract(outline)),
            ("替换段汉字范围", [max(1, band[0] - outside), band[1] - outside]),
            ("替换段目标汉字数", min(band[1] - outside, max(band[0] - outside, round(0.9 * chinese_count(original))))),
        ],
        '{"text":"替换后的完整段落"}',
        cap=3600,
        input_limit=7000,
        max_attempts=1,
    )
    replacement = response.get("text") if isinstance(response, dict) else None
    replacement = replacement.strip() if isinstance(replacement, str) else ""
    replacement = _without_boundary_echoes(text, replacement, start, end)
    candidate = text[:start] + replacement + text[end:]
    if not _valid_replacement(
        original,
        replacement,
        before_count,
        chinese_count(candidate),
        bible,
        None if skipped_choice else finding["current_quote"],
        band,
    ):
        return (
            text,
            raw,
            failed,
            dict(
                note,
                reason="invalid_bounded_replacement",
                missing_names=_missing_names(original, replacement, bible),
                proposed=replacement[:650],
            ),
        )
    recheck_instruction = (
        choice.RECHECK
        if skipped_choice
        else "核对本章重写段是否从上章的已成结果继续，逐字证据区是唯一上章来源。"
        "本章重写前及重写后都属于同一章，不能互相当上章证据。"
        "若仍用上章同一种应对重演一次交手，即使多了新理由或后续发展，也返回 replay。"
        "有具体新目的或新应对且有推进，才返回 clear；不确定用 unknown。"
        "保真只核对场面结束后仍成立、会影响后文的信息：新线索、人物选择、伤势、物件归属与损坏、约定和时限。"
        "已圈定重演里的步法、借力、兵器偏转、短暂站位和过程数值均属允许替换的过程；"
        "保住它们原先带来的持续后果即可。后来原文回提了已删过程时，修回指，不能要求恢复过程。"
        "继续看替换区之后的同章正文。若旁白还用‘方才’等词回指已删的交手，或把已删动作当作物件损伤等结果的原因，"
        "用 aftermath_edits 最多修三处直接依赖的完整原段，id 用后续正文编号，只改悬空回指及错误的因果归属，"
        "保留已存在的伤势、物件、期限和线索，不改对白，不增原因，长度与原段接近。"
        "直接写新场面中实际发生的事，删净已放弃的动作、数值和因果，不要用‘并非旧动作，而是……’再解释一遍。"
        "不需要修就留空。保真按这些修订采用后的全文判断。"
        "给出保真布尔、具体依据，以及新稿中逐字可核的当场行动。"
    )
    recheck_sections = [
        ("上一章末场", previous[-1200:])
        if skipped_choice
        else ("唯一上一章行动和落点证据", [finding["prior_action"], finding["prior_outcome"]]),
        ("本章替换区之前的接缝", text[max(0, start - 240) : start]),
        ("本章重写前", original),
        ("本章重写后", replacement),
        ("本章替换区之后的全文，仅核实稍后是否揭晓", text[end:])
        if skipped_choice
        else (
            "本章替换区之后的编号原段",
            [dict(id=n, text=text[end + a : end + b]) for n, (a, b) in enumerate(_aftermath_spans(text[end:]), 1)],
        ),
        ("本章原有合同", _contract(outline)),
    ]
    recheck_schema = (
        '{"status":"clear / skipped_choice / unknown","preserves_facts":true,"new_action_quote":"当事人具体动作",'
        '"consequence_quote":"现实后果或舍弃的机会","reason":"保真依据"}'
        if skipped_choice
        else '{"status":"clear","preserves_facts":true,"new_action_quote":"本章重写后行动原句",'
        '"aftermath_edits":[{"id":1,"text":"修正回指后的一段"}],"reason":"具体依据"}'
    )
    if skipped_choice and not _choice_input_fits(client, recheck_instruction, recheck_sections, recheck_schema):
        return text, raw, failed, dict(note, reason="choice_recheck_context_incomplete")
    checked = ask(
        client,
        prefix + "-recheck",
        recheck_instruction,
        recheck_sections,
        recheck_schema,
        cap=2800,
        effort="low",
        input_limit=_REVIEW_LIMIT,
        max_attempts=1,
    )
    checked = checked if isinstance(checked, dict) else {}
    quote = checked.get("new_action_quote")
    if (
        checked.get("status") != "clear"
        or checked.get("preserves_facts") is not True
        or not isinstance(quote, str)
        or len(quote.strip()) < 8
        or quote not in replacement
        or quote in original
    ):
        return text, raw, failed, dict(note, reason="adjacent_replay_recheck_failed", after=checked)
    if skipped_choice:
        consequence = checked.get("consequence_quote")
        if (
            not isinstance(consequence, str)
            or len(consequence.strip()) < 8
            or "\n" in quote + consequence
            or consequence not in replacement
            or quote in consequence
            or consequence in quote
            or checked.get("aftermath_edits")
        ):
            return text, raw, failed, dict(note, reason="choice_recheck_failed", after=checked)
    aftermath = _repair_aftermath(text[end:], checked.get("aftermath_edits", []))
    if aftermath is None:
        return text, raw, failed, dict(note, reason="invalid_adjacent_replay_aftermath", after=checked)
    candidate = text[:start] + replacement + aftermath
    if not band[0] <= chinese_count(candidate) <= band[1]:
        return text, raw, failed, dict(note, reason="adjacent_replay_final_word_band_failed", after=checked)
    extracted, invalid, _ = extract(client, prefix + "-state", candidate, state, bible, outline, [], final=True)
    if invalid:
        return text, raw, failed, dict(note, reason="adjacent_replay_state_failed")
    return candidate, extracted, False, dict(note, status="repaired", after=checked)
