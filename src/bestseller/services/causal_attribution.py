"""Reverse reader feedback into upstream artifact repair directives."""
# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.artifact_topology import ARTIFACT_TOPOLOGY, ArtifactNode
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.reader_panel_judge import ReaderFeedback
from bestseller.settings import AppSettings


class AttributionRecord(TypedDict):
    issue_id: str
    root_layer: str
    artifact_path: str
    missing: str
    repair_directive: str


async def attribute_root_causes(
    session: AsyncSession,
    settings: AppSettings,
    feedback: Sequence[ReaderFeedback],
    *,
    topology: Mapping[str, ArtifactNode] = ARTIFACT_TOPOLOGY,
    book_root: Path,
    workflow_run_id: UUID | None = None,
) -> list[AttributionRecord]:
    """Attribute reader issues to the earliest likely upstream artifact."""

    if not feedback:
        return []

    fallback_records = (
        _heuristic_record(item, index, topology=topology, book_root=book_root)
        for index, item in enumerate(feedback, 1)
    )
    fallback = json.dumps(list(fallback_records), ensure_ascii=False)
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_system_prompt(),
            user_prompt=_user_prompt(feedback, topology=topology, book_root=book_root),
            fallback_response=fallback,
            prompt_template="causal_attribution",
            prompt_version="v1",
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "causal_attribution", "feedback_count": len(feedback)},
            max_tokens_override=4096,
        ),
    )
    parsed = _parse_json_value(completion.content)
    records = _normalize_records(parsed, topology=topology, book_root=book_root)
    if records:
        return records
    return [
        _heuristic_record(item, index, topology=topology, book_root=book_root)
        for index, item in enumerate(feedback, 1)
    ]


def _system_prompt() -> str:
    return (
        "你是小说工程根因分析师。给你读者反馈和 artifact 拓扑。"
        "对每条反馈判断根因在哪一层上游 artifact，指出具体 artifact 路径，"
        "说明缺什么，并给出修复这份 artifact 的指令。"
        "不要建议新增硬规则 gate，也不要直接改正文。严格只输出 JSON array。"
    )


def _user_prompt(
    feedback: Sequence[ReaderFeedback],
    *,
    topology: Mapping[str, ArtifactNode],
    book_root: Path,
) -> str:
    return (
        f"## book_root\n{book_root.as_posix()}\n\n"
        "## artifact_topology\n"
        f"{json.dumps(topology, ensure_ascii=False, indent=2)}\n\n"
        "## reader_feedback\n"
        f"{json.dumps(list(feedback), ensure_ascii=False, indent=2)}\n\n"
        "## 输出 schema\n"
        '[{ "issue_id": "issue-1", "root_layer": "<topology key>", '
        '"artifact_path": "<path>", "missing": "<缺什么>", '
        '"repair_directive": "<修复指令>" }]'
    )


def _normalize_records(
    value: object,
    *,
    topology: Mapping[str, ArtifactNode],
    book_root: Path,
) -> list[AttributionRecord]:
    if isinstance(value, Mapping):
        raw = value.get("attributions") or value.get("records") or value.get("issues") or []
        items = raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []
    elif isinstance(value, Sequence) and not isinstance(value, str):
        items = value
    else:
        items = []

    records: list[AttributionRecord] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, Mapping):
            continue
        root_layer = _string(item.get("root_layer") or item.get("artifact_type"))
        if root_layer not in topology:
            root_layer = _infer_layer(_string(item), topology)
        artifact_path = _string(item.get("artifact_path") or item.get("path"))
        if not artifact_path:
            artifact_path = _resolve_artifact_path(book_root, root_layer)
        records.append(
            {
                "issue_id": _string(item.get("issue_id") or item.get("id")) or f"issue-{index}",
                "root_layer": root_layer,
                "artifact_path": artifact_path,
                "missing": (
                    _string(item.get("missing") or item.get("defect"))
                    or "缺少可执行上游信息"
                ),
                "repair_directive": _string(
                    item.get("repair_directive") or item.get("fix") or item.get("required_fix")
                )
                or "补足该 artifact 中会导致读者反馈的问题。",
            }
        )
    return records


def _heuristic_record(
    feedback: ReaderFeedback,
    index: int,
    *,
    topology: Mapping[str, ArtifactNode],
    book_root: Path,
) -> AttributionRecord:
    text = " ".join(
        str(feedback.get(key) or "")
        for key in ("issue", "evidence", "suggested_attribution_hint")
    )
    root_layer = _infer_layer(text, topology)
    return {
        "issue_id": f"issue-{index}",
        "root_layer": root_layer,
        "artifact_path": _resolve_artifact_path(book_root, root_layer),
        "missing": _infer_missing(text),
        "repair_directive": (
            f"修复 {root_layer}: 针对读者反馈「{feedback['issue']}」补足缺失信息，"
            "让下游生成不再依赖临场补丁。"
        ),
    }


def _infer_layer(text: str, topology: Mapping[str, ArtifactNode]) -> str:
    lowered = text.lower()
    candidates: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("character_card", ("人物", "角色", "主角", "名字", "称呼", "动机", "character")),
        ("rule_ledger", ("规则", "设定", "能力", "术法", "边界", "rule", "ledger")),
        ("chapter_outline", ("章纲", "细纲", "章节", "撞戏", "重复", "节奏", "outline")),
        ("scene_plan", ("场景", "转场", "空间", "位置", "scene")),
        ("world_bible", ("世界观", "地名", "地点", "组织", "world", "bible")),
        ("material_entry", ("物料", "线索", "证据", "伏笔", "material")),
    )
    for layer, keywords in candidates:
        if layer in topology and any(keyword in lowered for keyword in keywords):
            return layer
    return "chapter_outline" if "chapter_outline" in topology else next(iter(topology))


def _infer_missing(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("重复", "撞戏", "套话", "模板")):
        return "缺少不可替换的章节推进目标和差异化场景设计"
    if any(keyword in lowered for keyword in ("地名", "地点", "空间", "位置")):
        return "缺少稳定空间/地理连续性约束"
    if any(keyword in lowered for keyword in ("规则", "设定", "能力", "边界")):
        return "缺少规则边界、触发条件和铺垫顺序"
    if any(keyword in lowered for keyword in ("人物", "角色", "动机", "名字")):
        return "缺少角色身份、动机或称呼一致性"
    return "缺少足够具体的上游约束"


def _resolve_artifact_path(book_root: Path, root_layer: str) -> str:
    candidates_by_layer: dict[str, tuple[str, ...]] = {
        "chapter_text": ("chapter-*.md",),
        "scene_plan": ("**/*scene*plan*.json", "**/scene*.json"),
        "chapter_outline": ("**/*chapter*outline*.json", "**/*outline*.json"),
        "volume_plan": ("**/*volume*plan*.json", "**/volume*.json"),
        "rule_ledger": ("**/*rule*ledger*.json", "**/*ledger*.json"),
        "reveal_schedule": ("**/*reveal*schedule*.json", "**/*schedule*.json"),
        "character_card": ("**/*character*.json", "**/*cast*.json"),
        "world_bible": ("**/*world*bible*.json", "story-bible/*.json", "**/*bible*.json"),
        "series_bible": ("**/*series*bible*.json", "story-bible/*.json", "project.md"),
        "material_entry": ("**/*material*.json", "**/*knowledge*.json", "knowledge/**/*"),
        "distilled_mechanism": ("source-artifacts/**/*", "data/distillation/**/*"),
        "methodology_card": ("**/*methodology*.json", "**/*methodology*.md"),
    }
    for pattern in candidates_by_layer.get(root_layer, ()):
        for path in sorted(book_root.glob(pattern)):
            if path.is_file():
                return path.as_posix()
    return (book_root / f"{root_layer}.json").as_posix()


def _parse_json_value(text: str) -> object:
    stripped = text.strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    candidates = [stripped, unfenced]
    match = re.search(r"(\[.*\]|\{.*\})", unfenced, flags=re.S)
    if match:
        candidates.append(match.group(1))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return []


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping) or isinstance(value, Sequence):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value).strip()
    return str(value).strip()


__all__ = ["AttributionRecord", "attribute_root_causes"]
