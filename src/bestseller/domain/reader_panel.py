"""Reader-panel role schema for universal quality attribution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReferenceCorpusMode = Literal["none", "distillation"]

READER_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["issue", "location", "severity", "role", "evidence"],
        "properties": {
            "issue": {"type": "string"},
            "location": {"type": "string"},
            "severity": {"enum": ["blocker", "high", "medium", "low"]},
            "role": {"type": "string"},
            "evidence": {"type": "string"},
            "suggested_attribution_hint": {"type": ["string", "null"]},
        },
    },
}


@dataclass(frozen=True, slots=True)
class ReaderRole:
    name: str
    persona: str
    instruction: str
    reference_corpus: ReferenceCorpusMode = "none"
    output_schema: dict[str, Any] = field(default_factory=lambda: dict(READER_FEEDBACK_SCHEMA))


DEFAULT_PANEL: tuple[ReaderRole, ...] = (
    ReaderRole(
        name="普通读者",
        persona="网文 APP 重度用户, 耐心阈值 < 3 章",
        instruction=(
            "读完给你的章节, 列出所有让你想弃书的具体原因. "
            "每条标出具体段落或行号. 不要给评分, 给细节."
        ),
        reference_corpus="none",
    ),
    ReaderRole(
        name="严苛编辑",
        persona="头部网文平台签约编辑, 每天读 50 个新作",
        instruction=(
            "指出所有不专业的写法: 人物前后矛盾, 设定突然变更, "
            "场景跳跃, 对白同质化, 节奏失衡, 复读段落. "
            "每条挂 1 个具体位置."
        ),
        reference_corpus="none",
    ),
    ReaderRole(
        name="同类作家",
        persona="写过同类畅销作品的作家(同类型由 LLM 自行识别)",
        instruction=(
            "和给定的同类样本对比, 这本书的差距在哪? "
            "用具体技法对比, 不要泛泛而谈."
        ),
        reference_corpus="distillation",
    ),
)


__all__ = ["DEFAULT_PANEL", "READER_FEEDBACK_SCHEMA", "ReaderRole", "ReferenceCorpusMode"]
