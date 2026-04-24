"""L4 Output Hard Validator.

Runs on LLM output before it's persisted. Each ``Check`` inspects the draft
text and returns a list of ``Violation``s, optionally with ``severity=block``
which instructs L6 ``write_gate`` to halt the write and trigger regeneration.

Phase 1 ships only the two highest-confidence checks:
    * ``LanguageSignatureCheck`` — CJK leak in an English draft / vice versa.
    * ``LengthEnvelopeCheck``    — character count outside the allowed window.

Other checks (naming consistency, entity density, structural format) land in
later phases.  The ``OutputValidator`` class is agnostic to which checks are
enabled — the caller picks the list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol

from bestseller.services.checker_schema import (
    CheckerIssue,
    CheckerReport,
    Severity as CheckerSeverity,
)
from bestseller.services.invariants import CliffhangerType, ProjectInvariants


Severity = Literal["block", "warn", "info"]


# ---------------------------------------------------------------------------
# Data structures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """Single validator finding.

    ``code`` is the stable identifier used by ``write_gate`` to resolve the
    per-violation mode (block / audit_only) from config.
    ``prompt_feedback`` is the natural-language remediation instruction fed
    back into the LLM on the next regeneration attempt — quality matters.
    """

    code: str
    severity: Severity
    location: str
    detail: str
    prompt_feedback: str

    def as_checker_issue(
        self,
        *,
        can_override: bool | None = None,
        allowed_rationales: tuple[str, ...] = (),
    ) -> CheckerIssue:
        """Adapt to Phase A1 unified schema.

        Severity mapping: ``block → critical``, ``warn → medium``, ``info → low``.
        Default ``can_override``: block/critical is hard (False), everything
        else is soft (True) unless the caller explicitly overrides.
        """

        sev_map: dict[str, CheckerSeverity] = {
            "block": "critical",
            "warn": "medium",
            "info": "low",
        }
        checker_sev = sev_map.get(self.severity, "medium")
        default_override = self.severity != "block"
        return CheckerIssue(
            id=self.code,
            type="output_validator",
            severity=checker_sev,
            location=self.location,
            description=self.detail,
            suggestion=self.prompt_feedback,
            can_override=can_override if can_override is not None else default_override,
            allowed_rationales=allowed_rationales,
        )


@dataclass(frozen=True)
class ValidationContext:
    """Context the validators use — separated from the text itself so the
    same ``OutputValidator`` can be reused across scene, chapter, and audit
    invocations.

    ``allowed_names`` is the union of the project's seeded naming pool and
    the current bible's character roster (canonical names + aliases). Empty
    by default so checks that depend on it (``NamingConsistencyCheck``)
    gracefully no-op for callers that haven't populated it.
    """

    invariants: ProjectInvariants
    chapter_no: int | None = None
    scope: Literal["scene", "chapter"] = "chapter"
    allowed_names: frozenset[str] = frozenset()
    # Cliffhanger rotation window — filled from DiversityBudget at the
    # chapter-validation call site. Empty tuple means "first chapter" or
    # "rotation disabled"; checks should gracefully no-op.
    recent_cliffhangers: tuple[CliffhangerType, ...] = ()
    # Hype engine assignment + history for L5 validator checks. All
    # optional — callers that don't use the Hype Engine leave them empty
    # and the hype checks no-op. Imported via ``Any`` to avoid a module
    # cycle with ``hype_engine`` (which already imports invariants).
    assigned_hype_type: Any = None
    assigned_hype_recipe: Any = None
    recent_hype_types: tuple[Any, ...] = ()
    # Phase B1 — narrative-line gap report populated by call sites that
    # pre-compute ``narrative_line_tracker.report_gaps``. Left as ``Any``
    # to avoid a cycle with the tracker (the tracker already imports
    # ``genre_profile_thresholds`` which is leaf-level). ``None`` means
    # the project hasn't opted into line-dominance tracking, in which
    # case ``LineGapCheck`` no-ops.
    line_gap_report: Any = None


@dataclass(frozen=True)
class QualityReport:
    violations: tuple[Violation, ...]

    @property
    def blocks_write(self) -> bool:
        return any(v.severity == "block" for v in self.violations)

    @property
    def has_issues(self) -> bool:
        return bool(self.violations)

    def feedback_for_regen(self) -> str:
        """Natural-language integrated remediation prompt for the LLM."""

        if not self.violations:
            return ""
        header = (
            "你上次生成的内容未通过质量校验。请严格按照以下整改要求重写，"
            "保持剧情、人物、场景不变：\n"
        )
        lines = [
            f"{idx}) [{v.code}] {v.detail}\n   整改：{v.prompt_feedback}"
            for idx, v in enumerate(self.violations, 1)
        ]
        return header + "\n".join(lines)

    def as_checker_report(
        self,
        *,
        chapter: int,
        agent: str = "output-validator",
        soft_codes: frozenset[str] = frozenset(),
    ) -> CheckerReport:
        """Adapt to Phase A1 unified schema.

        ``soft_codes`` lets callers mark specific ``Violation.code`` values
        as soft (can_override=True) even if severity is ``block``. Used by
        Phase C to route pre-agreed soft violations through the Override
        Contract instead of hard-blocking.
        """

        issues = tuple(
            v.as_checker_issue(can_override=(v.code in soft_codes) or (v.severity != "block"))
            for v in self.violations
        )
        blocks = sum(1 for v in self.violations if v.severity == "block")
        warns = sum(1 for v in self.violations if v.severity == "warn")
        score = max(0, 100 - blocks * 20 - warns * 5)
        return CheckerReport(
            agent=agent,
            chapter=chapter,
            overall_score=score,
            passed=not self.blocks_write,
            issues=issues,
            metrics={
                "block_count": blocks,
                "warn_count": warns,
                "total_violations": len(self.violations),
            },
            summary=(
                "输出校验通过" if not self.has_issues
                else f"输出存在 {len(self.violations)} 条校验问题（{blocks} 阻塞 / {warns} 警告）"
            ),
        )


class Check(Protocol):
    code: str

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Language signature — Phase 1 check #1.
# ---------------------------------------------------------------------------


_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_WHITESPACE_RE = re.compile(r"\s+")


class LanguageSignatureCheck:
    """Reject drafts whose language signature disagrees with the project
    language.

    Thresholds:
        * English draft: CJK char ratio > 2% → block.
        * Chinese draft: "wordy Latin fragment" ratio > 10% → block.

    The ratio denominators are different on purpose — CJK chars are single
    glyphs so counting them directly is meaningful, while Latin runs need
    length filtering (3+ letters) to avoid counting commonplace acronyms.
    """

    code_cjk_in_en = "LANG_LEAK_CJK_IN_EN"
    code_latin_in_zh = "LANG_LEAK_LATIN_IN_ZH"
    # Sub-threshold visibility finding: sibling of the block code, same ratio
    # axis, but fires when the leak is *present* but below the block threshold.
    # Used for dashboards/scorecards to surface residual CJK glyphs that
    # slipped through but don't warrant blocking.
    code_cjk_in_en_residue = "LANG_RESIDUE_CJK_IN_EN"

    def __init__(
        self,
        *,
        cjk_in_en_ratio_max: float = 0.02,
        latin_in_zh_ratio_max: float = 0.10,
        max_samples_in_feedback: int = 3,
        cjk_in_en_residue_ratio_min: float = 0.0,
    ) -> None:
        """
        ``cjk_in_en_residue_ratio_min`` — lower bound (exclusive) on the CJK
        ratio for an English draft to emit an INFO-severity residue finding.
        Default ``0.0`` means "any CJK glyph at all, as long as it's below the
        block threshold." Raise it if the noise floor is too high.
        """

        self.cjk_in_en_ratio_max = cjk_in_en_ratio_max
        self.latin_in_zh_ratio_max = latin_in_zh_ratio_max
        self.max_samples_in_feedback = max_samples_in_feedback
        self.cjk_in_en_residue_ratio_min = cjk_in_en_residue_ratio_min

    @property
    def code(self) -> str:
        # Caller resolves mode per-violation; we advertise the "primary" code.
        return self.code_cjk_in_en

    def run(self, text: str, ctx: ValidationContext) -> list[Violation]:
        if not text:
            return []
        non_ws = _WHITESPACE_RE.sub("", text)
        if not non_ws:
            return []

        if ctx.invariants.language == "en":
            cjk_matches = list(_CJK_RE.finditer(text))
            ratio = len(cjk_matches) / max(len(non_ws), 1)
            if ratio <= self.cjk_in_en_ratio_max:
                # Sub-threshold: glyphs are present but below the block
                # ratio. Emit an INFO finding so scorecards show residual
                # CJK leaks that would otherwise be invisible.
                if cjk_matches and ratio > self.cjk_in_en_residue_ratio_min:
                    samples = self._unique_cjk_samples(text, cjk_matches)
                    residue_detail = (
                        f"CJK char ratio {ratio:.3%} below block limit "
                        f"{self.cjk_in_en_ratio_max:.1%} but {len(cjk_matches)} "
                        f"CJK glyph(s) present; examples: "
                        f"{', '.join(samples[: self.max_samples_in_feedback]) or 'n/a'}"
                    )
                    residue_feedback = (
                        f"本章残留 {len(cjk_matches)} 个中文字符（低于阻塞阈值），"
                        f"样例：{', '.join(samples[:5])}。"
                        "建议在下次重生时顺手清理，避免积累。"
                    )
                    return [
                        Violation(
                            code=self.code_cjk_in_en_residue,
                            severity="info",
                            location=f"cjk_count:{len(cjk_matches)}",
                            detail=residue_detail,
                            prompt_feedback=residue_feedback,
                        )
                    ]
                return []
            samples = self._unique_cjk_samples(text, cjk_matches)
            detail = (
                f"CJK char ratio {ratio:.3%} exceeds limit {self.cjk_in_en_ratio_max:.1%}; "
                f"examples: {', '.join(samples[: self.max_samples_in_feedback]) or 'n/a'}"
            )
            feedback = (
                f"本小说目标语言为英文，但上次输出包含中文字符（样例：{', '.join(samples[:5])}）。"
                "请找出所有中文片段并翻译为自然的英文，保持剧情/人物/场景不变。"
            )
            return [
                Violation(
                    code=self.code_cjk_in_en,
                    severity="block",
                    location=f"cjk_count:{len(cjk_matches)}",
                    detail=detail,
                    prompt_feedback=feedback,
                )
            ]

        # Chinese / default branch: penalize long Latin runs.
        latin_runs = [m.group(0) for m in _LATIN_WORD_RE.finditer(text)]
        if not latin_runs:
            return []
        latin_char_total = sum(len(w) for w in latin_runs)
        ratio = latin_char_total / max(len(non_ws), 1)
        if ratio <= self.latin_in_zh_ratio_max:
            return []
        samples = sorted({w for w in latin_runs if len(w) >= 4})[: self.max_samples_in_feedback]
        detail = (
            f"Latin char ratio {ratio:.3%} exceeds limit {self.latin_in_zh_ratio_max:.1%}; "
            f"examples: {', '.join(samples) or 'n/a'}"
        )
        feedback = (
            f"本小说目标语言为中文，但上次输出包含较多英文内容（样例：{', '.join(samples)}）。"
            "请把这些英文片段改写为符合语境的中文，保持剧情/人物/场景不变。"
        )
        return [
            Violation(
                code=self.code_latin_in_zh,
                severity="block",
                location=f"latin_runs:{len(latin_runs)}",
                detail=detail,
                prompt_feedback=feedback,
            )
        ]

    @staticmethod
    def _unique_cjk_samples(text: str, matches: list[re.Match[str]]) -> list[str]:
        """Extract short CJK context snippets to show the user/LLM."""

        samples: list[str] = []
        seen: set[str] = set()
        for match in matches:
            start = max(match.start() - 3, 0)
            end = min(match.end() + 3, len(text))
            snippet = text[start:end].strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                samples.append(snippet)
            if len(samples) >= 10:
                break
        return samples


# ---------------------------------------------------------------------------
# Length envelope — Phase 1 check #2.
# ---------------------------------------------------------------------------


def _count_effective_chars(text: str, language: str) -> int:
    """Count chars contributing to perceived length.

    For Chinese we strip whitespace (each CJK char is meaningful); for
    English we count non-whitespace characters which is a reasonable proxy
    for word count × 5-ish.
    """

    if not text:
        return 0
    return len(_WHITESPACE_RE.sub("", text))


class LengthEnvelopeCheck:
    """Reject drafts outside the per-project length envelope.

    The envelope is a hard wall: the 201-character ``xianxia ch-9`` and the
    10k-character over-long chapters both must be regenerated.  Borderline
    cases (±5% of wall) are not special-cased — regen with feedback is
    cheap relative to publishing a broken chapter.
    """

    code_under = "LENGTH_UNDER"
    code_over = "LENGTH_OVER"

    def __init__(self) -> None:
        pass

    @property
    def code(self) -> str:
        return self.code_under

    def run(self, text: str, ctx: ValidationContext) -> list[Violation]:
        if ctx.scope != "chapter":
            return []
        count = _count_effective_chars(text, ctx.invariants.language)
        env = ctx.invariants.length_envelope
        if count < env.min_chars:
            return [
                Violation(
                    code=self.code_under,
                    severity="block",
                    location="chapter",
                    detail=f"{count} chars < min {env.min_chars}",
                    prompt_feedback=(
                        f"本章有效字符数 {count}，低于下限 {env.min_chars}。"
                        f"请补充场景细节、感官描写、内心活动，达到 {env.target_chars} 字左右。"
                        "不要添加无关剧情或角色。"
                    ),
                )
            ]
        if count > env.max_chars:
            return [
                Violation(
                    code=self.code_over,
                    severity="block",
                    location="chapter",
                    detail=f"{count} chars > max {env.max_chars}",
                    prompt_feedback=(
                        f"本章有效字符数 {count}，超过上限 {env.max_chars}。"
                        "请压缩冗余描写、合并重复对白，保持情节结构不变。"
                    ),
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Naming consistency — bug #6 ("naming chaos").
# ---------------------------------------------------------------------------


# Top-100 百家姓 single-character surnames — covers ~85%+ of common names.
# Inlined as a string for readability; frozenset membership is O(1).
_ZH_TOP_SURNAMES: frozenset[str] = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "林"  # 林 is not in the Song-era 百家姓 prefix but is extremely common today.
)

# Disyllabic (compound) surnames — cover 欧阳/慕容/司马/etc. explicitly so
# the regex extraction doesn't fragment a 3-char name into a wrong bigram.
_ZH_COMPOUND_SURNAMES: tuple[str, ...] = (
    "欧阳", "慕容", "司马", "上官", "司徒", "诸葛", "夏侯",
    "东方", "皇甫", "尉迟", "令狐", "长孙", "宇文", "南宫",
    "轩辕", "端木", "独孤", "澹台", "公孙", "赫连",
)

_ZH_SURNAME_CLASS: str = "[" + "".join(sorted(_ZH_TOP_SURNAMES)) + "]"
_ZH_NAME_RE: re.Pattern[str] = re.compile(
    _ZH_SURNAME_CLASS + r"[\u4e00-\u9fff]{1,2}"
)


# Second-character stoplist — when the second char of a surname-prefixed
# candidate is one of these, the 2-char slice is overwhelmingly a common
# word/adverb compound, not a name. E.g. "时候" (time/when), "方向"
# (direction), "成熟" (mature), "金色" (golden). Empirically these cause
# the bulk of Chinese NAMING_OUT_OF_POOL false positives during audit.
# The list is intentionally conservative — only characters that essentially
# never appear as the second character of a real Chinese given name belong
# here.
_ZH_COMMON_WORD_2ND_CHARS: frozenset[str] = frozenset(
    # Time / space / direction / measure
    "候间机空节装代位向法式面才能色属钱融光何意务由时"
    # Adjective / state
    "熟功为长年员立围边全期详身静凡常地淡稳"
    # Grammatical particles / pronouns
    "的地了着过吗呢吧啊们"
    # Pronouns following 和/与/等 in "conjunction+pronoun" captures
    # e.g. "和她", "和他", "和我", "和你" — 和 is often the conjunction
    # "and", not the surname 和.
    "她他它我你您谁"
    # Adverbial connectors (often appear after a surname-character)
    "然而且就只是也都很非"
    # Very common "nominalizers" that pair with surname-characters
    "些处会后前里外上下中内"
    # More high-frequency 2nd-char non-name compounds observed in audit
    # sweeps: 时辰/许久/许多/明天/庞大/安排/柳叶/明日/明月/安然/庞杂/
    # 许是/安静/常见/平凡/顾不/顾自/顾及/…
    # Only characters that virtually never appear as the second character
    # of a real given name should be added here.
    "辰久多天大排叶日月然静见凡不自及"
    # Verb particles commonly tail-glued onto a preceding surname char
    "醒入去上下来到出回走起见会想看说听说"
    # Generic "many/few/some" quantifiers — 许多/诸多/…
    "少些"
    # Measure-word compounds (张纸/张脸/张网/张开/张照片 — 张 is a
    # surname but dominantly a measure word in modern fiction). Also
    # a measure-word combining char: 条, 块, 片.
    "纸脸网开照条口"
    # Common-noun 2nd chars seen in audit top-25 — 金纹/金丹/金线/金瞳/
    # 金道纹/皮肤/皮肤下/计划/计时/雷劫/元婴/云宗/方家/周家/水塔/
    # 成形/明白/张家.
    "纹丹线瞳肤划家宗劫婴塔形白路纪"
    # Additional high-volume 2nd-char false positives from v4 audit:
    # 方传来/方案/方势/方法/方便, 成一个/成某种/成交, 水一样/水道,
    # 计算, 云层/云海, 于有, 贺礼, 汤药, 雷云, 金灰/金红/金黑.
    "传案势法便某交样道算层海有礼药云灰红黑将"
)

# Third-character stoplist — for 3-char candidates the regex greedily
# captures "surname+2 chars"; we trim to 2 chars if the 3rd char is a
# grammatical particle / measure that can't be part of a given name.
_ZH_GRAMMATICAL_TAIL_CHARS: frozenset[str] = frozenset(
    "的地了着过吗呢吧啊们就是也都"
    # Extra very-common 3rd-char tails we saw in audit findings:
    # 钱福说/钱福没/孔微微/苏雪没/云掌第/方入场/孔骤然 —
    # these should retain their 2-char core.
    "说没微第场然看在和与到不就"
)


# Role-suffix tokens — Chinese cultivation / wuxia / generic novel conventions.
# When a candidate ends with one of these role tokens, stripping the token
# reveals the character's actual name prefix. "苏师姐" → "苏"; "周师兄" →
# "周"; "钱管事" → "钱"; "王真人" → "王". For a 1-char root we can't tell
# if it's in the pool (too ambiguous), so we fall back to the full
# surname+role candidate but with a *very* high frequency floor.
_ZH_ROLE_SUFFIXES: tuple[str, ...] = (
    "师兄", "师姐", "师弟", "师妹", "师父", "师母", "师叔", "师伯",
    "管事", "长老", "前辈", "后辈", "道友", "道长", "真人", "真君",
    "公子", "小姐", "姑娘", "夫人", "娘子", "郎君", "少爷", "掌柜",
    "城主", "宗主", "门主", "教主", "仙子", "仙君", "上仙",
    # Honorifics
    "大人", "老爷", "奶奶", "爷爷",
)


def _strip_role_suffix(candidate: str) -> str | None:
    """Return the surname prefix if ``candidate`` ends in a role suffix.

    "苏师姐" → "苏", "钱管事" → "钱", "王真人" → "王". Returns ``None``
    when no role suffix is present, signalling the caller should use the
    original candidate unchanged.
    """

    for suffix in _ZH_ROLE_SUFFIXES:
        if candidate.endswith(suffix) and len(candidate) > len(suffix):
            return candidate[: -len(suffix)]
    return None


# Leading Chinese conjunctions/prepositions that masquerade as surnames.
# "和" is BOTH a surname AND the ubiquitous conjunction "and"; in modern
# fiction the conjunction usage overwhelmingly dominates. When a candidate
# starts with one of these and the tail is 2+ chars, try the tail against
# the pool before flagging.
_ZH_LEADING_CONJUNCTIONS: tuple[str, ...] = ("和", "与", "及", "或")


def _strip_leading_conjunction(candidate: str) -> str | None:
    """"和林鸢" → "林鸢"; "与苏瑶" → "苏瑶". Returns ``None`` when no
    leading conjunction is present. We only return the tail when it's
    still ≥ 2 chars — a single-char surname-like tail isn't trustworthy.
    """

    if not candidate:
        return None
    if candidate[0] in _ZH_LEADING_CONJUNCTIONS and len(candidate) >= 3:
        return candidate[1:]
    return None


def _trim_zh_name_candidate(candidate: str) -> str | None:
    """Drop stopword-driven false positives and trim grammatical tails.

    Returns ``None`` when the candidate should be rejected entirely (its
    2-char prefix is a common compound noun), else returns a cleaned
    candidate string suitable for name-pool comparison.

    Examples:
      * "时候"     → None        (stoplist hit on 2nd char)
      * "周围几"   → None        (2-char prefix "周围" is stoplisted)
      * "钱福的"   → "钱福"      (strip grammatical tail "的")
      * "韩九的"   → "韩九"      (strip grammatical tail "的")
      * "苏师姐"   → "苏师姐"    (3-char candidate, no stoplist hit)
      * "云诀"     → "云诀"      (clean 2-char candidate)
    """

    if not candidate:
        return None
    # Reject if second char is stoplisted — the 2-char prefix is a word.
    if len(candidate) >= 2 and candidate[1] in _ZH_COMMON_WORD_2ND_CHARS:
        return None
    # Trim trailing grammatical particle for 3-char candidates — the real
    # name is the leading 2 chars ("韩九的" → "韩九").
    if (
        len(candidate) == 3
        and candidate[2] in _ZH_GRAMMATICAL_TAIL_CHARS
    ):
        return candidate[:2]
    return candidate

# English honorific + capitalized name; capture just the name portion.
_EN_HONORIFIC_NAME_RE: re.Pattern[str] = re.compile(
    r"\b(?:Mr|Mrs|Miss|Ms|Dr|Prof|Sir|Lady|Lord|Dame|Madam|Master|Mistress)"
    r"\.?\s+([A-Z][A-Za-z']{1,15}(?:\s+[A-Z][A-Za-z']{1,15})?)"
)
# Two or three consecutive title-cased words — "Elena Vance", "Jane Smith Wu".
_EN_MULTIWORD_NAME_RE: re.Pattern[str] = re.compile(
    r"\b([A-Z][a-z']{1,15}(?:\s+[A-Z][a-z']{1,15}){1,2})\b"
)

# Common English capitalized words that aren't names — used to suppress
# obvious false positives like "The Grand Canyon" or weekday names.
_EN_CAPITAL_NON_NAMES: frozenset[str] = frozenset({
    "The", "A", "An", "I", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "January", "February", "March",
    "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "God", "Lord", "Lady",  # stripped as titles
})

# Sentence-starter conjunctions and subordinators that the multi-word
# regex greedily absorbs into a name capture — "And Rowan" → regex sees
# `And Rowan` as a title-cased run because English sentence starts always
# capitalize. When the first word of a multi-word candidate is one of
# these, we retry the pool lookup against the tail ("Rowan").
_EN_SENTENCE_STARTERS: frozenset[str] = frozenset({
    "And", "But", "Then", "So", "Or", "Yet", "For", "Nor",
    "If", "When", "While", "After", "Before", "Although",
    "Because", "Since", "Though", "Unless", "Until", "Whether",
    "Not", "Only", "Still", "Just", "Even",
})

# Contractions and similar capitalized-pronoun short forms that survive
# sentence-starter stripping but are NOT names. Exact-match skip list.
_EN_CONTRACTIONS: frozenset[str] = frozenset({
    "I'm", "I'll", "I've", "I'd",
    "He's", "He'd", "He'll",
    "She's", "She'd", "She'll",
    "It's", "It'd", "It'll",
    "We're", "We've", "We'd", "We'll",
    "They're", "They've", "They'd", "They'll",
    "You're", "You've", "You'd", "You'll",
    "Don't", "Doesn't", "Didn't", "Isn't", "Aren't", "Wasn't",
    "Weren't", "Can't", "Couldn't", "Won't", "Wouldn't",
    "Shouldn't", "Hasn't", "Haven't", "Hadn't",
    "That's", "There's", "Here's", "Who's", "What's",
    "Let's",
})


def _strip_en_sentence_starter(candidate: str) -> str | None:
    """"And Rowan" → "Rowan"; "But Kade Mercer" → "Kade Mercer". Returns
    ``None`` when no sentence-starter prefix is present. Only returns
    the tail when it's still a plausible name (≥ 1 word, starts with
    a capital letter).
    """

    if not candidate:
        return None
    words = candidate.split()
    if len(words) >= 2 and words[0] in _EN_SENTENCE_STARTERS:
        tail = " ".join(words[1:]).strip()
        if tail and tail[0].isupper():
            return tail
    return None


def _strip_en_possessive(candidate: str) -> str | None:
    """"Ashford's" → "Ashford"; "Pale Mother's" → "Pale Mother". Returns
    ``None`` when there's no trailing ``'s``.
    """

    if candidate.endswith("'s") and len(candidate) > 2:
        return candidate[:-2]
    return None


def _is_allowed_name(candidate: str, allowed: frozenset[str]) -> bool:
    """Return True when ``candidate`` corresponds to an allowed name.

    Allows both directions of prefix containment:
      * candidate starts with an allowed name (e.g., "林奚说" → allowed "林奚")
      * candidate is a prefix of an allowed name (e.g., "慕容" → allowed "慕容雪")

    The second direction handles regex bigram extraction that undershoots a
    disyllabic surname. Exact membership is tried first as the cheap path.
    """

    if not allowed:
        return False
    if candidate in allowed:
        return True
    for name in allowed:
        if candidate.startswith(name) or name.startswith(candidate):
            return True
    return False


class NamingConsistencyCheck:
    """Flag proper nouns in the draft that are not in the project naming pool.

    The check is deliberately conservative:
      * If the allowlist (invariants' seed pool ∪ ctx.allowed_names) is empty
        we no-op — there's nothing to compare against.
      * A candidate name must appear at least ``frequency_floor`` times
        (default 2) to be flagged. One-off occurrences are usually spurious
        regex hits (place names, titles).
      * The check's default severity is ``block`` but the gate default maps
        ``NAMING_OUT_OF_POOL`` → ``audit_only`` so Phase 1 observes accuracy
        before escalating to a hard block.

    Detection strategies:
      * Chinese: regex over top-100 single-character surnames + explicit
        list of compound surnames (欧阳/慕容/司马 …). 2- and 3-char names are
        both captured.
      * English: honorific-anchored names (Mr./Mrs./Dr./Sir + Cap) OR
        multi-word title-case runs (Jane Smith, Elena Vance).
    """

    code = "NAMING_OUT_OF_POOL"

    def __init__(
        self,
        *,
        frequency_floor: int = 2,
        max_samples_in_feedback: int = 8,
    ) -> None:
        self.frequency_floor = frequency_floor
        self.max_samples_in_feedback = max_samples_in_feedback

    def run(self, text: str, ctx: ValidationContext) -> list[Violation]:
        if not text:
            return []
        allowed = self._collect_allowed(ctx)
        if not allowed:
            return []

        language = ctx.invariants.language
        if language.lower().startswith("zh"):
            rogue = self._rogue_names_zh(text, allowed)
        else:
            rogue = self._rogue_names_en(text, allowed)

        # Filter to frequency floor.
        rogue = {name: count for name, count in rogue.items() if count >= self.frequency_floor}
        if not rogue:
            return []

        samples = sorted(rogue.items(), key=lambda kv: -kv[1])[: self.max_samples_in_feedback]
        sample_str = ", ".join(f"{name}×{cnt}" for name, cnt in samples)
        allowed_preview = ", ".join(sorted(allowed)[:20])
        detail = (
            f"{len(rogue)} name(s) not in pool: "
            f"{sample_str}"
        )
        prompt_feedback = (
            f"本章出现命名池外的人名：{sample_str}。"
            f"命名池（部分）：{allowed_preview}{' …' if len(allowed) > 20 else ''}。"
            "请将这些角色替换为命名池中合适的名字，或者如果确实需要引入新角色，"
            "请使用命名池中预留的候选名。不要临时杜撰新名字。"
        )
        return [
            Violation(
                code=self.code,
                severity="block",
                location=f"rogue_names:{len(rogue)}",
                detail=detail,
                prompt_feedback=prompt_feedback,
            )
        ]

    @staticmethod
    def _collect_allowed(ctx: ValidationContext) -> frozenset[str]:
        roster: set[str] = set(ctx.allowed_names)
        scheme = ctx.invariants.naming_scheme
        if scheme is not None:
            for name in scheme.seed_pool:
                if name and name.strip():
                    roster.add(name.strip())
        return frozenset(roster)

    @staticmethod
    def _rogue_names_zh(text: str, allowed: frozenset[str]) -> dict[str, int]:
        """Return a counter of rogue name candidates in Chinese text."""

        counts: dict[str, int] = {}
        # 1) Compound-surname extraction — these are rarer than single-char
        # surnames but would be miscounted if handled by the generic regex.
        for compound in _ZH_COMPOUND_SURNAMES:
            if compound not in text:
                continue
            # Find compound + 1-2 Han chars.
            for match in re.finditer(
                re.escape(compound) + r"[\u4e00-\u9fff]{1,2}", text
            ):
                candidate = match.group(0)
                if _is_allowed_name(candidate, allowed):
                    continue
                # Role-suffix form: "司马师兄" → look up "司马" in pool.
                role_stripped = _strip_role_suffix(candidate)
                if role_stripped and _is_allowed_name(role_stripped, allowed):
                    continue
                counts[candidate] = counts.get(candidate, 0) + 1

        # 2) Single-char surname extraction. The stoplist filter suppresses
        # common-noun false positives ("时候", "周围几", …) that would
        # otherwise swamp the findings with non-names.
        for match in _ZH_NAME_RE.finditer(text):
            # Skip regex matches that land *inside* a compound surname —
            # e.g. "司马师兄" at position 0 has compound "司马" consumed by
            # pass (1); without this guard the single-surname regex would
            # also match "马师兄" starting at position 1 and produce a
            # spurious rogue name. If the preceding char plus the current
            # surname form a compound, the compound pass already owned
            # this region.
            start = match.start()
            if start > 0 and text[start - 1 : start + 1] in _ZH_COMPOUND_SURNAMES:
                continue
            candidate = match.group(0)
            # Skip if the candidate itself is a compound-prefixed form
            # (e.g. the compound pass already processed "司马师" as a
            # superset capture of our single-surname hit).
            if any(candidate.startswith(comp) for comp in _ZH_COMPOUND_SURNAMES):
                continue
            cleaned = _trim_zh_name_candidate(candidate)
            if cleaned is None:
                continue
            # Role-suffix aware lookup: "苏师姐" → allow if "苏" is in pool
            # (single-character root is too ambiguous to trust for *new*
            # rogue detection — we only use this path to *accept*, never
            # to promote a single char into the counter).
            role_stripped = _strip_role_suffix(cleaned)
            if role_stripped and _is_allowed_name(role_stripped, allowed):
                continue
            # Leading-conjunction aware lookup: "和林鸢" → "林鸢" → allow
            # if "林鸢" is in pool. 和 as a conjunction is vastly more
            # common than 和 as a surname in modern fiction.
            conj_stripped = _strip_leading_conjunction(cleaned)
            if conj_stripped and _is_allowed_name(conj_stripped, allowed):
                continue
            if _is_allowed_name(cleaned, allowed):
                continue
            counts[cleaned] = counts.get(cleaned, 0) + 1
        return counts

    @staticmethod
    def _rogue_names_en(text: str, allowed: frozenset[str]) -> dict[str, int]:
        """Return a counter of rogue name candidates in English text."""

        counts: dict[str, int] = {}

        def _resolve_against_pool(candidate: str) -> str | None:
            """Return the cleaned candidate to record (or ``None`` if the
            candidate is already accounted for by the pool via any of our
            stripping heuristics: possessive 's, sentence-starter
            conjunction, or a combination).
            """

            if _is_allowed_name(candidate, allowed):
                return None
            # Strip possessive 's and retry — "Ashford's" → "Ashford".
            poss_stripped = _strip_en_possessive(candidate)
            if poss_stripped and _is_allowed_name(poss_stripped, allowed):
                return None
            # Strip leading sentence-starter — "And Rowan" → "Rowan".
            starter_stripped = _strip_en_sentence_starter(candidate)
            if starter_stripped and _is_allowed_name(starter_stripped, allowed):
                return None
            # Combined: "And Kade Mercer's" → "Kade Mercer".
            if starter_stripped:
                combo = _strip_en_possessive(starter_stripped)
                if combo and _is_allowed_name(combo, allowed):
                    return None
            if poss_stripped:
                combo = _strip_en_sentence_starter(poss_stripped)
                if combo and _is_allowed_name(combo, allowed):
                    return None
            # Contractions that survive conjunction-stripping ("And I'm"
            # → "I'm") are never names — skip entirely.
            for variant in (starter_stripped, candidate):
                if variant and variant in _EN_CONTRACTIONS:
                    return None
            # Single-word residue after conjunction strip that's still
            # obviously not a name (months, weekdays, articles, common
            # stopwords) → skip.
            if starter_stripped:
                tail_words = starter_stripped.split()
                if len(tail_words) == 1 and tail_words[0] in _EN_CAPITAL_NON_NAMES:
                    return None
            # Unresolvable; return the cleanest variant we have so the
            # finding shows the reader the base name (not the conjunction
            # / possessive noise).
            for variant in (starter_stripped, poss_stripped, candidate):
                if variant:
                    return variant
            return candidate

        # 1) Honorific-anchored names — "Mr. Parker" / "Mrs. Elena Vance".
        for match in _EN_HONORIFIC_NAME_RE.finditer(text):
            candidate = match.group(1).strip()
            if not candidate:
                continue
            resolved = _resolve_against_pool(candidate)
            if resolved is None:
                continue
            counts[resolved] = counts.get(resolved, 0) + 1

        # 2) Two- or three-word title-case runs.
        for match in _EN_MULTIWORD_NAME_RE.finditer(text):
            candidate = match.group(1).strip()
            if not candidate:
                continue
            # Drop runs that include obvious non-names (months, weekdays, titles).
            words = candidate.split()
            if any(w in _EN_CAPITAL_NON_NAMES for w in words):
                continue
            resolved = _resolve_against_pool(candidate)
            if resolved is None:
                continue
            counts[resolved] = counts.get(resolved, 0) + 1

        return counts


# ---------------------------------------------------------------------------
# Opening entity density — bug #11 ("first chapter overloads with 11+ names").
# ---------------------------------------------------------------------------


class EntityDensityCheck:
    """Chapter 1 must not exceed ``max_entities`` distinct named entities in
    its first ``head_lines`` lines.

    Reader memory has finite bandwidth. The four historical productions all
    debut with 11+ named entities in the opening (characters, factions,
    cities, powers) — the reader drops out before reaching the hook. We cap
    the opening at 5 entities (protagonist + antagonist + setting + one
    mystery node + one supporting character) and defer the rest.

    Scope: chapter 1 only. Scene-level drafts are exempt (the chapter is
    assembled from scenes; the density budget applies to the assembled
    whole, not any single scene).

    Detection reuses the name regexes from ``NamingConsistencyCheck``:
      * Chinese: surname-anchored 2-3 char names (single + compound surnames).
      * English: honorific-anchored names + multi-word title-case runs.
    """

    code = "OPENING_ENTITY_OVERLOAD"

    def __init__(
        self,
        *,
        head_lines: int = 150,
        max_entities: int = 5,
        max_samples_in_feedback: int = 10,
    ) -> None:
        self.head_lines = head_lines
        self.max_entities = max_entities
        self.max_samples_in_feedback = max_samples_in_feedback

    def run(self, text: str, ctx: ValidationContext) -> list[Violation]:
        if not text:
            return []
        if ctx.chapter_no != 1 or ctx.scope != "chapter":
            return []

        head = "\n".join(text.splitlines()[: self.head_lines])
        if not head.strip():
            return []

        entities = self._extract_entities(head, ctx.invariants.language)
        if len(entities) <= self.max_entities:
            return []

        sorted_entities = sorted(entities)
        preview = ", ".join(sorted_entities[: self.max_samples_in_feedback])
        ellipsis = " …" if len(sorted_entities) > self.max_samples_in_feedback else ""
        detail = (
            f"{len(entities)} distinct named entities in first "
            f"{self.head_lines} lines (limit {self.max_entities})"
        )
        prompt_feedback = (
            f"第一章前 {self.head_lines} 行引入了 {len(entities)} 个命名实体："
            f"{preview}{ellipsis}。读者的记忆带宽有限，超过 "
            f"{self.max_entities} 个新名字/新势力会让开篇失去焦点。"
            f"请只保留最多 {self.max_entities} 个：主角 + 核心冲突方 + 场景地标 + "
            "最多 1 个悬念节点。其他角色与设定延后到后续章节再引入。"
        )
        return [
            Violation(
                code=self.code,
                severity="block",
                location=f"chapter:1:head:entities:{len(entities)}",
                detail=detail,
                prompt_feedback=prompt_feedback,
            )
        ]

    @staticmethod
    def _extract_entities(text: str, language: str) -> set[str]:
        if language.lower().startswith("zh"):
            return EntityDensityCheck._extract_zh(text)
        return EntityDensityCheck._extract_en(text)

    @staticmethod
    def _extract_zh(text: str) -> set[str]:
        """Collect distinct Chinese name candidates (single + compound surnames).

        Same stopword + role-suffix filter as NamingConsistencyCheck so the
        entity-density count doesn't include "时候" / "方向" / grammatical-tail
        candidates, and treats "苏师姐" as the single entity "苏" (when it
        resolves against the pool upstream) or keeps it unified locally so
        the opener-density count doesn't double-count role-referenced chars.
        """

        entities: set[str] = set()
        for compound in _ZH_COMPOUND_SURNAMES:
            if compound not in text:
                continue
            for match in re.finditer(
                re.escape(compound) + r"[\u4e00-\u9fff]{1,2}", text
            ):
                candidate = match.group(0)
                stripped = _strip_role_suffix(candidate)
                entities.add(stripped if stripped else candidate)
        for match in _ZH_NAME_RE.finditer(text):
            # Skip positions inside a compound surname (see NamingConsistency).
            start = match.start()
            if start > 0 and text[start - 1 : start + 1] in _ZH_COMPOUND_SURNAMES:
                continue
            candidate = match.group(0)
            if any(candidate.startswith(comp) for comp in _ZH_COMPOUND_SURNAMES):
                continue  # already captured via compound pass
            cleaned = _trim_zh_name_candidate(candidate)
            if cleaned is None:
                continue
            # Collapse role-referenced mentions into the surname root so
            # "苏师姐"/"苏师妹"/"苏" count as one entity, not three.
            stripped = _strip_role_suffix(cleaned)
            entities.add(stripped if stripped else cleaned)
        return entities

    @staticmethod
    def _extract_en(text: str) -> set[str]:
        """Collect distinct English named-entity candidates.

        Normalizes via the same strip-helpers used by NamingConsistencyCheck
        so "And Rowan" / "Rowan's" / "Rowan" all collapse to one entity.
        """

        entities: set[str] = set()
        for match in _EN_HONORIFIC_NAME_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate:
                entities.add(_canonicalize_en_candidate(candidate))
        for match in _EN_MULTIWORD_NAME_RE.finditer(text):
            candidate = match.group(1).strip()
            if not candidate:
                continue
            words = candidate.split()
            if any(w in _EN_CAPITAL_NON_NAMES for w in words):
                continue
            entities.add(_canonicalize_en_candidate(candidate))
        return entities


def _canonicalize_en_candidate(candidate: str) -> str:
    """Apply the strip-helpers iteratively to reach a canonical form.

    "And Rowan's" → "Rowan"; "Pale Mother's" → "Pale Mother"; "Kade" → "Kade".
    """

    current = candidate
    for _ in range(3):  # bounded — each helper reduces length
        poss = _strip_en_possessive(current)
        if poss:
            current = poss
            continue
        starter = _strip_en_sentence_starter(current)
        if starter:
            current = starter
            continue
        break
    return current


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------


class OutputValidator:
    """Composes a list of ``Check``s into a single ``validate`` pass."""

    def __init__(self, checks: list[Check]) -> None:
        self.checks = list(checks)

    def validate(self, text: str, ctx: ValidationContext) -> QualityReport:
        violations: list[Violation] = []
        for check in self.checks:
            violations.extend(check.run(text, ctx) or [])
        return QualityReport(tuple(violations))


def build_phase1_validator(
    *,
    cjk_in_en_ratio_max: float = 0.02,
    latin_in_zh_ratio_max: float = 0.10,
) -> OutputValidator:
    """Factory for the Phase 1 default configuration.

    Phase 1 ships *only* the two highest-confidence checks so the CI audit
    produces almost-zero false positives against novels written before L1-L6
    was in place. Use ``build_full_audit_validator`` when you want the
    broader retrospective sweep (naming / entity / dialog / POV).
    """

    return OutputValidator(
        [
            LanguageSignatureCheck(
                cjk_in_en_ratio_max=cjk_in_en_ratio_max,
                latin_in_zh_ratio_max=latin_in_zh_ratio_max,
            ),
            LengthEnvelopeCheck(),
        ]
    )


def build_full_audit_validator(
    *,
    cjk_in_en_ratio_max: float = 0.02,
    latin_in_zh_ratio_max: float = 0.10,
    naming_frequency_floor: int = 2,
    entity_head_lines: int = 150,
    entity_max_entities: int = 5,
    include_chapter_checks: bool = True,
) -> OutputValidator:
    """Factory for the full retrospective audit validator (L4 + L5 subset).

    Layers composed:

    * L4: ``LanguageSignatureCheck`` — CJK / Latin signature mismatches.
    * L4: ``LengthEnvelopeCheck``   — per-chapter character count envelope.
    * L4: ``NamingConsistencyCheck`` — rogue proper nouns vs seed pool.
    * L4: ``EntityDensityCheck``   — first-chapter 150-line entity cap.
    * L5: ``DialogIntegrityCheck`` — unclosed quote paragraphs.
    * L5: ``POVLockCheck``         — narrative POV drift.

    ``CliffhangerRotationCheck`` is deliberately excluded — it requires a
    time-series ``ctx.recent_cliffhangers`` window that only the iterative
    chapter-by-chapter caller can feed. The ``ContentAuditor`` composes it
    separately when it has that context available.

    The factory tolerates absent context gracefully: NamingConsistency no-ops
    when ``allowed_names`` is empty, EntityDensityCheck no-ops for
    chapters != 1, and so on — so callers can pass one ``ValidationContext``
    per chapter without special-casing.
    """

    # Lazy imports to avoid a cycle — output_validator must not import
    # chapter_validator at module load because chapter_validator imports
    # Check/Violation/ValidationContext from here.
    from bestseller.services.chapter_validator import (
        DialogIntegrityCheck,
        POVLockCheck,
    )

    checks: list[Check] = [
        LanguageSignatureCheck(
            cjk_in_en_ratio_max=cjk_in_en_ratio_max,
            latin_in_zh_ratio_max=latin_in_zh_ratio_max,
        ),
        LengthEnvelopeCheck(),
        NamingConsistencyCheck(frequency_floor=naming_frequency_floor),
        EntityDensityCheck(
            head_lines=entity_head_lines,
            max_entities=entity_max_entities,
        ),
    ]
    if include_chapter_checks:
        checks.extend([DialogIntegrityCheck(), POVLockCheck()])
    return OutputValidator(checks)
