"""Shared anti-default-motif / anti-template guardrails.

Single source of truth for the two template families that keep colonising
generation prompts *across every layer* of the pipeline:

1. **Debt / ledger framing** — the golden finger, its cost, or interpersonal
   relationships expressed as 债/账本/欠条/认账/讨账 ("owe-and-repay").
2. **Death-revival / family-annihilation template** — 亡夫(妻)归来 / 借尸还魂 /
   死者复活复仇 / 灭门遗孤, plus the family-trauma default motivation
   (亲人失踪死亡 → 复仇/寻真相).

History: the anti-debt guard was fully built at the *conception* layer and the
*prose* layer, but the *planning* layer (planner world_spec / cast_spec /
volume_plan / outline / story_design_kernel) had **no guard at all** — so the
banned framing leaked back in when the LLM elaborated the mandated
"代价 / 成本 / 阴谋" fields (evidence book「龙椅上坐着我亡夫」: 讨账/认账/欠账 +
借尸还魂 born entirely at the planning stage). There was also no deterministic
*death* filter anywhere (only debt had one), and the family-trauma regex was
blind to 亡夫/亡妻/借尸还魂.

Every consumer imports the token banks + block builders + dominance detectors
from here so the lists can never drift apart again; the cross-book leakage
regression test asserts the banks stay in sync.
"""

from __future__ import annotations

import re
from typing import Any

# ── Debt / ledger vocabulary ────────────────────────────────────────────────
# ``账`` alone is too broad (账号/结账/账户) so only ledger-specific compounds are
# listed; ``债`` is debt-specific enough to stand alone. 2026-07-14: added
# 认账/讨账/索账/对账/欠款/呆账/坏账 — the evidence book leaked 认账/讨账 which the
# old list (only 讨债/欠账) never caught.
DEBT_LEDGER_TOKENS: tuple[str, ...] = (
    "债", "账本", "账簿", "欠条", "欠账", "记账", "债务", "连本带利",
    "抹账", "还债", "债币", "赊", "赎身", "抵押", "借贷", "欠债",
    "讨债", "债主", "入账", "记一笔", "利息",
    # 2026-07-14 gap-closure (evidence book「龙椅上坐着我亡夫」使用的正是前两个):
    "认账", "讨账", "索账", "对账", "欠款", "呆账", "坏账",
)

# ── Death-revival / annihilation template vocabulary ────────────────────────
# The worn 玄幻/male-channel template the model reverts to on a cold start:
# a dead spouse/kin returns (借尸还魂/诈尸/死者归来) to settle an old score, or a
# massacre orphan seeks revenge. Deliberately NOT including bare 死/亡 (too broad
# — legitimate mortality appears everywhere); only revival/annihilation compounds.
DEATH_REVIVAL_TOKENS: tuple[str, ...] = (
    "亡夫", "亡妻", "亡妇", "亡儿", "亡女", "遗孀", "未亡人",
    "借尸还魂", "借尸", "还魂", "还阳", "诈尸", "死而复生", "起死回生",
    "死者归来", "死人复活", "开棺", "掘坟", "灭门遗孤", "灭门血仇",
)

# Family-trauma default-motivation pattern (relative × disappearance/death/secret),
# now extended so 亡夫/亡妻/亡儿… + 借尸还魂/诈尸/死者归来 are also caught (the old
# pattern required full 丈夫/妻子 + 死亡 and was blind to the 亡- prefix form).
DEATH_MOTIF_RE = re.compile(
    r"("
    r"(父母|父亲|母亲|双亲|家人|亲人|亲属|兄长|哥哥|姐姐|妹妹|弟弟|妻子|丈夫|未婚妻|未婚夫)"
    r"[^。！？；;，,\n]{0,12}"
    r"(失踪|消失|死亡|死去|被害|遇害|惨死|离奇|旧案|真相|身世|血脉|秘密)"
    r"|"
    r"(失踪|消失|死亡|死去|被害|遇害|惨死|离奇|旧案|真相|身世|血脉|秘密)"
    r"[^。！？；;，,\n]{0,12}"
    r"(父母|父亲|母亲|双亲|家人|亲人|亲属)"
    r"|"
    r"(亡夫|亡妻|亡妇|亡儿|亡女|遗孀|未亡人|借尸还魂|借尸|还魂|还阳|诈尸|死而复生"
    r"|起死回生|死者归来|死人复活|开棺|掘坟|灭门遗孤|灭门血仇)"
    r")"
)


def _blob(*texts: Any) -> str:
    return " ".join(str(t) for t in texts if t)


def mentions_debt_theme(*texts: Any) -> bool:
    """True when the given text already frames the story around debt/lending.

    Used to *respect explicit user intent*: a book the user deliberately wants
    about debt collection must not be gagged. Callers must pass ONLY the original
    user description / hints here — never pipeline-generated content (see
    ``snapshot_user_intent``): a tournament champion that happens to contain a
    single 债 must not disable the guard for the whole downstream pipeline.
    """

    blob = _blob(*texts)
    return any(token in blob for token in DEBT_LEDGER_TOKENS)


def mentions_death_revival_theme(*texts: Any) -> bool:
    """True when the user explicitly asked for a death-revival / 借尸还魂 premise."""

    blob = _blob(*texts)
    return any(token in blob for token in DEATH_REVIVAL_TOKENS)


def _dominance(text: Any, tokens: tuple[str, ...], *, threshold: int = 2) -> bool:
    blob = str(text or "")
    if not blob:
        return False
    hits = 0
    for token in tokens:
        hits += blob.count(token)
        if hits >= threshold:
            return True
    return False


def is_debt_dominated(text: Any) -> bool:
    """True when a mechanism / world-model leans on ledger framing (≥2 hits).

    A single incidental ``一笔旧债`` in passing does not trip it, but
    ``债币/欠账/入账`` (or ``认账``+``讨账``) stacked into one mechanism does.
    """

    return _dominance(text, DEBT_LEDGER_TOKENS)


def is_death_revival_dominated(text: Any) -> bool:
    """True when a concept leans on the death-revival template (≥2 hits).

    The death twin of :func:`is_debt_dominated`. One backstory death does not
    trip it; 亡夫+借尸还魂+开棺 stacked into the core concept does.
    """

    return _dominance(text, DEATH_REVIVAL_TOKENS)


# ── Intent snapshot ─────────────────────────────────────────────────────────
_USER_INTENT_KEY = "_user_intent_snapshot"


def snapshot_user_intent(ctx: dict[str, Any]) -> None:
    """Freeze the ORIGINAL user-supplied intent before the pipeline mutates ctx.

    ``ctx['description']`` is later overwritten with the tournament champion /
    enrichment, so the debt/death intent-exemptions must read this immutable
    snapshot instead — otherwise the pipeline disables its own guard the moment
    a generated concept contains a stray 债/账 token.
    """

    if _USER_INTENT_KEY in ctx:
        return
    ctx[_USER_INTENT_KEY] = _blob(
        ctx.get("description"),
        ctx.get("user_hints"),
        ctx.get("premise_seed"),
        ctx.get("user_description"),
    )


def _user_intent(ctx: dict[str, Any] | None) -> str:
    ctx = ctx or {}
    snap = ctx.get(_USER_INTENT_KEY)
    if isinstance(snap, str):
        return snap
    # Fallback for callers that never snapshotted: use only user-supplied fields,
    # never pipeline-generated ``description`` when a champion may have been merged.
    return _blob(ctx.get("user_hints"), ctx.get("premise_seed"), ctx.get("user_description")) or _blob(
        ctx.get("description")
    )


def user_requested_debt(ctx: dict[str, Any] | None) -> bool:
    return mentions_debt_theme(_user_intent(ctx))


def user_requested_death_revival(ctx: dict[str, Any] | None) -> bool:
    return mentions_death_revival_theme(_user_intent(ctx))


# ── Guardrail prompt blocks (pure text; callers decide when to suppress) ─────
def anti_debt_block(*, is_en: bool) -> str:
    """Ban ledger framing of the power / cost / relationships."""

    if is_en:
        return (
            "\n\n[Anti-debt-metaphor guardrail — hard default]\n"
            "Unless the user explicitly asked for a debt/lending/bookkeeping premise, the "
            "power, its cost, and interpersonal relationships must NOT be a financial "
            "ledger. Ban debt/IOU/ledger/account/repayment/'owe-and-repay' framing as the "
            "FORM of the power, its price, or the bonds between characters. A cost is only "
            "written when it derives inevitably from the power's own causality (what you "
            "use is what bears the mark); if it cannot be derived, write no cost at all. "
            "Never bolt on random amnesia / lifespan-tax / resource-debt style system "
            "taxes. Express bonds through reciprocity, leverage, and trust — matched to "
            "the world's own laws."
        )
    return (
        "\n\n【反债务化护栏 · 硬性默认】\n"
        "除非用户明确要求写“债务/借贷/记账”题材,力量体系、其代价、以及人物之间的关系"
        "【绝不表达为金融记账形态】:禁止债、账本、欠条、欠账、认账、讨账、记账、债务、"
        "连本带利、抹账、还债、“欠了要还”这类债务隐喻,当作力量、代价或人际羁绊的主体框架。"
        "代价不是必选槽位:只有能从力量自身的机制因果里必然推导出来时才写"
        "(用了什么,就在什么上留下痕迹),推导不出来就不写代价;禁止随机失忆、扣命、"
        "掉寿命、资源债这类与行动无因果的系统收税。"
        "羁绊改用互惠、把柄、信任、亏欠情分——与本书世界规律匹配。"
    )


def anti_death_default_block(*, is_en: bool) -> str:
    """Ban the family-trauma / death-revival default template."""

    if is_en:
        return (
            "\n\n[Anti-default-motif guardrail — death/revenge template]\n"
            "Unless the user explicitly asked for it, do NOT default to: a dead spouse/kin "
            "returning (soul-transfer/resurrection/'the dead come back'), a massacre orphan's "
            "revenge, family disappearance/murder, hidden-bloodline old cases, or generic "
            "vengeance. These are the platform's most worn tropes. Build the drive from the "
            "genre, the reader promise, the profession/system/world rules, and the opening "
            "event — a fresh initiating crisis, not a grave."
        )
    return (
        "\n\n【反默认母题护栏 · 死亡/复仇模板】\n"
        "除非用户明确要求,【禁止】默认写:亡夫/亡妻/亲人借尸还魂或死而复生归来讨旧账、"
        "灭门遗孤复仇、亲人失踪/被害旧案、隐藏血脉身世、通用复仇。这些是全平台最烂大街的套路。"
        "主角驱动必须从题材、读者承诺、职业/制度/世界规则、当前开局事件中生成——"
        "给一个新鲜的开局危机,而不是又从一座坟、一具尸、一桩灭门案开始。"
    )


def planner_anti_default_block(ctx: dict[str, Any] | None = None, *, is_en: bool) -> str:
    """Combined debt + death guard for planner prompts, honouring user intent.

    Returns the anti-debt block unless the user asked for a debt premise, plus
    the anti-death block unless the user asked for a death-revival premise. Reads
    the frozen user-intent snapshot (never pipeline-generated content).
    """

    parts: list[str] = []
    if not user_requested_debt(ctx):
        parts.append(anti_debt_block(is_en=is_en))
    if not user_requested_death_revival(ctx):
        parts.append(anti_death_default_block(is_en=is_en))
    return "".join(parts)
