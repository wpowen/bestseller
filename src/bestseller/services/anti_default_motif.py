"""RETIRED (2026-08-02): the anti-default-motif police.

This module used to be the single source of truth for two "forbidden" story
families — debt/ledger framing (债/账本/欠条/讨账) and death-revival /
family-annihilation (亡夫归来/借尸还魂/灭门遗孤) — plus a minimal-cost
vocabulary filter. Every layer imported its token banks, its dominance
detectors, and its guardrail prompt blocks.

**Why it was retired.** Two independent failures, both fatal:

1. *As prompt text.* The guardrail blocks enumerated the motifs they banned, so
   every book's context carried the framework's list of corpses and ledgers.
   Telling a model what not to write is how you get it written. (Deleted from
   prompts 2026-08-01.)

2. *As an output gate.* Death and debt are ordinary story material. A rival who
   dies, a breakthrough that costs 灵力, a helper owed a favour — these are what
   novels are made of, not pollution. Worse, the framework simultaneously
   ORDERED costs (per-chapter ``cost_or_tradeoff``, the 代价账 hard gates, the
   no-free-win material rules) and then vetoed the artifact for containing them.
   Live evidence 2026-08-02: two books died in the foundation and outline stages
   on PLANNER_UNREQUESTED_LEDGER_MOTIF / PLANNER_MINIMAL_COST_IRREVERSIBLE_
   SELF_DAMAGE after 4 and 3 attempts, having written exactly what they were told.

**What replaces it.** Nothing on this axis. Cross-book sameness is prevented at
the source — no framework-authored motif content in any prompt — and by the
deterministic cross-book fingerprint check. A book's own vocabulary is the
book's business.

The module is kept as a neutral shim so the ~50 call sites across conception,
planner, tournament, architect and story_source stay importable and become
no-ops. Detectors return False, intent probes return True (any residual
``if not user_requested_X`` guard therefore passes), and block builders return
"". Token banks remain as inert data for tests that assert the retirement.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── Inert vocabulary records (no longer used for any judgement) ──────────────
DEBT_LEDGER_TOKENS: tuple[str, ...] = ()
DEATH_REVIVAL_TOKENS: tuple[str, ...] = ()
ANONYMOUS_DEATH_TOKENS: tuple[str, ...] = ()
MINIMAL_COST_SEMANTIC_TOKENS: tuple[str, ...] = ()
MINIMAL_COST_OBLIGATION_RES: tuple[re.Pattern[str], ...] = ()

# Patterns kept only so ``.search(...)`` call sites stay valid; they match nothing.
_NEVER = re.compile(r"(?!x)x")
DEBT_OWED_MONEY_RE = _NEVER
DEATH_MOTIF_RE = _NEVER
IRREVERSIBLE_SELF_COST_RE = _NEVER
MERIDIAN_INJURY_RE = _NEVER


def _blob(*texts: Any) -> str:
    """Join heterogeneous payloads into one searchable string (still used by callers)."""

    parts: list[str] = []
    for text in texts:
        if text is None:
            continue
        if isinstance(text, str):
            parts.append(text)
        else:
            try:
                parts.append(json.dumps(text, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(str(text))
    return " ".join(parts)


# ── Detectors — all retired, all False ──────────────────────────────────────
def mentions_debt_theme(*texts: Any) -> bool:
    del texts
    return False


def contains_debt_motif(text: Any) -> bool:
    del text
    return False


def contains_owed_money_seed(text: Any) -> bool:
    del text
    return False


def mentions_death_revival_theme(*texts: Any) -> bool:
    del texts
    return False


def mentions_death_theme(*texts: Any) -> bool:
    del texts
    return False


def contains_default_death_motif(text: Any) -> bool:
    del text
    return False


def contains_irreversible_self_cost(text: Any) -> bool:
    del text
    return False


def contains_minimal_cost_violation(text: Any) -> bool:
    """Retired. A cost-style preference never made a book's cost vocabulary illegal."""

    del text
    return False


# ── 2026-08-13 靶向复活（仅冠军级，其余层维持退役）─────────────────────────
# 《摸一摸，救我妹》（欠条/垫债/三吊钱）与《我替娘讨旧账》（旧账×3+灵堂开棺）
# 连续两本用户书撞进同一默认族——冠军级的债务/丧葬引力在退役后失去了全部
# 反压：user_requested_debt 恒 True + is_debt_dominated 恒 False 使 finalize
# 与 winner 两处检查成为双保险死代码。本次复活遵守退役档案里的两条死因：
# ① 不进任何 prompt（检测器专用；重试反馈不点名词汇，沿用本体漂移渲染器的
#   withhold 策略）；② 不禁一本书自己的素材——支配判定（≥2 个子族或≥3 次
#   出现）+ 用户意图豁免（用户自己的输入提过该族即视为选择）+ 只在构思冠军
#   层生效，规划/正文层的旧警察不复活。
_DEFAULT_DEBT_FAMILY_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"账(?![号户])"),
    re.compile(r"[债賬]|欠(?:[债款钱条账]|下)"),
    re.compile(r"讨[债账]|要账|清算旧"),
    re.compile(r"灵堂|棺|出殡|殡[仪葬]|丧(?!尸)"),
    re.compile(r"阳寿|寿元"),
)


def default_debt_family_hits(text: Any) -> tuple[str, ...]:
    """命中的子族样式列表（冠军级检测与选题沉底共用）。"""

    blob = text if isinstance(text, str) else _blob(text)
    return tuple(
        pattern.pattern for pattern in _DEFAULT_DEBT_FAMILY_RES if pattern.search(blob)
    )


def is_debt_dominated(text: Any) -> bool:
    """冠军级支配判定：≥2 个子族在场，或全族合计出现 ≥3 次。

    单次顺带提及（如女频冠军里一处「账目」）不构成支配——退役档案的
    第二死因（把普通故事素材当污染）由该阈值防复发。"""

    blob = text if isinstance(text, str) else _blob(text)
    if not blob:
        return False
    distinct = sum(1 for p in _DEFAULT_DEBT_FAMILY_RES if p.search(blob))
    if distinct >= 2:
        return True
    total = sum(len(p.findall(blob)) for p in _DEFAULT_DEBT_FAMILY_RES)
    return total >= 3


def contains_core_debt_framing(payload: Any) -> bool:
    del payload
    return False


def is_death_revival_dominated(text: Any) -> bool:
    del text
    return False


def is_anonymous_death_dominated(text: Any) -> bool:
    del text
    return False


# ── Intent snapshot ─────────────────────────────────────────────────────────
_USER_INTENT_KEY = "_user_intent_snapshot"


def snapshot_user_intent(ctx: dict[str, Any]) -> None:
    """Freeze the original user intent. Harmless to keep: other code reads it."""

    if _USER_INTENT_KEY in ctx:
        return
    ctx[_USER_INTENT_KEY] = _blob(
        ctx.get("description"),
        ctx.get("user_hints"),
        ctx.get("premise_seed"),
        ctx.get("user_description"),
    )


def user_requested_debt(ctx: dict[str, Any] | None) -> bool:
    """诚实的用户意图探针（2026-08-13 复活）。

    用户自己的输入（故事创意/描述/提示）里提过债务/丧葬族，或显式打开
    allow_debt_theme，都视为用户的选择——用户点名的族永远不算污染。
    恒 True 的退役语义等于「所有书都是用户要的账」，让冠军级检查双保险
    失效，连续两本用户书撞进同一默认族。"""

    if not isinstance(ctx, dict):
        return False
    if bool(ctx.get("allow_debt_theme")):
        return True
    intent_blob = str(ctx.get(_USER_INTENT_KEY) or "")
    if not intent_blob:
        intent_blob = _blob(
            ctx.get("description"), ctx.get("user_hints"), ctx.get("premise_seed")
        )
    return bool(default_debt_family_hits(intent_blob))


def user_requested_death_revival(ctx: dict[str, Any] | None) -> bool:
    del ctx
    return True


def user_requested_death_theme(ctx: dict[str, Any] | None) -> bool:
    del ctx
    return True


# ── Guardrail prompt blocks — retired, render nothing ────────────────────────
def anti_debt_block(*, is_en: bool) -> str:
    del is_en
    return ""


def anti_death_default_block(*, is_en: bool) -> str:
    del is_en
    return ""


def planner_anti_default_block(ctx: dict[str, Any] | None = None, *, is_en: bool) -> str:
    del ctx, is_en
    return ""
