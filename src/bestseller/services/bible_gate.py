"""L2 Bible Completeness Gate.

The Bible gate sits between bible generation and chapter generation. Its
job is to refuse commercial-quality shortcuts at the *planning* stage so the
downstream pipeline never has to paper over missing foundations.

Every validator addresses a historical bug the four in-flight productions
exhibited — they all stem from a bible that skimped on the commercial
anchors readers use to remember characters and feel suspense:

* ``CharacterIPAnchorCheck`` — protagonists must carry 3+ concrete quirks
  and a core wound (bug #14: "no memorable features").
* ``CharacterPersonhoodCheck`` — protagonists must carry a psych profile,
  a life history, a family imprint, and a belief system (sibling of bug
  #14: "characters feel like plot functions, not people").
* ``VillainCharismaCheck`` — primary antagonists must declare ≥4 of the
  7 villain_charisma fields so the villain reads as a tragic rival, not
  a difficulty slider.
* ``AntagonistMotiveLedger`` — distinct antagonists cannot share the same
  "被轻视/复仇" template (bug #8: "every antagonist feels the same").
* ``WorldTaxonomyUniqueness`` — a power system that fully matches a
  boilerplate progression list is rejected (bug #13: "2010 upgrade template").
* ``NamingPoolSize`` — the naming pool must be at least 2× the expected
  character count so the writer room never has to improvise ad-hoc names
  mid-book (bug #6: "naming chaos").
* ``ThemeSignatureCheck`` — the project must carry a single-sentence
  theme_statement + dramatic_question so every later decision echoes back
  to the same meta-frame (prerequisite for #5/#9 fixes).

Each check returns a ``BibleDeficiency`` whose ``prompt_feedback`` is the
exact integration instruction handed to the bible regeneration loop —
"fix this specifically, don't just re-roll the dice".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Protocol
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.contradiction import (
    ContradictionCheckResult,
    ContradictionViolation,
    ContradictionWarning,
)
from bestseller.domain.story_bible import CharacterInput
from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.invariants import ProjectInvariants
from bestseller.services.writing_profile import is_english_language


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BibleDeficiency:
    """A single, actionable gap in the bible.

    ``prompt_feedback`` is what the bible regeneration step feeds back to the
    LLM — it must be specific enough that a re-roll is deterministic, not a
    reshuffled cliché.
    """

    code: str
    location: str
    detail: str
    prompt_feedback: str

    def as_checker_issue(self) -> CheckerIssue:
        """Adapt to Phase A1 unified schema. Bible deficiencies are hard —
        the bible is the foundation; you cannot override it."""

        return CheckerIssue(
            id=self.code,
            type="bible_gate",
            severity="high",
            location=self.location,
            description=self.detail,
            suggestion=self.prompt_feedback,
            can_override=False,
        )


@dataclass(frozen=True)
class BibleCompletenessReport:
    deficiencies: tuple[BibleDeficiency, ...]

    @property
    def passes(self) -> bool:
        return not self.deficiencies

    def by_code(self) -> dict[str, list[BibleDeficiency]]:
        bucket: dict[str, list[BibleDeficiency]] = {}
        for d in self.deficiencies:
            bucket.setdefault(d.code, []).append(d)
        return bucket

    def feedback_for_regen(self) -> str:
        if not self.deficiencies:
            return ""
        lines = ["Bible 回炉整改清单——请精确针对以下问题重写相关字段："]
        for idx, d in enumerate(self.deficiencies, 1):
            lines.append(f"\n{idx}) [{d.code}] {d.location}\n   现状：{d.detail}\n   整改：{d.prompt_feedback}")
        return "\n".join(lines)

    def as_checker_report(self, chapter: int = 0) -> CheckerReport:
        """Adapt to Phase A1 unified schema. ``chapter`` defaults to 0 because
        bible-gate runs *before* chapter generation — it's a project-level
        check, but the scorecard needs a chapter slot."""

        issues = tuple(d.as_checker_issue() for d in self.deficiencies)
        score = 100 if self.passes else max(0, 100 - len(self.deficiencies) * 10)
        return CheckerReport(
            agent="bible-gate",
            chapter=chapter,
            overall_score=score,
            passed=self.passes,
            issues=issues,
            metrics={"deficiency_count": len(self.deficiencies)},
            summary=(
                "Bible 完备性校验通过"
                if self.passes
                else f"Bible 存在 {len(self.deficiencies)} 条硬性缺陷，必须整改"
            ),
        )


@dataclass(frozen=True)
class BibleDraft:
    """What the checker sees — a Bible *candidate*, not yet committed.

    Kept as a dataclass rather than reusing the ORM models because the gate
    runs *before* bible materialization: we validate the pydantic draft, not
    the persisted rows.
    """

    characters: tuple[CharacterInput, ...]
    power_system_tiers: tuple[str, ...] = ()
    power_system_name: str | None = None
    naming_pool: tuple[str, ...] = ()
    expected_character_count: int = 0
    theme_statement: str | None = None
    dramatic_question: str | None = None


# ---------------------------------------------------------------------------
# Validator protocol.
# ---------------------------------------------------------------------------


class BibleValidator(Protocol):
    code: str

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]: ...  # pragma: no cover - protocol


# ---------------------------------------------------------------------------
# Character IP anchors (bug #14).
# ---------------------------------------------------------------------------


class CharacterIPAnchorCheck:
    """Protagonists need >=3 concrete quirks AND a core wound.

    Three is the commercial-editor rule of thumb — below that, the
    protagonist dissolves into an archetype (stoic swordsman, clever
    princess, reluctant chosen one). Antagonists get a lighter bar (>=2) so
    the editor room can still ship supporting villains fast.

    ``core_wound`` is the psychological through-line that explains every
    irrational decision a character makes. It is separate from ``flaw``
    (which is situational) and ``fear`` (which is reactive); without it,
    emotional payoffs feel arbitrary and the narrative drifts into
    "told, not earned" beats (historical bug #9).
    """

    code = "CHARACTER_IP_ANCHOR_MISSING"

    PROTAGONIST_MIN_QUIRKS = 3
    ANTAGONIST_MIN_QUIRKS = 2

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        for char in draft.characters:
            anchor = char.ip_anchor
            quirk_count = len([q for q in anchor.quirks if q and q.strip()])
            role_lower = (char.role or "").lower()

            if "protagonist" in role_lower:
                required_quirks = self.PROTAGONIST_MIN_QUIRKS
            elif "antagonist" in role_lower:
                required_quirks = self.ANTAGONIST_MIN_QUIRKS
            else:
                continue  # supporting cast exempt — would explode the bible

            if quirk_count < required_quirks:
                yield BibleDeficiency(
                    code=self.code,
                    location=f"character:{char.name}",
                    detail=(
                        f"{char.name} 当前仅有 {quirk_count} 个 quirk，"
                        f"需 >={required_quirks}"
                    ),
                    prompt_feedback=(
                        f"为角色 {char.name} 补充至少 "
                        f"{required_quirks - quirk_count} 个可记忆的具体特征（例：小提琴"
                        f"演奏、洁癖、左手食指关节断裂、口头禅 "
                        f"'这不科学'）。每一个特征必须具体到一个动作或物件，"
                        f"不能写成抽象描述（如 '冷酷' / '善良' 不合格）。"
                    ),
                )

            if not anchor.core_wound or not anchor.core_wound.strip():
                if "protagonist" in role_lower:
                    yield BibleDeficiency(
                        code="CORE_WOUND_MISSING",
                        location=f"character:{char.name}",
                        detail=f"{char.name} 缺少 core_wound",
                        prompt_feedback=(
                            f"为角色 {char.name} 写出一个 core_wound "
                            f"(心理核心创伤)：一个一句话的过去事件，"
                            f"它驱动该角色所有非理性决定。例：'七岁那年目睹母亲"
                            f"被处决'、'被师父当众宣判无修炼资格'。"
                            f"该创伤必须贯穿全书，反复被触发、被治愈或被重演。"
                        ),
                    )


# ---------------------------------------------------------------------------
# Personhood layer — protagonist must read like a person, antagonist must
# have charisma. The IP anchor check above guards memorability; these
# checks guard humanity. (Sibling of bug #14 / bug #8.)
# ---------------------------------------------------------------------------


class CharacterPersonhoodCheck:
    """Protagonists need a psychology, a past, a family, and beliefs.

    The IP anchor check ensures readers *remember* a character; this check
    ensures readers *believe* the character. Without psych_profile,
    life_history, family_imprint, or beliefs, the LLM falls back to
    archetype defaults — chapter prompts produce technically-correct
    decisions that feel hollow because they have no internal cause.

    Required for protagonists; optional for everyone else (forcing a 12-
    field personhood layer on every supporting character would explode
    the bible). Antagonists are handled by ``VillainCharismaCheck`` below.
    """

    code = "CHARACTER_PERSONHOOD_INCOMPLETE"

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        for char in draft.characters:
            if "protagonist" not in (char.role or "").lower():
                continue

            missing: list[str] = []
            psych = char.psych_profile
            if not (psych.mbti or psych.enneagram or psych.big_five or psych.temperament):
                missing.append("psych_profile（MBTI/九型/OCEAN/气质 至少其一）")

            history = char.life_history
            has_history = bool(
                history.formative_events
                or history.education
                or history.career_history
                or history.defining_moments
            )
            if not has_history:
                missing.append("life_history（formative_events / defining_moments / education / career_history）")

            family = char.family_imprint
            has_family = bool(
                family.parenting_style
                or family.family_socioeconomic
                or family.sibling_dynamics
                or family.inherited_values
            )
            if not has_family:
                missing.append("family_imprint（parenting_style / sibling_dynamics / inherited_values）")

            beliefs = char.beliefs
            has_beliefs = bool(
                beliefs.religion
                or beliefs.philosophical_stance
                or beliefs.ideology
            )
            if not has_beliefs:
                missing.append("beliefs（religion / philosophical_stance / ideology）")

            if missing:
                yield BibleDeficiency(
                    code=self.code,
                    location=f"character:{char.name}",
                    detail=f"{char.name} 缺少人格底层：{ '；'.join(missing) }",
                    prompt_feedback=(
                        f"主角 {char.name} 必须以真实的人来塑造。请补全以下字段：\n"
                        + "\n".join(f"  - {m}" for m in missing)
                        + "\n这些字段决定了 ta 在每一章中如何做选择、说什么话、对谁让步。"
                        f"参考真实的 MBTI/九型/Big Five 数据集与生命经历，写出一个"
                        f"具体的人，不要写抽象类型。"
                    ),
                )


class VillainCharismaCheck:
    """Antagonists must have a noble seed, a redeemable trait, and a code.

    A pure-evil villain is a stat block. Commercial bestsellers turn
    villains into rivals readers grieve when they fall — that requires
    declaring the noble motivation, the pain origin, the redeeming
    qualities, and a personal code. Without these, the LLM writes
    villains who escalate violence linearly until the protagonist wins,
    which reads as filler rather than tragedy.

    Required for ``role`` = ``antagonist``. Major antagonists must declare
    at least 4 of 7 fields; lieutenant-tier antagonists are exempt
    (caught by ``"antagonist_lieutenant"`` substring).
    """

    code = "VILLAIN_CHARISMA_MISSING"
    REQUIRED_FIELD_COUNT = 4

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        for char in draft.characters:
            role_lower = (char.role or "").lower()
            if "antagonist" not in role_lower:
                continue
            if "lieutenant" in role_lower or "henchman" in role_lower:
                continue  # supporting heavies exempt

            v = char.villain_charisma
            populated = sum(
                1
                for present in (
                    v.noble_motivation,
                    v.pain_origin,
                    v.philosophical_appeal,
                    v.tragic_irony,
                    v.protagonist_mirror,
                    bool(v.redeeming_qualities),
                    bool(v.personal_code),
                )
                if present
            )

            if populated < self.REQUIRED_FIELD_COUNT:
                yield BibleDeficiency(
                    code=self.code,
                    location=f"character:{char.name}",
                    detail=(
                        f"反派 {char.name} villain_charisma 仅填 {populated}/7，"
                        f"需 >={self.REQUIRED_FIELD_COUNT}"
                    ),
                    prompt_feedback=(
                        f"反派 {char.name} 不能是纯坏。请至少填写 "
                        f"{self.REQUIRED_FIELD_COUNT} 项 villain_charisma 字段："
                        f"\n  - noble_motivation：ta 出发点中合理甚至高尚的部分"
                        f"\n  - pain_origin：ta 黑化的具体伤痛事件"
                        f"\n  - redeeming_qualities：让读者心软的具体瞬间（≥1 条）"
                        f"\n  - philosophical_appeal：ta 世界观中读者会动摇赞同的部分"
                        f"\n  - personal_code：ta 绝不做的事（≥1 条），让 ta 不是疯子"
                        f"\n  - tragic_irony：为达目的反而毁掉初心所爱"
                        f"\n  - protagonist_mirror：与主角的相似处（同根而异路）"
                        f"\n反派的目标是让读者在 ta 落败时也感到失落，不是只想看 ta 死。"
                    ),
                )


# ---------------------------------------------------------------------------
# Antagonist motive uniqueness (bug #8).
# ---------------------------------------------------------------------------


_STOPWORDS_ZH_EN = {
    "的", "了", "是", "在", "和", "与", "及", "对", "被", "因", "为", "要", "让",
    "the", "and", "to", "of", "a", "an", "for", "by", "in", "on", "with",
}


def _keyword_bag(text: str | None) -> set[str]:
    """Split on non-letter / non-CJK, lowercase, drop stopwords.

    Used to compute Jaccard similarity between antagonist motives. Accurate
    enough for duplicate-template detection without dragging in an NLP
    dependency.
    """
    if not text:
        return set()
    import re
    tokens = re.findall(r"[\u4e00-\u9fffA-Za-z]+", text)
    return {t.lower() for t in tokens if t.lower() not in _STOPWORDS_ZH_EN and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


class AntagonistMotiveLedger:
    """Reject two antagonists whose motive bag-of-keywords Jaccard > threshold.

    Keyword bag rather than an embedding similarity because we only want to
    catch the "both are 被轻视 therefore 复仇" template, not stylistic
    overlap. 0.4 empirically rejects the four-novel-template defect while
    leaving genuine rhymed-motif pairs (two betrayers, different causes)
    free to stand.
    """

    code = "ANTAGONIST_MOTIVE_OVERLAP"
    SIMILARITY_THRESHOLD = 0.4

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        antagonists = [
            c for c in draft.characters
            if "antagonist" in (c.role or "").lower()
        ]
        if len(antagonists) < 2:
            return

        bags = [(c, _keyword_bag(c.goal) | _keyword_bag(c.background) | _keyword_bag(c.secret)) for c in antagonists]

        for (a, bag_a), (b, bag_b) in combinations(bags, 2):
            sim = _jaccard(bag_a, bag_b)
            if sim > self.SIMILARITY_THRESHOLD:
                yield BibleDeficiency(
                    code=self.code,
                    location=f"characters:{a.name},{b.name}",
                    detail=f"动机相似度 {sim:.2f} > {self.SIMILARITY_THRESHOLD}",
                    prompt_feedback=(
                        f"反派 {a.name} 与 {b.name} 的动机关键词重叠率过高 "
                        f"({sim:.0%})。两人必须有本质不同的驱动力："
                        f"比如一个要权力、另一个要救赎；一个要永生、另一个"
                        f"要毁灭。请重写其中一位的 goal / background / secret，"
                        f"使关键词几乎不重合。"
                    ),
                )


# ---------------------------------------------------------------------------
# World taxonomy uniqueness (bug #13).
# ---------------------------------------------------------------------------


class WorldTaxonomyUniqueness:
    """Reject power systems whose tier labels match well-known templates.

    Boilerplate templates are listed as ordered tuples so exact-sequence
    matches fail; scattered tiers that just happen to share words don't
    trip the check.
    """

    code = "WORLD_TAXONOMY_BOILERPLATE"

    BOILERPLATE_BLACKLIST: tuple[tuple[str, ...], ...] = (
        ("炼气", "筑基", "金丹", "元婴"),  # 仙侠标准模板
        ("见习", "骑士", "大师", "传奇"),  # 西幻 2010 模板
        ("novice", "apprentice", "master", "legend"),
        ("bronze", "silver", "gold", "platinum"),
    )

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        tiers = tuple(t.strip().lower() for t in draft.power_system_tiers if t)
        if not tiers:
            return
        for blacklist in self.BOILERPLATE_BLACKLIST:
            bl_lower = tuple(b.lower() for b in blacklist)
            if all(bl in tiers for bl in bl_lower):
                yield BibleDeficiency(
                    code=self.code,
                    location=f"power_system:{draft.power_system_name or '<unnamed>'}",
                    detail=f"tiers 完全命中模板：{list(blacklist)}",
                    prompt_feedback=(
                        f"当前力量体系的 tier 名称 {list(draft.power_system_tiers)} "
                        f"与 2010 年代通用模板完全重合，读者一眼就能猜到每一阶"
                        f"所代表的能力。请重新设计 tier 名称，使其贴合本书独有的"
                        f"世界观（例：不是 '炼气→筑基' 而是 '承脉→裂象→铸神'）。"
                        f"每一阶应暗示一种独特的能力机制，而不仅仅是更强。"
                    ),
                )
                break  # 一个黑名单命中即足以发一条 finding


# ---------------------------------------------------------------------------
# Naming pool size (bug #6).
# ---------------------------------------------------------------------------


class NamingPoolSize:
    """Naming pool must be at least 2x the expected character count.

    The 2x multiplier gives the writer room a "reserve" so new supporting
    characters have pre-approved names ready — we observed 4 productions
    drift into mid-book ad-hoc naming precisely because the pool ran dry
    around chapter 30.
    """

    code = "NAMING_POOL_UNDERSIZED"
    MULTIPLIER = 2.0

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        if not draft.expected_character_count:
            return
        required = int(draft.expected_character_count * self.MULTIPLIER)
        have = len([n for n in draft.naming_pool if n and n.strip()])
        if have < required:
            yield BibleDeficiency(
                code=self.code,
                location="naming_pool",
                detail=f"池容量 {have} < 要求 {required} (expected × {self.MULTIPLIER})",
                prompt_feedback=(
                    f"命名池当前有 {have} 个名字，但本书预计有 "
                    f"{draft.expected_character_count} 个命名角色，"
                    f"需预留 {required} 个名字以应对中后期新增人物。"
                    f"请补充至少 {required - have} 个风格一致的候选名字。"
                ),
            )


# ---------------------------------------------------------------------------
# Theme signature + dramatic question.
# ---------------------------------------------------------------------------


class ThemeSignatureCheck:
    """Every commercial bestseller answers a single dramatic question.

    A missing theme_statement / dramatic_question is the root cause of
    bug #9 ("情感告知不演出") — without a declared theme, each chapter
    decides its own emotional rhetoric, and the story never accumulates
    into something singular. We require both at the project level; they
    live on ProjectModel (not StoryBible) so the prompt constructor can
    echo them into every downstream chapter prompt.
    """

    code = "THEME_SIGNATURE_MISSING"

    def check(
        self, draft: BibleDraft, invariants: ProjectInvariants
    ) -> Iterable[BibleDeficiency]:
        theme = (draft.theme_statement or "").strip()
        dq = (draft.dramatic_question or "").strip()
        if not theme:
            yield BibleDeficiency(
                code="THEME_STATEMENT_MISSING",
                location="project",
                detail="theme_statement 为空",
                prompt_feedback=(
                    "请写出一句话主题 (theme_statement)：全书要向读者证明的"
                    "单一核心命题。例：'真正的力量来自承认自己的脆弱'、"
                    "'复仇不是救赎，而是被救赎者抛弃的代价'。"
                    "主题必须是一个可被证伪的断言，不能是'关于成长'这种描述。"
                ),
            )
        if not dq:
            yield BibleDeficiency(
                code="DRAMATIC_QUESTION_MISSING",
                location="project",
                detail="dramatic_question 为空",
                prompt_feedback=(
                    "请写出全书的戏剧性问题 (dramatic_question)："
                    "一个最后一章才能回答的 yes/no 问题。"
                    "例：'林奚能否在拯救妹妹与守护家族之间找到两全？'、"
                    "'这场瘟疫背后的真相会让主角毁灭还是重生？'"
                    "该问题必须驱动每一章的悬念走向。"
                ),
            )


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------


def default_validators() -> list[BibleValidator]:
    """Phase 2 default set — matches the five listed in the module docstring."""

    return [
        CharacterIPAnchorCheck(),
        CharacterPersonhoodCheck(),
        VillainCharismaCheck(),
        AntagonistMotiveLedger(),
        WorldTaxonomyUniqueness(),
        NamingPoolSize(),
        ThemeSignatureCheck(),
    ]


def validate_bible_completeness(
    draft: BibleDraft,
    invariants: ProjectInvariants,
    validators: Iterable[BibleValidator] | None = None,
) -> BibleCompletenessReport:
    """Run every validator and aggregate findings.

    Validators are independent — one failing doesn't short-circuit the
    others, so the regeneration step gets the full integration list in
    a single pass (not a whack-a-mole).
    """

    checks = list(validators) if validators is not None else default_validators()
    findings: list[BibleDeficiency] = []
    for validator in checks:
        findings.extend(validator.check(draft, invariants))
    return BibleCompletenessReport(deficiencies=tuple(findings))


# ---------------------------------------------------------------------------
# Dict → BibleDraft adapter.
# ---------------------------------------------------------------------------


def build_draft_from_materialization_content(
    *,
    book_spec_content: dict | None,
    world_spec_content: dict | None,
    cast_spec_content: dict | None,
) -> BibleDraft:
    """Assemble a ``BibleDraft`` from the raw dict content passed to
    ``materialize_story_bible``.

    The gate runs on candidate content — before persistence — so we cannot
    rely on the ORM rows. We lift the fields the five validators care about
    out of the three spec dicts (book/world/cast) into a single struct.

    Fields that aren't present yet (e.g. ``theme_statement`` may live under
    several legacy aliases) are coerced to ``None`` so the validators can
    report them as deficiencies rather than crashing on a KeyError.
    """

    # Characters: combine protagonist + antagonist + supporting_cast.
    cast_spec = cast_spec_content or {}
    character_dicts: list[dict] = []
    if isinstance(cast_spec.get("protagonist"), dict):
        character_dicts.append({**cast_spec["protagonist"], "role": "protagonist"})
    if isinstance(cast_spec.get("antagonist"), dict):
        character_dicts.append({**cast_spec["antagonist"], "role": "antagonist"})
    for member in cast_spec.get("supporting_cast") or []:
        if isinstance(member, dict):
            character_dicts.append(member)

    characters: list[CharacterInput] = []
    for raw in character_dicts:
        try:
            characters.append(CharacterInput.model_validate(raw))
        except Exception:  # pragma: no cover - defensive
            # Malformed character dict shouldn't crash the gate; it'll be
            # caught separately by CharacterIPAnchorCheck via the surviving
            # shape. Skip rather than fail loud.
            continue

    # Power system tiers: nested under world_spec.power_system.
    world_spec = world_spec_content or {}
    power_system = world_spec.get("power_system") if isinstance(world_spec, dict) else None
    if isinstance(power_system, dict):
        raw_tiers = power_system.get("tiers") or []
        power_system_tiers = tuple(str(t) for t in raw_tiers if t)
        power_system_name = power_system.get("name")
    else:
        power_system_tiers = ()
        power_system_name = None

    # Naming pool: union of declared character names + any explicit pool
    # fields (book_spec.naming_pool, cast_spec.naming_pool).
    naming_pool_set: set[str] = set()
    book_spec = book_spec_content or {}
    for source in (book_spec.get("naming_pool"), cast_spec.get("naming_pool")):
        if isinstance(source, list):
            naming_pool_set.update(str(n).strip() for n in source if n)
    for char_dict in character_dicts:
        name = char_dict.get("name")
        if name:
            naming_pool_set.add(str(name).strip())

    expected_character_count = (
        int(book_spec.get("expected_character_count") or 0)
        or int(cast_spec.get("expected_character_count") or 0)
        or len(character_dicts)
    )

    # Theme / dramatic question: check the common aliases. Commercial bibles
    # store these under book_spec, but older drafts used themes[] / top-level
    # strings. Prefer the explicit field; fall back to the first theme.
    theme_statement: str | None = None
    raw_theme = book_spec.get("theme_statement")
    if isinstance(raw_theme, str) and raw_theme.strip():
        theme_statement = raw_theme.strip()
    else:
        themes = book_spec.get("themes")
        if isinstance(themes, list):
            for item in themes:
                if isinstance(item, str) and item.strip():
                    theme_statement = item.strip()
                    break

    dramatic_question_raw = book_spec.get("dramatic_question")
    dramatic_question = (
        dramatic_question_raw.strip()
        if isinstance(dramatic_question_raw, str) and dramatic_question_raw.strip()
        else None
    )

    return BibleDraft(
        characters=tuple(characters),
        power_system_tiers=power_system_tiers,
        power_system_name=power_system_name,
        naming_pool=tuple(sorted(naming_pool_set)),
        expected_character_count=expected_character_count,
        theme_statement=theme_statement,
        dramatic_question=dramatic_question,
    )


# ---------------------------------------------------------------------------
# Per-chapter runtime checks (Phase A6 + C1).
# ---------------------------------------------------------------------------
#
# The pre-bible gate above validates the *draft* before anything is persisted.
# The functions below run per-chapter, after feedback extraction, to catch
# violations that only surface once prose is generated: stance flips without
# a supporting ArcBeat milestone, resurrections snuck past contradiction
# checks, etc. These emit the same ``ContradictionViolation`` shape so the
# regen_loop can treat all layer-2 findings uniformly.


_STANCE_TURNING_BEAT_KINDS = {
    "turning_point",
    "betrayal",
    "reveal",
    "reconciliation",
    "alliance",
    "climax",
}


async def validate_chapter_against_bible(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    only_enforce_from_chapter: int | None = None,
    language: str | None = None,
) -> ContradictionCheckResult:
    """Run bible-level checks that require the chapter to be finalized.

    Currently runs ``StanceFlipJustificationCheck`` — the stance column on
    ``CharacterStateSnapshotModel`` for the current chapter is compared
    against the prior non-null snapshot, and any flip must be justified by
    either (a) a recent milestone relationship event (already enforced in
    contradiction.py) OR (b) an ArcBeatModel of a turning-point-style
    beat_kind scoped to this chapter.

    Parameters
    ----------
    only_enforce_from_chapter
        When set, chapters at or below this number are reported as
        audit-only warnings rather than block-level violations. Used when
        re-enabling the gate on a historical project so pre-existing stance
        history doesn't carpet the regen_loop with fabricated regressions.
    """
    from bestseller.infra.db.models import (
        ArcBeatModel,
        CharacterModel,
        CharacterStateSnapshotModel,
    )

    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    audit_only = (
        only_enforce_from_chapter is not None
        and chapter_number <= only_enforce_from_chapter
    )
    _is_en = is_english_language(language)

    # Pull stance-bearing snapshots for this chapter.
    snap_stmt = select(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.project_id == project_id,
        CharacterStateSnapshotModel.chapter_number == chapter_number,
        CharacterStateSnapshotModel.stance.is_not(None),
    )
    chapter_snaps = list(await session.scalars(snap_stmt))

    if not chapter_snaps:
        return ContradictionCheckResult(
            passed=True, violations=[], warnings=[], checks_run=1
        )

    # Build a chapter-scoped ArcBeat lookup so we can tell whether the stance
    # flip is grounded in a planned turning-point beat.
    beat_stmt = select(ArcBeatModel).where(
        ArcBeatModel.project_id == project_id,
        ArcBeatModel.scope_chapter_number == chapter_number,
    )
    chapter_beats = list(await session.scalars(beat_stmt))
    chapter_beat_kinds = {
        (b.beat_kind or "").strip().lower() for b in chapter_beats
    }
    turning_beat_present = bool(
        chapter_beat_kinds & _STANCE_TURNING_BEAT_KINDS
    )

    _hostile = {"enemy", "rival", "antagonist"}
    _friendly = {"ally", "friend", "mentor", "protagonist"}

    def _is_flip(prev: str | None, curr: str | None) -> bool:
        if not prev or not curr:
            return False
        return (
            (prev in _hostile and curr in _friendly)
            or (prev in _friendly and curr in _hostile)
        )

    for snap in chapter_snaps:
        character = await session.get(CharacterModel, snap.character_id)
        if character is None:
            continue

        prior_snap = await session.scalar(
            select(CharacterStateSnapshotModel)
            .where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.character_id == character.id,
                CharacterStateSnapshotModel.chapter_number < chapter_number,
                CharacterStateSnapshotModel.stance.is_not(None),
            )
            .order_by(
                CharacterStateSnapshotModel.chapter_number.desc(),
                CharacterStateSnapshotModel.scene_number.desc().nullslast(),
                CharacterStateSnapshotModel.created_at.desc(),
            )
            .limit(1)
        )
        prior_stance = prior_snap.stance if prior_snap is not None else None

        if not _is_flip(prior_stance, snap.stance):
            continue

        if turning_beat_present:
            continue

        if _is_en:
            message = (
                f"'{character.name}' stance flipped {prior_stance} → "
                f"{snap.stance} in chapter {chapter_number}, but no "
                "turning_point / betrayal / reveal ArcBeat is scoped here."
            )
        else:
            message = (
                f"「{character.name}」在第{chapter_number}章立场由 "
                f"{prior_stance} 翻为 {snap.stance}，"
                "但本章未安排 turning_point / betrayal / reveal 类 ArcBeat。"
            )

        if audit_only:
            warnings.append(
                ContradictionWarning(
                    check_type="stance_flip_justification",
                    message=message,
                    recommendation=(
                        "Historical chapter — audit only." if _is_en
                        else "历史章节，仅记录不阻断。"
                    ),
                )
            )
        else:
            violations.append(
                ContradictionViolation(
                    check_type="stance_flip_justification",
                    severity="error",
                    message=message,
                    evidence=(
                        f"prior_stance={prior_stance}; "
                        f"chapter_beats={sorted(chapter_beat_kinds)}"
                    ),
                )
            )

    passed = not violations
    if violations:
        logger.info(
            "bible_gate: %d stance_flip violation(s) at project=%s chapter=%d",
            len(violations),
            project_id,
            chapter_number,
        )

    return ContradictionCheckResult(
        passed=passed,
        violations=violations,
        warnings=warnings,
        checks_run=1,
    )
