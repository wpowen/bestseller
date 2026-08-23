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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
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


#: 作品结构单位 + 位置词 = 在谈论「这本书怎么排的」而不是故事本身。
#: 2026-08-23 真机（验证书 9）：「上一章替他挡刀的人，下一章就得哭着求他还
#: 人情」——读者此刻还没开始读，「上一章」对他没有指涉。三层量具全漏：
#: 正文 AI 味检测器对 128 字简介 0 命中（它为几千字正文校准）、简介病理
#: 0 命中、确定性吸引力门还给了 75.15 分首轮过线。
#:
#: 判据只认**结构单位**（章/卷/节/篇/回）紧跟或紧随位置词，因此
#: 「账本翻到下一页」（实物）、「私章按在契书上」（器物）不受影响。
_CHAPTER_META_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:上|下|前|后|本|首|末|第[一二三四五六七八九十百零\d]+)\s*[一两]?\s*[章卷回](?![节法程程])"),
    re.compile(r"[章卷回]\s*(?:末|首|尾)"),
)


def _detect_chapter_meta(text: str) -> list[PathologyFinding]:
    """简介谈论章节/卷 = 系统在描述自己的组织结构，不是故事在讲自己。"""

    hits: list[str] = []
    for pattern in _CHAPTER_META_RES:
        for m in pattern.finditer(text):
            start = max(0, m.start() - 8)
            hits.append(text[start : m.end() + 8].strip())
    if not hits:
        return []
    return [
        PathologyFinding(
            code="BLURB_CHAPTER_META",
            # 只挣重写：简介可以改，不该因此毙掉整本书。
            severity="warn",
            excerpt="；".join(hits[:3]),
            detail=(
                "简介在谈论章节/卷等作品结构单位——读者此刻还没开始读，"
                "「上一章」对他没有指涉。改成故事内的时间或事件。"
            ),
        )
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
    findings.extend(_detect_chapter_meta(text))
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
    findings.extend(
        _detect_aversive_imagery(
            text,
            fatal_distinct=int(_cfg(config, "aversive_fatal_distinct", 2)),
        )
    )
    findings.extend(_detect_deadline_pileup(text))
    return findings


# ---------------------------------------------------------------------------
# 期限堆叠 — 一段简介只许一条倒计时
# ---------------------------------------------------------------------------
#
# 2026-08-07 真机 custom-xianxia-1786090118：简介里同时出现「灶火只剩一个时辰」
# 「灶火今夜就凉」「房租月底断缴」「限他一个月吞干净」——四个期限互相打架，
# 读者第一眼就断定这书没逻辑。根源是生成 prompt 主动索要「具体的、即将发生的
# 期限」，每句各自合格、合起来矛盾；而全链没有任何一处测时间线自洽。
#
# 校准（同厌恶意象的方法）：42 条真实爆款简介中「期限表达 ≥2」的为 **0 条**，
# 坏简介 4 条。阈值取 ≥3 fatal / ==2 warn——两个期限在嵌套压力叙事里理论上
# 合法（爆款语料没出现过，但不替未来的好文案封死），三个以上没有任何正当理由。
#
# 正则只抓**带压力标记**的期限（只剩/限/之内/之前/就要/今夜…），
# 「三天后他回来了」这种叙事时间跳跃不算期限。

_DEADLINE_EXPR_RE = re.compile(
    r"(?:一个时辰|[一两三半]个?时辰|今夜|今晚|当晚|天亮前|三天|七天|[三五七]日|"
    r"半月|半个月|月底|[一三]个月|百日|一年)"
)
_DEADLINE_PRESSURE_MARKS = (
    # 单字「前/内」只在紧邻期限表达的 ±4 字窗口里查（见调用处），不会全文乱匹配。
    # 自闭环 r4 实测：「月底前必须交」「一个月内挤走」因只收「之前/之内」而漏网，
    # 三期限候选未触发 fatal。
    "只剩", "还剩", "限", "之内", "之前", "内", "前", "就要", "天亮前", "今夜", "今晚", "断",
)


def _detect_deadline_pileup(text: str) -> list[PathologyFinding]:
    found: set[str] = set()
    for m in _DEADLINE_EXPR_RE.finditer(text):
        ctx = text[max(0, m.start() - 4) : m.end() + 4]
        if any(k in ctx for k in _DEADLINE_PRESSURE_MARKS):
            found.add(m.group(0))
    if len(found) < 2:
        return []
    severity = "fatal" if len(found) >= 3 else "warn"
    listed = "、".join(sorted(found))
    return [
        PathologyFinding(
            code="DEADLINE_PILEUP",
            severity=severity,
            excerpt=listed,
            detail=(
                f"简介同时压着 {len(found)} 条倒计时（{listed}）。实测 42 条真实"
                "爆款简介没有一条超过 1 个期限——多条期限互相打架是读者判定"
                "「没逻辑」的第一眼信号。保留最狠的那一条，其余删掉或并入它。"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# 厌恶意象密度 — 简介不是让读者反胃的地方
# ---------------------------------------------------------------------------
#
# 2026-08-07 真机 custom-xuanhuan-1786023406：用户读完简介的第一反应是
# 「感官上让人感觉有点恶心」，而当时**每一道关都合法通过**（标题 83.2、简介
# 72.5、画像判官 3/3 会点 8.67、arena 0.50、质量判官 0.78）。
#
# 直接问 LLM「你恶心吗」不管用：画像判官给这本书的 aversion 只有 2.0/10，
# 原话「虫蛊味儿冲但不恶心」——模型不能可靠地自述生理反应。改成数触发词就
# 立刻分开了。对 config/appeal_reference_blurbs.yaml 的 42 条真实爆款简介实测：
#
#   40/42 条命中数为 0；仅有的两条各只有 1 个词
#     （法医秦明「腐」、穿书自救指南「吐」——法医/恐怖/盗墓题材也不例外）
#   本书 7 个不同触发词 / 8 处（臭水沟·虫卵·蚁后·啃开·胀成·嫌他脏·皮包骨）
#
# 因此阈值取**不同词数 ≥2**：整个爆款语料无一命中，本书超线 3.5 倍。用不同词
# 数而不是密度，是因为简介只有一两百字，单个词就能把「每千字」推到 12 以上（法医
# 秦明就是这么来的）——短文本上密度是噪声，词种数才是信号。
#
# 词表只收**引发生理排斥的具体物象与动作**，不收负面情绪词（恐惧/绝望/痛苦都是
# 正常卖点）。这条界线是它不会误伤黑暗题材的原因。

# 强触发 = 固有恶心的物象/状态，单词即有生理排斥力。
# 弱触发（身体侵入动词）= 语境依赖：蚁后啃开手腕恶心，案板剖鱼是做菜，青光钻进
# 掌心是标准金手指觉醒——自闭环 r4 实测「剖开鲤鱼+光钻进掌心」被误判 fatal。
# 判据：fatal 必须至少含 1 个强触发；纯弱触发不报。
_AVERSIVE_IMAGERY: dict[str, tuple[str, ...]] = {
    "秽物": ("臭水沟", "泔水", "粪", "尿", "呕", "呕吐", "痰", "脓", "腐烂", "馊", "霉", "蛆", "腥臭", "恶臭"),
    "虫豸": ("虫卵", "蟑螂", "蜈蚣", "毒虫", "蚁后", "幼虫", "虫尸", "虫豸"),
    "体液": ("脓血", "涎水", "唾液", "黏液", "血水"),
    "膨胀腐坏": ("胀成", "肿胀", "溃烂", "生疮", "发臭"),
    "污秽状态": ("嫌他脏", "肮脏", "污秽", "邋遢", "皮包骨"),
}
_AVERSIVE_CONTEXTUAL: tuple[str, ...] = (
    "啃开", "钻进", "咬穿", "剖开", "掏出", "挖出", "灌进", "爬进", "寄生",
)


def _detect_aversive_imagery(text: str, *, fatal_distinct: int) -> list[PathologyFinding]:
    """简介里的生理厌恶触发词过多 → fatal（触发 gate cap）。"""

    strong: list[str] = []
    for words in _AVERSIVE_IMAGERY.values():
        strong.extend(w for w in words if w in text)
    weak = [w for w in _AVERSIVE_CONTEXTUAL if w in text]
    if not strong:
        return []
    hits = strong + weak
    distinct = sorted(set(hits))
    if len(distinct) < max(1, fatal_distinct):
        return []
    return [
        PathologyFinding(
            code="AVERSIVE_IMAGERY",
            severity="fatal",
            excerpt="、".join(distinct[:8]),
            detail=(
                f"简介出现 {len(distinct)} 个生理厌恶触发词"
                f"（{'、'.join(distinct[:8])}）。实测 42 条真实爆款简介中 40 条为 0、"
                "另 2 条各只有 1 个（含法医/恐怖题材）。换成不引起反胃的具体意象，"
                "或把这些细节留到正文里。"
            ),
        )
    ]


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


# ---------------------------------------------------------------------------
# 事实接地 — 简介不得发明已批准构思里没有的事实
# ---------------------------------------------------------------------------
#
# 2026-08-09 真机 custom-xianxia-1786282198《废脉炉子天天骂我》。生成 prompt 第⑧条
# 白纸黑字写着「只许使用【故事脊柱】【故事核】里已有的人物、物品、数字，不得发明
# 新实体」。模型照样发明了三个，而**全链没有一处在检查它有没有遵守**：
#
#   「而他娘，就是被这宗门以枯竭为由丢进那片星云」  ← premise 里没有任何母亲
#   「天亮前，器炼堂主事就要撞见这只邪器」          ← premise 里没有任何期限
#
# 那段简介跑完全部现有尺子：病理检测器零发现、AI 味 0.0 分。它们测的是别的维度
# （期限**堆叠**要≥2条、厌恶意象、模板残留、正文腔），没有一把在问「这句话的依据
# 在哪」。读者看到的「逻辑不通」正是这个：简介许了正文不会兑现的诺。
#
# 判据只覆盖**封闭且可核对**的三类断言，不做开放式语义比对——简介本来就该压缩改写，
# 逐词比对必然误报。三类都用「这一类在简介里出现、而在整份已批准构思里完全不出现」
# 作阈值，是可解释的硬事实差集：
#   ① 亲缘实体（娘/父亲/妹妹…）——具体的人，凭空多一个人是硬伤
#   ② 期限压力（天亮前/七日内…）——凭空多一条倒计时，正文必然兑现不了
#   ③ 死亡断言（死了/丧生…）——凭空写死一个人，与正文正典直接冲突
#
# 词表是**结构类别**（亲属称谓、期限表达、死亡谓词），不是母题内容，且只作检测器
# 输入、永不进生成 prompt——与 2026-08-03《雾街债主》种词事故的那类词表不同类。

#: 亲属称谓。只收能独立指人的，且下面用负向上下文排除粘连词
#: （姑娘/娘子/老板娘/新娘 不是「娘」，他娘的 是脏话不是母亲）。
_KIN_TERMS: tuple[str, ...] = (
    "娘", "母亲", "妈", "父亲", "爹", "爸",
    "妹妹", "姐姐", "哥哥", "弟弟", "儿子", "女儿",
    "妻子", "丈夫", "未婚妻", "未婚夫", "外婆", "奶奶", "爷爷", "外公",
)
#: 让 ``娘``/``妈``/``爹`` 这类单字称谓失效的粘连上下文。
_KIN_FALSE_FRIENDS: dict[str, tuple[str, ...]] = {
    "娘": ("姑娘", "娘子", "老板娘", "新娘", "娘家", "他娘的", "她娘的", "娘娘"),
    "妈": ("妈的", "妈呀"),
    "爹": ("爹的",),
}
#: 死亡断言。写死一个人是正文必须兑现的硬事实。
_DEATH_CLAIMS: tuple[str, ...] = (
    "死了", "died", "丧生", "身亡", "遇害", "惨死", "暴毙", "咽了气", "没回来", "再没回来",
)


def _mentions(term: str, text: str) -> bool:
    """``term`` 是否作为独立断言出现（排除已知粘连词）。"""

    if term not in text:
        return False
    friends = _KIN_FALSE_FRIENDS.get(term, ())
    if not friends:
        return True
    scrubbed = text
    for friend in friends:
        scrubbed = scrubbed.replace(friend, "　" * len(friend))
    return term in scrubbed


def _pressure_deadlines(text: str) -> set[str]:
    """带压力标记的期限表达（「三天后他回来了」这类叙事跳跃不算）。"""

    found: set[str] = set()
    for m in _DEADLINE_EXPR_RE.finditer(text):
        ctx = text[max(0, m.start() - 4) : m.end() + 4]
        if any(k in ctx for k in _DEADLINE_PRESSURE_MARKS):
            found.add(m.group(0))
    return found


def _deadline_expressions(text: str) -> set[str]:
    """全部期限表达，**不看**有没有压力标记。

    接地判据必须用这一个，不能用 ``_pressure_deadlines``：构思常把同一条时间约束
    写成中性的循环窗口（「每三天一次的井口窗口」），简介合法地把它表述成压力
    （「三天之内必须拿到」）。要求 canon 侧也带压力标记，就会把这种正当改写判成
    凭空捏造——写这条检测器时的第一版就是这么误报的，被自己的负对照测试抓住。
    """

    return {m.group(0) for m in _DEADLINE_EXPR_RE.finditer(text)}


def detect_ungrounded_blurb_claims(
    blurb: str, *, canon_text: str
) -> list[PathologyFinding]:
    """简介里出现、而已批准构思里完全没有的硬事实。

    ``canon_text`` 必须是**整份**已批准材料（premise + story_spine + hook_card）拼接
    ——只拿 premise 比会误报：真机负对照《灵根废我用烂账翻盘》的简介写「赶在天亮前
    把泥封糊回去」，这条期限不在 premise 里，但在 hook_card.decision_proof 里，是
    合法接地的。

    Fail-open：``canon_text`` 太短（无从判断）时一律放行，宁可漏过不可误伤。
    """

    blurb = str(blurb or "")
    canon = str(canon_text or "")
    if not blurb.strip() or len(canon) < 80:
        return []

    findings: list[PathologyFinding] = []

    invented_kin = [
        term
        for term in _KIN_TERMS
        if _mentions(term, blurb) and not _mentions(term, canon)
    ]
    # 「母亲」命中时「妈」往往同现，只报最长的那个，避免一件事报三遍。
    invented_kin = [
        term
        for term in invented_kin
        if not any(other != term and term in other for other in invented_kin)
    ]
    if invented_kin:
        findings.append(
            PathologyFinding(
                code="BLURB_UNGROUNDED_KIN",
                severity="fatal",
                excerpt="、".join(invented_kin),
                detail=(
                    f"简介凭空多出亲人（{'、'.join(invented_kin)}），已批准构思里没有"
                    "这个人。简介许下的人物关系，正文必须兑现——这是读者判定「逻辑"
                    "不通」的第一眼信号。只写构思里已有的人。"
                ),
            )
        )

    canon_deadlines = _deadline_expressions(canon)
    ungrounded_deadlines = sorted(
        expr for expr in _pressure_deadlines(blurb) if expr not in canon_deadlines
    )
    if ungrounded_deadlines:
        listed = "、".join(ungrounded_deadlines)
        findings.append(
            PathologyFinding(
                code="BLURB_UNGROUNDED_DEADLINE",
                severity="fatal",
                excerpt=listed,
                detail=(
                    f"简介给出了构思里不存在的倒计时（{listed}）。凭空造的期限正文"
                    "兑现不了，读者第一章就会发现被骗。改用构思里真实存在的时间压力，"
                    "或者不写期限。"
                ),
            )
        )

    invented_death = [
        claim
        for claim in _DEATH_CLAIMS
        if claim in blurb and not any(c in canon for c in _DEATH_CLAIMS)
    ]
    # 「再没回来」蕴含「没回来」——只报最长的那条，一件事不报两遍。
    invented_death = [
        claim
        for claim in invented_death
        if not any(other != claim and claim in other for other in invented_death)
    ]
    if invented_death:
        findings.append(
            PathologyFinding(
                code="BLURB_UNGROUNDED_DEATH",
                severity="fatal",
                excerpt="、".join(sorted(set(invented_death))[:4]),
                detail=(
                    "简介写死了人，而已批准构思里没有任何死亡事实。"
                    "写死一个人是正文必须承接的硬设定，不能在文案里临时加。"
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# 正典人名一致性 — 冠军简介不得换掉主角
# ---------------------------------------------------------------------------
#
# 2026-08-06 真机：文案工序的冠军简介直接覆盖 synopsis，覆盖前只过「禁用母题词
# 消毒」和「句界截断」两道。冠军把主角从正典的「纪蛰」换成了凭空的「沈落」，
# 于是**对外见光的那份简介，主角名是错的**，而书里其他所有字段都还叫纪蛰。
# 根因是文案工序为防黑话泄漏刻意收窄了输入，模型据此自由发挥人名，而下游没有
# 任何一道在校验冠军与正典是否同一个人。
#
# 判据刻意要求「两个条件同时成立」，把误报压到最低：
#   (1) 正典主角名在简介里**没出现**，且
#   (2) 简介里出现了正典文本里也没有的人名
# 只满足 (1) 是合法的无名第三人称/第一人称写法；只满足 (2) 是正常引入配角。
# 两者同时成立才是「换人」。人名抽取复用章节层同一个检测器，不另起一套。


def champion_swaps_protagonist(
    champion: str,
    *,
    canon_text: str,
    protagonist_name: str,
) -> tuple[str, ...] | None:
    """冠军简介是否把主角换成了正典外的人。

    返回 ``None`` 表示放行；否则返回简介里出现、而正典里没有的人名（供日志与
    审计），调用方应拒绝该冠军并回退到 v0。``protagonist_name`` 为空时无从判断，
    一律放行——宁可漏过，不可在正典本身缺失时误伤。
    """

    champion = str(champion or "")
    protagonist_name = str(protagonist_name or "").strip()
    if not champion or not protagonist_name:
        return None
    if protagonist_name in champion:
        return None

    from bestseller.services.output_validator import (
        NamingConsistencyCheck,
    )

    allowed = frozenset({protagonist_name})
    # 正典文本里已经出现的人名一律合法——配角本来就该能进简介。
    canon_names = set(NamingConsistencyCheck._rogue_names_zh(str(canon_text or ""), allowed))
    rogue = NamingConsistencyCheck._rogue_names_zh(
        champion, frozenset(allowed | canon_names)
    )
    rogue = _filter_extraction_noise(rogue, champion)
    if not rogue:
        return None
    return tuple(sorted(rogue))


#: 这些词里的首字是常见姓氏，但在词中根本不是姓。人名抽取器是「top-100 姓氏正则
#: 扫正文」，它的误报率天生就高（output_validator 自己的注释写着这一点，所以它在
#: 章节层只发 warn）。本函数却是**破坏性**的——命中就丢掉整份文案冠军——所以必须
#: 自带护栏。
#: 真机 2026-08-10《搓背》：简介里的「师傅和这位道士」被切出人名「傅和这」，
#: 冠军简介被整份丢弃、回退到只有一句话的 v0，读者画像 0/3 会点，书当场死。
_NAME_EXTRACTION_FALSE_FRIENDS: tuple[str, ...] = (
    "师傅", "太傅", "少傅", "师父", "老师", "大师", "师兄", "师姐", "师弟", "师妹",
    "掌柜", "东家", "当家", "公子", "姑娘", "先生", "夫人", "娘子",
)


def _filter_extraction_noise(rogue: dict[str, int], text: str) -> dict[str, int]:
    """滤掉人名抽取的噪声：粘连词切片 + 只出现一次的孤例。

    只过滤粘连词切片——首字取自「师傅/掌柜」这类复合词而非姓氏。

    ⚠️ 不要在这里加频次下限。第一版加了「至少出现 2 次」，理由是"真被换掉的主角
    是主语、会通篇复现"——听着合理，但本仓库那条真实回归用例（2026-08-06
    纪蛰→沈落）里，冒名主角**只出现过 1 次**。频次门会让这个函数存在的理由本身
    失效。是既有测试当场拦下了这次改坏。
    """

    scrubbed = str(text or "")
    for friend in _NAME_EXTRACTION_FALSE_FRIENDS:
        scrubbed = scrubbed.replace(friend, "　" * len(friend))
    return {
        name: count for name, count in (rogue or {}).items() if name in scrubbed
    }


__all__ = [
    "PathologyFinding",
    "champion_swaps_protagonist",
    "derive_book_jargon_terms",
    "detect_blurb_pathology",
    "truncate_at_sentence",
]
