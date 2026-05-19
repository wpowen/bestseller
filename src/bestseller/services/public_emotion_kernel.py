"""Project-local public emotion contracts.

The public-emotion kernel captures which reader segment a single book speaks
for and how that shared emotion is translated into this book's genre material.
It is intentionally project-scoped: the framework provides the schema and
checks, not reusable story settings or title formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


PUBLIC_EMOTION_KERNEL_VERSION = 1


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "public_emotion_methodology.yaml"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "；".join(item for item in (_text(item) for item in value) if item)
    if isinstance(value, dict):
        parts = [item for item in (_text(v) for v in value.values()) if item]
        return "；".join(parts)
    return str(value).strip()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [text for text in (_text(item) for item in value) if text]
    text = _text(value)
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


class PublicEmotionSegment(BaseModel, frozen=True):
    """A project-specific reader group and its shared emotional pressure."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    group_label: str = Field(min_length=1)
    life_context: str = Field(min_length=1)
    public_emotion: str = Field(min_length=1)
    unsaid_sentence: str = Field(min_length=1)
    desired_compensation: str = Field(min_length=1)


class PublicEmotionBridge(BaseModel, frozen=True):
    """How one public emotion becomes this book's genre-facing promise."""

    model_config = ConfigDict(extra="ignore")

    bridge_id: str = Field(min_length=1)
    source_segment_id: str = ""
    bridge_type: str = Field(min_length=1)
    public_anchor: str = Field(min_length=1)
    genre_translation: str = Field(min_length=1)
    story_hook: str = Field(min_length=1)
    reader_payoff: str = Field(min_length=1)
    title_hook: str = Field(min_length=1)
    risk_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("bridge_id")):
            data["bridge_id"] = _first_text(data.get("id"), data.get("key"), "bridge")
        if not _text(data.get("bridge_type")):
            data["bridge_type"] = _first_text(data.get("type"), data.get("bridge"), "情感桥")
        if not _text(data.get("title_hook")):
            data["title_hook"] = _first_text(data.get("title"), data.get("headline"))
        if not _text(data.get("reader_payoff")):
            data["reader_payoff"] = _first_text(
                data.get("payoff"),
                data.get("desired_compensation"),
                data.get("story_hook"),
            )
        data["risk_notes"] = _text_list(data.get("risk_notes"))
        return data


class PublicEmotionKernel(BaseModel, frozen=True):
    """Project-scoped public-emotion design contract."""

    model_config = ConfigDict(extra="ignore")

    version: int = PUBLIC_EMOTION_KERNEL_VERSION
    target_segments: list[PublicEmotionSegment] = Field(default_factory=list)
    emotion_bridges: list[PublicEmotionBridge] = Field(default_factory=list)
    reader_comment_triggers: list[str] = Field(default_factory=list)
    forbidden_misreads: list[str] = Field(default_factory=list)
    project_specificity_notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data["reader_comment_triggers"] = _text_list(data.get("reader_comment_triggers"))
        data["forbidden_misreads"] = _text_list(data.get("forbidden_misreads"))
        if not _text(data.get("project_specificity_notes")):
            data["project_specificity_notes"] = _first_text(
                data.get("project_specificity"),
                data.get("specificity_notes"),
            )
        return data


@dataclass(frozen=True)
class PublicEmotionIssue:
    code: str
    severity: str
    path: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicEmotionGateReport:
    present: bool
    valid: bool
    issues: tuple[PublicEmotionIssue, ...] = ()
    target_segment_count: int = 0
    emotion_bridge_count: int = 0
    issue_codes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.valid and not any(
            issue.severity in {"critical", "high"} for issue in self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "valid": self.valid,
            "passed": self.passed,
            "target_segment_count": self.target_segment_count,
            "emotion_bridge_count": self.emotion_bridge_count,
            "issue_codes": list(self.issue_codes),
            "issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity,
                    "path": issue.path,
                    "message": issue.message,
                    "evidence": dict(issue.evidence),
                }
                for issue in self.issues
            ],
        }


@lru_cache(maxsize=1)
def load_public_emotion_methodology_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def public_emotion_kernel_from_dict(data: dict[str, Any]) -> PublicEmotionKernel:
    return PublicEmotionKernel.model_validate(data)


def public_emotion_kernel_to_dict(kernel: PublicEmotionKernel) -> dict[str, Any]:
    return kernel.model_dump(mode="json")


def evaluate_public_emotion_kernel(
    kernel: PublicEmotionKernel | dict[str, Any] | None,
) -> PublicEmotionGateReport:
    """Validate commercial usefulness without enforcing one global story template."""

    if not kernel:
        issue = PublicEmotionIssue(
            code="PUBLIC_EMOTION_KERNEL_MISSING",
            severity="warning",
            path="public_emotion_kernel",
            message=(
                "Missing project-local public emotion kernel; planning can proceed, "
                "but the book has no explicit audience emotion bridge."
            ),
        )
        return PublicEmotionGateReport(
            present=False,
            valid=False,
            issues=(issue,),
            issue_codes=(issue.code,),
        )
    try:
        parsed = (
            public_emotion_kernel_from_dict(dict(kernel))
            if isinstance(kernel, dict)
            else kernel
        )
    except ValidationError as exc:
        issue = PublicEmotionIssue(
            code="PUBLIC_EMOTION_KERNEL_INVALID",
            severity="high",
            path="public_emotion_kernel",
            message="PublicEmotionKernel is present but fails schema validation.",
            evidence={"validation_error": str(exc)},
        )
        return PublicEmotionGateReport(
            present=True,
            valid=False,
            issues=(issue,),
            issue_codes=(issue.code,),
        )

    issues: list[PublicEmotionIssue] = []
    if not parsed.target_segments:
        issues.append(
            PublicEmotionIssue(
                code="PUBLIC_EMOTION_TARGET_MISSING",
                severity="warning",
                path="public_emotion_kernel.target_segments",
                message="No concrete audience segment is defined.",
            )
        )
    if not parsed.emotion_bridges:
        issues.append(
            PublicEmotionIssue(
                code="PUBLIC_EMOTION_BRIDGE_MISSING",
                severity="high",
                path="public_emotion_kernel.emotion_bridges",
                message="No public-emotion-to-genre bridge is defined.",
            )
        )

    segment_ids = {segment.id for segment in parsed.target_segments}
    for index, bridge in enumerate(parsed.emotion_bridges):
        path = f"public_emotion_kernel.emotion_bridges[{index}]"
        if bridge.source_segment_id and bridge.source_segment_id not in segment_ids:
            issues.append(
                PublicEmotionIssue(
                    code="PUBLIC_EMOTION_BRIDGE_SEGMENT_UNKNOWN",
                    severity="warning",
                    path=f"{path}.source_segment_id",
                    message="Bridge points at a segment id that does not exist.",
                    evidence={"source_segment_id": bridge.source_segment_id},
                )
            )
        if _looks_generic(bridge.genre_translation) or _looks_generic(bridge.title_hook):
            issues.append(
                PublicEmotionIssue(
                    code="PUBLIC_EMOTION_GENERIC_TRANSLATION",
                    severity="warning",
                    path=path,
                    message=(
                        "Bridge translation appears generic; this must stay bound to "
                        "the current book rather than a reusable cross-book template."
                    ),
                    evidence={
                        "genre_translation": bridge.genre_translation,
                        "title_hook": bridge.title_hook,
                    },
                )
            )

    codes = tuple(issue.code for issue in issues)
    return PublicEmotionGateReport(
        present=True,
        valid=True,
        issues=tuple(issues),
        target_segment_count=len(parsed.target_segments),
        emotion_bridge_count=len(parsed.emotion_bridges),
        issue_codes=codes,
    )


def _looks_generic(value: str) -> bool:
    text = _text(value)
    if not text:
        return True
    generic_tokens = ("待定", "模板", "通用", "占位", "某个", "某种", "{", "}")
    return any(token in text for token in generic_tokens)


def render_public_emotion_prompt_block(
    kernel: PublicEmotionKernel | dict[str, Any] | None,
    *,
    max_segments: int = 3,
    max_bridges: int = 4,
    language: str = "zh-CN",
) -> str:
    if not kernel:
        return ""
    try:
        parsed = (
            public_emotion_kernel_from_dict(dict(kernel))
            if isinstance(kernel, dict)
            else kernel
        )
    except ValidationError:
        return ""
    is_en = language.lower().startswith("en")
    lines = (
        ["[public_emotion_core | project-local audience bridge]"]
        if is_en
        else ["【public_emotion_core · 本书专属公共情绪桥】"]
    )
    for segment in parsed.target_segments[:max_segments]:
        if is_en:
            lines.append(
                "- Segment "
                f"{segment.id}: group={segment.group_label}; emotion={segment.public_emotion}; "
                f"unsaid={segment.unsaid_sentence}; compensation={segment.desired_compensation}"
            )
        else:
            lines.append(
                "- 受众段 "
                f"{segment.id}: 群体={segment.group_label}; 公共情绪={segment.public_emotion}; "
                f"未说出口的话={segment.unsaid_sentence}; 补偿幻想={segment.desired_compensation}"
            )
    for bridge in parsed.emotion_bridges[:max_bridges]:
        if is_en:
            lines.append(
                "- Bridge "
                f"{bridge.bridge_id} ({bridge.bridge_type}): public anchor={bridge.public_anchor}; "
                f"genre translation={bridge.genre_translation}; payoff={bridge.reader_payoff}; "
                f"title hook={bridge.title_hook}"
            )
        else:
            lines.append(
                "- 情绪桥 "
                f"{bridge.bridge_id}（{bridge.bridge_type}）: 公共锚点={bridge.public_anchor}; "
                f"类型转译={bridge.genre_translation}; 读者补偿={bridge.reader_payoff}; "
                f"标题钩子={bridge.title_hook}"
            )
    if parsed.forbidden_misreads:
        label = "Forbidden misreads" if is_en else "禁止误读"
        lines.append(f"- {label}: {'; '.join(parsed.forbidden_misreads[:5])}")
    if parsed.project_specificity_notes:
        label = "Project-locality" if is_en else "项目专属性"
        lines.append(f"- {label}: {parsed.project_specificity_notes}")
    return "\n".join(lines)


def build_public_emotion_kernel_seed(
    *,
    book_spec: dict[str, Any] | None = None,
    commercial_brief: dict[str, Any] | None = None,
    project_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a conservative project-local seed from existing planning inputs.

    The seed avoids inventing a universal setting. It only rephrases known
    project metadata into a contract that a planner can refine.
    """

    book = _mapping(book_spec)
    commercial = _mapping(commercial_brief)
    metadata = _mapping(project_metadata)
    title = _first_text(book.get("title"), metadata.get("title"), "未命名作品")
    genre = _first_text(book.get("genre"), metadata.get("genre"), "类型故事")
    premise = _first_text(
        book.get("premise"),
        commercial.get("premise"),
        metadata.get("premise"),
        metadata.get("logline"),
    )
    target_audiences = _text_list(
        metadata.get("target_audiences")
        or commercial.get("target_audiences")
        or book.get("target_audiences")
        or book.get("audience")
    )
    audience = target_audiences[0] if target_audiences else f"{genre}目标读者"
    unique_hook = _first_text(
        metadata.get("unique_hook"),
        commercial.get("unique_hook"),
        book.get("unique_hook"),
        metadata.get("logline"),
        book.get("logline"),
        premise,
    )
    bridge_title = _first_text(
        commercial.get("title_hook"),
        metadata.get("primary_title"),
        title,
    )
    return {
        "version": PUBLIC_EMOTION_KERNEL_VERSION,
        "target_segments": [
            {
                "id": "segment-primary",
                "group_label": audience,
                "life_context": premise or f"围绕{genre}的核心处境进入故事。",
                "public_emotion": _first_text(
                    commercial.get("public_emotion"),
                    commercial.get("reader_emotion"),
                    metadata.get("reader_emotion"),
                    "对当前处境的压力、渴望或不甘。",
                ),
                "unsaid_sentence": _first_text(
                    commercial.get("unsaid_sentence"),
                    "为什么我不能换一种方式赢？",
                ),
                "desired_compensation": _first_text(
                    commercial.get("desired_compensation"),
                    commercial.get("reader_promise"),
                    "主角用本书独有设定给出可见补偿。",
                ),
            }
        ],
        "emotion_bridges": [
            {
                "bridge_id": "bridge-primary",
                "source_segment_id": "segment-primary",
                "bridge_type": "value_bridge",
                "public_anchor": _first_text(commercial.get("public_anchor"), audience),
                "genre_translation": unique_hook or f"把公共情绪转译为{genre}的本书专属冲突。",
                "story_hook": unique_hook or premise or f"{title}的核心故事钩子。",
                "reader_payoff": _first_text(
                    commercial.get("reader_payoff"),
                    commercial.get("reader_promise"),
                    "兑现本书专属的情绪补偿。",
                ),
                "title_hook": bridge_title,
            }
        ],
        "reader_comment_triggers": _text_list(commercial.get("reader_comment_triggers")),
        "forbidden_misreads": [
            "公共情绪只能服务本书设定，不能变成跨书固定模板。",
            "不得把现实群体仇恨、违法行为或低俗刺激包装成卖点。",
        ],
        "project_specificity_notes": f"该公共情绪桥仅绑定《{title}》，不得作为其他项目默认设定。",
    }


__all__ = [
    "PUBLIC_EMOTION_KERNEL_VERSION",
    "PublicEmotionBridge",
    "PublicEmotionGateReport",
    "PublicEmotionIssue",
    "PublicEmotionKernel",
    "PublicEmotionSegment",
    "build_public_emotion_kernel_seed",
    "evaluate_public_emotion_kernel",
    "load_public_emotion_methodology_config",
    "public_emotion_kernel_from_dict",
    "public_emotion_kernel_to_dict",
    "render_public_emotion_prompt_block",
]
