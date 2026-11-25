"""Shared placement and fact guards for model-suggested single-paragraph insertions."""

import re

from .text import _paragraph_spans


_CLOSING_DENIAL = re.compile(
    r"(?<![是要莫岂])(?:不是|并非)(?:(?!而是|却是|[，,]正?是)[^。！？!?；;])*[。！!…—]*[”’」』]?$"
)
_CLOSING_ANSWER = re.compile(r"^[“‘「『]?(?:而是|正是|是(?![夜日时以啊的][，,。！!]|否|吗|不是))[^，,。！？!?；;：:\s]")
_IDENTITY_TRANSITIONS = re.compile(
    r"(?P<subject>[\u4e00-\u9fffA-Za-z·]{1,8})[^。！？；\n]{0,8}"
    r"(?P<verb>正式(?:登记|成为)|登记为|成为|升为|晋升为|册立为|封为|授予[^。！？；\n]{0,6}身份)"
    r"(?P<role>[\u4e00-\u9fffA-Za-z0-9·]{1,16})"
)
_QUOTED_IDENTITY = re.compile(
    r"(?P<verb>添名|登记)[^。！？；\n]{0,12}[“\"'](?P<subject>[\u4e00-\u9fffA-Za-z·]{1,8})"
    r"[，,:：](?P<role>[\u4e00-\u9fffA-Za-z0-9·]{1,16})[”\"']"
)
_ACTION = re.compile(
    r"(?P<left>[\u4e00-\u9fffA-Za-z·]{1,12})(?P<gap>[^。！？；\n]{0,8})"
    r"(?P<verb>抢回|夺回|夺得|拿回|取回|救出|击败|击退|制服|擒住|认输|封住|解开|夺取|收复)"
    r"(?P<object>[\u4e00-\u9fffA-Za-z0-9·]{1,12})"
)
_INCAPACITATION = re.compile(
    r"(?P<left>[\u4e00-\u9fffA-Za-z·]{1,12})(?P<gap>[^。！？；\n]{0,8})"
    r"(?P<verb>倒下|昏厥|失去意识|被擒|跪地|死去|死亡|毙命|断气)"
)
_TECHNIQUE = re.compile(
    r"(?:以|用|使出|施展|催动|运起|施以|挥出|发动|祭出)"
    r"(?P<name>[\u4e00-\u9fffA-Za-z·]{2,12})(?P<kind>式|诀|功|法|剑|掌|刀|枪|印|起手|术)"
)
_NEGATION = re.compile(r"(?:没有|没能|未能|未曾|尚未|并未|不曾|不再|不要|不能|未|没|不)")
_INTENTION = re.compile(r"(?:打算|准备|想要|欲|计划|决定|将要|要去|试图|意图)")
_REWARD_PAYOFF_TYPES = frozenset(("得到回报", "收获", "得到收获", "资源", "物品"))
_ORDINARY_RETRIEVAL_ACTIONS = frozenset(("拿回", "取回"))
_SKIP_KEYS = frozenset(
    (
        "patch",
        "body",
        "edits",
        "scene",
        "core_application",
        "core_development",
        "growth_target",
        "ability_use",
    )
)


def _collect_strings(value, *, key=""):
    """Collect strings for diagnostics/plans, never proposed patch or future-core fields."""
    if key in _SKIP_KEYS:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child_key, child in value.items() for item in _collect_strings(child, key=child_key)]
    if isinstance(value, list):
        return [item for child in value for item in _collect_strings(child)]
    return []


def _current_evidence(text, raw):
    """Use the saved body as evidence; extraction summaries cannot self-attest."""
    return [text]


def _sentence_at(text, start, end=None):
    end = len(text) if end is None else end
    left = max(text.rfind(mark, 0, start) for mark in "。！？；\n") + 1
    right_candidates = [pos for mark in "。！？；\n" if (pos := text.find(mark, end)) >= 0]
    right = min(right_candidates, default=len(text))
    return text[left:right]


def _affirmative(text, start, end):
    """Quoted questions, negations, and intentions are not completed events."""
    sentence = _sentence_at(text, start, end)
    marker = text[start:end]
    marker_start = sentence.find(marker)
    local = sentence[max(0, marker_start - 12) : marker_start] if marker_start >= 0 else ""
    if _NEGATION.search(marker) or _INTENTION.search(marker):
        return False
    if any(mark in sentence for mark in ("“", "‘", '"')) and max(
        sentence.rfind(mark) for mark in ("“", "‘", '"')
    ) > max(sentence.rfind(mark) for mark in ("”", "’", '"')):
        return False
    if "？" in sentence or "吗" in local or "吗" in marker:
        return False
    return not _NEGATION.search(local) and not _INTENTION.search(local)


def _event_supported(text, subject, verb, obj):
    """Require the same subject, verb, and object in one affirmative body clause."""
    pattern = re.compile(rf"{re.escape(subject)}[^。！？；\n]{{0,8}}{re.escape(verb)}{re.escape(obj)}")
    for match in pattern.finditer(text):
        if _affirmative(text, match.start(), match.end()):
            return True
    return False


def _identity_supported(text, subject, role):
    """Recognize an already established identity without requiring transition wording."""
    pattern = re.compile(
        rf"(?:{re.escape(subject)}[^。！？；\n]{{0,6}}(?:是|为|乃|属|列为|登记为)"
        rf"[^。！？；\n]{{0,4}}{re.escape(role)}|{re.escape(role)}"
        rf"[^，,。！？；\n]{{0,4}}{re.escape(subject)})"
    )
    for match in pattern.finditer(text):
        if not re.search(r"(?:不是|并非|非|尚未)", match.group(0)) and _affirmative(text, match.start(), match.end()):
            return True
    return False


def _future_plan_has(finding, future_outline):
    if not isinstance(future_outline, dict):
        return False
    needle = finding.get("quote", "")
    return bool(needle and any(needle in value for value in _collect_strings(future_outline)))


def _future_action_has(subject, verb, obj, future_outline):
    if not isinstance(future_outline, dict) or not obj:
        return False
    actions = "抢回|夺回|夺得|拿回|取回|救出|击败|击退|制服|擒住|认输|封住|解开|夺取|收复"
    pattern = re.compile(
        rf"(?:{actions})[^。！？；\n]{{0,10}}{re.escape(obj)}|"
        rf"{re.escape(obj)}[^。！？；\n]{{0,10}}(?:{actions})"
    )
    return any(pattern.search(value) for value in _collect_strings(future_outline))


def _identity_subject(match, text):
    if match.re is _QUOTED_IDENTITY:
        return match.group("subject")
    prefix = text[match.start() : match.start("verb")]
    prefix = re.sub(r"(?:正式|已经|当众|公开|随后|终于|仍然)+$", "", prefix)
    found = re.search(r"[\u4e00-\u9fffA-Za-z·]{1,8}$", prefix)
    return found.group(0) if found else match.group("subject")


def _explicit_reward_contract(match, outline):
    """A due reward may be created by the payoff repair itself.

    This narrow compatibility rule preserves the existing payoff contract: it
    does not authorize identity changes, new techniques, or incapacitation.
    """
    if not isinstance(outline, dict) or outline.get("payoff_due") is not True:
        return False
    if outline.get("payoff_type") not in _REWARD_PAYOFF_TYPES:
        return match.group("verb") in _ORDINARY_RETRIEVAL_ACTIONS
    return match.group("verb") in {"抢回", "夺回", "夺得", "拿回", "取回", "救出", "夺取", "收复"}


def patch_fact_findings(text, patch_text, raw=None, outline=None, future_outline=None):
    """Return affirmative event claims not evidenced by this chapter.

    Mentions, quoted questions, negations, and plans are ignored. A completed
    transition/action needs the same entity/object (or named technique) in the
    current body; unquoted state/summary/plan fields cannot prove completion.
    """
    if not isinstance(patch_text, str) or not patch_text.strip():
        return [{"kind": "empty_patch", "quote": ""}]
    findings = []
    evidence = "\n".join(_current_evidence(text, raw or {}))
    identity_matches = list(_IDENTITY_TRANSITIONS.finditer(patch_text)) + list(_QUOTED_IDENTITY.finditer(patch_text))
    for match in identity_matches:
        if not _affirmative(patch_text, match.start(), match.end()):
            continue
        subject, role = _identity_subject(match, patch_text), match.group("role")
        if subject and role and _identity_supported(evidence, subject, role):
            continue
        finding = dict(kind="identity_transition", quote=match.group(0), reason="not_grounded_in_current_chapter")
        if _future_plan_has(finding, future_outline):
            finding["reason"] = "planned_for_later_chapter"
        findings.append(finding)

    for pattern in (_ACTION, _INCAPACITATION):
        for match in pattern.finditer(patch_text):
            if not _affirmative(patch_text, match.start(), match.end()):
                continue
            subject = match.group("left")
            verb = match.group("verb")
            obj = (match.groupdict().get("object") or "").rstrip("后了着并又")
            future = _future_action_has(subject, verb, obj, future_outline)
            # Exact entity/object support is intentionally stricter than a keyword.
            if not future and (
                _event_supported(evidence, subject, verb, obj) or _explicit_reward_contract(match, outline)
            ):
                continue
            finding = dict(kind="conflict_resolution", quote=match.group(0), reason="not_grounded_in_current_chapter")
            if future or _future_plan_has(finding, future_outline):
                finding["reason"] = "planned_for_later_chapter"
            findings.append(finding)

    for match in _TECHNIQUE.finditer(patch_text):
        if not _affirmative(patch_text, match.start(), match.end()):
            continue
        name = match.group("name")
        if any(marker in name for marker in "的之在将把与并又"):
            continue
        if name in evidence:
            continue
        finding = dict(kind="unintroduced_technique", quote=match.group(0), reason="not_grounded_in_current_chapter")
        if _future_plan_has(finding, future_outline):
            finding["reason"] = "planned_for_later_chapter"
        findings.append(finding)
    return findings


def place_guarded_patch(
    text,
    value,
    *,
    raw=None,
    outline=None,
    future_outline=None,
    max_chars=500,
    keep_final=True,
):
    """Place a patch only when its milestone claims are grounded in this chapter."""
    candidate, placement = place_tail_bounded_patch(text, value, max_chars=max_chars, keep_final=keep_final)
    if placement["status"] != "placed":
        return text, placement
    findings = patch_fact_findings(
        text,
        value.get("text") if isinstance(value, dict) else None,
        raw,
        outline,
        future_outline,
    )
    if findings:
        return text, dict(placement, status="rejected", reason="future_fact", findings=findings)
    return candidate, dict(placement, status="placed", findings=[])


def place_tail_bounded_patch(text, value, *, max_chars=500, keep_final=True):
    """Append insertions after existing events and report any model-position relocation.

    keep_final leaves the last paragraph last, so an insertion never lands after the chapter's closing beat.
    """
    spans = _paragraph_spans(text)
    requested = value.get("after") if isinstance(value, dict) else None
    patch = value.get("text") if isinstance(value, dict) else None
    if type(requested) is not int or not 1 <= requested <= len(spans):
        return text, dict(status="invalid", reason="invalid_after", requested_after=requested)
    if not isinstance(patch, str) or not patch.strip() or "\n" in patch or len(patch) > max_chars:
        return text, dict(status="invalid", reason="invalid_patch_text", requested_after=requested)
    if keep_final and len(spans) == 1 and value.get("replace") is not True:
        return text, dict(status="invalid", reason="no_room_before_final", requested_after=requested)
    tail = len(spans) - 1 if keep_final and len(spans) > 1 else len(spans)
    effective = requested if value.get("replace") is True else tail
    start, end = spans[effective - 1]
    if value.get("replace") is True:
        changed = text[:start] + patch.strip() + text[end:]
    else:
        if effective < len(spans):
            next_start, next_end = spans[effective]
            if _CLOSING_DENIAL.search(text[start:end].rstrip()) and _CLOSING_ANSWER.match(
                text[next_start:next_end].lstrip()
            ):
                return text, dict(
                    status="rejected",
                    reason="splits_closing_reveal",
                    requested_after=requested,
                    effective_after=effective,
                )
        changed = text[:end] + "\n\n" + patch.strip() + text[end:]
    return changed, dict(
        status="placed",
        placement="requested"
        if effective == requested
        else "before_final"
        if effective < len(spans)
        else "relocated_to_tail",
        requested_after=requested,
        effective_after=effective,
    )
