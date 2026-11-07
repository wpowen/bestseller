"""Deterministic chapter-ending and due-payoff candidates for instruction 42."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import re

import yaml

from .endings import apply_tail
from .storage import save
from .text import _paragraph_spans, chinese_count


_RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/ending_rules.yaml").read_text())["momentum"]
ACTIVE_TYPES = tuple(_RULES["active_types"])
FLAT_TYPE = _RULES["flat_type"]


def _paragraphs(text):
    if not isinstance(text, str):
        return []
    return [text[a:b].strip() for a, b in _paragraph_spans(text) if text[a:b].strip()]


def diagnose_ending(text):
    """Return a conservative candidate from only the final three paragraphs."""
    paragraphs = _paragraphs(text)
    tail = "\n\n".join(paragraphs[-3:])
    types = [kind for kind, pattern in _RULES["signals"].items() if re.search(pattern, tail)]
    terminal = paragraphs[-1] if paragraphs else ""
    explicit_flat = bool(terminal and re.search(_RULES["flat_terminal"].strip(), terminal))
    return dict(
        classification="hook_candidate" if types else "flat_candidate",
        types=types,
        explicit_flat=explicit_flat,
        tail=tail,
        paragraph_count=min(3, len(paragraphs)),
        status="Inference",
        basis="deterministic_last_2_3_paragraph_candidate",
    )


def payoff_evidence(text, metadata):
    """Require an exact saved quote before a due payoff counts as fulfilled."""
    metadata = metadata if isinstance(metadata, dict) else {}
    due = metadata.get("payoff_due") is True
    if not due:
        return dict(classification="not_due", status="Evidence", quote=None)
    payoff = metadata.get("payoff") if isinstance(metadata.get("payoff"), dict) else {}
    quote = payoff.get("quote") if isinstance(payoff.get("quote"), str) else ""
    grounded = bool(quote.strip() and quote.strip() in text)
    saved_fulfilled = metadata.get("payoff_fulfilled")
    fulfilled = saved_fulfilled is True if isinstance(saved_fulfilled, bool) else payoff.get("fulfilled") is True
    return dict(
        classification="fulfilled" if grounded and fulfilled else "unfulfilled",
        status="Evidence" if grounded and fulfilled else "Unknown",
        quote=quote if grounded else None,
        reason=None if grounded and fulfilled else "missing_grounded_payoff_quote",
    )


def book_momentum(chapters):
    endings, payoffs = [], []
    for chapter in chapters:
        ending = diagnose_ending(chapter.get("text", ""))
        endings.append(dict(chapter=chapter.get("number"), **ending))
        payoff = payoff_evidence(chapter.get("text", ""), chapter.get("meta", {}))
        payoffs.append(dict(chapter=chapter.get("number"), **payoff))
    flat = [row["chapter"] for row in endings if row["classification"] == "flat_candidate"]
    due = [row for row in payoffs if row["classification"] != "not_due"]
    fulfilled = [row for row in due if row["classification"] == "fulfilled"]
    return dict(
        reference_only=True,
        ending=dict(
            flat_count=len(flat),
            flat_chapters=flat,
            checks=endings,
            status="Inference",
            limitation="Deterministic candidates are not reader-quality conclusions.",
        ),
        payoff=dict(
            due=len(due),
            fulfilled=len(fulfilled),
            rate=len(fulfilled) / len(due) if due else None,
            unfulfilled_chapters=[row["chapter"] for row in due if row["classification"] == "unfulfilled"],
            checks=payoffs,
            status="Evidence" if due else "Unknown",
        ),
    )


def schedule_ending_types(start, end, *, stage_end=None, recent=(), prefer_flat_close=False):
    """Rotate concrete ending contracts; no active type occurs over twice per stage."""
    seen = [row.get("ending_type") for row in recent if row.get("ending_type") in ACTIVE_TYPES]
    counts = Counter(seen)
    previous = seen[-1] if seen else None
    result = []
    for number in range(start, end + 1):
        if prefer_flat_close and number == stage_end:
            kind = FLAT_TYPE
        else:
            choices = [kind for kind in ACTIVE_TYPES if kind != previous and counts[kind] < 2]
            if not choices:
                choices = [kind for kind in ACTIVE_TYPES if kind != previous]
            kind = min(choices, key=lambda value: (counts[value], ACTIVE_TYPES.index(value)))
        result.append(dict(number=number, ending_type=kind))
        if kind in ACTIVE_TYPES:
            counts[kind] += 1
        previous = kind
    return result


def _brief(value, default, limit=60):
    text = re.split(r"[；。！？\n]", str(value or ""), maxsplit=1)[0].strip()
    return text[:limit] or default


def _fallback_content(chapter, kind):
    scene = next((row for row in reversed(chapter.get("scenes", [])) if isinstance(row, dict)), {})
    actor = _brief(next(iter(scene.get("characters", []) or []), "主角"), "主角", 20)
    place = _brief(scene.get("where") or chapter.get("title"), "末场", 40)
    obstacle = _brief(scene.get("obstacle"), "眼前冲突")
    choice = _brief(scene.get("choice") or scene.get("goal"), "处理眼前冲突")
    goal = _brief(scene.get("goal"), "保住本章目标")
    templates = {
        "迫近危险": f"{actor}发现{obstacle}仍在逼近，尚未脱险",
        "刚揭事实": f"{actor}这才发现，{obstacle}背后还有未说明的事实",
        "悬停动作": f"{actor}刚要{choice}，动作停在结果发生前",
        "两难选择": f"{actor}只能先在{goal}与{obstacle}之间当场选一项",
        "反常物件": f"{place}的既有物件忽然出现反常变化，{actor}尚未查明原因",
        "关系裂痕": f"{actor}没有回答关于{choice}的追问，关系留下裂痕",
        FLAT_TYPE: f"{actor}完成本阶段行动，在{place}平静收束",
    }
    return templates[kind]


def apply_ending_contract(chapters, *, stage_end=None):
    """Normalize plan fields before prose generation, retaining model-specific content."""
    result, counts = [], Counter()
    previous = None
    for chapter in deepcopy(chapters):
        number = chapter.get("number")
        kind = chapter.get("ending_type")
        content = chapter.get("ending_content") or chapter.get("hook") or ""
        content = re.sub(r"^章末留下什么：[^；]+；具体内容：", "", str(content)).strip().rstrip("。")
        detected = diagnose_ending(content)["types"]
        invalid_flat = kind == FLAT_TYPE and number != stage_end
        if kind not in (*ACTIVE_TYPES, FLAT_TYPE) or invalid_flat or kind == previous or counts[kind] >= 2:
            eligible = [value for value in detected if value != previous and counts[value] < 2]
            kind = eligible[0] if eligible else schedule_ending_types(number, number, recent=result)[0]["ending_type"]
        if not content or kind in ACTIVE_TYPES and kind not in detected:
            content = _fallback_content(chapter, kind)
        chapter.update(
            ending_type=kind,
            ending_content=content,
            hook=f"章末留下什么：{kind}；具体内容：{content}。只用本章既有人物、冲突和物件落实。",
        )
        counts[kind] += 1
        previous = kind
        result.append(chapter)
    return result


def _apply_patch(text, value):
    spans = _paragraph_spans(text)
    after = value.get("after") if isinstance(value, dict) else None
    patch = value.get("text") if isinstance(value, dict) else None
    if type(after) is not int or not 1 <= after <= len(spans):
        return text, False
    if not isinstance(patch, str) or not patch.strip() or "\n" in patch or len(patch) > 500:
        return text, False
    offset = spans[after - 1][1]
    return text[:offset] + "\n\n" + patch.strip() + text[offset:], True


def _tail_patch(value):
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str):
        paragraphs = _paragraphs(value[0])
        if len(paragraphs) > 2:
            return paragraphs[-2:]
    return value


def _within_saved_maximum(text, outline):
    word_range = outline.get("word_range")
    return not (
        isinstance(word_range, (list, tuple))
        and len(word_range) == 2
        and isinstance(word_range[1], (int, float))
        and chinese_count(text) > word_range[1]
    )


def _extract(extract_state, client, name, text, state, bible, outline, rows):
    return extract_state(client, name, text, state, bible, outline, rows, final=True)


def _next_outline(client, number):
    out = getattr(client, "out", None)
    if not out:
        return {}
    direct = Path(out) / f"writing_outlines/{number + 1:03d}.json"
    if direct.is_file():
        return json.loads(direct.read_text())
    reports = sorted((Path(out) / "outline-diversity").glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in reversed(reports):
        report = json.loads(path.read_text())
        planned = report.get("planned_chapters", report.get("chapters", []))
        found = next((row for row in planned if row.get("number") == number + 1), None)
        if found:
            return found
    return {}


def repair_after_draft(
    client,
    prefix,
    text,
    raw,
    failed,
    state,
    bible,
    outline,
    next_outline,
    rows,
    extract_state,
    ask,
):
    """Attempt one bounded payoff patch and one two-paragraph tail repair."""
    if not outline.get("ending_type"):
        return text, raw, failed, {"status": "not_applicable_missing_ending_contract"}
    next_outline = next_outline or _next_outline(client, outline.get("number", 0))
    note = dict(
        payoff={"status": "not_due"},
        hook={"status": "kept"},
        before=dict(
            ending=diagnose_ending(text), payoff=payoff_evidence(text, dict(raw, payoff_due=outline.get("payoff_due")))
        ),
        next_outline_number=next_outline.get("number"),
    )
    current, observed = text, raw
    payoff_meta = dict(observed, payoff_due=outline.get("payoff_due") is True)
    if outline.get("payoff_due") is True:
        if payoff_evidence(current, payoff_meta)["classification"] == "fulfilled":
            note["payoff"] = {"status": "kept"}
        else:
            answer = ask(
                client,
                prefix + "-momentum-payoff-repair",
                "只补一处本章应兑现的具体收获、反杀或揭示；不改其他段落，不新增人物、主线或规则。",
                [("正文", current), ("章纲兑现要求", outline), ("下一章章纲，只读防冲突", next_outline)],
                json.dumps({"patch": {"after": 1, "text": ""}}, ensure_ascii=False),
                cap=1200,
                input_limit=10000,
                max_attempts=1,
            )
            candidate, changed = _apply_patch(current, answer.get("patch") if isinstance(answer, dict) else None)
            candidate_raw, candidate_failed, _ = _extract(
                extract_state, client, prefix + "-momentum-payoff-recheck", candidate, state, bible, outline, rows
            )
            candidate_meta = dict(candidate_raw, payoff_due=True)
            if (
                changed
                and _within_saved_maximum(candidate, outline)
                and not candidate_failed
                and payoff_evidence(candidate, candidate_meta)["classification"] == "fulfilled"
            ):
                current, observed = candidate, candidate_raw
                note["payoff"] = {"status": "repaired"}
            else:
                note["payoff"] = {"status": "rolled_back", "reason": "payoff_recheck_unknown"}

    diagnosis = diagnose_ending(current)
    payoff_before_hook = payoff_evidence(current, dict(observed, payoff_due=outline.get("payoff_due")))
    if (
        diagnosis["classification"] == "flat_candidate"
        and not outline.get("ending")
        and outline.get("hook")
        and outline.get("ending_type") != FLAT_TYPE
    ):
        answer = ask(
            client,
            prefix + "-momentum-hook-repair",
            "只重写正文最后两段，落实章纲指定的具体未解决钩子；不新增人物、主线或规则，不提前写下一章。",
            [("正文最后三段", diagnosis["tail"]), ("章末要求", outline.get("hook")), ("下一章章纲", next_outline)],
            json.dumps({"tail": ["", ""]}, ensure_ascii=False),
            cap=1200,
            input_limit=10000,
            max_attempts=1,
        )
        candidate, changed = apply_tail(current, _tail_patch(answer.get("tail") if isinstance(answer, dict) else None))
        recheck = diagnose_ending(candidate)
        if changed and _within_saved_maximum(candidate, outline) and recheck["classification"] == "hook_candidate":
            candidate_raw, candidate_failed, _ = _extract(
                extract_state, client, prefix + "-momentum-hook-recheck", candidate, state, bible, outline, rows
            )
            candidate_payoff = payoff_evidence(candidate, dict(candidate_raw, payoff_due=outline.get("payoff_due")))
            payoff_preserved = (
                payoff_before_hook["classification"] != "fulfilled" or candidate_payoff["classification"] == "fulfilled"
            )
            if not candidate_failed and payoff_preserved:
                current, observed = candidate, candidate_raw
                note["hook"] = {"status": "repaired", "types": recheck["types"]}
            else:
                reason = "state_recheck_failed" if candidate_failed else "payoff_regressed"
                note["hook"] = {"status": "rolled_back", "reason": reason}
        else:
            note["hook"] = {"status": "rolled_back", "reason": "hook_recheck_flat_or_mismatched"}
    note["after"] = dict(
        ending=diagnose_ending(current),
        payoff=payoff_evidence(current, dict(observed, payoff_due=outline.get("payoff_due"))),
    )
    if getattr(client, "out", None):
        save(Path(client.out) / "steps" / f"{prefix}-momentum.json", note)
    return current, observed, failed, note
