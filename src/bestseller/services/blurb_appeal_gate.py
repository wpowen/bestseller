"""Deterministic 简介/书名/标签 click-power gate (zero-token).

Answers the core product question: *would a reader click on this book from the
blurb alone?*  Scores the listing package (title + synopsis + premise + tags) on
an 11-dimension rubric (``config/story_appeal.yaml`` → ``blurb_rubric``) using
regex + genre lexicons only — no LLM.  Mirrors the heuristic style of
``opening_hook_density_gate.py``.

The semantic / deep-judgment dimensions live in
:mod:`bestseller.services.premise_appeal_judge` (LLM).  This gate is the fast,
deterministic clickability proxy and is always safe to run.
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF003 — Chinese punctuation is intentional in the lexicons.
import re
from typing import Any

from bestseller.domain.appeal import AppealDimension, BlurbAppealVerdict

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_SENTENCE_SPLIT_RE = re.compile(r"[。？！!?\n]")
_DIGIT_RE = re.compile(r"[0-9一二三四五六七八九十百千万亿]")
# A *specific* quantity: Arabic digits, or a CJK number bound to a strong unit
# (年/天/万/亿…). Distinguishes "三天内一个亿" (concrete stakes) from the vague
# "一段旅程 / 一个故事" where 一 is just a filler counter.
_CONCRETE_NUM_RE = re.compile(
    r"[0-9]|[两二三四五六七八九十百千万亿]\s*[年天岁月日万亿元块层重品阶级章里米届岁]"
    r"|第[0-9一二三四五六七八九十百千]"
)
_INTENSIFIER_RE = re.compile(
    r"非常|极其|无比|十分|格外|异常|绝美|惊艳|震撼人心|举世无双|绝世|惊天|无尽"
)
_FIRST_PERSON_RE = re.compile(r"我(?!们)|吾")  # exclude 我们 (narrator filler)

# ── 新读者可懂度（黑话过载）检测 ─────────────────────────────────────────────
# 简介的职责是让"零基础新读者一眼看懂+想点"。生造的机制黑话/系统词/编号会让人
# 看不懂、不知道爽点在哪。注意只打【生造/跨域堆砌】词,不打宗门/厉鬼/灵根这类
# 单个常见题材词(题材读者本就认识)。与 concreteness 互补:具体≠可懂。
_JARGON_BRACKET_RE = re.compile(r"[「『][^」』]{1,12}[」』]")  # 「低危怪谈」式生造标签
_JARGON_CODE_RE = re.compile(
    r"[A-Za-z]{2,}[0-9]*|[#＃]\s*[0-9]{2,}|[SABCDEＳ]\s*级|[0-9]+\s*号档?"
)  # AR / #0371 / S级 / 0371号 等编号档位
# 生造机制/系统/赛博/经济杠杆词根(跨域堆砌的标志,非单个常见题材名词)
_JARGON_STEMS: tuple[str, ...] = (
    "灵码", "词条", "编辑器", "编译", "算法", "数据化", "数据流", "代码",
    "杠杆", "越级", "掉档", "档位", "词缀", "录入", "名册", "禁忌线",
    "目击即", "存在杠杆", "口碑越级", "面板", "数值", "解锁", "刷新",
    "副本", "属性栏", "灵警", "可视化", "编号",
)


def _score_comprehensibility(synopsis: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    """新读者可懂度:生造黑话/编号/系统词密度越高 → 越看不懂 → 分越低。"""

    text = synopsis or ""
    n = max(_cjk_len(text), 1)
    stem_hits = _count_hits(text, _JARGON_STEMS)
    bracket_hits = len(_JARGON_BRACKET_RE.findall(text))
    code_hits = len(_JARGON_CODE_RE.findall(text))
    coined = stem_hits + bracket_hits + code_hits
    per_100 = coined / (n / 100.0)
    score = _clamp(5.0 - 0.9 * per_100, 0.0, 5.0)
    # 绝对量兜底:≥4 个生造词,新读者必劝退,封到 ≤2.0
    if coined >= 4:
        score = min(score, 2.0)
    rationale = (
        f"生造黑话 {coined} 处(词根{stem_hits}/标签{bracket_hits}/编号{code_hits})"
        f"，每百字 {per_100:.1f}"
    )
    return score, rationale, {"coined": coined, "per_100": round(per_100, 2)}


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def _count_hits(text: str, terms: list[str] | tuple[str, ...]) -> int:
    """Number of distinct terms from ``terms`` that appear in ``text``."""

    if not text:
        return 0
    return sum(1 for t in terms if t and str(t) in text)


def _first_sentence(text: str) -> str:
    for seg in _SENTENCE_SPLIT_RE.split((text or "").strip()):
        seg = seg.strip()
        if seg:
            return seg
    return ""


def _last_sentence(text: str) -> str:
    segs = [s.strip() for s in _SENTENCE_SPLIT_RE.split((text or "").strip()) if s.strip()]
    return segs[-1] if segs else ""


def _clamp(value: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, value))


def _lex(lexicon: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = lexicon.get(key) if isinstance(lexicon, dict) else None
    if isinstance(raw, (list, tuple)):
        return tuple(str(x) for x in raw if str(x).strip())
    return ()


# ---------------------------------------------------------------------------
# Per-dimension scorers — each returns (score_0_5, rationale, evidence).
# ---------------------------------------------------------------------------


# 身份的【结构】线索（题材中立）：职业/角色后缀 + 「是一名X / 的工作 / 唯一能…的人」。
# 救爽文身份词表(废柴/赘婿/战神…)认不出的现实/悬疑/怪谈身份(殡仪馆夜班工/法医/守墓人)。
_ROLE_IDENTITY_RE = re.compile(
    r"殡仪馆|夜班|守墓|守夜|更夫|入殓|殓|法医|仵作|捕快|衙役|刑警|警探|协警|侦探|"
    r"保安|司机|外卖|快递|程序员|码农|医生|护士|律师|教师|社畜|临时工|实习|主播|"
    r"店长|会计|出纳|客服|话务|清洁工|环卫|矿工|渔民|猎人|镖师|账房|更夫|看守|"
    r"(?:是|当|做)[一了]?(?:名|个|位|届)[^，。,.！!？?]{1,8}(?:工|员|师|警|医|生|官|匠|者|夫|长)|"
    r"的工作(?:是|就是)|职业是|唯一(?:能|会|可以)[^，。,.]{0,16}的人"
)


def _score_selling_triad(combined: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    has_identity = (
        _count_hits(combined, _lex(lex, "identity_markers")) > 0
        or bool(_ROLE_IDENTITY_RE.search(combined))
    )
    has_conflict = (
        _count_hits(combined, _lex(lex, "conflict_verbs")) > 0
        or _count_hits(combined, _lex(lex, "high_arousal_emotion")) > 0
        # 结构性冲突/威胁：两难/切肤/迫近/骇异任一在场 = 有处境冲突（题材中立）。
        or bool(_embodied_emotion_categories(combined))
    )
    has_cost = _count_hits(combined, _lex(lex, "cost_markers")) > 0
    present = sum((has_identity, has_conflict, has_cost))
    # 2/3 要素(身份+冲突,代价常隐含)给 4.0——奇幻/现实强稿常缺显式"代价词"，
    # 不应因此被压到 3.5；三要素全齐才满分。
    score = {3: 5.0, 2: 4.0, 1: 2.0, 0: 0.5}[present]
    missing = [
        name
        for name, ok in (("身份", has_identity), ("冲突", has_conflict), ("代价", has_cost))
        if not ok
    ]
    rationale = "三要素齐备" if not missing else f"缺要素：{'/'.join(missing)}"
    return score, rationale, {"present": present, "missing": missing}


def _score_hook_strength(synopsis: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    first = _first_sentence(synopsis)
    first_len = _cjk_len(first)
    # 三选二信号
    has_contrast = bool(re.search(r"却|但|竟|偏偏|反而|不料|没想到|原来", first))
    has_involving = bool(_FIRST_PERSON_RE.search(first) or "“" in first or '"' in first
                         or re.search(r"[？?]", first))
    has_uncertainty = (
        _count_hits(first, _lex(lex, "curiosity_markers")) > 0
        or bool(re.search(r"[？?]", first))
    )
    signals = sum((has_contrast, has_involving, has_uncertainty))
    # 首句越短越好（≤30 字满分基线），信号越多越好
    len_base = 3.0 if first_len <= 30 else (2.0 if first_len <= 45 else 1.0)
    score = _clamp(len_base + signals * 0.7)
    rationale = f"首句{first_len}字，强信号{signals}/3"
    return score, rationale, {"first_len": first_len, "signals": signals}


def _score_differentiation(combined: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    red = _lex(lex, "red_ocean_tropes")
    red_hits = _count_hits(combined, red)
    has_reversal = _count_hits(combined, _lex(lex, "reversal_markers")) > 0
    has_specific = bool(_DIGIT_RE.search(combined)) or bool(re.search(r"[“”\"]", combined))
    score = 4.0 - red_hits * 1.0 + (0.5 if has_reversal else 0) + (0.5 if has_specific else 0)
    score = _clamp(score)
    rationale = (
        f"命中红海套路{red_hits}处" if red_hits else "未见明显套路化"
    )
    return score, rationale, {"red_ocean_hits": red_hits}


def _score_anti_template(combined: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    blk = _lex(lex, "template_blacklist")
    blk_hits = _count_hits(combined, blk)
    ellipsis = combined.count("……") + combined.count("...")
    bangs = combined.count("！") + combined.count("!")
    punct_flood = (ellipsis > 2) or (bangs > 3)
    score = _clamp(5.0 - blk_hits * 1.0 - (1.0 if punct_flood else 0))
    rationale = (
        f"AI腔/模板句{blk_hits}处" + ("，标点泛滥" if punct_flood else "")
        if (blk_hits or punct_flood)
        else "无模板腔"
    )
    return score, rationale, {"template_hits": blk_hits, "punct_flood": punct_flood}


def _score_open_loop_end(synopsis: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    last = _last_sentence(synopsis)
    spoiled = _count_hits(synopsis, _lex(lex, "spoiler_markers")) > 0
    has_q = bool(re.search(r"[？?]", last))
    has_curiosity = _count_hits(last, _lex(lex, "curiosity_markers")) > 0
    has_open = bool(re.search(r"却|直到|可…|将|即将|等待|未知|谁也不知道", last))
    if spoiled:
        score, rationale = 1.5, "结尾疑似剧透结局"
    elif has_q or has_curiosity:
        score, rationale = 5.0, "结尾留悬念问句"
    elif has_open:
        score, rationale = 4.0, "结尾开放式"
    else:
        score, rationale = 3.0, "结尾平淡，未留强钩"
    return score, rationale, {"last": last[:60], "spoiled": spoiled}


def _score_genre_signal(
    title: str, synopsis: str, tags: list[str], lex: dict[str, Any], genre_terms: tuple[str, ...]
) -> tuple[float, str, dict]:
    package = f"{title} {synopsis} {' '.join(tags or [])}"
    own = tuple(
        dict.fromkeys(
            [
                *_lex(lex, "emotion_palette"),
                *_lex(lex, "reader_anchors"),
                *_lex(lex, "golden_finger_forms"),
                *genre_terms,
            ]
        )
    )
    hits = _count_hits(package, own) if own else 0
    # 标签与正文/书名信号一致性：标签词至少部分出现在简介
    tag_echo = sum(1 for t in (tags or []) if t and str(t) in synopsis)
    score = _clamp(2.5 + min(hits, 4) * 0.5 + min(tag_echo, 2) * 0.25)
    rationale = f"题材信号命中{hits}，标签呼应{tag_echo}"
    return score, rationale, {"genre_hits": hits, "tag_echo": tag_echo}


def _score_concreteness(synopsis: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    first_person = bool(_FIRST_PERSON_RE.search(synopsis))
    has_digit = bool(_CONCRETE_NUM_RE.search(synopsis))
    has_quote = bool(re.search(r"[“”\"]", synopsis))
    # A protagonist *container* — first person, a named/quoted speaker, or a
    # clear 3rd-person hero reference. Most bestseller blurbs are 3rd person, so
    # 他/她 must NOT be punished; abstract blurbs with no person at all are.
    has_protagonist = first_person or has_quote or ("他" in synopsis or "她" in synopsis)
    score = 1.0
    if has_protagonist:
        score += 1.5
    if has_digit:
        score += 1.5
    if first_person or has_quote:
        score += 1.0   # extra immersion for POV / direct speech
    score = _clamp(score)
    rationale = (
        "具体化充分" if score >= 4 else "有主角但缺具体细节" if has_protagonist else "全抽象无主角"
    )
    return score, rationale, {"first_person": first_person, "has_digit": has_digit}


# 具身情绪信号（题材中立）：情绪由【处境/结构】承载，而非堆砌爽文情绪词。
# 这一通道救「show-don't-tell」型悬疑/怪谈/正剧/治愈简介——它们靠两难抉择、
# 切肤关系利害、迫近威胁、骇异反常制造点击冲动，却命不中 high_arousal_emotion
# 那张爽文词表，于是被关键词通道误判为「无情绪」并被命门(emotion<3.0)封顶 78，
# 倒逼重生成更廉价的堆词稿——与正文层 scene-emotion-hook-scorer 同一种器械错。
# 刻意设高门槛(要结构不要单词)：只有「代价词」而无两难/切肤的烧脑冷稿不应被抬起。
_EMBODIED_EMOTION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 两难/不可逆抉择：在两种失去之间被迫选择（最强情绪驱动）
    ("两难", (r"是.{0,12}还是", r"要么.{0,12}要么", r"二选一", r"押上", r"赌上",
              r"拿.{0,6}换", r"保.{0,6}还是", r"只能眼睁睁", r"非.{0,6}不可")),
    # 切肤关系利害：所爱之人的生死/唯一（代入最强）
    ("切肤", (r"(妹妹|弟弟|母亲|父亲|爸|妈|孩子|女儿|儿子|爱人|妻子|丈夫|唯一)"
              r".{0,18}(死|活|救|没了|失去|换|命)",
              r"(救|保住|活下去).{0,8}(她|他|孩子|妹妹|母亲|家人)")),
    # 迫近威胁/倒计时：时间压到眼前
    ("迫近", (r"倒计时", r"凌晨", r"午夜", r"天亮前", r"来不及", r"期限",
              r"最后.{0,3}(夜|天|小时|一笔)", r"[七三五].{0,2}(天|小时|日)内")),
    # 骇异反常/失控：日常被异样撕开（悬疑/怪谈情绪源）
    ("骇异", (r"睁开了?眼", r"镜子里", r"(自己|他|她)的名字", r"凭空", r"本不该",
              r"回过头", r"消失")),
)


def _embodied_emotion_categories(text: str) -> list[str]:
    """Distinct *kinds* of dramatic charge carried by situation (not keywords)."""

    if not text:
        return []
    return [name for name, pats in _EMBODIED_EMOTION_SIGNALS
            if any(re.search(p, text) for p in pats)]


def _score_emotion_charge(synopsis: str, lex: dict[str, Any]) -> tuple[float, str, dict]:
    head = synopsis[:30]
    # (1) 关键词通道（原行为）：高唤起情绪词 + 本题材情绪色板（题材感知，零成本）
    terms = _lex(lex, "high_arousal_emotion") + _lex(lex, "emotion_palette")
    hits = _count_hits(synopsis, terms)
    kw_front = _count_hits(head, terms) > 0
    kw_score = 1.5 + min(hits, 4) * 0.7 + (1.0 if kw_front else 0)
    # (2) 具身通道（新增）：处境/两难/切肤/迫近/骇异承载的情绪
    cats = _embodied_emotion_categories(synopsis)
    emb_front = bool(_embodied_emotion_categories(head))
    emb_score = 1.5 + min(len(cats), 4) * 0.85 + (0.6 if emb_front else 0)
    # 只升不降：取两通道较高者，绝不弱化既有达标稿（no-op 安全 + 不可下调博弈）
    use_emb = emb_score > kw_score
    score = _clamp(max(kw_score, emb_score))
    front = kw_front or emb_front
    rationale = (
        f"具身情绪{len(cats)}类({'/'.join(cats)})" + ("，且前置" if emb_front else "")
        if use_emb
        else f"高唤起情绪{hits}处" + ("，且前置" if kw_front else "")
    )
    return score, rationale, {
        "emotion_hits": hits,
        "embodied_categories": cats,
        "front_loaded": front,
        "channel": "embodied" if use_emb else "keyword",
    }


def _score_adjective_thrift(synopsis: str) -> tuple[float, str, dict]:
    total = max(_cjk_len(synopsis), 1)
    intensifiers = len(_INTENSIFIER_RE.findall(synopsis))
    de_count = synopsis.count("的") + synopsis.count("地")
    ratio = (intensifiers * 3 + de_count) / total
    score = _clamp(5.0 - ratio * 12.0)
    rationale = "动词驱动、克制" if score >= 4 else "形容词/修饰偏多"
    return score, rationale, {"intensifiers": intensifiers, "ratio": round(ratio, 3)}


def _score_length_format(
    synopsis: str, envelope: dict[str, Any]
) -> tuple[float, str, dict]:
    length = _cjk_len(synopsis)
    lo = int(envelope.get("min", 80)) if isinstance(envelope, dict) else 80
    hi = int(envelope.get("max", 220)) if isinstance(envelope, dict) else 220
    paragraphs = len([ln for ln in (synopsis or "").splitlines() if ln.strip()])
    if lo <= length <= hi:
        score = 5.0
    elif length < lo:
        score = _clamp(5.0 - (lo - length) / max(lo, 1) * 5.0)
    else:
        score = _clamp(5.0 - (length - hi) / max(hi, 1) * 5.0)
    if paragraphs < 2:
        score = _clamp(score - 0.5)
    rationale = f"{length}字（带 {lo}-{hi}），{paragraphs}段"
    return score, rationale, {"length": length, "envelope": [lo, hi]}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_blurb_appeal(
    *,
    title: str,
    synopsis: str,
    premise: str = "",
    tags: list[str] | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str = "zh",
    config: dict[str, Any] | None = None,
    lexicon: dict[str, Any] | None = None,
    platform: str | None = None,
    genre_terms: tuple[str, ...] = (),
) -> BlurbAppealVerdict:
    """Score the listing package for click-power. Pure / deterministic.

    ``config`` / ``lexicon`` are injected by the orchestrator; when omitted they
    are lazily loaded from :mod:`bestseller.services.story_appeal` (deferred
    import avoids a circular dependency).
    """

    if config is None or lexicon is None:
        from bestseller.services.story_appeal import (
            load_story_appeal_config,
            resolve_genre_lexicon,
        )

        config = config or load_story_appeal_config()
        lexicon = lexicon if lexicon is not None else resolve_genre_lexicon(genre, sub_genre)

    tags = list(tags or [])
    synopsis = str(synopsis or "")
    combined = f"{title or ''} {synopsis} {premise or ''}"
    rubric = config.get("blurb_rubric", {}) if isinstance(config, dict) else {}

    envelope = _resolve_platform_envelope(config, platform)

    raw: dict[str, tuple[float, str, dict]] = {
        "selling_triad": _score_selling_triad(combined, lexicon),
        "hook_strength": _score_hook_strength(synopsis, lexicon),
        "differentiation": _score_differentiation(combined, lexicon),
        "anti_template": _score_anti_template(combined, lexicon),
        "open_loop_end": _score_open_loop_end(synopsis, lexicon),
        "genre_signal": _score_genre_signal(title or "", synopsis, tags, lexicon, genre_terms),
        "concreteness": _score_concreteness(synopsis, lexicon),
        "emotion_charge": _score_emotion_charge(synopsis, lexicon),
        "adjective_thrift": _score_adjective_thrift(synopsis),
        "length_format": _score_length_format(synopsis, envelope),
        "comprehensibility": _score_comprehensibility(synopsis, lexicon),
    }

    dims: list[AppealDimension] = []
    weighted = 0.0
    total_weight = 0.0
    findings: list[str] = []
    suggestions: list[str] = []
    for key, (score, rationale, evidence) in raw.items():
        spec = rubric.get(key, {}) if isinstance(rubric, dict) else {}
        weight = float(spec.get("weight", 0)) if isinstance(spec, dict) else 0.0
        label = str(spec.get("label", key)) if isinstance(spec, dict) else key
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

    # ── 点击命门 floor（一票否决）──────────────────────────────────────────
    # 钩子强度 + 情绪强度是真正驱动点击的两维。加权平均会让一堆"表面达标"维
    # (三要素齐/有具体/没AI腔/有问号结尾)把这两维的低分稀释掉 —— 于是一篇
    # 烧脑、不抓人的简介也能堆到 85（真实案例《规则漏洞不保护我》:钩子3.0/情绪
    # 1.5 却总分85.2）。故对命门维设硬 floor:任一未过线 → 总分封顶到 cap(<80)，
    # 表面分再高也不达标。
    bar_cfg = config.get("meets_bar", {}) if isinstance(config, dict) else {}
    floors = bar_cfg.get("blurb_critical_floors") or {}
    cap = float(bar_cfg.get("critical_floor_cap", 78))
    by_key = {d.key: d for d in dims}
    breached = [
        (str((rubric.get(k, {}) or {}).get("label", k)), by_key[k].score, float(v))
        for k, v in floors.items()
        if k in by_key and by_key[k].score < float(v)
    ]
    # AND 语义：只有当【所有】命门维(钩子+情绪)都弱时才封顶——即"既无强钩、又无强情绪"
    # 的真·不可点击稿(如《规则漏洞不保护我》钩子3.0+情绪1.5)。若其一够强(如现实题材
    # 钩子平但情绪满分)，情绪/钩子可单独扛起点击力，不封顶。
    if floors and len(breached) == len(floors) and total > cap:
        total = cap
        names = "、".join(f"{lab}({sc:.1f}<{fl:.1f})" for lab, sc, fl in breached)
        findings.append(
            f"[点击命门全失] {names} → 总分封顶 {cap:.0f}（既无强钩又无强情绪，不可点击）"
        )

    # ── 立意↔调性一致 cap ──────────────────────────────────────────────────
    # 高级/严肃立意被简介稀释成爽文套路(打脸/跪地/碾压/逆袭/全村喊冤)= 调性背叛、显廉价。
    # 只罚【立意里严肃信号≥阈值 且 简介爽文套词扎堆】的错配；纯爽文题材不罚。
    tone_cfg = config.get("tone_consistency", {}) if isinstance(config, dict) else {}
    cliche_beats = _lex(tone_cfg, "shuangwen_cliche_beats")
    serious_signals = _lex(tone_cfg, "serious_concept_signals")
    if cliche_beats and serious_signals:
        serious_min = int(tone_cfg.get("serious_signal_min", 2))
        sat_min = int(tone_cfg.get("cliche_saturation_min", 3))
        tone_cap = float(tone_cfg.get("tone_cap", cap))
        n_serious = _count_hits(premise or "", serious_signals)
        n_cliche = _count_hits(synopsis, cliche_beats)
        if n_serious >= serious_min and n_cliche >= sat_min and total > tone_cap:
            total = tone_cap
            findings.append(
                f"[立意↔调性错配] 立意严肃/高概念(命中{n_serious})，简介却堆 {n_cliche} 个爽文套路"
                f"(打脸/跪地/碾压/逆袭…) → 调性背叛、显廉价 → 封顶 {tone_cap:.0f}"
            )
            suggestions.append(
                "简介改用与立意一致的张力(代价/异化/抉择/恐惧)，删打脸/跪地/碾压/逆袭等爽文套词"
            )

    # ── 新读者可懂度 cap（独立一票否决）────────────────────────────────────
    # 黑话过载(生造机制/系统/编号堆砌)让新读者看不懂、不知爽点 → 列表页必劝退。
    # 独立 cap(不与钩子/情绪 AND):再有钩子有情绪,看不懂也卖不动。
    comp_dim = by_key.get("comprehensibility")
    comp_floor = float(bar_cfg.get("comprehensibility_floor", 2.5))
    comp_cap = float(bar_cfg.get("comprehensibility_cap", 60))
    if comp_dim is not None and comp_dim.score < comp_floor and total > comp_cap:
        total = comp_cap
        findings.append(
            f"[新读者看不懂] 可懂度{comp_dim.score:.1f}<{comp_floor:.1f}（{comp_dim.rationale}）"
            f" → 生造黑话过载、新读者一眼劝退 → 封顶 {comp_cap:.0f}"
        )
        suggestions.append(
            "把生造黑话/编号/系统词全部去掉或就地换成大白话,让没读过设定的人也秒懂主角要干嘛、爽在哪"
        )

    grade = _grade_from_total(total, config)
    return BlurbAppealVerdict(
        total=total,
        grade=grade,
        dimensions=tuple(dims),
        findings=tuple(findings),
        suggestions=tuple(suggestions),
        language=language,
    )


def _resolve_platform_envelope(config: dict[str, Any], platform: str | None) -> dict[str, Any]:
    table = config.get("platform_blurb", {}) if isinstance(config, dict) else {}
    if isinstance(table, dict):
        if platform and platform in table:
            return table[platform]
        return table.get("default", {"min": 80, "max": 220})
    return {"min": 80, "max": 220}


def _grade_from_total(total: float, config: dict[str, Any]) -> str:
    grades = config.get("grades", {}) if isinstance(config, dict) else {}
    recommend = float(grades.get("recommend", 80))
    consider = float(grades.get("consider", 65))
    if total >= recommend:
        return "recommend"
    if total >= consider:
        return "consider"
    return "pass"


_SUGGESTIONS: dict[str, str] = {
    "selling_triad": "简介前两行补齐「身份+开局冲突+失败代价」三要素",
    "hook_strength": "首句压到 30 字内，用对比/疑问/第一人称制造强钩",
    "differentiation": "去掉烂大街套路，点出一句别人没写过的独家设定",
    "anti_template": "删除'本以为/却没想到/何去何从'等 AI 腔模板句，少用省略号感叹号",
    "open_loop_end": "结尾换成开放式悬念问句，绝不剧透结局",
    "genre_signal": "让书名/标签/简介的题材信号一致，避免互相打架",
    "concreteness": "给主角具名或第一人称，加入具体数字/地点/物件",
    "emotion_charge": "把退婚/背叛/重生等高唤起情绪事件提到开头",
    "adjective_thrift": "减少形容词堆砌，改用强动词驱动",
    "length_format": "把简介长度调进平台字数带并分段",
    "comprehensibility": "删掉生造黑话/编号/系统词,换成新读者一眼能懂的大白话",
}

__all__ = ["evaluate_blurb_appeal"]
