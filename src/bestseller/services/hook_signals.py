"""Shared hook-signal vocabulary for end-of-scene / end-of-chapter hook detection.

Three deterministic hook evaluators historically each carried their own
hand-tuned ``_HOOK_TERMS`` list:

* ``qimao_opening_gate`` (chapter-end ``weak_hook`` gate),
* ``reviews`` (scene-level ``hook_strength`` scorer),
* ``whole_book_quality_gate`` (``chapter_hook_missing`` gate).

Those lists drifted apart and — more importantly — only recognised a narrow
band of "suspense prop" vocabulary (footsteps, phones, alarms, doors). They were
blind to the highest-value Chinese web-novel hook classes, producing false
negatives: an ending that sets up an **appointment / time-bomb** ("今晚十一点半，
地铁三号线末班"), issues a **threat / pursuit** ("他们会找到你的"), or lands an
**open identity question** ("你是什么") was scored ``weak_hook`` because none of
those phrasings appeared in the whitelist.

This module is the single source of truth for the broadened, genre-neutral hook
vocabulary. Each evaluator merges :data:`SHARED_HOOK_TERMS` into its existing
list (kept additive so nothing that used to pass stops passing) and may call
:func:`tail_contains_hook` for the regex-aware check.
"""

from __future__ import annotations

import re

# ── Appointment / time-bomb: a concrete future commitment pulls the reader on ──
HOOK_TERMS_APPOINTMENT: tuple[str, ...] = (
    "今晚",
    "今夜",
    "明晚",
    "明天",
    "后天",
    "凌晨",
    "午夜",
    "半夜",
    "之前",
    "截止",
    "限你",
    "限期",
    "期限",
    "末班",
    "老地方",
    "见面",
    "赴约",
    "等你",
    "来换",
    "来取",
    "倒数",
    "还剩",
    "只剩",
    "约定",
    "见一面",
)

# ── Threat / pursuit: an antagonistic force is closing in ────────────────────
HOOK_TERMS_THREAT: tuple[str, ...] = (
    "会找到你",
    "找上门",
    "找到你",
    "跑不掉",
    "别想逃",
    "逃不掉",
    "盯上",
    "盯上了",
    "已经知道",
    "后果自负",
    "留给你",
    "等着你",
    "不会放过",
    "追查",
    "追踪",
    "追杀",
    "追上来",
    "锁定",
    "威胁",
    "警告",
    "暴露",
    "被发现",
)

# ── Open question: an identity/causal question, even without a "？" char ──────
HOOK_TERMS_OPEN_QUESTION: tuple[str, ...] = (
    "到底",
    "究竟",
    "是什么",
    "是谁",
    "为什么",
    "怎么会",
    "凭什么",
    "难道",
    "难不成",
    "什么人",
)

# Concrete clock / countdown patterns (an explicit deadline is a strong hook).
HOOK_TIME_PATTERN = re.compile(
    r"(\d{1,2}\s*[:：]\s*\d{2})"  # 11:30
    r"|(\d{1,2}\s*点(半|钟)?)"  # 十一点半 / 11点
    r"|(\d+\s*分钟(内|后|之内)?)"  # 30分钟内
    r"|(\d+\s*小时(内|后|之内)?)"  # 3小时后
)

# Union used by callers to extend their own term lists. Kept as a tuple so it can
# be concatenated onto existing ``_HOOK_TERMS`` tuples without dedup surprises.
SHARED_HOOK_TERMS: tuple[str, ...] = (
    *HOOK_TERMS_APPOINTMENT,
    *HOOK_TERMS_THREAT,
    *HOOK_TERMS_OPEN_QUESTION,
)


def tail_contains_hook(text: str, *, extra_terms: tuple[str, ...] = ()) -> bool:
    """Return True if ``text`` contains any broadened hook signal.

    Checks (a) the shared appointment/threat/open-question vocabulary, (b) any
    caller-supplied ``extra_terms`` (e.g. genre-specific or the caller's legacy
    list), (c) an explicit clock/countdown pattern, and (d) a trailing question
    mark. Callers are expected to pass only the *tail* window of the text so a
    mid-scene mention of "明天" doesn't count as an ending hook.
    """
    if not text:
        return False
    haystack = str(text)
    for term in (*SHARED_HOOK_TERMS, *extra_terms):
        if term and term in haystack:
            return True
    if HOOK_TIME_PATTERN.search(haystack):
        return True
    return haystack.rstrip().endswith(("？", "?", "！", "!"))
