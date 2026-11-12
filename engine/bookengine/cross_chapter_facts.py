"""Quote-grounded facts that later prose and repair text must not rewrite."""

from copy import deepcopy
import json
import re

from .precision import _sentences


_ACTIONS = (
    "反接",
    "斩断",
    "切断",
    "夺回",
    "救出",
    "脱险",
    "抓获",
    "被擒",
    "击败",
    "杀死",
    "死亡",
    "摧毁",
    "开启",
    "关闭",
    "失去",
    "获得",
)
_NEGATION = r"(?:并非|不是|没有|并未|从未|并没有|未曾)"
_NON_COMPLETION = re.compile(
    r"若|如果|一旦|倘若|试图|想要|打算|准备|可能|或许|是否|能否|本可以|必须|需要|要让|应当|务必"
)


def _quote(row, text):
    quote = row.get("quote") if isinstance(row, dict) else None
    return quote.strip() if isinstance(quote, str) and quote.strip() and quote.strip() in text else ""


def merge_cross_chapter_delta(state, delta, text, chapter):
    """Merge only critical events backed by a verbatim quote in this chapter."""
    result = deepcopy(state) if isinstance(state, dict) else {}
    ledger = deepcopy(result.get("continuity_ledger")) if isinstance(result.get("continuity_ledger"), dict) else {}
    saved = ledger.get("critical_events") if isinstance(ledger.get("critical_events"), list) else []
    delta = delta if isinstance(delta, dict) else {}
    for row in delta.get("critical_events", []) if isinstance(delta.get("critical_events"), list) else []:
        if not isinstance(row, dict) or not (quote := _quote(row, text)):
            continue
        summary = row.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        item = {"summary": summary.strip()[:100], "chapter": chapter, "quote": quote}
        if not any(
            old.get("chapter") == chapter and old.get("quote") == quote for old in saved if isinstance(old, dict)
        ):
            saved.append(item)
    ledger["critical_events"] = saved[-80:]
    result["continuity_ledger"] = ledger
    return result


def critical_events(state):
    ledger = state.get("continuity_ledger") if isinstance(state, dict) else None
    rows = ledger.get("critical_events") if isinstance(ledger, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and row.get("quote") and row.get("chapter")]


def _bigrams(text):
    compact = re.sub(r"[^\u4e00-\u9fff]", "", text)
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _negates_action(text, action):
    return bool(re.search(rf"(?:并非|并不是)[^。！？]{{0,16}}{re.escape(action)}[^。！？]{{0,30}}而是", text))


def _same_action_subject(previous, current, action):
    if action == "反接":
        return "线" in previous and "线" in current and "控" in previous and "控" in current
    return len((_bigrams(previous) & _bigrams(current)) - _bigrams(action)) >= 2


def _contradicts(previous, current):
    for action in _ACTIONS:
        if action not in previous or action not in current:
            continue
        if not _negates_action(current, action):
            continue
        if _negates_action(previous, action) or _NON_COMPLETION.search(previous):
            continue
        if _same_action_subject(previous, current, action):
            return action
    return ""


def deterministic_rewrite_findings(original, candidate, state, chapter):
    """Find explicit denials added by a repair against quote-grounded prior events."""
    added = [candidate[start:end] for start, end in _sentences(candidate) if candidate[start:end] not in original]
    findings = []
    for current in added:
        for event in critical_events(state):
            if event["chapter"] >= chapter or not _contradicts(event["quote"], current):
                continue
            findings.append(
                {
                    "previous_chapter": event["chapter"],
                    "previous_quote": event["quote"],
                    "current_quote": current,
                    "reason": "修复文字显式否定前文已完成的行动或结果",
                    "status": "Evidence",
                }
            )
    return findings


def review_repair_continuity(ask, client, prefix, original, candidate, state, chapter):
    """Recheck repair-only prose against prior events; accept only grounded findings."""
    facts = critical_events(state)
    deterministic = deterministic_rewrite_findings(original, candidate, state, chapter)
    if not facts:
        return deterministic
    response = ask(
        client,
        prefix + "-conflict-continuity-recheck",
        "只核对修复新增或替换的文字是否否定、改写前文已经发生的行动或结果。"
        "只报告确定冲突，逐字引用前文和本章；新增解释不得变成揭示另一种过去。",
        [
            ("前文已发生的关键事件", json.dumps(facts, ensure_ascii=False)),
            ("修复前正文", original),
            ("修复候选正文", candidate),
        ],
        '{"findings":[{"previous_chapter":1,"previous_quote":"前文逐字引文","current_quote":"本章逐字引文","reason":"原因"}]}',
        cap=1800,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    rows = response.get("findings", []) if isinstance(response, dict) else []
    grounded = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        previous = next(
            (
                event
                for event in facts
                if row.get("previous_chapter") == event.get("chapter")
                and row.get("previous_quote") == event.get("quote")
            ),
            None,
        )
        current = row.get("current_quote")
        if (
            previous
            and isinstance(current, str)
            and current.strip()
            and current in candidate
            and current not in original
        ):
            grounded.append({**row, "status": "agent_editorial_not_human"})
    unique = {
        (row["previous_chapter"], row["previous_quote"], row["current_quote"]): row
        for row in [*deterministic, *grounded]
    }
    return list(unique.values())


def guard_all_repairs(ask, client, prefix, original, candidate, state, chapter):
    """Keep the draft when any later repair rewrites a quote-grounded prior event."""
    if candidate == original:
        return candidate, {"status": "unchanged", "findings": []}
    findings = review_repair_continuity(ask, client, prefix, original, candidate, state, chapter)
    if findings:
        return original, {"status": "rolled_back", "reason": "rewrites_prior_event", "findings": findings}
    return candidate, {"status": "checked", "findings": []}


def cross_chapter_checkup(chapters):
    """Offline evidence for explicit later denials of an earlier completed action."""
    history = []
    issues = []
    for chapter in sorted(chapters, key=lambda row: row.get("number", 0)):
        number, text = chapter.get("number", 0), chapter.get("text", "")
        for start, end in _sentences(text):
            current = text[start:end]
            match = next(
                (old for old in reversed(history) if old["chapter"] < number and _contradicts(old["quote"], current)),
                None,
            )
            if match:
                issues.append(
                    {
                        "chapters": [match["chapter"], number],
                        "previous_quote": match["quote"],
                        "quote": current,
                        "status": "Evidence",
                    }
                )
            if (
                any(action in current for action in _ACTIONS)
                and not any(_negates_action(current, action) for action in _ACTIONS)
                and not _NON_COMPLETION.search(current)
            ):
                history.append({"chapter": number, "quote": current})
    return {
        "counts": {"rewritten_prior_events": len(issues)},
        "rewritten_prior_events": issues,
        "authority": "deterministic_explicit_denial",
    }
