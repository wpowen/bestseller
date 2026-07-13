# ruff: noqa: RUF001
from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

from bestseller.domain.anti_commonsense_hook import (
    HookScore,
    HookSpec,
    HookStrengthFinding,
    HookStrengthGateReport,
)

REJECT_H_NORM = 15.0
SEED_H_NORM = 30.0
REVIEW_H_NORM = 45.0

_OPPOSITION_HINTS = (
    "必须",
    "越",
    "反而",
    "不能",
    "亏",
    "死",
    "失败",
    "误解",
    "规则",
    "代价",
    "forced",
    "must",
    "lose",
    "death",
    "misread",
    "cost",
)
# Antagonist-visibility vocabulary. Misunderstanding scoring rewards hooks
# that put a concrete opposition (person, group, institution) on the page.
_VILLAIN_HINTS = (
    "敌人",
    "对手",
    "反派",
    "对家",
    "死敌",
    "旁人",
    "围观",
    "群众",
    "前任",
    "婆家",
    "师门",
    "世家",
    "朝堂",
    "上司",
    "客户",
    "顾客",
    "金主",
    "public",
    "enemy",
    "rival",
    "antagonist",
)
_HIGH_REWARD_HINTS = ("权限", "跃迁", "资源", "证据", "声望", "真相", "identity", "power")
_COST_HINTS = ("代价", "失去", "折损", "风险", "牺牲", "反噬", "cost", "risk", "lose")
_ANCHOR_STOPWORDS = {
    "主角",
    "读者",
    "故事",
    "小说",
    "平台",
    "一个",
    "一部",
    "核心",
    "持续",
    "升级",
    "都市",
    "修仙",
    "职业",
    "长篇",
}
# ── Generic anchor auto-extraction (通用型能力, 禁止题材绑定) ──────────
# Anchors are derived from the premise text itself via the patterns below.
# The vocabularies here are strictly *category-level* markers shared across
# Chinese web-novel genres (mechanic nouns, stake nouns, occupation suffixes,
# common surnames). Book-specific proper nouns must NEVER be added here —
# that was the 2026-06-11 regression where a hardcoded word table from one
# book left every new premise with almost no anchors.
_COMMON_SURNAMES = (
    "王李张刘陈杨黄赵周吴徐孙朱马胡郭林何高梁郑罗宋谢唐韩曹许邓萧冯曾程蔡彭"
    "潘袁于董余苏叶吕魏蒋田杜丁沈姜范江傅钟卢汪戴崔任陆廖姚方金邱夏谭韦贾邹"
    "石熊孟秦阎薛侯雷白龙段郝孔邵史毛常万顾赖武康贺严尹钱施牛洪龚"
)
_NAME_NOISE_CHARS = set("的了是在和有又不就也都要这那其之与及或被把对向从让使个一二三说着过来去为")
_NAME_INTRO_RE = re.compile(
    r"(?:主角|主人公|男主角?|女主角?)(是|叫|名叫|名为)?[：:\s]*([一-鿿]{2,4})"
)
_NAME_FOLLOW_PATTERN = r"(?:是|，|。|：|（|想|要|被|靠|发现|成为|获得|入职|刚|正)"
_IDENTITY_VERB_RE = re.compile(
    r"(?:成为|担任|身为|作为|当上|入职|应聘|考上|沦为|穿成)"
    r"(?:了)?(?:一[名个位]|个)?"
    r"([一-鿿A-Za-z]{2,10}?)(?=[，。！？；：、的（）()\s]|$)"
)
_IDENTITY_MARKERS = (
    "员",
    "师",
    "官",
    "经理",
    "总监",
    "助理",
    "专员",
    "主管",
    "顾问",
    "主播",
    "老板",
    "掌柜",
    "掌门",
    "宗主",
    "城主",
    "医生",
    "警察",
    "教授",
    "队长",
    "店长",
    "侦探",
    "特工",
)
_MECHANISM_MARKERS = (
    "面板",
    "系统",
    "空间",
    "签到",
    "词条",
    "天赋",
    "神通",
    "金手指",
    "外挂",
    "商城",
    "抽奖",
    "契约",
    "血脉",
    "图鉴",
    "模拟器",
    "副本",
    "封神",
)
_PRESSURE_MARKERS = (
    "裁员",
    "失业",
    "开除",
    "辞退",
    "降职",
    "淘汰",
    "考核",
    "考编",
    "转正",
    "编制",
    "审批",
    "试用期",
    "期限",
    "倒计时",
    "债务",
    "欠债",
    "破产",
    "退婚",
    "退学",
    "除名",
    "封杀",
    "雪藏",
    "处分",
    "问责",
    "追杀",
    "灭门",
    "通缉",
    "诅咒",
    "绝症",
    "赔偿",
    "违约",
    "背锅",
)
_QUOTED_TERM_RE = re.compile(r"[「『【“\"']([一-鿿A-Za-z·]{2,8})[」』】”\"']")
_LATIN_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,11}")
_TERM_BOUNDARY_CHARS = set(
    "的地得在是和与或了把被向从对于里中内外上下又再们个各每"
    "，。！？；：、（）()【】「」『』《》“”\"'·…—-～~ \t\n"
)
_TITLE_GRAM_NOISE_CHARS = set("的了是在我你他她它和与或就都也很不一这那有无之其为于把被向从")


def _clamp_int(value: float, low: int = 0, high: int = 10) -> int:
    return max(low, min(high, round(value)))


def _clamp_float(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if math.isnan(value) or math.isinf(value):
        return low
    return max(low, min(high, value))


def _text(value: object) -> str:
    return str(value or "").strip()


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，、/／|]", value) if part.strip()]
    if isinstance(value, Mapping):
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _clean_anchor(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    text = text.strip("《》“”\"'：:，,。.!！？?；;（）()[]【】")
    if not (2 <= len(text) <= 12):
        return ""
    if text in _ANCHOR_STOPWORDS:
        return ""
    return text


def _append_anchor(group: dict[str, list[str]], key: str, value: object) -> None:
    text = _clean_anchor(value)
    if not text:
        return
    values = group.setdefault(key, [])
    if text not in values:
        values.append(text)


def _strip_anchor_stopword_prefix(text: str) -> str:
    for stop in _ANCHOR_STOPWORDS:
        if text.startswith(stop) and len(text) - len(stop) >= 2:
            return text[len(stop) :]
    return text


def _expand_marker_terms(text: str, marker: str, *, max_len: int = 8) -> list[str]:
    """Expand each marker occurrence leftward to the enclosing content term.

    With marker 面板, a premise span like "...识人面板..." yields 识人面板;
    expansion stops at function words, punctuation, or ``max_len``.
    """

    terms: list[str] = []
    for match in re.finditer(re.escape(marker), text):
        start = match.start()
        while (
            start > 0
            and match.end() - start < max_len
            and text[start - 1] not in _TERM_BOUNDARY_CHARS
            and re.match(r"[一-鿿A-Za-z0-9]", text[start - 1])
        ):
            start -= 1
        terms.append(text[start : match.end()])
    return terms


def _latin_terms(text: str) -> list[tuple[str, list[str], int]]:
    """Return (lowercase key, original-case variants, total count) tuples."""

    variants: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for token in _LATIN_TERM_RE.findall(text):
        key = token.lower()
        counts[key] = counts.get(key, 0) + 1
        bucket = variants.setdefault(key, [])
        if token not in bucket:
            bucket.append(token)
    return [(key, bucket, counts[key]) for key, bucket in variants.items()]


def _auto_protagonist_anchors(text: str) -> list[str]:
    explicit: list[str] = []
    for connector, raw_name in _NAME_INTRO_RE.findall(text):
        name = raw_name
        for idx in range(1, len(raw_name)):
            if raw_name[idx] in _NAME_NOISE_CHARS:
                name = raw_name[:idx]
                break
        if len(name) < 2 or any(marker in name for marker in _MECHANISM_MARKERS):
            continue
        # Without an explicit naming connector, "主角" is often followed by a
        # verb phrase, not a name — require a surname-led token in that case.
        if not connector and name[0] not in _COMMON_SURNAMES:
            continue
        explicit.append(name)
    candidates: dict[str, int] = {}
    for match in re.finditer(rf"[{_COMMON_SURNAMES}][一-鿿]{{1,2}}", text):
        token = match.group(0)
        for cand in (token[:2], token):
            if len(cand) < 2 or cand in candidates:
                continue
            if any(ch in _NAME_NOISE_CHARS for ch in cand[1:]):
                continue
            candidates[cand] = text.count(cand)
    scored: list[tuple[int, str]] = []
    for cand, freq in candidates.items():
        introduced = bool(re.search(re.escape(cand) + _NAME_FOLLOW_PATTERN, text))
        if freq >= 2 or introduced:
            scored.append((freq + (1 if introduced else 0), cand))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [*explicit, *[cand for _, cand in scored[:4]]]


def _auto_identity_anchors(text: str) -> list[str]:
    anchors = [match.group(1) for match in _IDENTITY_VERB_RE.finditer(text)]
    for _key, latin_variants, _count in _latin_terms(text):
        for token in latin_variants:
            if token.isupper() and 2 <= len(token) <= 5:
                anchors.append(token)
    for marker in _IDENTITY_MARKERS:
        if marker not in text:
            continue
        for term in _expand_marker_terms(text, marker):
            if len(marker) == 1 and len(term) < 3:
                continue
            if any(ch in _NAME_NOISE_CHARS for ch in term):
                continue
            anchors.append(term)
    return anchors


def _auto_mechanism_anchors(text: str, *, title: str) -> list[str]:
    # Marker-expanded terms first: they are the highest-precision mechanism
    # names and must survive the per-group cap; quoted spans also catch
    # dialogue/nicknames, so they rank last.
    anchors: list[str] = []
    for marker in _MECHANISM_MARKERS:
        if marker in text:
            anchors.extend(_expand_marker_terms(text, marker))
    for _key, latin_variants, count in _latin_terms(text):
        if count < 2:
            continue
        if any(token.isupper() and len(token) <= 5 for token in latin_variants):
            continue  # routed to identity (HR/CEO-style role tokens)
        anchors.extend(latin_variants)
    for quoted in _QUOTED_TERM_RE.findall(text):
        if title and quoted in title:
            continue
        anchors.append(quoted)
    return anchors


def _auto_pressure_anchors(text: str) -> list[str]:
    return [marker for marker in _PRESSURE_MARKERS if marker in text]


def _title_anchor_tokens(title: str) -> list[str]:
    """Short-gram title anchors: robust against partial title echoes in hooks.

    The previous behavior emitted one contiguous up-to-6-char chunk, so a hook
    had to quote the title nearly verbatim to count the title group.
    """

    tokens: list[str] = []
    for run in re.findall(r"[一-鿿]{2,}", title):
        if len(run) <= 6:
            tokens.append(run)
        for size in (3, 2):
            for idx in range(len(run) - size + 1):
                gram = run[idx : idx + size]
                if any(ch in _TITLE_GRAM_NOISE_CHARS for ch in gram):
                    continue
                tokens.append(gram)
    tokens.extend(_LATIN_TERM_RE.findall(title))
    return list(dict.fromkeys(tokens))


def _extract_context_texts(context: Mapping[str, Any]) -> dict[str, str]:
    return {
        "premise": " ".join(
            _text(context.get(key))
            for key in ("premise", "synopsis", "short_intro", "logline")
            if _text(context.get(key))
        ),
        "title": " ".join(
            _text(context.get(key)) for key in ("title", "primary_title") if _text(context.get(key))
        ),
        "genre": " ".join(
            [
                _text(context.get("genre")),
                _text(context.get("sub_genre")),
                " ".join(_string_list(context.get("tags"))),
            ]
        ),
    }


def premise_anchor_groups(premise_context: Mapping[str, Any] | str | None) -> dict[str, list[str]]:
    """Derive deterministic story anchors used to reject semantically wrong hooks.

    The gate intentionally stays lightweight: it only enforces alignment when the
    caller supplies enough concrete anchors. Generic genre labels alone do not
    trigger a hard mismatch.
    """

    if premise_context is None:
        return {}
    context: Mapping[str, Any]
    if isinstance(premise_context, str):
        context = {"premise": premise_context}
    elif isinstance(premise_context, Mapping):
        context = premise_context
    else:
        return {}

    groups: dict[str, list[str]] = {}
    raw_groups = context.get("title_anchor_groups")
    if isinstance(raw_groups, Mapping):
        for key, value in raw_groups.items():
            for item in _string_list(value):
                _append_anchor(groups, str(key), item)

    for item in context.get("main_characters") or ():
        if not isinstance(item, Mapping):
            continue
        _append_anchor(groups, "protagonist", item.get("name"))
        _append_anchor(groups, "identity", item.get("identity") or item.get("role"))

    dna = context.get("story_title_dna")
    if isinstance(dna, Mapping):
        _append_anchor(groups, "protagonist", dna.get("protagonist"))
        _append_anchor(groups, "identity", dna.get("identity"))
        _append_anchor(groups, "mechanism", dna.get("central_action") or dna.get("payoff"))
        _append_anchor(groups, "pressure", dna.get("stakes") or dna.get("conflict"))

    texts = _extract_context_texts(context)
    # Cross-group dedupe for auto-derived anchors: one premise word must not
    # fill two groups, or a single hook word would count as two-group alignment.
    seen = {value for values in groups.values() for value in values}

    def _append_auto(key: str, value: object) -> None:
        cleaned = _strip_anchor_stopword_prefix(_clean_anchor(value))
        if len(cleaned) < 2 or cleaned in seen:
            return
        seen.add(cleaned)
        _append_anchor(groups, key, cleaned)

    premise_text = texts["premise"]
    if premise_text:
        for name in _auto_protagonist_anchors(premise_text):
            _append_auto("protagonist", name)
        for identity in _auto_identity_anchors(premise_text):
            _append_auto("identity", identity)
        for mechanism in _auto_mechanism_anchors(premise_text, title=texts["title"]):
            _append_auto("mechanism", mechanism)
        for pressure in _auto_pressure_anchors(premise_text):
            _append_auto("pressure", pressure)

    for token in _string_list(context.get("tags")):
        _append_anchor(groups, "genre", token)
    for token in _title_anchor_tokens(texts["title"]):
        _append_auto("title", token)

    return {key: values[:8] for key, values in groups.items() if values}


def hook_premise_alignment(
    spec: HookSpec,
    premise_context: Mapping[str, Any] | str | None,
) -> tuple[bool, dict[str, list[str]], dict[str, list[str]]]:
    groups = premise_anchor_groups(premise_context)
    concrete_groups = {
        key: values
        for key, values in groups.items()
        if key != "genre" and any(value not in _ANCHOR_STOPWORDS for value in values)
    }
    if len(concrete_groups) < 2:
        return True, {}, groups
    hook_text = " ".join(
        [
            spec.one_liner,
            spec.core_rule,
            spec.genre,
            spec.setting_locale or "",
            spec.protagonist_role or "",
            spec.base_desire,
            spec.reversal,
            *(str(item) for item in spec.rewards),
            *(str(item) for item in spec.costs),
            *(str(item) for item in spec.constraints.values()),
            *(str(item) for item in spec.arc_engine),
        ]
    )
    matched = {
        key: [value for value in values if value and value in hook_text]
        for key, values in concrete_groups.items()
    }
    matched = {key: values for key, values in matched.items() if values}
    return len(matched) >= 2, matched, groups


def _score_delta(spec: HookSpec) -> int:
    combined = f"{spec.base_desire} {spec.reversal} {spec.core_rule} {spec.one_liner}".lower()
    hits = sum(1 for token in _OPPOSITION_HINTS if token.lower() in combined)
    explicit_pair = bool(
        spec.base_desire and spec.reversal and spec.base_desire not in spec.reversal
    )
    return _clamp_int(4 + hits * 1.2 + (1.5 if explicit_pair else 0), 1, 10)


def _score_reward(spec: HookSpec) -> int:
    text = " ".join([*spec.rewards, spec.one_liner]).lower()
    hits = sum(1 for token in _HIGH_REWARD_HINTS if token.lower() in text)
    return _clamp_int(3 + len(spec.rewards) * 1.4 + hits, 1, 10)


def _score_constraint(spec: HookSpec) -> int:
    dimensions = len([v for v in spec.constraints.values() if v])
    anti_cheat_bonus = min(2.0, len(spec.anti_cheat) * 0.6)
    return _clamp_int(2 + dimensions * 1.5 + anti_cheat_bonus, 1, 10)


def _score_penalty(spec: HookSpec) -> int:
    text = " ".join([*spec.costs, spec.one_liner, spec.core_rule]).lower()
    hits = sum(1 for token in _COST_HINTS if token.lower() in text)
    return _clamp_int(2 + len(spec.costs) * 1.8 + hits * 0.6, 1, 10)


def _score_misunderstanding(spec: HookSpec) -> int:
    text = spec.misunderstanding or ""
    if not text:
        return 3
    durable = any(token in text for token in ("外界", "敌人", "旁人", "public", "enemy"))
    # Also reward hooks whose misunderstanding explicitly names an antagonist —
    # a visible opposing party is the single biggest CN retention predictor.
    villain_visible = any(token in text for token in _VILLAIN_HINTS)
    bonus = 1.5 if durable else 0
    if villain_visible:
        bonus += 0.8
    return _clamp_int(6 + bonus, 1, 10)


def _score_expansion(spec: HookSpec) -> int:
    """Score opening narrative traction, never claimed serial capacity.

    The public field name remains for stored-report compatibility.  Actual
    200/500/1000-chapter capacity is evaluated by ``SerialityProof``.  Emotion
    slogans and the number of claimed arc axes deliberately earn no points.
    """
    score = 3.0
    if spec.protagonist_role:
        score += 2.0
    if spec.opening_frame:
        score += 2.0
    if spec.hook_type:
        score += 1.0
    if 20 <= len(spec.one_liner.strip()) <= 120:
        score += 1.0
    if any(token in spec.core_rule for token in ("每", "必须", "只能", "cannot", "must")):
        score += 1.0
    return _clamp_int(score, 1, 10)


def _score_learning_cost(spec: HookSpec) -> int:
    one_liner = spec.one_liner.strip()
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", one_liner))
    latin_words = len(re.findall(r"[A-Za-z]+", one_liner))
    length = cjk_chars if cjk_chars >= latin_words else latin_words
    if 25 <= cjk_chars <= 60 or 7 <= latin_words <= 22:
        return 4
    if length <= 0:
        return 10
    if length < 12:
        return 6
    if length <= 90:
        return 5
    return 8


def _verdict_for_h_norm(h_norm: float) -> str:
    if h_norm < REJECT_H_NORM:
        return "reject"
    if h_norm < SEED_H_NORM:
        return "seed"
    if h_norm < REVIEW_H_NORM:
        return "review"
    return "expand"


def score_hook(
    spec: HookSpec | str,
    *,
    platform_profile: Mapping[str, Any] | None = None,
) -> HookScore:
    """Calculate normalized hook strength with deterministic rules."""

    hook_spec = extract_hook_spec_from_text(spec) if isinstance(spec, str) else spec
    delta = _score_delta(hook_spec)
    reward = _score_reward(hook_spec)
    constraint = _score_constraint(hook_spec)
    penalty = _score_penalty(hook_spec)
    misunderstanding = _score_misunderstanding(hook_spec)
    expansion = _score_expansion(hook_spec)
    learning_cost = _score_learning_cost(hook_spec)
    raw = (
        100.0
        * (delta / 10.0)
        * (reward / 10.0)
        * (constraint / 10.0)
        * (penalty / 10.0)
        * (misunderstanding / 10.0)
        * (expansion / 10.0)
        / max(0.3, learning_cost / 10.0)
    )
    h_norm = round(_clamp_float(raw), 2)
    return HookScore(
        delta=delta,
        reward=reward,
        constraint=constraint,
        penalty=penalty,
        misunderstanding=misunderstanding,
        expansion=expansion,
        learning_cost=learning_cost,
        h_norm=h_norm,
        verdict=_verdict_for_h_norm(h_norm),  # type: ignore[arg-type]
    )


def extract_hook_spec_from_text(text: str) -> HookSpec:
    """Coarse heuristic fallback for free-string premise evaluation.

    This path is a deterministic rough estimate, not semantic premise extraction.
    Production planning should pass a structured HookSpec.
    """

    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        clean = "普通人想改变命运，却被迫承担反常识规则的代价。"
    costs: list[str] = []
    if any(token in clean for token in _COST_HINTS):
        costs.append("每次兑现爽点都必须支付可见代价")
    constraints = {"ban": "不能绕开核心反常识规则"}
    if any(token in clean for token in ("时间", "deadline", "每天", "每次")):
        constraints["time"] = "触发窗口受时间限制"
    if any(token in clean for token in ("规则", "必须", "不能", "must", "rule")):
        constraints["method"] = "必须按规则指定方式完成"
    misunderstanding = "外界误读主角真实意图" if any(
        token in clean for token in ("误解", "以为", "迪化", "misread")
    ) else None
    return HookSpec(
        mechanism_key="free_text",
        genre="",
        setting_locale=None,
        protagonist_role=None,
        base_desire="改变命运",
        reversal=clean[:180],
        rewards=("命运翻盘",),
        constraints=constraints,
        anti_cheat=("不能无代价重复触发",),
        costs=tuple(costs or ["失败会留下后续债务"]),
        misunderstanding=misunderstanding,
        arc_engine=("规则升级", "代价升级"),
        one_liner=clean[:120],
        core_rule=clean[:240],
    )


def evaluate_hook_strength_gate(
    spec: HookSpec | str,
    *,
    min_h_norm: float = SEED_H_NORM,
    platform_profile: Mapping[str, Any] | None = None,
    premise_context: Mapping[str, Any] | str | None = None,
) -> HookStrengthGateReport:
    hook_spec = extract_hook_spec_from_text(spec) if isinstance(spec, str) else spec
    score = score_hook(hook_spec, platform_profile=platform_profile)
    findings: list[HookStrengthFinding] = []
    suggestions: list[str] = []
    if score.delta < 6:
        findings.append(
            HookStrengthFinding(
                code="weak_reversal",
                severity="high",
                message="Hook reversal is not clearly opposed to the base desire.",
                path="reversal",
                repair_action=(
                    "Make the protagonist's normal desire collide with a mandatory "
                    "opposite action."
                ),
            )
        )
        suggestions.append("补强欲望与反转之间的正面冲突。")
    if score.constraint < 6:
        findings.append(
            HookStrengthFinding(
                code="thin_constraints",
                severity="medium",
                message="Hook has too few operational constraints or anti-cheat rules.",
                path="constraints",
                repair_action=(
                    "Add time/object/method/ban constraints and explicit anti-cheat rules."
                ),
            )
        )
        suggestions.append("增加时间、对象、方式、禁止项或反作弊规则。")
    if score.penalty < 6:
        findings.append(
            HookStrengthFinding(
                code="low_cost",
                severity="medium",
                message="Reward lacks a visible cost or aftereffect.",
                path="costs",
                repair_action="Attach a recurring cost to every successful use of the hook.",
            )
        )
        suggestions.append("给每次成功绑定可见代价或后效。")
    if score.h_norm < min_h_norm:
        findings.append(
            HookStrengthFinding(
                code="below_h_norm_threshold",
                severity="high",
                message=f"H_norm {score.h_norm:.2f} is below threshold {min_h_norm:.2f}.",
                path="h_norm",
                repair_action=(
                    "Rewrite the premise with stronger reversal, constraints, cost, "
                    "misunderstanding, or expansion axes."
                ),
            )
        )
    aligned, _matched_anchor_groups, _anchor_groups = hook_premise_alignment(
        hook_spec,
        premise_context,
    )
    if not aligned:
        findings.append(
            HookStrengthFinding(
                code="hook_premise_mismatch",
                severity="high",
                message="Hook does not match the concrete premise anchors.",
                path="premise_context",
                repair_action=(
                    "Regenerate a hook that names the protagonist identity, core mechanism, "
                    "or opening pressure from the approved premise."
                ),
            )
        )
        suggestions.append("重写 hook，使其明确贴合主角身份、核心机制或开局压力。")
    hard_failed = any(
        finding.code == "hook_premise_mismatch" and finding.severity == "high"
        for finding in findings
    )
    passed = score.h_norm >= min_h_norm and not hard_failed
    return HookStrengthGateReport(
        findings=tuple(findings),
        h_norm=score.h_norm,
        passed=passed,
        rewrite_suggestions=tuple(suggestions),
        score=score,
        verdict="pass" if passed else "reject" if hard_failed else "warn_only",
    )


def repair_hook_spec_once(
    spec: HookSpec,
    report: HookStrengthGateReport,
    *,
    premise_context: Mapping[str, Any] | str | None = None,
) -> HookSpec:
    """Apply one deterministic strengthening pass based on gate findings.

    When ``premise_context`` is supplied, the rewrite is checked against the
    premise anchors: the formula-pool one_liner rebuild can drop anchor words
    that only lived in the original one_liner, so a repair that breaks an
    alignment the original spec had falls back to the original one_liner.
    """

    constraints = dict(spec.constraints)
    anti_cheat = list(spec.anti_cheat)
    costs = list(spec.costs)
    rewards = list(spec.rewards)
    arc_engine = list(spec.arc_engine)

    codes = {finding.code for finding in report.findings}
    if "hook_premise_mismatch" in codes:
        return spec
    if "weak_reversal" in codes and "method" not in constraints:
        constraints["method"] = "每次兑现奖励前必须执行与正常欲望相反的可见动作"
    if "thin_constraints" in codes:
        constraints.setdefault("time", "触发必须发生在明确时限或场域内")
        constraints.setdefault("ban", "禁止用最直观捷径绕开核心代价")
        anti_cheat.extend(["同一对象重复触发收益衰减", "绕开代价会反噬"])
    if "low_cost" in codes:
        costs.extend(["每次成功都会留下公开误解或资源债务", "代价会在下一轮升级"])
    if "below_h_norm_threshold" in codes:
        rewards.extend(["权限提升", "真相碎片"])
        arc_engine.extend(["代价升级", "误解升级", "规则边界升级"])

    deduped_constraints = {key: value for key, value in constraints.items() if value}
    deduped_anti_cheat = tuple(dict.fromkeys(item for item in anti_cheat if item))
    deduped_costs = tuple(dict.fromkeys(item for item in costs if item))
    deduped_rewards = tuple(dict.fromkeys(item for item in rewards if item))
    deduped_arc = tuple(dict.fromkeys(item for item in arc_engine if item))
    misunderstanding = spec.misunderstanding or "外界误读主角真实意图并持续放大风险"

    # Build a provisional spec with the strengthened fields, then route the
    # one_liner through the same formula pool the original renderer uses, so
    # repaired hooks read in the same voice as freshly generated ones.
    provisional = spec.model_copy(
        update={
            "rewards": deduped_rewards,
            "costs": deduped_costs,
        }
    )
    from bestseller.services.hook_formula_pool import render_one_liner_for_spec

    one_liner = render_one_liner_for_spec(provisional, formula_id=spec.expression_style or None)
    core_rule = (
        f"{spec.core_rule} 每次触发必须同时满足限制、反作弊与可见代价；"
        "下一轮代价或误解必须升级。"
    )
    repaired = spec.model_copy(
        update={
            "rewards": deduped_rewards,
            "constraints": deduped_constraints,
            "anti_cheat": deduped_anti_cheat,
            "costs": deduped_costs,
            "misunderstanding": misunderstanding,
            "arc_engine": deduped_arc,
            "one_liner": one_liner[:240],
            "core_rule": core_rule[:500],
        }
    )
    if premise_context is not None:
        aligned_before, _, _ = hook_premise_alignment(spec, premise_context)
        aligned_after, _, _ = hook_premise_alignment(repaired, premise_context)
        if aligned_before and not aligned_after:
            repaired = repaired.model_copy(update={"one_liner": spec.one_liner})
    return repaired


def hook_strength_report_to_dict(report: HookStrengthGateReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


__all__ = [
    "REJECT_H_NORM",
    "REVIEW_H_NORM",
    "SEED_H_NORM",
    "evaluate_hook_strength_gate",
    "extract_hook_spec_from_text",
    "hook_premise_alignment",
    "hook_strength_report_to_dict",
    "premise_anchor_groups",
    "repair_hook_spec_once",
    "score_hook",
]
