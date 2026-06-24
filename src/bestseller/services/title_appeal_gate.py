"""Deterministic 书名/标题 click-power gate (zero-token).

Answers the product question the blurb gate ignores: *is the book's TITLE
logical and click-worthy?*  A reader scanning a ranking list sees the title
first — an illogical or limp title ("规则漏洞不保护我") kills the click before
the synopsis is ever read.  The blurb gate scores the synopsis only and passes
the title through untouched, so a bad title was never gated at all.

This gate scores the title alone on a 5-dimension rubric using regex + small
lexicons (no LLM), mirroring :mod:`bestseller.services.blurb_appeal_gate`:

  * length_fit        — platform-head titles are short & punchy (4–12 zh chars)
  * hook_power        — protagonist agency / reversal / concept-collision signals
  * graspable_subject — names a concrete entity (person/role/object/place/action)
  * coherence         — NO defect (malformed claim, function-word leak, fragment);
                        neutral baseline so we never punish a title we can't parse
  * anti_generic      — not a red-ocean cliché stem / AI-template title

All weights, thresholds and lexicons have built-in defaults and are overridable
via ``config/story_appeal.yaml`` → ``title_rubric`` (so the gate scores
correctly even when the config lags the code — no silent zero-out).

The "逻辑命门" floor (一票否决): a title whose coherence dimension is breached
(an illogical / malformed claim) is capped below the bar regardless of how
punchy it otherwise looks — an incoherent title cannot pass.
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese punctuation/lexicons are intentional.
import re
from typing import Any

from bestseller.domain.appeal import AppealDimension, TitleAppealVerdict

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_PROTAGONIST_RE = re.compile(r"我(?!们)|本座|本王|老子|朕|咱")
# Reversal / curiosity / strong-concept signal families (title hooks).
_HOOK_TOKENS: tuple[str, ...] = (
    "反派", "逆袭", "反杀", "竟", "居然", "原来", "其实", "却",
    "最强", "无敌", "全球", "全网", "全民", "无限", "亿", "万倍",
    "重生", "穿越", "系统", "开局", "签到", "退婚", "下山", "回到",
    "假", "真", "伪", "装", "苟", "摆烂", "躺平", "扮猪",
    "从", "到", "之", "杀疯", "通天", "成神", "封神", "夺",
)
# Concrete entity families → "graspable subject".
_IDENTITY_TOKENS: tuple[str, ...] = (
    "我", "他", "她", "你", "师", "王", "帝", "尊", "主", "君", "侯", "将",
    "医", "兵", "神", "仙", "魔", "妖", "鬼", "龙", "皇", "后", "妃", "夫人",
    "少爷", "千金", "赘婿", "保安", "外卖", "司机", "律师", "总裁", "老板",
    "学生", "教授", "刑警", "侦探", "杀手", "佣兵", "护士", "厨子", "农民",
    "奶奶", "爷爷", "女儿", "儿子", "前妻", "前夫", "继母",
)
# Abstract jargon nouns that, as a sentence's *agent of a care-verb*, read as a
# category error in a title ("规则漏洞不保护我" — a loophole can't protect a person).
_ABSTRACT_JARGON: tuple[str, ...] = (
    "规则", "漏洞", "机制", "逻辑", "数据", "概率", "算法", "参数",
    "协议", "条款", "流程", "函数", "变量", "字段", "接口", "权限",
    "数值", "公式", "定律", "概念", "维度", "指标", "模型", "代码",
)
# Care/agency verbs that take a person as object — pairing an *abstraction* with
# the *negated* form of these is the malformed-claim pattern we penalize.
_CARE_VERBS: tuple[str, ...] = (
    "保护", "守护", "庇护", "眷顾", "偏爱", "照顾", "放过", "原谅",
    "帮", "救", "爱", "等", "认", "管", "懂", "信", "理", "疼",
)
_PRONOUN_OBJ = r"(?:我|你|他|她|我们|你们|他们)"
_NEG = r"(?:不|没|没有|无法|不再|不会|不能|从不|绝不)"
# <jargon> ... <neg><care-verb><pronoun>   e.g. 规则漏洞不保护我
_MALFORMED_CLAIM_RE = re.compile(
    rf"(?:{'|'.join(_ABSTRACT_JARGON)})[^，。,]{{0,6}}{_NEG}(?:{'|'.join(_CARE_VERBS)}){_PRONOUN_OBJ}"
)
# Function-word / explainer / template leak (cf. chapter-title-function-dot leak).
# A single colon is fine in a book title (subtitle format «诡秘之主：…»), so it is
# NOT penalized — only chapter/template artifacts (· brackets parens) are leaks.
_FUNCTION_LEAK_RE = re.compile(r"·|【|】|\(|\)|（|）")
_TEMPLATE_STEMS: tuple[str, ...] = (
    "之我", "的我", "记", "录", "考", "论", "卷宗", "档案", "笔记", "手册",
    "指南", "说明", "报告", "纪要", "登记", "复盘", "盘点", "解析", "图鉴",
)
# Red-ocean / overused cliché title stems → anti_generic penalty.
_CLICHE_STEMS: tuple[str, ...] = (
    "都市之", "最强系统", "我的极品", "绝世神医", "超级兵王", "至尊", "无上",
    "逆天改命", "天才医生", "极品兵王", "校花的", "美女总裁", "贴身高手",
)
# Dangling fragment — title ends mid-thought.
_FRAGMENT_TAIL_RE = re.compile(r"(?:的|了|地|得|和|与|或|在|把|被|从|向|对)$")


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _count_distinct(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for t in terms if t and t in text)


def _clamp(value: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, value))


def _lex(cfg_lex: dict[str, Any] | None, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Config-overridable lexicon: config wins, built-in default otherwise."""

    raw = cfg_lex.get(key) if isinstance(cfg_lex, dict) else None
    if isinstance(raw, (list, tuple)) and raw:
        return tuple(str(x) for x in raw if str(x).strip())
    return default


# ---------------------------------------------------------------------------
# Per-dimension scorers — each returns (score_0_5, rationale, evidence).
# ---------------------------------------------------------------------------


def _score_length_fit(title: str, lo: int, hi: int) -> tuple[float, str, dict]:
    n = _cjk_len(title)
    if lo <= n <= hi:
        score = 5.0
    elif n == lo - 1 or n == hi + 1:
        score = 4.0
    elif n <= 2:
        score = 3.0  # ultra-short: iconic when strong (赘婿) but risky for a new book
    elif n >= hi + 4:
        score = 1.5  # too long for a ranking-list head title
    else:
        score = 3.0
    return score, f"标题 {n} 字（甜区 {lo}-{hi}）", {"zh_len": n}


def _score_hook_power(title: str, lex: dict[str, Any] | None) -> tuple[float, str, dict]:
    hooks = _lex(lex, "hook_tokens", _HOOK_TOKENS)
    has_prot = bool(_PROTAGONIST_RE.search(title))
    n_hook = _count_distinct(title, hooks)
    # concept collision: ≥2 distinct identity/role nouns juxtaposed unexpectedly
    ids = _lex(lex, "identity_tokens", _IDENTITY_TOKENS)
    n_ids = _count_distinct(title, ids)
    families = (1 if has_prot else 0) + min(n_hook, 2) + (1 if n_ids >= 2 else 0)
    score = {0: 1.5, 1: 3.0, 2: 4.0, 3: 4.5}.get(families, 5.0)
    return (
        score,
        f"钩子信号 {families}（主角{int(has_prot)}/反转概念{n_hook}/概念碰撞{int(n_ids >= 2)}）",
        {"protagonist": has_prot, "hook_tokens": n_hook, "identity": n_ids},
    )


def _score_graspable_subject(title: str, lex: dict[str, Any] | None) -> tuple[float, str, dict]:
    ids = _lex(lex, "identity_tokens", _IDENTITY_TOKENS)
    n_ids = _count_distinct(title, ids)
    has_num = bool(re.search(r"[0-9一二三四五六七八九十百千万亿]", title))
    jargon = _lex(lex, "abstract_jargon", _ABSTRACT_JARGON)
    n_jargon = _count_distinct(title, jargon)
    # A concrete graspable entity (person/role/object) anchors the reader; a title
    # built only from abstract jargon ("规则漏洞") gives nothing to picture.
    if n_ids >= 1 or has_num:
        score = 5.0 if (n_ids >= 1 and n_jargon == 0) else 4.0
    elif n_jargon >= 1:
        score = 2.5  # abstract-only — hard to picture
    else:
        score = 3.5
    return (
        score,
        f"可抓主体：具象实体{n_ids}/数字{int(has_num)}/抽象黑话{n_jargon}",
        {"identity": n_ids, "has_num": has_num, "jargon": n_jargon},
    )


def _score_coherence(title: str, lex: dict[str, Any] | None) -> tuple[float, str, dict]:
    """Neutral baseline (5.0) — drops ONLY on a detected defect (never punishes
    a title we cannot parse), so good titles are not false-failed."""

    score = 5.0
    flags: list[str] = []
    if _MALFORMED_CLAIM_RE.search(title):
        score = min(score, 2.0)
        flags.append("病句:抽象概念充当施动者(如'漏洞不保护我')")
    if _FUNCTION_LEAK_RE.search(title):
        score = min(score, 2.5)
        flags.append("功能词/标点泄漏(·：【】等)")
    stems = _lex(lex, "template_stems", _TEMPLATE_STEMS)
    if _count_distinct(title, stems) > 0:
        score = min(score, 3.0)
        flags.append("公文/模板腔(记/录/档案/登记等)")
    if _FRAGMENT_TAIL_RE.search(title.strip()):
        score = min(score, 3.0)
        flags.append("半截句(以的/了/在等结尾)")
    rationale = "通顺无硬伤" if not flags else "；".join(flags)
    return score, rationale, {"flags": flags}


def _score_anti_generic(title: str, lex: dict[str, Any] | None) -> tuple[float, str, dict]:
    # 产品红线：纯题材/分类名（都市高武 / 高武世界 / 末世）绝不是书名 → 直接 0 分，
    # 让任何复用本 gate 的选择流（_polish_title 等）都不会把题材名选成书名。
    from bestseller.services.platform_title_workflow import (  # noqa: PLC0415
        is_bare_taxonomy_title,
    )

    if is_bare_taxonomy_title(title):
        return 0.0, "纯题材/分类名，不是真正的书名", {"bare_taxonomy": True}
    stems = _lex(lex, "cliche_stems", _CLICHE_STEMS)
    hits = [s for s in stems if s in title]
    if not hits:
        score = 5.0
    elif len(hits) == 1:
        score = 3.0
    else:
        score = 1.5
    rationale = "无烂大街套路" if not hits else f"套路化书名:{'、'.join(hits[:3])}"
    return score, rationale, {"cliche_hits": hits}


# Default rubric weights (sum 100). Config ``title_rubric.<key>.weight`` overrides.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "length_fit": 16.0,
    "hook_power": 26.0,
    "graspable_subject": 16.0,
    "coherence": 24.0,
    "anti_generic": 18.0,
}
_DEFAULT_LABELS: dict[str, str] = {
    "length_fit": "长度适配",
    "hook_power": "钩子张力",
    "graspable_subject": "可抓主体",
    "coherence": "逻辑通顺",
    "anti_generic": "反套路独家",
}
_SUGGESTIONS: dict[str, str] = {
    "length_fit": "压到 4-12 字，一眼可读完",
    "hook_power": "加主角能动性/反转/强概念碰撞（如『我』+逆转结果）",
    "graspable_subject": "给一个能想象的具体实体（人/身份/物件/动作），别只堆抽象词",
    "coherence": "改成通顺、能成立的一句话，别让抽象概念去『保护/放过』人",
    "anti_generic": "避开都市之/最强系统/绝世神医等烂大街壳，换独家概念",
}


def _grade_from_total(total: float, config: dict[str, Any] | None) -> str:
    grades = (config or {}).get("grades", {}) if isinstance(config, dict) else {}
    rec = float(grades.get("recommend", 80))
    con = float(grades.get("consider", 65))
    if total >= rec:
        return "recommend"
    if total >= con:
        return "consider"
    return "pass"


def evaluate_title_appeal(
    title: str,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    config: dict[str, Any] | None = None,
    language: str = "zh",
) -> TitleAppealVerdict:
    """Score a book title for click-power + logic. Pure / deterministic / zero-token.

    English (no-CJK) titles get a neutral passing verdict — the zh heuristics do
    not apply and we must not false-fail (system is zh-first; en is advisory).
    """

    if config is None:
        from bestseller.services.story_appeal import load_story_appeal_config

        config = load_story_appeal_config()

    title = str(title or "").strip()
    rubric = config.get("title_rubric", {}) if isinstance(config, dict) else {}
    lex = rubric.get("lexicon", {}) if isinstance(rubric, dict) else {}

    # English / empty title → neutral pass-through (no zh signal to judge).
    if title and not _has_cjk(title):
        dim = AppealDimension(
            key="coherence", label=_DEFAULT_LABELS["coherence"], score=4.0,
            weight=100.0, rationale="非中文标题，中文启发式不适用（advisory）", evidence={},
        )
        return TitleAppealVerdict(
            total=80.0, grade="recommend", dimensions=(dim,), language="en",
        )

    lo = int((rubric.get("length") or {}).get("min", 4)) if isinstance(rubric, dict) else 4
    hi = int((rubric.get("length") or {}).get("max", 12)) if isinstance(rubric, dict) else 12

    raw: dict[str, tuple[float, str, dict]] = {
        "length_fit": _score_length_fit(title, lo, hi),
        "hook_power": _score_hook_power(title, lex),
        "graspable_subject": _score_graspable_subject(title, lex),
        "coherence": _score_coherence(title, lex),
        "anti_generic": _score_anti_generic(title, lex),
    }

    dims: list[AppealDimension] = []
    weighted = 0.0
    total_weight = 0.0
    findings: list[str] = []
    suggestions: list[str] = []
    for key, (score, rationale, evidence) in raw.items():
        spec = rubric.get(key, {}) if isinstance(rubric, dict) else {}
        if not isinstance(spec, dict):
            spec = {}
        weight = float(spec.get("weight", _DEFAULT_WEIGHTS[key]))
        label = str(spec.get("label", _DEFAULT_LABELS[key]))
        score = _clamp(score)
        dims.append(
            AppealDimension(
                key=key, label=label, score=score, weight=weight,
                rationale=rationale, evidence=evidence,
            )
        )
        weighted += (score / 5.0) * weight
        total_weight += weight
        if score < 2.5:
            findings.append(f"[{label}] {rationale}")
            suggestions.append(_SUGGESTIONS.get(key, f"提升「{label}」"))

    total = (weighted / total_weight * 100.0) if total_weight else 0.0

    # ── 逻辑命门 floor（一票否决）──────────────────────────────────────────
    # 一个不通顺/不成立的书名(如"规则漏洞不保护我"——抽象概念充当施动者)，
    # 哪怕长度合适、字面有"我"，也绝不该达标。coherence 维破线 → 总分封顶到 cap。
    bar_cfg = config.get("meets_bar", {}) if isinstance(config, dict) else {}
    coh = next((d for d in dims if d.key == "coherence"), None)
    coh_floor = float((rubric.get("coherence_floor", 2.5)) if isinstance(rubric, dict) else 2.5)
    cap = float(bar_cfg.get("critical_floor_cap", 78))
    if coh is not None and coh.score < coh_floor and total > cap:
        total = cap
        findings.append(
            f"[逻辑命门] 书名不通顺（{coh.rationale}）→ 总分封顶 {cap:.0f}（不达标）"
        )
        if _SUGGESTIONS["coherence"] not in suggestions:
            suggestions.append(_SUGGESTIONS["coherence"])

    # ── 题材名命门 floor（一票否决）────────────────────────────────────────
    # 纯题材/分类名（都市高武 / 高武世界 / 末世）绝不是书名（产品红线：题材名 ≠ 书名），
    # 必须明确不达标 → 总分压到 consider 线以下，任何择优流都不会把它选成书名。
    from bestseller.services.platform_title_workflow import (  # noqa: PLC0415
        is_bare_taxonomy_title,
    )

    taxonomy_cap = float(bar_cfg.get("taxonomy_floor_cap", 40))
    if title and is_bare_taxonomy_title(title) and total > taxonomy_cap:
        total = taxonomy_cap
        findings.append(
            f"[题材名命门] 书名只是题材/分类标签 → 总分封顶 {taxonomy_cap:.0f}（不达标）"
        )
        suggestions.append("换成一个有主角/动作/反转的真正书名，别用题材名")

    grade = _grade_from_total(total, config)
    return TitleAppealVerdict(
        total=total,
        grade=grade,
        dimensions=tuple(dims),
        findings=tuple(findings),
        suggestions=tuple(suggestions),
        language=language,
    )


__all__ = ["evaluate_title_appeal"]
