"""Heuristic paragraph-level explanation density with bounded prose repair."""

import re

from .checks import high_confidence
from .introductions import world_term_registry
from .promise_io import precise_ask
from .text import _paragraph_spans
from tools.book_checkup import clean_prose


RATIO_THRESHOLD = 0.24
MIN_EXPLANATION_CHARS = 220
CONSECUTIVE_THRESHOLD = 2
SHORT_TEXT_LIMIT = 500
SHORT_RATIO_THRESHOLD = 0.4

_DIALOGUE = re.compile(r"“[^”]*”")
_CAUSAL = re.compile(
    r"因为|所以|因此|意味着|说明|证明|原来|如果|若(?!有若无)|只要|一旦|除非|之所以|取决于|"
    r"不是.{0,16}而是|并非.{0,16}而是"
)
_TEMPORAL_ACTION = re.compile(r"直到.{0,24}才")
_MODAL = re.compile(r"必须|不得|不能|只能|不必|只需|可以|能够|才会|才能|便会|便能|不会|并不")
_ANALYTIC = re.compile(
    r"判断|认定|发现|明白|知道|看出|真正|根本|其实|显然|足以|同一|每一|两边|一边|另一|"
    r"前者|后者|矛盾|对不上|相比|对照|分别|先后|层层剥开|露出"
)
_TECHNICAL = re.compile(r"规则|境界|阵|纹|槽|机关|兽|感官|听觉|气味|车辙|痕迹|证词|供状|轨|路|门|印|契约")


def _cjk_length(text):
    return len(re.findall(r"[\u3400-\u9fff]", text))


def _classify(paragraph):
    prose = _DIALOGUE.sub("", paragraph)
    chars = _cjk_length(prose)
    causal = len(_CAUSAL.findall(prose))
    modal = len(_MODAL.findall(prose))
    analytic = len(_ANALYTIC.findall(prose))
    technical = len(_TECHNICAL.findall(prose))
    inference_reason = "temporal_action_not_explanation" if _TEMPORAL_ACTION.search(prose) else None
    category = None
    if chars >= 35 and (modal >= 2 or "规则" in prose) and analytic + causal >= 1:
        category = "rule_repeat"
    elif chars >= 35 and causal >= 1 and causal + modal + analytic >= 2:
        category = "causality"
    elif (
        chars >= 45
        and technical >= 4
        and (
            analytic >= 2
            or technical >= 6
            and causal + modal >= 1
            or re.search(r"证词|供状|调兽令", prose)
            and (analytic + causal >= 1 or "却" in prose)
        )
    ):
        category = "setting"
    return dict(
        category=category,
        chars=chars,
        causal_markers=causal,
        modal_markers=modal,
        analytic_markers=analytic,
        technical_markers=technical,
        inference_reason=inference_reason if category is None else None,
    )


def analyze_explanation_density(text):
    spans = _paragraph_spans(text)
    issues = []
    inferences = []
    total = 0
    for index, (start, end) in enumerate(spans, 1):
        paragraph = text[start:end]
        result = _classify(paragraph)
        total += result["chars"]
        if result["category"]:
            issues.append(dict(id=index, quote=paragraph, start=start, end=end, **result))
        elif result["inference_reason"]:
            inferences.append(
                dict(id=index, quote=paragraph, start=start, end=end, reason=result["inference_reason"], decision="Inference")
            )
    explanation_chars = sum(row["chars"] for row in issues)
    issue_ids = {row["id"] for row in issues}
    longest = run = 0
    for index in range(1, len(spans) + 1):
        run = run + 1 if index in issue_ids else 0
        longest = max(longest, run)
    ratio = explanation_chars / max(1, total)
    dense = ratio >= RATIO_THRESHOLD and explanation_chars >= MIN_EXPLANATION_CHARS
    dense = dense or ratio >= 0.15 and explanation_chars >= 330 and len(issues) >= 5
    dense = dense or total <= SHORT_TEXT_LIMIT and ratio >= SHORT_RATIO_THRESHOLD
    return {
        "tier": "dense" if dense else "normal",
        "ratio": round(ratio, 4),
        "explanation_chars": explanation_chars,
        "non_dialogue_chars": total,
        "longest_run": longest,
        "issues": issues,
        "inferences": inferences,
        "basis": "finite surface heuristic; agent_editorial_not_human",
    }


def _protected_literals(text, bible):
    names = [
        card.get("name")
        for card in bible.get("characters", [])
        if isinstance(bible.get("characters"), list)
        if isinstance(card, dict) and isinstance(card.get("name"), str)
    ]
    terms = list(world_term_registry(bible))
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    return sorted({value for value in [*names, *terms, *numbers] if isinstance(value, str) and value and value in text})


def repair_explanation_density(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    before = analyze_explanation_density(text)
    note = {"status": before["tier"], "before": before, "after": before, "edits": []}
    if before["tier"] != "dense":
        return text, raw, failed, note
    targets = [dict(id=row["id"], original=row["quote"], category=row["category"]) for row in before["issues"]]
    protected = _protected_literals("\n".join(row["original"] for row in targets), bible)
    response = precise_ask(
        ask,
        client,
        prefix + "-explanation-density-repair",
        "只改点名段：把重复规则、成串因果说明改成现场动作或自然对白，或删除已被前文展示过的重复解释。"
        "信息、人物选择、因果结果和保护原词不得丢。每个text只能是一段，不能增设定。",
        [("只可修改的解释段", targets), ("必须逐字保留的实体、术语与数字", protected)],
        '{"edits":[{"id":1,"original":"原段","text":"替换后的单个自然段"}]}',
        cap=6000,
        required_sources=[row["original"] for row in targets],
    )
    if isinstance(response, dict) and response.get("status") == "skipped":
        return text, raw, failed, dict(note, status="skipped", reason=response.get("reason"))
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not edits:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edits")
    by_id = {row["id"]: row for row in targets}
    spans = _paragraph_spans(text)
    candidate = text
    for edit in sorted(edits, key=lambda row: row.get("id", 0), reverse=True):
        source = by_id.get(edit.get("id"))
        replacement = edit.get("text")
        if (
            not source
            or edit.get("original") != source["original"]
            or not isinstance(replacement, str)
            or not replacement.strip()
            or re.search(r"\n\s*\n", replacement)
        ):
            return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edit_target")
        start, end = spans[edit["id"] - 1]
        candidate = candidate[:start] + replacement.strip() + candidate[end:]
    after = analyze_explanation_density(candidate)
    note.update(after=after, edits=edits, protected=protected)
    if (
        after["tier"] == "dense"
        or any(value not in candidate for value in protected)
        or clean_prose(candidate)[0] != candidate
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="density_or_information_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-explanation-final-state", candidate, state, bible, outline, [], final=True
    )
    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="final_state_or_continuity_failed")
    return candidate, checked, False, dict(note, status="checked")


def explanation_density_checkup(chapters):
    rows = [dict(chapter=chapter["number"], **analyze_explanation_density(chapter["text"])) for chapter in chapters]
    return {
        "ratio_threshold": RATIO_THRESHOLD,
        "minimum_explanation_chars": MIN_EXPLANATION_CHARS,
        "consecutive_paragraph_threshold": CONSECUTIVE_THRESHOLD,
        "dense_chapters": [row["chapter"] for row in rows if row["tier"] == "dense"],
        "chapters": rows,
        "basis": "finite surface heuristic calibrated on eval-52 and eval-57; Inference",
    }
