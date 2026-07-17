"""Deterministic 简介病理检测（零 token）。

审计发现（2026-07-09，真机 tracked-rulehorror-v1）：``blurb_appeal_gate`` 是词表
加权打分器，对以下三类病天然免疫——病句照样能拿到 ≥68 分一次通过、零重生：

  1. 同义反复选择句："保饭碗还是丢工作？"（两侧是同一件事的正反面，不构成选择）。
  2. 机制黑话泄漏：设计文档里的机制术语（削薄/反写/压制升级……）直接漏进读者文案。
  3. 模板插值残留：钩子模板机械拼接产生的重复短语/断裂从句。

外加一个"喘不上气"信号（流水长句）：单句过长、逗号堆砌到没有呼吸点。

本模块只做检测，不做打分决策——``blurb_appeal_gate.evaluate_blurb_appeal`` 把
``fatal`` 级 finding 当作独立一票否决 cap 的触发条件（与 comprehensibility_cap/
progression_cap 同构）。``derive_book_jargon_terms`` 从本书的设计字段（金手指/
机制/kernel 描述）派生黑话词表——按书派生，不是全局词表，避免误伤正常叙事用词。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese punctuation/fixtures are intentional.

_DEFAULT_ANTONYM_VERB_PAIRS: tuple[tuple[str, str], ...] = (
    ("保", "丢"), ("留", "失"), ("活", "死"), ("守", "弃"),
)
_DEFAULT_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("饭碗", "工作", "岗位", "职位"),
    ("性命", "命", "活路"),
)
_DEFAULT_MECHANISM_STEMS: tuple[str, ...] = (
    "削薄", "反写", "判定", "权限", "阈值", "溢价", "回写", "锚点", "观测",
    "协议", "结算", "压制", "重写权",
    # 学术/技术词类(2026-07-09《我靠签契改地脉》persona划走理由"拓扑名词脑瓜子疼")：
    # 高概念杂交常带入的现代学科词汇,出现在设定文本里合法,出现在读者简介里劝退。
    "拓扑", "语义", "边界条件", "坐标系", "参数", "变量", "函数", "熵值", "算力",
)
_DEFAULT_REDUPLICATION_WHITELIST: tuple[str, ...] = (
    "一步一步", "越来越", "越战越", "日复一日", "年复一年", "一天一天",
    "一点一点", "层层", "步步", "一级一级",
)

_SENTENCE_SPLIT_RE = re.compile(r"[。？！!?\n]")
_CHOICE_CLAUSE_RE = re.compile(
    r"([^，,。！!？?；;]{1,20})[，,]?\s*还是\s*([^，,。！!？?；;]{1,20})[？?]"
)
_JARGON_BRACKET_RE = re.compile(r"[「『][^」』]{1,12}[」』]|[\"“][^\"”]{2,6}[\"”]")
_SENTENCE_END_RE = re.compile(r"[。！？…]")


@dataclass(frozen=True)
class PathologyFinding:
    """一条病理发现。``severity`` 只有 ``fatal``（触发 gate cap）与 ``warn``（仅提示）。"""

    code: str
    severity: str
    excerpt: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "excerpt": self.excerpt,
            "detail": self.detail,
        }


def _cfg(config: Mapping[str, Any] | None, key: str, default: Any) -> Any:
    if not isinstance(config, Mapping):
        return default
    value = config.get(key, default)
    return value if value is not None else default


def _detect_tautology_choice(
    text: str,
    *,
    antonym_verb_pairs: Iterable[tuple[str, str]],
    synonym_groups: Iterable[Iterable[str]],
) -> list[PathologyFinding]:
    """"X还是Y？"选择句,当 X/Y 是同一件事的正反面（同义名词+反义动词）时命中。

    刻意保守：动词必须来自配置的反义对、名词必须落在同一同义组，双重条件都满足
    才判定为病句——避免误伤真正的两难选择（"救她，还是保住自己的秘密？"）。
    """

    findings: list[PathologyFinding] = []
    pairs = list(antonym_verb_pairs)
    groups = [tuple(g) for g in synonym_groups]
    for match in _CHOICE_CLAUSE_RE.finditer(text or ""):
        seg_a, seg_b = match.group(1), match.group(2)
        antonym_hit = any(
            (v1 in seg_a and v2 in seg_b) or (v2 in seg_a and v1 in seg_b)
            for v1, v2 in pairs
        )
        if not antonym_hit:
            continue
        noun_hit = any(
            any(term in seg_a for term in group) and any(term in seg_b for term in group)
            for group in groups
        )
        if noun_hit:
            findings.append(
                PathologyFinding(
                    code="tautology_choice",
                    severity="fatal",
                    excerpt=match.group(0),
                    detail=f"「{seg_a}」与「{seg_b}」是同一件事的正反面，不构成真选择",
                )
            )
    return findings


def _detect_jargon_leak(
    text: str, *, book_jargon_terms: tuple[str, ...], fatal_hits: int
) -> list[PathologyFinding]:
    if not book_jargon_terms:
        return []
    hits = [term for term in book_jargon_terms if term in (text or "")]
    if not hits:
        return []
    severity = "fatal" if len(hits) >= fatal_hits else "warn"
    return [
        PathologyFinding(
            code="jargon_leak",
            severity=severity,
            excerpt="、".join(hits[:6]),
            detail=f"命中本书设计术语 {len(hits)} 处（{'/'.join(hits[:6])}），读者视角无感",
        )
    ]


def _find_repeated_ngram(
    sentence: str, *, min_len: int, max_len: int, whitelist: tuple[str, ...]
) -> str | None:
    n = len(sentence)
    if n < min_len * 2:
        return None
    upper = min(max_len, n // 2)
    for length in range(upper, min_len - 1, -1):
        seen: set[str] = set()
        for i in range(n - length + 1):
            chunk = sentence[i : i + length]
            if chunk in whitelist or not chunk.strip():
                continue
            if any(p in chunk for p in "，,。！!？?；;"):
                continue
            if chunk in seen:
                return chunk
            seen.add(chunk)
    return None


def _detect_template_residue(
    text: str, *, min_len: int, max_len: int, whitelist: tuple[str, ...]
) -> list[PathologyFinding]:
    findings: list[PathologyFinding] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        repeated = _find_repeated_ngram(
            sentence, min_len=min_len, max_len=max_len, whitelist=whitelist
        )
        if repeated:
            findings.append(
                PathologyFinding(
                    code="template_residue",
                    severity="fatal",
                    excerpt=repeated,
                    detail=f"「{repeated}」在同一句内重复出现，疑似模板插值残留/断裂",
                )
            )
    return findings


def _detect_run_on_sentence(
    text: str, *, min_len: int, min_commas: int
) -> list[PathologyFinding]:
    findings: list[PathologyFinding] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        comma_count = sentence.count("，") + sentence.count(",") + sentence.count("；")
        if len(sentence) > min_len and comma_count >= min_commas:
            findings.append(
                PathologyFinding(
                    code="run_on_sentence",
                    severity="warn",
                    excerpt=sentence[:60] + ("…" if len(sentence) > 60 else ""),
                    detail=f"单句 {len(sentence)} 字、{comma_count} 个逗号，读者喘不上气",
                )
            )
    return findings


# 全知吊胃口模板(2026-07-17 用户终审"钩子/简介AI味很足"):叙述者跳出故事替读者
# 吊胃口的收尾腔——三道病理筛与persona判官(3/3)全放行,但冷读者一眼识别。
# 悬念必须落在具体的、即将发生的威胁/选择/期限上,不许用全知旁白空转。
_TEASE_TEMPLATE_RE = re.compile(
    r"殊不知|却不知道|还不知道|自己都不知道|到底还[^。！？]{0,8}什么"
    r"|命运的齿轮|一切才刚刚开始|敬请期待|等待着[他她它][的们]将是"
)


def _detect_template_tease(text: str) -> list[PathologyFinding]:
    return [
        PathologyFinding(
            code="template_tease",
            severity="fatal",
            excerpt=m.group(0),
            detail="全知旁白式吊胃口模板——把悬念改写成具体的、即将发生的威胁/选择/期限",
        )
        for m in _TEASE_TEMPLATE_RE.finditer(text)
    ]


def detect_blurb_pathology(
    text: str,
    *,
    book_jargon_terms: tuple[str, ...] = (),
    config: Mapping[str, Any] | None = None,
) -> list[PathologyFinding]:
    """对简介文本跑四个确定性病理检测器，返回全部命中的 finding（可能为空）。"""

    text = str(text or "")
    if not text.strip():
        return []

    antonym_pairs = tuple(
        tuple(pair) for pair in _cfg(config, "antonym_verb_pairs", _DEFAULT_ANTONYM_VERB_PAIRS)
    )
    synonym_groups = tuple(
        tuple(g) for g in _cfg(config, "synonym_groups", _DEFAULT_SYNONYM_GROUPS)
    )
    jargon_fatal_hits = int(_cfg(config, "jargon_fatal_hits", 3))
    min_ngram_len = int(_cfg(config, "min_ngram_len", 4))
    max_ngram_len = int(_cfg(config, "max_ngram_len", 16))
    reduplication_whitelist = tuple(
        _cfg(config, "reduplication_whitelist", _DEFAULT_REDUPLICATION_WHITELIST)
    )
    run_on_min_len = int(_cfg(config, "run_on_sentence_min_len", 80))
    run_on_min_commas = int(_cfg(config, "run_on_sentence_min_commas", 4))

    findings: list[PathologyFinding] = []
    findings.extend(_detect_template_tease(text))
    findings.extend(
        _detect_tautology_choice(
            text, antonym_verb_pairs=antonym_pairs, synonym_groups=synonym_groups
        )
    )
    findings.extend(
        _detect_jargon_leak(
            text, book_jargon_terms=book_jargon_terms, fatal_hits=jargon_fatal_hits
        )
    )
    findings.extend(
        _detect_template_residue(
            text,
            min_len=min_ngram_len,
            max_len=max_ngram_len,
            whitelist=reduplication_whitelist,
        )
    )
    findings.extend(
        _detect_run_on_sentence(text, min_len=run_on_min_len, min_commas=run_on_min_commas)
    )
    return findings


# ---------------------------------------------------------------------------
# derive_book_jargon_terms — 按书从设计字段派生黑话词表（非全局词表）
# ---------------------------------------------------------------------------


def _collect_strings(obj: Any, *, out: list[str], budget: list[int]) -> None:
    """递归收集 dict/list 里的字符串叶子值，带预算防病态膨胀。"""

    if budget[0] <= 0:
        return
    if isinstance(obj, str):
        if obj.strip():
            out.append(obj)
            budget[0] -= len(obj)
        return
    if isinstance(obj, Mapping):
        for value in obj.values():
            if budget[0] <= 0:
                return
            _collect_strings(value, out=out, budget=budget)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            if budget[0] <= 0:
                return
            _collect_strings(item, out=out, budget=budget)


_DESIGN_FIELD_KEYS: tuple[str, ...] = (
    "golden_finger", "power_system", "power_structure", "world_model",
    "public_emotion_kernel", "compliance_boundary_kernel", "emotion_driven_kernel",
    "entry_system_kernel", "story_design_kernel", "ideology_kernel",
    "identity_manifest",
    # 概念淘汰赛冠军(2026-07-09 真机《我靠签契改地脉》教训)：高概念自带的
    # 学术/机构词汇(拓扑/界枢署/语义)会经 spine/premise 渗入简介，persona 判官
    # 划走理由"名词堆得脑瓜子疼"。把冠军概念文本纳入派生源 → 文案淘汰赛把这些
    # 词当禁用词 → 逼它翻译成大白话。
    "high_concept",
)


def derive_book_jargon_terms(
    metadata: Mapping[str, Any],
    *,
    entity_whitelist: Iterable[str] = (),
    config: Mapping[str, Any] | None = None,
    char_budget: int = 20_000,
) -> tuple[str, ...]:
    """从本书设计字段（金手指/机制/kernel）派生"本书黑话词表"。

    双重条件：候选词既要是配置里的机制形态词根，又要真实出现在本书的设定文本
    里，才算数——避免用一张全局词表误伤所有书共享的正常叙事用词。
    """

    if not isinstance(metadata, Mapping):
        return ()

    design_text_parts: list[str] = []
    budget = [char_budget]
    for key in _DESIGN_FIELD_KEYS:
        if key in metadata:
            _collect_strings(metadata[key], out=design_text_parts, budget=budget)
    hook_spec = metadata.get("hook_spec")
    if isinstance(hook_spec, Mapping) and "core_rule" in hook_spec:
        _collect_strings(hook_spec["core_rule"], out=design_text_parts, budget=budget)
    design_text = " ".join(design_text_parts)

    mechanism_stems = tuple(_cfg(config, "mechanism_stems", _DEFAULT_MECHANISM_STEMS))
    stem_hits = tuple(stem for stem in mechanism_stems if stem in design_text)

    bracket_hits = tuple(
        dict.fromkeys(
            m.strip("「』『」\"“”") for m in _JARGON_BRACKET_RE.findall(design_text)
        )
    )

    whitelist = tuple(str(w) for w in entity_whitelist if str(w).strip())
    candidates = dict.fromkeys((*stem_hits, *bracket_hits))
    terms = tuple(
        term for term in candidates
        if term and not any(term in w or w in term for w in whitelist)
    )
    return terms


# ---------------------------------------------------------------------------
# truncate_at_sentence — 句界安全截断（替代裸的 [:N] + "..." 硬截半句）
# ---------------------------------------------------------------------------


def truncate_at_sentence(text: str, limit: int) -> str:
    """在句界截断到 ``limit`` 字以内；找不到合适句界才退回硬截+省略号。"""

    text = str(text or "")
    if len(text) <= limit:
        return text
    window = text[:limit]
    last_end = -1
    for m in _SENTENCE_END_RE.finditer(window):
        last_end = m.end()
    if last_end >= max(1, int(limit * 0.5)):
        return window[:last_end]
    return window[: max(0, limit - 3)] + "..."


__all__ = [
    "PathologyFinding",
    "derive_book_jargon_terms",
    "detect_blurb_pathology",
    "truncate_at_sentence",
]
