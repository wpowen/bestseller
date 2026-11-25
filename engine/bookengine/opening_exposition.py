"""Review over-explanation where readers first enter the story."""

import re

from tools.book_checkup import clean_prose

from .checks import high_confidence
from .promise_io import precise_ask
from .text import _paragraph_spans, chinese_count


OPENING_CHARS = 500
_DIALOGUE = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"')
_STAKES = re.compile(r"(?:若|如果|一旦)[^。！？\n]{3,32}[，,][^。！？\n]{0,12}(?:就|便|连|只能)")
_ROLE = re.compile(
    r"(?:奉命|受命|负责|专管|掌管|专司)[^。！？\n]{6,38}的[^。！？\n]{0,12}(?:弟子|执事|守卫|管事|伙计|侍卫)"
)


def opening_explanation_candidates(text):
    """Keep the first page in order and flag narration for editorial review."""
    opening, targets = [], {}
    total = 0
    for number, (start, end) in enumerate(_paragraph_spans(text), 1):
        paragraph = text[start:end]
        opening.append(dict(id=number, text=paragraph))
        narration = _DIALOGUE.sub("", paragraph)
        if _STAKES.search(narration) or _ROLE.search(narration):
            targets[number] = (start, end)
        total += chinese_count(paragraph)
        if total >= OPENING_CHARS:
            break
    return opening, targets


def _opening_edits(text, supplied, targets):
    edits = {}
    for edit in supplied:
        number = edit.get("id") if isinstance(edit, dict) else None
        if type(number) is not int or number not in targets or number in edits:
            return None
        start, end = targets[number]
        original = text[start:end]
        replacement = edit.get("text")
        reason = edit.get("reason")
        if (
            edit.get("original") != original
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(replacement, str)
            or not replacement.strip()
            or "\n" in replacement
            or _DIALOGUE.findall(replacement) != _DIALOGUE.findall(original)
            or re.findall(r"\d+", replacement) != re.findall(r"\d+", original)
        ):
            return None
        edits[number] = dict(edit, text=replacement.strip())
    return {number: edit for number, edit in edits.items() if edit["text"] != edit["original"]}


def repair_opening_explanations(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    if outline.get("number") != 1:
        return text, raw, failed, None
    opening, targets = opening_explanation_candidates(text)
    note = dict(status="checked", candidates=list(targets), edits=[])
    if not targets:
        return text, raw, failed, note
    response = precise_ask(
        ask,
        client,
        prefix + "-opening-exposition-repair",
        "你是小说责编。看各段与前后开篇的关系，只改可改段。去掉叙述者抢先解释现场已经表现出来的事，"
        "也去掉围观人物刚出场就介绍的背景职责。保留必要利益和新出现的关键事实，"
        "用已有动作、物件或眼前反应让读者看出人物要保住什么。不添事件、线索或设定，不改对白或说话人。"
        "每处保留一段；无需改时 edits 留空。original 必须逐字引用。",
        [("按顺序给出的完整开篇段", opening), ("只可改的段号", list(targets))],
        '{"edits":[{"id":1,"original":"完整原段","text":"修改后的一段","reason":"具体编辑理由"}]}',
        cap=3000,
        required_sources=[text[start:end] for start, end in targets.values()],
    )
    supplied = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(supplied, list):
        return text, raw, failed, dict(note, status="skipped", reason="opening_editor_response_unknown")
    edits = _opening_edits(text, supplied, targets)
    if edits is None:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_opening_edit")
    if not edits:
        return text, raw, failed, note
    candidate = text
    for number, edit in sorted(edits.items(), reverse=True):
        start, end = targets[number]
        candidate = candidate[:start] + edit["text"] + candidate[end:]
    note["edits"] = list(edits.values())
    band = outline.get("word_range")
    if clean_prose(candidate)[0] != candidate or (
        isinstance(band, (list, tuple)) and len(band) == 2 and not band[0] <= chinese_count(candidate) <= band[1]
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="opening_format_or_word_band_failed")
    after = [dict(row, text=edits[row["id"]]["text"]) if row["id"] in edits else row for row in opening]
    result = precise_ask(
        ask,
        client,
        prefix + "-opening-exposition-recheck",
        "逐段对照两版开头。能否确认与现场有关的事实、关键物件及其状态、人物行动、说话人和原有伏笔保留，"
        "且没有新事件或设定？省略围观者的背景职责不等于否认其存在。新稿是否让已有动作和对话"
        "直接推进眼下处境，减少作者解释？分别返回布尔值，不确定用 false。只返回 JSON。",
        [("原开篇", opening), ("新开篇", after), ("动过的段", list(edits))],
        '{"facts_preserved":false,"exposition_reduced":false,"reason":"具体依据"}',
        cap=1800,
    )
    note["recheck"] = result
    if not isinstance(result, dict) or (
        result.get("facts_preserved") is not True or result.get("exposition_reduced") is not True
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="opening_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-opening-exposition-final-state", candidate, state, bible, outline, [], final=True
    )
    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="opening_continuity_failed")
    return candidate, checked, False, dict(note, status="repaired")
