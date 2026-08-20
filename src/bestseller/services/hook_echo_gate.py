"""Hook Echo Gate — enforce hook continuity between adjacent chapters.

The single biggest cause of catastrophic chapter-2 dropout (the 63%
cliff seen in 青囊不语问阴阳 and 99% of mid-tier AI-generated books)
is that chapter 2 fails to **cash in or escalate** the hooks chapter 1
established. The LLM tends to open a fresh narrative branch instead of
delivering on the promise it just made.

Bestselling serial authors do the opposite reflexively: every chapter
echoes — by name, by promise, by escalation — at least one hook from
the previous chapter. This gate enforces that reflex.

Pipeline:
    prev chapter text + current chapter text
        → extract hook tokens from prev (named entities, suspense words,
          trailing question/exclamation phrases, cliffhanger phrases)
        → score current chapter's "echo coverage"
        → return HookEchoReport: coverage ratio + finding severity

The gate is non-fatal by default — emits an audit finding for chapters
that miss the threshold. Pipeline integration is opt-in via
``quality_gates.hook_echo.block_on_failure``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
import re
from typing import Any

from bestseller.services.progress_context import emit_gate_result

logger = logging.getLogger(__name__)


# Suspense / cliffhanger words that榜单 authors actually use — these are
# the high-signal tokens worth tracing across chapters.
_SUSPENSE_TOKENS = (
    "却", "但", "然而", "突然", "忽然", "下一刻", "话音未落", "话音方落",
    "竟然", "居然", "原来", "原本", "谁知", "孰料", "不料",
    "竟是", "竟然是", "竟敢",
    "真相", "秘密", "名单", "令牌", "信物",
    "倒计时", "限期", "三日内", "七日后",
)

_LOW_SIGNAL_HOOK_TOKENS = {
    "却",
    "但",
    "然而",
    "突然",
    "忽然",
    "下一刻",
    "话音未落",
    "话音方落",
    "竟然",
    "居然",
    "原来",
    "原本",
    "谁知",
    "孰料",
    "不料",
    "竟是",
    "竟然是",
    "竟敢",
}

# Cliffhanger end-of-chapter markers — when present in prev chapter's
# tail, they should be ECHOED in current chapter's head.
_CLIFFHANGER_TAIL_PHRASES = (
    # A bare "门外/身后" is ordinary spatial narration, not a promise.  Keep
    # only event-shaped tail markers here; domain-specific motifs are supplied
    # through extra_domain_tokens by the caller.
    "脚步声", "钟声", "鼓声", "号角",
    "破门而入", "推门", "敲门",
    "—未完—", "未完",
)

_HOOK_TOKEN_SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "倒计时": ("倒数", "限时", "时限", "最后期限", "时间在倒着走"),
    "限期": ("时限", "期限", "最后期限", "限时"),
    "三日内": ("三天内", "三日之内", "只剩三天", "三天期限"),
    "七日后": ("七天后", "第七天", "七日期限"),
    "门外": ("门口", "门后", "门板后", "门槛外"),
    "身后": ("背后", "后方", "后背"),
    "脚步声": ("脚步", "足音", "步声", "脚步逼近"),
    "敲门": ("叩门", "拍门", "敲门声", "叩门声"),
    "推门": ("门被推开", "推开门", "门扇开了"),
    "破门而入": ("撞门而入", "冲进门", "门被撞开"),
    "名单": ("名册", "账册", "账页", "花名册", "名录"),
    "令牌": ("牌子", "腰牌", "信牌"),
    "信物": ("凭证", "旧物", "物证"),
    "秘密": ("隐情", "秘辛", "真相"),
    "真相": ("答案", "隐情", "谜底"),
}

# 2026-06-25 去通用性污染：原硬编码一本悬疑书的私货钩子词(第八张脸/回执镜片/镜债/
# 认账/账页/铜钱/罗盘/青囊/三短一长)，作为"legacy静态域token"对每本书的 extract_hook_tokens
# 生效。本书自有钩子意象由调用方经 extra_domain_tokens 传入(生产/验收同源)，不应在
# 通用门里焊死单本书词。清空。
_DOMAIN_HOOK_TOKENS: tuple[str, ...] = ()

# 2026-06-25 去通用性污染：删掉镜债/青囊那本悬疑书的私货语义组
# (mirror_receipt/account_debt/burial_claim/mirror_action — 回执镜片/认账/镜债/镜中林渊)，
# 它们只对那一本书的语义救援有利，对其它题材是死权重。保留跨题材通用的结构关系组。
# 本书自有意象的语义救援由 extra_domain_tokens(锚点)提供，见 check_hook_echo。
_SEMANTIC_HOOK_GROUPS: Mapping[str, tuple[str, ...]] = {
    "corpse": ("尸体", "死者", "遗体", "尸身", "死人", "那具尸"),
    "survival": (
        "活到现在", "活着", "活下来", "没死", "保住命", "续命", "留命",
    ),
    "countdown": ("倒计时", "倒数", "限时", "时限", "最后期限", "时间在倒着走"),
    "door_arrival": (
        "门外", "门口", "门后", "脚步声", "脚步", "足音", "叩门", "敲门",
    ),
    "truth_reveal": ("真相", "秘密", "隐情", "谜底", "答案", "秘辛"),
    "eye_action": (
        "睁眼", "闭眼", "睁开", "闭上", "对视", "对望", "盯着", "盯住",
        "凝视", "目光", "眼神", "眼睛", "眼底", "眼底闪",
    ),
    "voice_calling": (
        "呼喊", "喊", "叫", "喊声", "叫声", "声音", "回响", "回应", "应声",
        "应答", "答应", "回声", "嗓音", "嗓子",
    ),
    "protagonist_self": (
        "他自己", "自己的", "自己",
    ),}

# Chinese question marks and exclamations attached to short phrases are
# rhetorical questions / cliffhanger lines worth tracking.
_QUESTION_TAIL_RE = re.compile(r"[^\n。！？!?]{3,30}[？?]")

# Named-entity proxy: 2-4 char strings that recur in dialogue context
# (followed by 说 / 道 / 喊 / 笑 etc.) — these are usually character names.
_DIALOGUE_NAME_RE = re.compile(r"([一-鿿]{2,4})(?:[说道喊笑问答嗤哼])")
_DIALOGUE_NAME_STOP_CHARS = frozenset(
    # 2026-08-20：原表有「不」却独缺「没」，于是贪婪正则把「钟楼没说话」
    # 抽成人名「钟楼没」（真机出现 4 次、只有 25% 被回声 = 制造假 miss）。
    # 补的是**形状**（否定词永不出现在人名里），不是再往 stopword 表里
    # 加一条实例——表里那条 "今天没" 正是补实例的痕迹。
    "的了一是在有和与也都就又还很更被把将着过出进来去上下一不没我你他她它这那"
)

# Generic roles and sentence fragments frequently match the loose Chinese
# dialogue-attribution regex (e.g. "妇人笑", "孩子说", "像要喊"). They are
# not named hooks and must never become a chapter-to-chapter hard dependency.
_DIALOGUE_NAME_STOPWORDS = frozenset(
    {
        "妇人", "孩子", "奶娘", "老人", "路人", "男人", "女人", "少年", "少女",
        "声音", "脚步", "身后", "门外", "像要", "今天没", "这时", "那人", "有人",
    }
)

# Direct quote name attribution (e.g. “……” X 说)
_QUOTE_NAME_RE = re.compile(r"[“「『][^”」』\n]{2,}[”」』]\s*[一-鿿]{2,4}\s*[说道笑问喊]")


@dataclass(frozen=True)
class HookEchoFinding:
    """One audit finding from the Hook Echo Gate."""

    code: str  # "HOOK_ECHO_LOW" | "HOOK_ECHO_MISSING" | "HOOK_ECHO_OK"
    severity: str  # "critical" | "high" | "info"
    coverage: float  # 0..1
    chapter_position: int
    prev_hook_tokens: tuple[str, ...]
    matched_tokens: tuple[str, ...]
    missed_tokens: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class HookEchoReport:
    """Output of the gate — chapter-level summary + finding."""

    chapter_position: int
    coverage: float
    finding: HookEchoFinding
    prev_chapter_position: int | None = None

    @property
    def passed(self) -> bool:
        return self.finding.severity == "info"

    def to_prompt_block(self, language: str = "zh-CN") -> str:
        """Render as a remediation block to inject into the next regenerate prompt."""

        if self.passed:
            return ""
        if language.lower().startswith("zh"):
            lines = ["【钩子回环 — 本章未呼应上一章】"]
            lines.append(
                f"- 上一章 (第 {self.prev_chapter_position} 章) 留下的钩子: "
                + "; ".join(self.finding.prev_hook_tokens[:8])
            )
            if self.finding.matched_tokens:
                lines.append(
                    "- 本章已呼应: " + "; ".join(self.finding.matched_tokens[:6])
                )
            if self.finding.missed_tokens:
                lines.append(
                    "- 本章**漏掉**的关键钩子 (必须至少呼应一半): "
                    + "; ".join(self.finding.missed_tokens[:6])
                )
            lines.append(
                f"- 当前回环覆盖率: {self.coverage:.0%}（榜单一线 ≥ 60%，下限 50%）"
            )
            lines.append(
                "- 重写要求：必须在前 1000 字内显式呼应上面漏掉的钩子之一，"
                "形式可以是兑现承诺、升级悬念、人物现身或反转交代。"
            )
            return "\n".join(lines)
        lines = [
            "[Hook Echo — current chapter does not honor the previous chapter's hooks]",
            f"- Previous chapter ({self.prev_chapter_position}) hooks: "
            + "; ".join(self.finding.prev_hook_tokens[:8]),
            f"- Coverage: {self.coverage:.0%} (bar: ≥50%)",
        ]
        return "\n".join(lines)


def extract_hook_tokens(
    text: str,
    *,
    max_tokens: int = 20,
    extra_domain_tokens: Sequence[str] = (),
) -> list[str]:
    """Extract hook-signal tokens from a chapter.

    ``extra_domain_tokens`` carries BOOK-derived hook vocabulary (e.g. the
    book's imagery-system anchor phrases). Domain tokens are book content,
    not framework content — callers on the production side and the
    validation side must pass the same list so coverage stays consistent.
    """

    if not text:
        return []

    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        token = token.strip()
        if not token or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    # Book-derived domain objects/motifs are often the actual commercial
    # hook — scan them first (highest signal).
    for token in extra_domain_tokens:
        token = str(token or "").strip()
        if token and token in text:
            _add(token)

    # Suspense words (high signal)
    for word in _SUSPENSE_TOKENS:
        if word in text:
            _add(word)

    # Cliffhanger phrases in last 500 chars
    tail = text[-500:]
    for phrase in _CLIFFHANGER_TAIL_PHRASES:
        if phrase in tail:
            _add(phrase)

    # Legacy static domain tokens (kept for existing books whose plans and
    # prior chapters were produced with them; never extended — new domain
    # vocabulary must come from the book via ``extra_domain_tokens``).
    for token in _DOMAIN_HOOK_TOKENS:
        if token in text:
            _add(token)

    # Trailing question phrases — extract just the noun-heavy core. Questions
    # in the middle of a chapter are often local interrogation beats, not
    # next-chapter promises.
    for q in _QUESTION_TAIL_RE.findall(tail)[-5:]:
        cleaned = _clean_question_token(q)
        if 3 <= len(cleaned) <= 30:
            _add(cleaned)

    # Named entities via dialogue attribution (high recall on Chinese names)
    for name_match in _DIALOGUE_NAME_RE.findall(text):
        if name_match not in _DIALOGUE_NAME_STOPWORDS and _looks_like_dialogue_name(name_match):
            _add(name_match)

    high_signal_tokens = [
        token for token in tokens if token not in _LOW_SIGNAL_HOOK_TOKENS
    ]
    # If a chapter contains only generic suspense connectives, it has no
    # extractable hook.  Retaining the original list when this is empty made
    # low-signal words such as "却/忽然" become hard cross-chapter promises.
    tokens = high_signal_tokens

    return tokens[:max_tokens]


def _clean_question_token(raw: str) -> str:
    token = raw.rstrip("？?").strip(" \t\r\n“”‘’\"'")
    for separator in ("“", "”", "：", ":", "，", ",", "。", "；", ";"):
        if separator in token:
            token = token.rsplit(separator, 1)[-1].strip(" \t\r\n“”‘’\"'")
    return token


# 序数词形状：「第二/第三/第十/第2」。2026-08-20 真机《罚我守坟》——
# 裸序数被当人名抽成钩子 token，出现 5 次回声 4-5 次（80-100%），
# 下一章只要出现「第二」就算兑现，**结构上不可能失败**。与
# 「裸字『门』使 91% 钩子为幻影」同形：按形状挡，不按实例补词表。
_ORDINAL_TOKEN_RE = re.compile(r"^第[一二三四五六七八九十百千零两0-9]+$")


def _looks_like_dialogue_name(token: str) -> bool:
    token = token.strip()
    if not (2 <= len(token) <= 3):
        return False
    if any(ch in _DIALOGUE_NAME_STOP_CHARS for ch in token):
        return False
    if _ORDINAL_TOKEN_RE.match(token):
        return False
    return True


def _hook_token_variants(token: str) -> tuple[str, ...]:
    variants: list[str] = [token]
    variants.extend(_HOOK_TOKEN_SYNONYMS.get(token, ()))
    for canonical, synonyms in _HOOK_TOKEN_SYNONYMS.items():
        if token in synonyms and canonical not in variants:
            variants.append(canonical)
        if canonical in token:
            variants.extend(s for s in synonyms if s not in variants)
    return tuple(dict.fromkeys(v for v in variants if v))


def _hook_token_is_echoed(token: str, current_chapter_text: str) -> bool:
    if any(variant in current_chapter_text for variant in _hook_token_variants(token)):
        return True

    token_groups = _semantic_groups_for_text(token)
    if not token_groups:
        return False
    current_groups = _semantic_groups_for_text(current_chapter_text)
    return bool(token_groups & current_groups)


def _semantic_groups_for_text(text: str) -> set[str]:
    groups: set[str] = set()
    if not text:
        return groups
    for group_name, variants in _SEMANTIC_HOOK_GROUPS.items():
        if any(variant in text for variant in variants):
            groups.add(group_name)
    return groups


def check_hook_echo(
    *,
    prev_chapter_text: str,
    current_chapter_text: str,
    current_chapter_position: int,
    prev_chapter_position: int | None = None,
    min_coverage: float = 0.5,
    target_coverage: float = 0.65,
    early_chapter_threshold: int = 10,
    extra_domain_tokens: Sequence[str] = (),
) -> HookEchoReport:
    """Score a chapter's hook continuity vs the previous chapter.

    ``min_coverage`` is the hard floor — below this, the finding is
    severity 'critical' and the chapter should be rewritten.
    ``target_coverage`` is the榜单 baseline — between min and target is
    'high' severity (audit-only).

    For chapters > ``early_chapter_threshold`` the gate falls back to
    advisory only — the retention battle is mostly won/lost in golden
    three plus early arc.
    """

    if current_chapter_position < 2:
        # Chapter 1 has no previous chapter to echo; always passes.
        return HookEchoReport(
            chapter_position=current_chapter_position,
            coverage=1.0,
            prev_chapter_position=prev_chapter_position,
            finding=HookEchoFinding(
                code="HOOK_ECHO_OK",
                severity="info",
                coverage=1.0,
                chapter_position=current_chapter_position,
                prev_hook_tokens=(),
                matched_tokens=(),
                missed_tokens=(),
                detail="chapter 1 has no echo requirement",
            ),
        )

    prev_tokens = extract_hook_tokens(
        prev_chapter_text, extra_domain_tokens=extra_domain_tokens
    )
    if not prev_tokens:
        return HookEchoReport(
            chapter_position=current_chapter_position,
            coverage=1.0,
            prev_chapter_position=prev_chapter_position,
            finding=HookEchoFinding(
                code="HOOK_ECHO_OK",
                severity="info",
                coverage=1.0,
                chapter_position=current_chapter_position,
                prev_hook_tokens=(),
                matched_tokens=(),
                missed_tokens=(),
                detail="previous chapter had no extractable hook tokens",
            ),
        )

    matched: list[str] = []
    missed: list[str] = []
    for token in prev_tokens:
        if _hook_token_is_echoed(token, current_chapter_text):
            matched.append(token)
        else:
            missed.append(token)

    coverage = len(matched) / len(prev_tokens)

    is_early = current_chapter_position <= early_chapter_threshold

    # ──────────────────────────────────────────────────────────────────
    # Semantic-overlap rescue path (2026-05-23):
    # Pure token bag-of-words misses parallel/opposite-action echoes —
    # e.g. ch1 ends "镜中林渊忽然睁开了眼" and ch2 opens "镜中的林渊睁眼
    # 时，真正的林渊先把自己的眼睛闭上". Both share semantic groups
    # {mirror_action, eye_action, protagonist_self} but no overlapping
    # noun-tokens.  When that happens we treat the chapter as having
    # echoed at the semantic level — bump coverage so the gate does not
    # fire false-critical on a stylistically strong opening.
    # ──────────────────────────────────────────────────────────────────
    prev_groups = _semantic_groups_for_text(
        prev_chapter_text[-800:] if prev_chapter_text else ""
    )
    curr_groups = _semantic_groups_for_text(
        current_chapter_text[:800] if current_chapter_text else ""
    )
    shared_groups = prev_groups & curr_groups
    # Genre-fair rescue (2026-06-24): the static _SEMANTIC_HOOK_GROUPS encode
    # a few detective-specific relations (mirror_action/account_debt/…). Only
    # detective books could ever share those, so they got rescued from
    # false-criticals while other genres did not — a homogenization pressure
    # (non-detective books forced into more echo-rewrites). The book's OWN
    # imagery anchors (already threaded via extra_domain_tokens, identical on
    # production + validation side) are equally valid semantic echoes: when the
    # same anchor recurs in both the prev tail and the current head, that IS a
    # thematic hook echo. Count each shared anchor as one pseudo-group so every
    # genre gets equivalent rescue density.
    prev_tail = prev_chapter_text[-800:] if prev_chapter_text else ""
    curr_head = current_chapter_text[:800] if current_chapter_text else ""
    shared_anchors = {
        str(t).strip()
        for t in extra_domain_tokens
        if str(t).strip() and str(t).strip() in prev_tail and str(t).strip() in curr_head
    }
    effective_shared = len(shared_groups) + len(shared_anchors)
    semantic_rescued = False
    if coverage < target_coverage and effective_shared >= 3:
        # Re-score using semantic overlap density. Treat 3+ shared groups/anchors
        # as full coverage; 4+ give strong coverage. This caps "coverage" reading
        # at <= 1.0 and never goes below token-based coverage.
        semantic_boost = min(1.0, max(coverage, 0.7 + 0.1 * (effective_shared - 3)))
        if semantic_boost > coverage:
            coverage = semantic_boost
            semantic_rescued = True

    if coverage >= target_coverage:
        severity = "info"
        code = "HOOK_ECHO_OK"
        detail = (
            f"strong echo coverage ({coverage:.0%})"
            + (
                f"; semantic-overlap rescue: shared groups {sorted(shared_groups)}"
                + (f" + anchors {sorted(shared_anchors)}" if shared_anchors else "")
                if semantic_rescued
                else ""
            )
        )
    elif coverage >= min_coverage:
        severity = "high" if is_early else "info"
        code = "HOOK_ECHO_LOW" if is_early else "HOOK_ECHO_OK"
        detail = (
            f"coverage {coverage:.0%} is above floor but below target — "
            f"prefer ≥{target_coverage:.0%}"
        )
    else:
        severity = "critical" if is_early else "high"
        code = "HOOK_ECHO_MISSING"
        detail = (
            f"coverage {coverage:.0%} below {min_coverage:.0%} floor — "
            f"chapter likely fails to honor prior promises"
        )

    emit_gate_result(
        "hook_echo_gate",
        verdict="pass" if severity == "info" else "blocked",
        severity=severity,
        score=round(coverage * 100, 1),
        reasons=missed,
        chapter=current_chapter_position,
    )
    return HookEchoReport(
        chapter_position=current_chapter_position,
        coverage=coverage,
        prev_chapter_position=prev_chapter_position,
        finding=HookEchoFinding(
            code=code,
            severity=severity,
            coverage=coverage,
            chapter_position=current_chapter_position,
            prev_hook_tokens=tuple(prev_tokens),
            matched_tokens=tuple(matched),
            missed_tokens=tuple(missed),
            detail=detail,
        ),
    )


def render_hook_echo_block(
    report: HookEchoReport | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Pre-write prompt block — inject the previous chapter's hooks so
    the LLM has them top-of-mind when writing the current chapter."""

    if report is None:
        return ""
    if isinstance(report, Mapping):
        # Loose payload — render best-effort
        tokens = list(report.get("prev_hook_tokens") or [])[:8]
        prev_pos = report.get("prev_chapter_position")
    else:
        tokens = list(report.finding.prev_hook_tokens)[:8]
        prev_pos = report.prev_chapter_position
    if not tokens:
        return ""

    if language.lower().startswith("zh"):
        lines = [
            "【钩子回环 — 必须呼应上一章】",
            f"- 上一章 (第 {prev_pos} 章) 留下的钩子（必须在本章前 1000 字内呼应至少 1 个）:",
        ]
        for token in tokens:
            lines.append(f"  · {token}")
        lines.append(
            "- 呼应形式: 兑现承诺 / 升级悬念 / 人物再现 / 反转交代。"
        )
        lines.append(
            "- 切勿在本章开新支线或全章铺垫，留人优先于布世界。"
        )
        return "\n".join(lines)

    return "[Hook Echo — must honor previous chapter's hooks: " + "; ".join(tokens) + "]"


__all__ = [
    "HookEchoFinding",
    "HookEchoReport",
    "check_hook_echo",
    "extract_hook_tokens",
    "render_hook_echo_block",
]
