"""故事脊柱 (story spine) — 一等公民故事核产物（2026-07-08 框架层）。

用户终审钉死的最后一层缺口："不知道在讲啥、没什么故事性"——构思产出的是
设定拼贴（世界观/规则/金手指），没有一根被强制、被验收、被全程传导的
**故事问题**：谁+要什么+为什么是现在+谁挡着+输了失去什么+读者追什么问题。

本模块 = 脊柱的 schema + 确定性验收 + 下游渲染：
* 构思 finalize 强制产出 story_spine 六字段；
* validate_story_spine 确定性验收（空字段/模糊目标/问题不是疑问句 → 违规），
  违规触发一次聚焦重写（conception 内），仍不过 fail-open 落日志；
* render_story_spine_block 把脊柱渲染成一小块 prompt，注入大纲/章纲/场景写手
  ——全书每一层都看得见同一根脊柱，章节目标必须服务它。
"""

from __future__ import annotations

from typing import Any

SPINE_FIELDS: tuple[str, ...] = (
    "who",       # 主角一句话身份（名字+处境）
    "wants",     # 具体可验收的目标（拿到X/救出X/在X之前做到X）
    "why_now",   # 触发事件：为什么是现在非动不可
    "against",   # 挡路的人/势力/规则（有名字或有形态）
    "stakes",    # 做不到就失去什么（具体：命/人/身份/家）
    "question",  # 读者追的问题（一句疑问句）
)

# 单独出现即判"目标模糊"的空泛词——目标必须有宾语、可验收。
_VAGUE_WANTS: frozenset[str] = frozenset(
    {"活下去", "生存", "变强", "变得更强", "赚钱", "复仇", "逆袭", "崛起", "自由"}
)

_FIELD_LABELS_ZH: dict[str, str] = {
    "who": "主角是谁",
    "wants": "他要什么",
    "why_now": "为什么是现在",
    "against": "谁/什么挡着",
    "stakes": "输了失去什么",
    "question": "读者追问",
}


def validate_story_spine(spine: Any) -> list[str]:
    """确定性验收，返回违规清单（空=合格）。零 LLM 成本。"""

    if not isinstance(spine, dict) or not spine:
        return ["story_spine 缺失——构思必须产出故事脊柱六字段"]
    violations: list[str] = []
    for key in SPINE_FIELDS:
        value = str(spine.get(key) or "").strip()
        label = _FIELD_LABELS_ZH[key]
        if not value:
            violations.append(f"{key}({label}) 为空")
            continue
        if len(value) > 120:
            violations.append(f"{key}({label}) 超过120字——脊柱是一句话不是段落")
    wants = str(spine.get("wants") or "").strip()
    if wants and (wants in _VAGUE_WANTS or len(wants) < 5):
        violations.append(
            f"wants(他要什么)='{wants}' 太模糊——目标必须具体可验收"
            "（拿到X/救出X/在X之前做到X），'活下去/变强'这类词不构成故事目标"
        )
    question = str(spine.get("question") or "").strip()
    if question and not (
        question.endswith("？")
        or question.endswith("?")
    ):
        violations.append("question(读者追问) 必须是一句疑问句（以？结尾）")
    return violations


def render_story_spine_block(spine: Any, *, is_en: bool = False) -> str:
    """渲染注入下游 prompt 的脊柱块。spine 无效时返回空串（优雅降级）。"""

    if not isinstance(spine, dict) or validate_story_spine(spine):
        return ""
    if is_en:
        return (
            "[Story spine — every volume/chapter/scene must serve it]\n"
            f"WHO: {spine['who']} | WANTS: {spine['wants']} | WHY NOW: {spine['why_now']}\n"
            f"AGAINST: {spine['against']} | STAKES: {spine['stakes']}\n"
            f"READER QUESTION: {spine['question']}\n"
        )
    return (
        "【故事脊柱——全书唯一主线，卷/章/场景都必须服务它，不许跑偏】\n"
        f"{spine['who']}，想要{spine['wants']}——因为{spine['why_now']}；"
        f"但{spine['against']}挡着；做不到，{spine['stakes']}。\n"
        f"读者一路追的问题：{spine['question']}\n"
        "每一章都要让主角离这个目标更近一步或更远一步（并让读者看见）；"
        "与脊柱无关的支线，砍。\n"
    )


__all__ = ["SPINE_FIELDS", "validate_story_spine", "render_story_spine_block"]
