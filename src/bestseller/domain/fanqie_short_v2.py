# ruff: noqa: RUF001
"""Fanqie short-story v2 quality-loop contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

RETENTION_GATE_DEADLINES: tuple[dict[str, str], ...] = (
    {"position": "30w", "rule": "异常、羞辱、危机或倒计时必须出现"},
    {"position": "80w", "rule": "主角必须在场且承受明确压力"},
    {"position": "150w", "rule": "读者必须知道主角不能退让的损失"},
    {"position": "300w", "rule": "必须出现第一次反击信号或小反馈"},
    {"position": "1000w", "rule": "必须兑现第一次小爆点"},
    {"position": "30%", "rule": "压迫-行动-回报循环必须闭合"},
)


class FanqieShortEmotionCard(BaseModel):
    """One abstract, anti-copy social-emotion card for short-story planning."""

    model_config = ConfigDict(str_strip_whitespace=True)

    emotion_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=80)
    emotion: str = Field(min_length=1, max_length=500)
    reader_pain: str = Field(min_length=1, max_length=1000)
    fictional_container: str = Field(min_length=1, max_length=1000)
    opening_pressure: str = Field(min_length=1, max_length=1000)
    payoff: str = Field(min_length=1, max_length=1000)
    compatible_tones: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    risk_boundary: str = Field(
        default="仅使用抽象社会情绪，不影射真实人物、真实公司、真实案件或特定作品。",
        max_length=1000,
    )
    source: str = Field(default="built_in_social_emotion_bank", max_length=128)

    @field_validator("compatible_tones", "tags", mode="before")
    @classmethod
    def _coerce_string_lists(cls, value: object) -> list[str]:
        return _coerce_string_list(value)

    def to_prompt_line(self) -> str:
        tones = "、".join(self.compatible_tones[:4]) or "通用"
        return (
            f"- [{self.emotion_id}] {self.category}/{tones}: {self.emotion}；"
            f"痛点：{self.reader_pain}；开局压力：{self.opening_pressure}；"
            f"回报：{self.payoff}"
        )


class FanqieShortEmotionStack(BaseModel):
    """Primary and secondary resonance targets for one short story."""

    model_config = ConfigDict(str_strip_whitespace=True)

    primary: FanqieShortEmotionCard
    secondary_cards: list[FanqieShortEmotionCard] = Field(default_factory=list, max_length=4)
    tone_mode: str = Field(default="爽文", max_length=80)
    laugh_point: str = Field(default="", max_length=500)
    tear_point: str = Field(default="", max_length=500)
    payoff_point: str = Field(default="", max_length=500)

    @property
    def cards(self) -> list[FanqieShortEmotionCard]:
        return [self.primary, *self.secondary_cards]

    def to_prompt_block(self) -> str:
        lines = [
            "【短篇社会情绪栈】",
            f"- 主情绪: {self.primary.category} / {self.primary.emotion}",
            f"- 模式: {self.tone_mode}",
            f"- 开局压力: {self.primary.opening_pressure}",
            f"- 爽点兑现: {self.payoff_point or self.primary.payoff}",
        ]
        if self.secondary_cards:
            lines.append(
                "- 副情绪: "
                + "；".join(
                    f"{card.category}/{card.emotion}" for card in self.secondary_cards[:3]
                )
            )
        if self.laugh_point:
            lines.append(f"- 喜剧点: {self.laugh_point}")
        if self.tear_point:
            lines.append(f"- 催泪点: {self.tear_point}")
        lines.append(f"- 安全边界: {self.primary.risk_boundary}")
        return "\n".join(lines)


class FanqieShortResourceCard(BaseModel):
    """A long-form asset converted into a short-story-safe planning card."""

    model_config = ConfigDict(str_strip_whitespace=True)

    resource_id: str = Field(min_length=1, max_length=128)
    card_type: str = Field(min_length=1, max_length=80)
    source: str = Field(default="long_form_adapter", max_length=128)
    name: str = Field(min_length=1, max_length=300)
    short_use: str = Field(min_length=1, max_length=1200)
    conflict_use: str = Field(default="", max_length=1200)
    payoff_use: str = Field(default="", max_length=1200)
    constraints: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("constraints", "risk_notes", "source_refs", mode="before")
    @classmethod
    def _coerce_string_lists(cls, value: object) -> list[str]:
        return _coerce_string_list(value)

    def to_prompt_line(self) -> str:
        parts = [f"- [{self.card_type}] {self.name}: {self.short_use}"]
        if self.conflict_use:
            parts.append(f"冲突用法：{self.conflict_use}")
        if self.payoff_use:
            parts.append(f"回报用法：{self.payoff_use}")
        if self.constraints:
            parts.append("约束：" + "；".join(self.constraints[:4]))
        return "；".join(parts)


class FanqieShortRewriteRoute(BaseModel):
    """Which short-story worker should repair a failed gate."""

    model_config = ConfigDict(str_strip_whitespace=True)

    finding_code: str = Field(min_length=1, max_length=128)
    worker: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=1000)
    target: str = Field(default="whole_story", max_length=128)
    priority: int = Field(default=1, ge=1, le=5)


class FanqieShortV2Blueprint(BaseModel):
    """Planner handoff object for the v2 short-story lane."""

    model_config = ConfigDict(str_strip_whitespace=True, arbitrary_types_allowed=True)

    title: str = Field(default="", max_length=500)
    logline: str = Field(default="", max_length=2000)
    emotion_stack: FanqieShortEmotionStack | None = None
    resource_cards: list[FanqieShortResourceCard] = Field(default_factory=list)
    opening_contract: Mapping[str, Any] = Field(default_factory=dict)
    retention_gates: tuple[dict[str, str], ...] = Field(default=RETENTION_GATE_DEADLINES)

    def to_prompt_block(self) -> str:
        lines = ["【短篇 V2 蓝图】"]
        if self.title:
            lines.append(f"- 标题: {self.title}")
        if self.logline:
            lines.append(f"- 梗概: {self.logline}")
        if self.emotion_stack is not None:
            lines.append(self.emotion_stack.to_prompt_block())
        if self.resource_cards:
            lines.append("【可复用长篇资源】")
            lines.extend(card.to_prompt_line() for card in self.resource_cards[:8])
        if self.opening_contract:
            compact = "；".join(
                f"{key}: {value}" for key, value in self.opening_contract.items() if value
            )
            if compact:
                lines.append(f"- 开篇合同: {compact}")
        return "\n".join(lines)


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.replace("\uff0c", ",").replace("\u3001", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
