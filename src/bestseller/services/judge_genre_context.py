"""Single source of *genre-neutral* judging context for the commercial quality judges.

Why this module exists
----------------------
The three commercial judges (chapter / outline / planning-readiness) historically
hardcoded one book's detective/exorcism jargon (青囊 / 罗盘 / 铜钱 / 认账 / 镜债 / 账线)
and one genre's commercial structure (the "client commissions a specialist" /
委托制 paradigm) into their prompts. Every book — 言情 / 科幻 / 历史 / 玄幻 — was then
scored against a detective editor's rubric, so the scores were *structurally* wrong
for non-detective genres (see docs/prompt-methodology-fusion-audit-2026-06.md §4c).

This module is the single place that turns a project's ``(genre, sub_genre, story_bible)``
into a :class:`JudgeGenreContext` the judges consume. It does NOT invent a new genre
taxonomy — it binds to the framework's existing infrastructure:

* :func:`bestseller.services.genre_review_profiles.resolve_genre_review_profile`
  — canonical genre→category resolver (also feeds scene/chapter review weights).
* :func:`bestseller.services.genre_profile_thresholds.resolve_thresholds`
  — canonical genre→numeric-knob resolver (hook/coolpoint/pacing).
* The book's own ``story_bible`` — the genre-neutral source of *this* book's
  specialist terms and key objects (never another book's nouns).

So a judge built on top of this module asks "does this chapter clear the bar **for
its own genre**, using **its own** rule terms" instead of "does it look like a 青囊
detective chapter".
"""

# This module is dominated by long Chinese rubric strings (E501 line length) and
# recurses over arbitrary JSON story-bible payloads (ANN401 Any), mirroring the
# convention in chapter_llm_quality_judge.py / outline_llm_judge.py.
# ruff: noqa: ANN401, E501

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_REFERENCE_CORPORA_DIR = (
    Path(__file__).resolve().parents[3] / "config" / "reference_corpora"
)

#: Universal fallback corpus key. Always present (config/reference_corpora/generic.yaml).
GENERIC_CORPUS_KEY = "generic"


# ---------------------------------------------------------------------------
# Story-logic checks — the "6 项故事合理性" generalised across genres.
#
# The detective/commission framing ("why did the client come to the protagonist
# instead of calling the police") is correct ONLY for commission-structured genres
# (detective / urban-supernatural service). For everything else we use the
# universal commercial-retention fundamentals and let each category overlay the
# items that actually matter for it. Each entry is a short imperative the judge
# must verify; the book's own terms are injected at render time.
# ---------------------------------------------------------------------------

# Genre-neutral default — applies to ANY genre. These are the true universals of
# commercial web-fiction retention, with zero genre-specific structure baked in.
_NEUTRAL_CHECKS_ZH: tuple[str, ...] = (
    "主角立得住:读者凭什么相信这个主角能驱动这条故事线?必须看到可信的身份/能力/处境/动机之一,而非凭空降临。",
    "入局动机充分:主角为什么卷入本章的核心冲突?欲望/威胁/旧账/责任/野心/好奇之一,理由要可被读者重构。",
    "核心卖点可见:本书的核心爽点/金手指/钩子(无论是什么)在黄金三章必须有一次在场可见的展示,不能只在背景里说他/它厉害。",
    "世界规则自洽:本书自己设定的规则(力量体系/社会规则/谜题规则/情感逻辑——以本书为准)前后一致、可被主角合理推断,不靠单一感官捷径替代推理。",
    "现实/常识不破:涉及现实流程与常识处给真实细节与因果链;若刻意反常,正文必须明确让角色意识到'不可能/异常'。",
    "信息密度可控:第一章不堆砌私设术语,靠现象+反应+悬念驱动,读者能跟得上。",
)
_NEUTRAL_CHECKS_EN: tuple[str, ...] = (
    "Protagonist holds up: why should the reader believe THIS protagonist drives the story? Show a credible identity / capability / situation / motive — not a deus-ex arrival.",
    "Reason to engage: why is the protagonist pulled into this chapter's core conflict? Desire / threat / old debt / duty / ambition / curiosity — reconstructible by the reader.",
    "Core hook on-screen: this book's central payoff / golden-finger / hook (whatever it is) is demonstrated visibly within the golden three chapters — never merely asserted.",
    "Self-consistent rules: whatever rules THIS book sets (power system / social rules / mystery rules / emotional logic) stay consistent and inferable — no single sensory shortcut replacing reasoning.",
    "Real-world logic intact: real detail + causal chain for any real procedure/common sense; if deliberately broken, the prose makes a character register it as impossible/abnormal.",
    "Controlled info density: chapter 1 does not dump private terminology; phenomena + reaction + suspense carry it so the reader keeps up.",
)

# Per-category overlays. Each maps category_key → checks that REPLACE the neutral
# default for that genre. Categories not listed here use the neutral default. The
# commission/detective framing lives ONLY under suspense-mystery (+ urban-supernatural
# via the resolver), so it can never leak into 言情/历史/科幻 again.
_CATEGORY_CHECKS_ZH: dict[str, tuple[str, ...]] = {
    "suspense-mystery": (
        "主角召唤合理性:读者凭什么信主角能解决?家学/师承/前案口碑/熟人转介/能力实证之一。",
        "委托/卷入动机:为什么找主角而不是 110/物业/家人/120,或主角为什么主动出现?关系链或线索要可重构。",
        "主角入场动机:钱/家族线索/职业惯性/旧账之一,不能莫名其妙出现在现场。",
        "能力实证:主角的特殊能力(以本书设定为准)本章必须有≥1次可验证展示。",
        "现实流程合理:报警/物业/医院/快递等反应符合常理;若反常,正文必须标记为异常。",
        "信息节奏:高概念可铺垫但第一章不术语堆砌,靠现象+反应+怀疑驱动。",
    ),
    "action-progression": (
        "主角立得住:出身/处境/缺憾让读者愿意跟随,起点低也要有翻盘的钩子。",
        "升级动机充分:主角为什么要变强/复仇/求生?目标要具体且有压力。",
        "金手指可见:本书的核心金手指/系统/功法(以本书设定为准)黄金三章必须有一次清晰的实战/实效展示,并付出可见代价。",
        "实力体系自洽:境界/等级/规则前后一致,战力可比较,主角的胜负有逻辑而非作者强行。",
        "爽点兑现:本章须兑现一个打脸/逆袭/突破/获得的爽点,给读者明确追更理由。",
        "信息密度可控:世界观与术语循序释放,第一章不一次性灌输设定。",
    ),
    "relationship-driven": (
        "人设立体:男女主(或核心关系方)有可被记住的具体特质,非工具人。",
        "情感动机可信:双方为何被彼此吸引/对立?动机要落到具体事件与处境,不是'命中注定'四个字。",
        "关系张力在场:本章须有一次可感的关系推进或拉扯(靠近/误会/试探/失控),不是干等。",
        "情感逻辑自洽:人物的好恶与转变有铺垫,前后行为一致,不为撒糖/虐心强行 OOC。",
        "化学反应可见:互动里有专属两人的细节(称呼/习惯/旧事),让 CP 感成立。",
        "信息密度可控:第一章不堆砌前史与设定,关系靠当下互动带出。",
    ),
    "strategy-worldbuilding": (
        "主角立得住:在格局中的身份/资源/抱负清晰,读者信他能搅动局势。",
        "入局动机充分:主角为何下场争/守/谋?利益/危机/责任之一,且有具体对手。",
        "谋略/格局可见:本书的核心博弈(权谋/战争/经营/科技——以本书为准)黄金三章须有一次可见的较量或推演。",
        "世界规则自洽:势力/制度/资源/规则前后一致,因果可推,胜负不靠主角光环。",
        "现实/逻辑不破:涉及制度流程与常识处经得起推敲;反常需有解释。",
        "信息密度可控:庞大设定循序释放,第一章不一次性铺开世界观。",
    ),
}
_CATEGORY_CHECKS_EN: dict[str, tuple[str, ...]] = {
    "suspense-mystery": (
        "Summon plausibility: why trust the protagonist? family craft / mentorship / prior-case reputation / referral / demonstrated skill.",
        "Why this protagonist: why come to them and not the police/property mgr/family — or why does the protagonist show up — with a reconstructible link or clue.",
        "Entry motive: money / family lead / professional habit / old debt — never an unexplained appearance at the scene.",
        "Capability proof: the protagonist's special ability (per THIS book's setting) gets ≥1 verifiable on-screen demonstration this chapter.",
        "Real-procedure logic: police/property/hospital/courier reactions match common sense; if abnormal, the prose marks it abnormal.",
        "Info pacing: high concept may be seeded but chapter 1 avoids jargon dumping — phenomena + reaction + doubt drive it.",
    ),
    "action-progression": (
        "Protagonist holds up: background/situation/flaw makes the reader want to follow; a low start still needs a comeback hook.",
        "Progression motive: why does the protagonist seek power/revenge/survival? concrete goal under pressure.",
        "Golden-finger on-screen: this book's core system/cheat/art (per its setting) shows one clear combat/effect demonstration in the golden three, at a visible cost.",
        "Self-consistent power: tiers/levels/rules stay consistent, power is comparable, wins follow logic not author fiat.",
        "Payoff delivered: this chapter delivers one face-slap/comeback/breakthrough/gain to justify reading on.",
        "Controlled info density: worldbuilding and terms release gradually; chapter 1 does not front-load the setting.",
    ),
    "relationship-driven": (
        "Three-dimensional cast: the leads (or core relationship parties) have memorable concrete traits — not function props.",
        "Credible attraction/conflict: why are they drawn to / opposed to each other? grounded in concrete events, not 'destiny'.",
        "Relationship tension present: this chapter lands one palpable relationship move (closeness/misunderstanding/probe/loss-of-control).",
        "Self-consistent emotional logic: likes/dislikes and turns are set up; behavior stays consistent — no OOC for the sake of sugar/angst.",
        "Visible chemistry: interactions carry details unique to the pair (nicknames/habits/shared history) that make the CP land.",
        "Controlled info density: chapter 1 does not dump backstory; the relationship emerges from present interaction.",
    ),
    "strategy-worldbuilding": (
        "Protagonist holds up: identity/resources/ambition within the larger game are clear; the reader believes they can move the board.",
        "Reason to engage: why does the protagonist enter the fight/defense/scheme? interest/crisis/duty, with a concrete adversary.",
        "Strategy/scale on-screen: the book's core contest (politics/war/management/tech — per its setting) shows one visible clash or deduction in the golden three.",
        "Self-consistent world: factions/institutions/resources/rules stay consistent and causal; outcomes don't rely on a protagonist halo.",
        "Real/logic intact: institutional procedure and common sense hold up; the abnormal needs explanation.",
        "Controlled info density: a large setting releases gradually; chapter 1 does not unfurl the whole world at once.",
    ),
}

# Categories that genuinely use the "client commissions a specialist" structure, so
# the commission-specific framing (why not the police, etc.) is appropriate. Every
# other genre uses generalized "why is the protagonist involved" framing.
_COMMISSION_STRUCTURE_CATEGORIES: frozenset[str] = frozenset(
    {"suspense-mystery", "urban-contemporary"}
)


@dataclass(frozen=True)
class JudgeGenreContext:
    """Everything a commercial judge needs to score a book *in its own genre*."""

    category_key: str
    corpus_key: str
    display_genre: str
    story_logic_checks_zh: tuple[str, ...]
    story_logic_checks_en: tuple[str, ...]
    specialist_terms: tuple[str, ...]
    key_objects: tuple[str, ...]
    signal_keywords: tuple[str, ...]
    uses_commission_structure: bool

    def render_story_logic_block(self, language: str = "zh") -> str:
        is_en = str(language or "").lower().startswith("en")
        checks = self.story_logic_checks_en if is_en else self.story_logic_checks_zh
        header = (
            "# STORY-LOGIC CHECKS (genre: "
            f"{self.display_genre}) — any clear miss → blocking\n"
            if is_en
            else f"# 故事合理性核查(题材:{self.display_genre})— 任一明显缺失即 blocking\n"
        )
        body = "\n".join(f"{i}. {c}" for i, c in enumerate(checks, start=1))
        return header + body + "\n"

    def render_own_terms_block(self, language: str = "zh") -> str:
        """Tell the judge to reason about THIS book's own terms, not a fixed genre's."""

        is_en = str(language or "").lower().startswith("en")
        terms = "、".join(self.specialist_terms) if self.specialist_terms else ""
        objects = "、".join(self.key_objects) if self.key_objects else ""
        if is_en:
            lines = [
                "# THIS BOOK'S OWN TERMS (judge by these — do NOT import another genre's nouns)",
                f"- specialist/rule terms: {terms or '(derive from the text; none preset)'}",
                f"- key objects/abilities: {objects or '(derive from the text; none preset)'}",
            ]
        else:
            lines = [
                "# 本书自有术语(按这些判断 — 不要套用其它题材的名词)",
                f"- 专业/规则术语:{terms or '(从正文判断,无预设)'}",
                f"- 关键道具/能力:{objects or '(从正文判断,无预设)'}",
            ]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Bible-derived term extraction (genre-neutral: each book contributes its own).
# ---------------------------------------------------------------------------


def derive_specialist_rule_terms(story_bible: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Pull THIS book's own specialist / rule terminology out of its worldview.

    Mirrors the genre-neutral extraction already used by the chapter generation
    input builder so the judge references the same book-owned vocabulary the
    writer was handed — never one detective book's jargon.
    """

    if not isinstance(story_bible, Mapping):
        return ()
    terms: list[str] = []

    def _collect(value: Any, depth: int = 0) -> None:
        if depth > 3 or len(terms) >= 24:
            return
        if isinstance(value, str):
            t = value.strip()
            if 2 <= len(t) <= 8 and t not in terms:
                terms.append(t)
        elif isinstance(value, Mapping):
            for key in ("name", "term", "title", "key", "label"):
                if isinstance(value.get(key), str):
                    _collect(value[key], depth + 1)
            for nested_key in (
                "terms", "rules", "systems", "power_system", "power_systems",
                "entries", "items", "glossary",
            ):
                if nested_key in value:
                    _collect(value[nested_key], depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                _collect(item, depth + 1)

    for field in (
        "worldview_kernel", "worldview", "power_system", "power_systems", "systems",
        "rules", "rule", "glossary", "terminology", "rule_terms", "specialist_terms",
        "key_terms",
    ):
        if field in story_bible:
            _collect(story_bible.get(field))
    return tuple(dict.fromkeys(terms))


def _derive_key_objects(story_bible: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(story_bible, Mapping):
        return ()
    objects: list[str] = []

    def _collect(value: Any, depth: int = 0) -> None:
        if depth > 3 or len(objects) >= 16:
            return
        if isinstance(value, str):
            t = value.strip()
            if 2 <= len(t) <= 10 and t not in objects:
                objects.append(t)
        elif isinstance(value, Mapping):
            for key in ("name", "title", "label"):
                if isinstance(value.get(key), str):
                    _collect(value[key], depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                _collect(item, depth + 1)

    for field in (
        "key_objects", "artifacts", "items", "props", "signature_objects",
        "abilities", "golden_finger", "core_ability",
    ):
        if field in story_bible:
            _collect(story_bible.get(field))
    return tuple(dict.fromkeys(objects))


# ---------------------------------------------------------------------------
# Reference-corpus availability.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def available_reference_corpus_keys() -> frozenset[str]:
    """Genre keys that have a hand-authored reference corpus on disk."""

    try:
        return frozenset(
            p.stem for p in _REFERENCE_CORPORA_DIR.glob("*.yaml") if p.is_file()
        )
    except OSError:
        return frozenset({GENERIC_CORPUS_KEY})


def resolve_reference_corpus_key(category_key: str) -> str:
    """Pick the corpus to score against: the genre's own if present, else generic.

    Crucially this NEVER falls back to ``suspense-mystery`` for a non-detective
    book — an unmatched genre gets the genre-neutral ``generic`` corpus instead of
    a detective sample set.
    """

    keys = available_reference_corpus_keys()
    if category_key in keys:
        return category_key
    if GENERIC_CORPUS_KEY in keys:
        return GENERIC_CORPUS_KEY
    return GENERIC_CORPUS_KEY


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_judge_genre_context(
    *,
    genre: str | None,
    sub_genre: str | None = None,
    story_bible: Mapping[str, Any] | None = None,
    genre_preset_key: str | None = None,
) -> JudgeGenreContext:
    """Resolve the genre-neutral judging context for a project.

    Binds to :func:`resolve_genre_review_profile` for the canonical category and
    signal keywords, then layers this book's own bible-derived terms on top.
    """

    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    try:
        profile = resolve_genre_review_profile(
            str(genre or "general-fiction"),
            sub_genre,
            genre_preset_key=genre_preset_key,
        )
        category_key = profile.category_key
        sk = profile.signal_keywords
        signal_keywords = tuple(
            dict.fromkeys(
                [*sk.conflict_terms_zh, *sk.hook_terms_zh, *sk.info_terms_zh]
            )
        )[:16]
    except Exception:
        category_key = "default"
        signal_keywords = ()

    checks_zh = _CATEGORY_CHECKS_ZH.get(category_key, _NEUTRAL_CHECKS_ZH)
    checks_en = _CATEGORY_CHECKS_EN.get(category_key, _NEUTRAL_CHECKS_EN)

    return JudgeGenreContext(
        category_key=category_key,
        corpus_key=resolve_reference_corpus_key(category_key),
        display_genre=str(genre or category_key or "general-fiction"),
        story_logic_checks_zh=checks_zh,
        story_logic_checks_en=checks_en,
        specialist_terms=derive_specialist_rule_terms(story_bible),
        key_objects=_derive_key_objects(story_bible),
        signal_keywords=signal_keywords,
        uses_commission_structure=category_key in _COMMISSION_STRUCTURE_CATEGORIES,
    )


__all__ = [
    "GENERIC_CORPUS_KEY",
    "JudgeGenreContext",
    "available_reference_corpus_keys",
    "derive_specialist_rule_terms",
    "resolve_judge_genre_context",
    "resolve_reference_corpus_key",
]
