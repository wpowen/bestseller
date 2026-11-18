"""One bounded main-scene prose repair; re-extract actual type and roll back unsafe edits."""

from copy import deepcopy

from .chapter_rhythm import CANONICAL_TYPES, normalize_chapter_type, relationship_contract
from .checks import high_confidence
from .promise_io import precise_ask
from .rhythm_model import ask_once, RhythmCallLimit
from .rhythm_contract_repair import _directory, planned_type_source
from .storage import load, save
from .text import _paragraph_spans, chinese_count
from tools.book_checkup import clean_prose
from tools.runtime_support import ProjectError, QuotaExhausted

FOCUS = {
    "对抗": "两场主戏以当前阻力下的行动、反制和当场结果推动冲突，不能只讨论路线与分工。",
    "谋划": "两场主戏比较不同方案、风险与代价，人物做出取舍及分工；不要提前执行下一轮对抗。",
    "揭秘": "两场主戏围绕现场线索的矛盾推翻旧判断、揭出局部真相并改变人物选择；不要只作路线分工。",
    "日常": "两场主戏以休整、照料、吃饭或生活行进呈现人物状态；不要变成战斗、破机关或长篇路线论证。",
    "收获": "两场主戏实际领取、试用或分配此前行动所得，呈现获得后的选择；不要重演争夺或新调查。",
}


def _type(raw):
    return normalize_chapter_type(raw.get("type")) if isinstance(raw, dict) else ""


def _quotes(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "quote" or key.endswith("_quote")) and isinstance(item, str) and item.strip():
                yield item
            elif isinstance(item, (dict, list)):
                yield from _quotes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _quotes(item)


def _facts(raw, text):
    keys = (
        "events",
        "r2_events",
        "gains",
        "costs",
        "injuries",
        "solutions",
        "payoff",
        "core_use",
        "continuity_delta",
        "opening_quote",
        "end_quote",
        "present_quotes",
        "character_locations",
    )
    return list(dict.fromkeys(q for key in keys for q in _quotes({key: raw.get(key)}) if q in text))


def _guard(original, candidate, before, after, invalid, state, outline, planned):
    if candidate == original:
        return "unchanged_prose"
    old, new = _paragraph_spans(original), _paragraph_spans(candidate)
    if not old or len(old) != len(new):
        return "paragraph_count_changed"
    if original[old[-1][0] : old[-1][1]] != candidate[new[-1][0] : new[-1][1]]:
        return "last_paragraph_changed"
    if any(q not in candidate for q in _facts(before, original)):
        return "lost_grounded_fact"
    count = chinese_count(candidate)
    low, high = outline.get("word_range") or (0, float("inf"))
    if not low <= count <= high or not 0.9 * chinese_count(original) <= count <= 1.1 * chinese_count(original):
        return "word_count_out_of_range"
    if clean_prose(candidate)[0] != candidate:
        return "prose_hygiene_failed"
    if any(
        abs(candidate.count(a) - candidate.count(b)) > abs(original.count(a) - original.count(b))
        for a, b in ("“”", "「」", "『』")
    ):
        return "quote_balance"
    if invalid or not isinstance(after, dict):
        return "state_recheck_failed"
    # The extractor's actual type alone: chapter_type(after) would let planning metadata win.
    if _type(after) != planned:
        return "after_type_mismatch"
    payoff = before.get("payoff") or {}
    if (
        isinstance(payoff, dict)
        and payoff.get("fulfilled") is True
        and isinstance(payoff.get("quote"), str)
        and payoff["quote"]
        and payoff["quote"] in original
    ):
        checked = after.get("payoff") or {}
        if (
            not isinstance(checked, dict)
            or checked.get("fulfilled") is not True
            or not checked.get("quote")
            or checked["quote"] not in candidate
        ):
            return "lost_grounded_payoff"
    if high_confidence(state, after.get("events", []), candidate):
        return "hard_fact_conflict"
    return None


def _targets(text):
    spans = _paragraph_spans(text)
    indexes = list(range(1, len(spans)))  # Last paragraph is always read-only.
    if len(indexes) > 16:
        indexes = sorted({indexes[round(i * (len(indexes) - 1) / 15)] for i in range(16)})
    return [dict(id=i, original=text[spans[i - 1][0] : spans[i - 1][1]]) for i in indexes]


def _candidate(text, response, targets):
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not edits:
        return None, [], "invalid_edits"
    allowed = {row["id"]: row["original"] for row in targets}
    seen, changed = set(), []
    for edit in edits:
        if not isinstance(edit, dict):
            return None, edits, "invalid_edit_target"
        pid, replacement = edit.get("id"), edit.get("text")
        if (
            type(pid) is not int
            or pid not in allowed
            or pid in seen
            or edit.get("original") != allowed[pid]
            or not isinstance(replacement, str)
            or not replacement.strip()
            or len(_paragraph_spans(replacement)) != 1
        ):
            return None, edits, "invalid_edit_target"
        seen.add(pid)
        if replacement.strip() != allowed[pid]:
            changed.append(edit)
    if not changed:
        return None, edits, "unchanged_prose"
    if len(changed) < min(8, len(targets)):
        return None, edits, "insufficient_main_scene_edits"
    candidate, spans = text, _paragraph_spans(text)
    for edit in sorted(changed, key=lambda row: row["id"], reverse=True):
        start, end = spans[edit["id"] - 1]
        candidate = candidate[:start] + edit["text"].strip() + candidate[end:]
    return candidate, edits, None


def repair_chapter_type(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Drop-in replacement for repair_relationship_rhythm, not an additional second pass.

    A separate attempt record is written before calling a model. Existing prose, steps and
    chapter records are untouched; even a rolled-back attempt cannot spend another repair.
    """
    planned, source = planned_type_source(outline)
    observed = _type(raw)
    note = dict(
        status="unchanged",
        planned_type=planned,
        planned_type_source=source,
        before_type=observed,
        after_type=observed,
        observed_type_source="extraction.type",
        edits=[],
    )
    if failed:
        return text, raw, failed, dict(note, status="skipped", reason="unreliable_extraction")
    if planned not in CANONICAL_TYPES or not observed:
        return text, raw, failed, dict(note, status="skipped", reason="unknown_type")
    if observed == planned:
        return text, raw, failed, note
    if planned == "情感" and not relationship_contract(outline):
        return text, raw, failed, dict(note, status="skipped", reason="missing_relationship_contract")
    directory = _directory(client)
    attempt = directory / f"type-attempt-{outline.get('number', 0):03d}.json" if directory else None
    owner = client if client is not None else ask
    attempts = getattr(owner, "_rhythm_type_attempts", {})
    if not hasattr(owner, "_rhythm_type_attempts"):
        owner._rhythm_type_attempts = attempts
    number = outline.get("number", prefix)
    source_value = dict(
        text=text,
        raw=deepcopy(raw),
        state=deepcopy(state),
        outline=deepcopy(outline),
        bible=deepcopy(bible),
        failed=failed,
    )
    previous = load(attempt) if attempt and attempt.exists() else attempts.get(number)
    if previous and previous.get("status") not in ("attempting", "waiting"):
        if previous.get("source") == source_value and previous.get("status") == "checked":
            saved = previous["result"]
            reason = _guard(text, saved["text"], raw, saved["raw"], saved["failed"], state, outline, planned)
            if not reason:
                return (saved["text"], deepcopy(saved["raw"]), saved["failed"], dict(saved["note"], reused=True))
            return text, raw, failed, dict(note, status="rolled_back", reason="saved_result_invalid:" + reason)
        return text, raw, failed, dict(note, status="skipped", reason="already_attempted")
    if previous and previous.get("source") != source_value:
        return text, raw, failed, dict(note, status="skipped", reason="attempt_source_changed")

    def finish(changed, checked, bad, detail):
        value = dict(
            source=source_value,
            status=detail["status"],
            result=dict(text=changed, raw=checked, failed=bad, note=detail),
        )
        attempts[number] = deepcopy(value)
        if attempt:
            save(attempt, value)
        return changed, checked, bad, detail

    finish(text, raw, failed, dict(note, status="attempting"))

    def bounded_ask(*args, **kwargs):
        return ask_once(ask, *args, **kwargs)

    try:
        if planned == "情感":
            from .relationship_rhythm import repair_relationship_rhythm

            candidate, checked, bad, detail = repair_relationship_rhythm(
                client,
                prefix,
                text,
                deepcopy(raw),
                failed,
                state,
                bible,
                outline,
                extract,
                bounded_ask,
                protected_tail=True,
            )
            note.update(detail, planned_type=planned, planned_type_source=source)
            if candidate == text:
                if note.get("status") == "checked":
                    note.update(status="rolled_back", reason="unchanged_prose")
                return finish(text, raw, failed, note)
            reason = _guard(text, candidate, raw, checked, bad, state, outline, planned)
        else:
            targets = _targets(text)
            if not targets:
                return finish(text, raw, failed, dict(note, status="skipped", reason="no_editable_main_scene"))
            response = precise_ask(
                bounded_ask,
                client,
                prefix + "-chapter-type-repair",
                "只替换给出的主场景段，至少改8段（可改不足8则全改），用自然场面落实指定章型，不能只改标签。"
                + FOCUS[planned]
                + "严格保留人物、地点、已经发生的关键事实、兑现及代价。末段逐字不动，"
                "不得加段、不得新增世界规则或改写已完成结果，字数在原稿±10%及指定范围内。",
                [
                    (
                        "规划章型与两场义务",
                        dict(type=planned, scenes=outline.get("scenes"), word_range=outline.get("word_range")),
                    ),
                    ("可改主场景段", targets),
                    ("关键事实与兑现原句只读", _facts(raw, text)),
                    ("完整正文只读", text),
                ],
                '{"edits":[{"id":1,"original":"逐字原段","text":"替换后的单个自然段"}]}',
                cap=6500,
                required_sources=[row["original"] for row in targets],
            )
            if isinstance(response, dict) and response.get("rhythm_model_failure"):
                return finish(
                    text,
                    raw,
                    failed,
                    dict(note, status="rolled_back", reason="source_model_failed:" + response["rhythm_model_failure"]),
                )
            if isinstance(response, dict) and (response.get("context_unknown") or response.get("status") == "skipped"):
                return finish(text, raw, failed, dict(note, status="skipped", reason="incomplete_source_context"))
            candidate, edits, reason = _candidate(text, response, targets)
            note.update(edits=edits)
            if reason:
                return finish(text, raw, failed, dict(note, status="rolled_back", reason=reason))
            # Cheap guards run before an additional extraction stage.
            reason = _guard(text, candidate, raw, raw, False, state, outline, observed)
            if reason:
                return finish(text, raw, failed, dict(note, status="rolled_back", reason=reason))
            checked, bad, _ = extract(
                client, prefix + "-chapter-type-final-state", candidate, state, bible, outline, [], final=True
            )
            reason = _guard(text, candidate, raw, checked, bad, state, outline, planned)
        note["after_type"] = _type(checked)
        if reason:
            return finish(text, raw, failed, dict(note, status="rolled_back", reason=reason))
        return finish(candidate, checked, False, dict(note, status="checked", adopted=True))
    except RhythmCallLimit:
        return finish(
            text, raw, failed, dict(note, status="rolled_back", reason="source_model_failed:response_limit_exhausted")
        )
    except (QuotaExhausted, ProjectError) as exc:
        finish(text, raw, failed, dict(note, status="waiting", reason="source_model_failed:" + type(exc).__name__))
        raise
    except InterruptedError:
        finish(text, raw, failed, dict(note, status="waiting", reason="source_model_interrupted"))
        raise
    except (ValueError, TypeError) as exc:
        return finish(
            text, raw, failed, dict(note, status="rolled_back", reason="source_model_failed:" + type(exc).__name__)
        )
