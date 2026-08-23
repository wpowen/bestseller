"""Prompt compaction helpers for scene-writer user prompts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompactionReport:
    original_chars: int
    compacted_chars: int
    saved_tokens_estimate: int
    # 被上限淘汰掉的段标题。淘汰此前完全静默——真机《罚我守坟》18/18 章
    # 每章都在丢规划段而没有任何一行日志。留痕不夺权，只让损失可查。
    evicted_sections: tuple[str, ...] = ()


# Craft-theory / metadata / reference-dump sections that belong to the planner
# and critic stages, not the writer's generation prompt. The writer still needs
# the compact execution core (distilled strategy, emotion kernel, public emotion,
# and event-unit contract), so those sections are intentionally preserved.
# Matched against the leading text of each top-level section; dropped in lean
# mode.
_LEAN_STRIP_MARKERS: tuple[str, ...] = (
    "【方法论 lineage",
    "## 文化原型",
    "文化原型 [cultural_archetypes",
    "### scene_templates",
    "### locale_templates",
    "### character_templates",
    "### power_systems",
    "### device_templates",
    "### world_settings",
    # Material-Forge reference catalogs (§slug dumps). The writer already gets a
    # short "素材锚点 §slug" pointer list; the full catalogs are reference, not
    # prose instructions.
    "### factions",
    "### character_archetypes",
    "### plot_patterns",
    "### thematic_motifs",
    "### emotion_arcs",
    "### dialogue_styles",
    "### anti_cliche_patterns",
    "### real_world_references",
    "## 风格参照 [reference_corpora",
    "# Reference corpus",
    # These are planning / scoring layers. They are intentionally not writer
    # instructions: in production they were injected repeatedly and carried
    # stale names, genre-specific examples, and contradictory "rewrite"
    # directives into the scene prompt.
    "【emotion_choreography",
    "【rhythm_engineering",
    "【information_choreography",
    "【章节位置档案",
    "【平台档案",
    "【chapter_signature_audit",
    "【场景锚定",
    "【emotion_driven_core",
    "【public_emotion_core",
    "【public_emotion_methodology",
    "【品类重写方向",
    "【五层思考契约",
    "【本章张力目标",
    "【地点复访约束",
    "【多样性预算",
)

# A new top-level section begins at a line starting with 【, a markdown header
# (#, ##, ###), or a === fence.
_SECTION_BOUNDARY = re.compile(r"(?=\n【)|(?=\n#{1,3} )|(?=\n=== )")

# Only dedupe / strip substantial sections so short repeated tags (【语言】…) are
# never touched.
_MIN_SECTION_CHARS = 200

# The writer context budget is deliberately much smaller than the planner's
# context. A prompt that survives compaction at 25–30k chars is not compacted
# in any useful sense and lets stale framework text compete with the scene
# contract. Keep a bounded execution prompt while retaining the leading scene
# contract and the trailing output/repair requirements.
_DEFAULT_MAX_CHARS = 10_000


def _split_sections(text: str) -> list[str]:
    return _SECTION_BOUNDARY.split(text)


def _strip_meta_sections(text: str) -> str:
    kept: list[str] = []
    for part in _split_sections(text):
        head = part.lstrip()[:60]
        if any(marker in head for marker in _LEAN_STRIP_MARKERS):
            continue
        kept.append(part)
    return "".join(kept)


def _dedupe_repeated_sections(text: str) -> str:
    """Drop verbatim-duplicate sections (e.g. 题材方法论 / choreography contracts
    that get injected twice). Exact-match only — zero risk of over-stripping."""

    seen: set[str] = set()
    kept: list[str] = []
    for part in _split_sections(text):
        key = part.strip()
        if len(key) >= _MIN_SECTION_CHARS:
            if key in seen:
                continue
            seen.add(key)
        kept.append(part)
    return "".join(kept)


def compact_user_prompt(
    raw_user_prompt: str,
    *,
    chapter_no: int,
    forbidden_terms_full: list[str],
    lean: bool = True,
) -> tuple[str, CompactionReport]:
    """Return a compacted user prompt plus a small savings report.

    When ``lean`` is set, abstract craft-theory / metadata / reference-dump
    sections are stripped and verbatim-duplicate sections collapsed so the
    scene-writer prompt stays focused on *what to write* rather than *writing
    theory*.
    """

    original = raw_user_prompt or ""
    compacted = original
    if lean:
        compacted = _dedupe_repeated_sections(compacted)
        compacted = _strip_meta_sections(compacted)
    compacted = _dedupe_chapter_contract_digest_blocks(compacted)
    compacted = _slice_forbidden_terms(compacted, chapter_no, forbidden_terms_full)
    compacted = _wrap_retention_findings(compacted)
    compacted = _prune_placeholder_lines(compacted)
    compacted = _collapse_blank_lines(compacted)
    compacted, evicted = _cap_prompt(compacted, max_chars=_DEFAULT_MAX_CHARS)
    if evicted:
        logger.info(
            "writer prompt over budget: %d sections evicted (chapter %s): %s",
            len(evicted),
            chapter_no,
            "/".join(evicted[:12]),
        )
    report = CompactionReport(
        original_chars=len(original),
        compacted_chars=len(compacted),
        saved_tokens_estimate=max(0, _estimate_tokens(original) - _estimate_tokens(compacted)),
        evicted_sections=evicted,
    )
    return compacted, report


# High-value sections that must survive capping regardless of position. A blind
# positional head/tail slice used to cut these out of the deep middle of a bloated
# prompt — most damagingly the POV inner-voice authorization, which an A/B run had
# proven closes the readability gap. Keep them whole even when over budget.
_PROTECTED_SECTION_MARKERS: tuple[str, ...] = (
    "字数与结构",
    "word count and structure",
    "整章逻辑合同",
    "whole-chapter logic contract",
    "隐藏节点执行规则",
    "hidden beat execution",
    "AI套话黑名单",
    "banned ai clich",
    # 反AI腔铁律 / 视角体验 / 位置感知块：这些是正文质量的承重墙，且都由
    # anti_ai_voice_discipline、pov_experience_discipline、prose_prompt_fusion
    # 三个单一真源渲染。它们此前不在保护名单里，靠 "内心" 偶然命中——把承重规则
    # 的存活寄托在别的 marker 的子串巧合上，是压缩器最容易复发的静默失效。
    "反AI腔",
    "语体与用词",
    "视角与体验",
    "开篇炸点律",
    "中段持续追读律",
    "故事脊柱",
    "分层故事脊柱",
    "黄金三章",
    "POV 人物弧",
    "POV人物弧",
    "内在结构",
    "内心",
    "内在嗓音",
    "写前验收契约",
    "故事问题落地",
    "章末收尾钩子",
    "收尾钩子",
    "冷读者定位",
    "决策协议",
    "主角决策",
    # 2026-08-24 真机复发：89 次触发压缩的写手调用里，**【章末规则】被淘汰 68 次**
    # （76%），而判它有罪的 ENDING_HOOK_MISSING 是 block 级、且是全书重写的头号
    # 阻断码 —— 框架先把要求从写手 prompt 里删掉，再因为它没做到而毙掉它。
    # 上面「章末收尾钩子」「收尾钩子」两个 marker 都对不上「章末规则」这个段名，
    # 正是本文件注释早就警告过的形状：**把承重规则的存活寄托在子串巧合上**。
    "章末规则",
    # 段名里自称硬约束/不可更改的，一律不参与位置淘汰。这四个当前碰巧没被淘汰过
    # （只有「卷级首章埋钩硬约束」出现过 7 次），列在这里是防复发的保险，
    # 不改变现状预算。新增自称硬约束的段会被 tests 里的结构守卫强制要求进这张表。
    "卷级首章埋钩硬约束",
    "前十章禁写与物件信号硬约束",
    # ↓ 写这张表时我自己漏掉的一条，被 tests 里的结构守卫当场抓出来。
    "前十章留存硬规则",
    "硬约束与门禁",
    "核心角色设定",
)

# 连贯性核心：按价值排序的 PLAN 层。消融证据（prose-prompt-diet）里
# PLAN 层的贡献高于 CONST，更高于 CRAFT，可上限淘汰此前只按"谁靠前、谁小"
# 决定去留——最大的那块（近期章节摘要 / 活动主线伏笔账本）永远第一个被跳过，
# 靠后的小块反而活下来。写手因此在后半段拿不到前情与伏笔账本。
# 这里不抬预算（10k 对应 8000 token 的写手上下文预算，是刻意的），
# 只把淘汰顺序从"位置与大小的意外"改成"按价值"。
# 排序即价值排序：先保证"这一章要写什么"（承接/爽点/契约/场景卡），
# 再保证"接得住前文"（前情/伏笔账本/硬事实）。
_CONTINUITY_SECTION_MARKERS: tuple[str, ...] = (
    "上一章硬承接",
    "本章爽点约束",
    "章节契约",
    "弱场景逻辑地图",
    "近期章节/场景摘要",
    "活动主线/伏笔/回收",
    "时间线与硬事实快照",
    "读者期望画面",
    # 故事圣经排在最后一档：它是静态世界信息，pack 与 system prompt 里另有
    # 一份；前情摘要与伏笔账本则**只有这一份**，丢了就没有第二个来源。
    # 排在这里保证它仍然赢过检索补充/方法论证据这类纯参考段。
    "故事圣经上下文",
)


_CAP_MARKER = "【写作提示已压缩】规划层冗余内容已移除；只服从本场正典、场景目标和输出契约。"


def _section_is_protected(section: str) -> bool:
    head = section.lstrip()[:80]
    return any(marker in head for marker in _PROTECTED_SECTION_MARKERS)


def _continuity_rank(section: str) -> int | None:
    """连贯性核心的价值序号；不属于核心返回 None。"""

    head = section.lstrip()[:80]
    for rank, marker in enumerate(_CONTINUITY_SECTION_MARKERS):
        if marker in head:
            return rank
    return None


def _section_label(section: str) -> str:
    head = section.lstrip()
    match = re.match(r"(【[^】]{1,24}】|#{1,3} [^\n]{1,40}|=== [^\n]{1,40})", head)
    return match.group(1).strip() if match else head[:24].strip()


def _cap_prompt(text: str, *, max_chars: int) -> tuple[str, tuple[str, ...]]:
    """Cap the writer prompt without deleting high-value middle sections.

    This is a safety boundary, not a quality rewrite. Rather than slicing an
    arbitrary head/tail (which silently dropped POV inner-voice, story spine and
    golden-three blocks that live in the middle), keep whole sections: the first
    section (scene contract), the last section (output/acceptance contract), every
    protected high-value section, then fill the remaining budget with the earliest
    normal sections. Protected sections may push the result modestly over budget —
    they are bounded and matter more than the char ceiling.
    """

    if max_chars <= 0 or len(text) <= max_chars:
        return text, ()

    sections = _split_sections(text)
    if len(sections) <= 2:
        # Unsectioned prompt: fall back to a head/tail slice that at least keeps
        # the scene contract and the output contract.
        available = max(0, max_chars - len(_CAP_MARKER) - 4)
        head_chars = available * 7 // 10
        tail_chars = available - head_chars
        head = text[:head_chars].rstrip()
        tail = text[-tail_chars:].lstrip()
        return f"{head}\n\n{_CAP_MARKER}\n\n{tail}", ("<未分段正文中段>",)

    first, last = sections[0], sections[-1]
    middle = sections[1:-1]
    keep_ids = {id(first), id(last)}
    used = len(first) + len(last) + len(_CAP_MARKER)
    # Protected sections are mandatory.
    for section in middle:
        if _section_is_protected(section):
            keep_ids.add(id(section))
            used += len(section)
    # 连贯性核心按价值序号优先入选（同序号保持出现顺序）。仍受预算约束——
    # 它们只是排在通用段前面，不像 protected 那样可以撑破上限。
    ranked = [
        (rank, index, section)
        for index, section in enumerate(middle)
        if id(section) not in keep_ids
        and (rank := _continuity_rank(section)) is not None
    ]
    for _rank, _index, section in sorted(ranked, key=lambda row: (row[0], row[1])):
        if used + len(section) > max_chars:
            continue
        keep_ids.add(id(section))
        used += len(section)
    # Fill remaining budget with the earliest normal sections.
    for section in middle:
        if id(section) in keep_ids:
            continue
        if used + len(section) > max_chars:
            continue
        keep_ids.add(id(section))
        used += len(section)

    out: list[str] = []
    elided = False
    evicted: list[str] = []
    for section in sections:
        if id(section) in keep_ids:
            out.append(section)
            continue
        evicted.append(_section_label(section))
        if not elided:
            out.append(f"\n\n{_CAP_MARKER}\n\n")
            elided = True
    return "".join(out), tuple(evicted)


def _dedupe_chapter_contract_digest_blocks(text: str) -> str:
    seen = False

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        block = match.group(0)
        if not seen:
            seen = True
            return _compact_json_payload(block)
        return '"chapter_contract_digest": "see: first chapter_contract_digest above"'

    return re.sub(
        r'"chapter_contract_digest"\s*:\s*(?:\{(?:[^{}]|\{[^{}]*\})*\}|\[[^\]]*\]|"[^"]*")',
        replace,
        text,
        flags=re.S,
    )


def _compact_json_payload(block: str) -> str:
    key, _, raw_value = block.partition(":")
    value = raw_value.strip().rstrip(",")
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        return block
    compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    suffix = "," if block.rstrip().endswith(",") else ""
    return f"{key}:{compact}{suffix}"


def _slice_forbidden_terms(text: str, chapter_no: int, forbidden_terms_full: list[str]) -> str:
    if not forbidden_terms_full:
        return text
    allowed = _terms_for_chapter(chapter_no, forbidden_terms_full)
    if len(allowed) >= len(forbidden_terms_full):
        return text
    replacement = "forbidden_early_leaks_active=" + json.dumps(
        allowed,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    patterns = (
        r"forbidden_early_leaks_archived[^:\n]*:\s*\[[^\]]*\]",
        r'"forbidden_early_leaks(?:_archived)?"\s*:\s*\[[^\]]*\]',
    )
    compacted = text
    for pattern in patterns:
        compacted = re.sub(pattern, replacement, compacted, flags=re.S)
    return compacted


def _terms_for_chapter(chapter_no: int, terms: list[str]) -> list[str]:
    """Which forbidden-leak terms stay active for this chapter.

    ``terms`` is already the project-derived front-10 forbidden-signal list, so it is
    book-specific by construction. We MUST NOT intersect it with hardcoded proper nouns
    from any one book (that emptied the list for every other book and leaked the 青囊
    cast into all projects). Keeping the full list for early chapters is the safe,
    genre-neutral behaviour: no late-reveal term leaks early.
    """

    return list(terms)


def _wrap_retention_findings(text: str) -> str:
    marker = "retention_gate_last_findings"
    if marker not in text or "<REPAIR_HINT>" in text:
        return text
    pattern = re.compile(
        r"(?P<block>(?:^|\n).{0,80}retention_gate_last_findings.{0,4000}?)(?=\n\S|$)",
        flags=re.S,
    )

    def replace(match: re.Match[str]) -> str:
        block = match.group("block").strip()
        return f"\n<REPAIR_HINT>\n{block}\n</REPAIR_HINT>\n"

    return pattern.sub(replace, text, count=1)


# Placeholder values various render_* producers emit when a field is unset. These
# lines carry zero signal and dilute the writer's instructions, so they are pruned
# from every scene-writer prompt.
_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "暂无",
        "未指定",
        "自动/未指定",
        "暂无明确卖点",
        "暂无明确开篇合同",
        "待定",
        "未设置",
        "无",
        "none",
        "unspecified",
        "auto/unspecified",
        "none specified",
        "n/a",
        "tbd",
    }
)

# A human-readable "label: value" line: optional bullet, a short label (no quotes /
# braces so JSON object lines are never matched), a colon, then the value.
_LABEL_VALUE_LINE = re.compile(
    r"^[\s\-*•]*(?:\*\*|【)?[^:：{}\"\n]{1,40}(?:\*\*|】)?\s*[:：]\s*(?P<value>.+?)\s*$"
)


def _prune_placeholder_lines(text: str) -> str:
    """Drop ``label: <placeholder>`` lines (暂无 / 未指定 / none …). Conservative:
    only single-line ``label: value`` bullets are considered, and any line carrying
    a quote or brace is skipped so JSON payloads are never corrupted."""

    kept: list[str] = []
    for line in text.split("\n"):
        if '"' in line or "{" in line or "}" in line:
            kept.append(line)
            continue
        match = _LABEL_VALUE_LINE.match(line)
        if match:
            value = match.group("value").strip().strip("。.！!；;，,、 ")
            value = value.strip("【】[]()（）").strip().lower()
            if value in _PLACEHOLDER_VALUES:
                continue
        kept.append(line)
    return "\n".join(kept)


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9_]+", text))
    punct = len(re.findall(r"[^\w\s]", text))
    return han + int(latin * 1.3) + int(punct * 0.5)


__all__ = ["CompactionReport", "compact_user_prompt"]
