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
        "# ROLE\n"
        "你是小说工程根因分析师——专门把读者表层抱怨反推到上游物料的具体缺陷。\n"
        "你深谙小说生产流水线：series_bible → world_bible → chapter_outline → scene_plan → 正文。\n"
        "你做过 100+ 部签约长篇的归因诊断，能从读者「这段不对劲」精确定位到「rule_ledger 第 X 条缺触发条件」。\n"
        "\n"
        "# CONTEXT\n"
        "你会收到：读者反馈清单 + artifact 拓扑（哪些上游产物存在）。\n"
        "你的产出会驱动 artifact 修复工单——你写得越准，下游修得越对，避免再触发同一问题。\n"
        "\n"
        "# TASK\n"
        "对每条 reader_feedback：\n"
        "1. 判断**根因**在哪一层上游 artifact（不是表层正文）\n"
        "2. 指出具体 artifact 路径\n"
        "3. 说明这份 artifact 缺什么\n"
        "4. 给出修复这份 artifact 的具体指令（不是修正文！）\n"
        "\n"
        "# CONSTRAINTS\n"
        "- root_layer 必须是 topology 中的 key，不要造新名词\n"
        "- 不要建议新增硬规则 gate（不是你的工作）\n"
        "- 不要直接改正文（你的修复目标是物料，不是产物）\n"
        "- 严格只输出 JSON array，无 markdown 围栏\n"
        "- repair_directive 必须可执行（不要写「补充信息」，要写「在 rule_ledger 第 R-007 条加 trigger_condition: 子时入镜」）\n"
        "\n"
        "# THINKING（产 JSON 前在脑内 4 步）\n"
        "1. 对每条 feedback：先判断它在抱怨哪一类问题（人物 / 规则 / 空间 / 节奏 / 物料）\n"
        "2. 逆推到拓扑：这类问题最早可能由哪一层 artifact 失误导致？\n"
        "3. 定位具体路径：从 book_root 出发，按 root_layer 找最匹配的文件\n"
        "4. repair_directive 必须具体：给到字段级 / 条款级，不能停留在大方向\n"
        "\n"
        "# OUTPUT FORMAT（严格 JSON array）\n"
        '[{"issue_id": "issue-1", "root_layer": "<topology key>", '
        '"artifact_path": "<path>", "missing": "<缺什么>", '
        '"repair_directive": "<具体可执行的修复指令>"}]'
    )


def _user_prompt(
    feedback: Sequence[ReaderFeedback],
    *,
    topology: Mapping[str, ArtifactNode],
    book_root: Path,
) -> str:
    return (
        "## 任务参数\n"
        f"- book_root：{book_root.as_posix()}\n"
        f"- topology 层数：{len(topology)}\n"
        f"- reader_feedback 条数：{len(feedback)}\n"
        "\n## artifact_topology\n"
        f"```json\n{json.dumps(topology, ensure_ascii=False, indent=2)}\n```\n"
        "\n## reader_feedback\n"
        f"```json\n{json.dumps(list(feedback), ensure_ascii=False, indent=2)}\n```\n"
        "\n## 立即开始\n"
        "按 system 中的 4 步 THINKING 逐条归因，输出严格 JSON array。"
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
