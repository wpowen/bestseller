"""Generic upstream artifact health audit for the quality attribution loop."""
# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings


class ArtifactHealth(TypedDict):
    artifact_path: str
    is_healthy: bool
    defects: list[str]
    fix_directives: list[str]
    independence_score: float


async def audit_artifact_health(
    session: AsyncSession,
    settings: AppSettings,
    artifact_path: Path,
    *,
    upstream_context: Mapping[str, Path] | None = None,
    distilled_refs: Sequence[Path] = (),
    workflow_run_id: UUID | None = None,
) -> ArtifactHealth:
    """Judge whether one artifact can independently drive distinctive downstream text."""

    artifact_text = _read_text(artifact_path, limit=30000)
    heuristic = _heuristic_health(artifact_path, artifact_text)
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_system_prompt(),
            user_prompt=_user_prompt(
                artifact_path,
                artifact_text=artifact_text,
                upstream_context=upstream_context or {},
                distilled_refs=distilled_refs,
            ),
            fallback_response=json.dumps(heuristic, ensure_ascii=False),
            prompt_template="artifact_health_audit",
            prompt_version="v1",
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "artifact_health", "artifact_path": artifact_path.as_posix()},
            max_tokens_override=4096,
        ),
    )
    parsed = _parse_json_object(completion.content)
    return _normalize_health(parsed or heuristic, artifact_path=artifact_path)


def _system_prompt() -> str:
    return (
        "# ROLE\n"
        "你是通用小说 artifact 健康度审计员。\n"
        "你做过 100+ 部签约长篇的物料独立性体检，特别擅长识别「换一本书也能套用」的模板化垃圾物料。\n"
        "你的判断标准来自：编辑培训手册的「物料独特性 5 维度」+ 你自己反复见过的劣质物料样本。\n"
        "\n"
        "# CONTEXT\n"
        "你只看 artifact 本身 + 上游上下文，**不看正文**。\n"
        "你的产出会被另一个 LLM 用来决定：这份 artifact 是直接使用、还是触发回头补强。\n"
        "\n"
        "# TASK\n"
        "判断这份 artifact 是否足以独立支撑下游产生**不可替换**的章节产物。\n"
        "\n"
        "# CONSTRAINTS · 判定核心\n"
        "- 如果换一本书也能套用 → 必须判 is_healthy=false\n"
        "- 如果只含「推进剧情 / 制造悬念 / 埋下伏笔」这种通用套话 → 必须判不健康\n"
        "- 如果信息量过低 / 占位符过多 → 必须判不健康\n"
        "- 不要建议新增硬规则 gate（不是你的工作）\n"
        "- 严格只输出 JSON object，无 markdown 围栏\n"
        "\n"
        "# THINKING（产 JSON 前在脑内 4 步）\n"
        "1. 通读 artifact，标记其中**只属于本书**的具体名词（人物 / 地点 / 物件 / 规则）\n"
        "2. 比对：通用套话 vs 不可替换的本书事实——比例是多少？\n"
        "3. 假想：把同类作品的物料换到这里，是否完全可以替换？能 = 不健康\n"
        "4. 给 fix_directives 时必须具体（不要说「补充细节」，要说「补入主角的康熙铜钱在 ch5 的具体损耗描述」）\n"
        "\n"
        "# OUTPUT FORMAT（严格 JSON，无围栏）\n"
        '{"artifact_path": str, "is_healthy": bool, "defects": [str], '
        '"fix_directives": [str], "independence_score": float}'
    )


def _user_prompt(
    artifact_path: Path,
    *,
    artifact_text: str,
    upstream_context: Mapping[str, Path],
    distilled_refs: Sequence[Path],
) -> str:
    context = {
        key: _read_text(path, limit=5000)
        for key, path in upstream_context.items()
    }
    refs = {path.name: _read_text(path, limit=4000) for path in distilled_refs[:6]}
    return (
        "## 任务参数\n"
        f"- artifact_path：{artifact_path.as_posix()}\n"
        f"- artifact 字数：{len(artifact_text)}\n"
        f"- 上游上下文条数：{len(context)}\n"
        f"- 蒸馏参照数：{len(refs)}\n"
        "\n## artifact 内容\n"
        f"```\n{artifact_text}\n```\n"
        "\n## upstream_context（artifact 的上游依赖）\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n"
        "\n## distilled_refs（同类作品蒸馏参照）\n"
        f"```json\n{json.dumps(refs, ensure_ascii=False, indent=2)}\n```\n"
        "\n## 三大审视问题\n"
        "1. 如果让你写下游产物，这份 artifact 提供的信息够吗？\n"
        "2. 这份 artifact 是不是换一本书也能套用？\n"
        "3. 和同类作品参照相比，它缺什么深度？\n"
        "\n## 立即开始\n"
        "按 system 中的 4 步 THINKING 思考，输出严格 JSON。"
    )


def _heuristic_health(artifact_path: Path, artifact_text: str) -> ArtifactHealth:
    text = artifact_text.strip()
    defects: list[str] = []
    generic_markers = (
        "推进剧情",
        "制造悬念",
        "埋下伏笔",
        "增强冲突",
        "情绪递进",
        "揭示秘密",
        "人物成长",
        "节奏紧凑",
        "高能反转",
        "待补充",
        "TODO",
        "占位",
    )
    marker_hits = sum(text.count(marker) for marker in generic_markers)
    unique_tokens = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text))
    if not text:
        defects.append("artifact 为空，无法驱动下游生成")
    if marker_hits >= 3:
        defects.append("存在模板化套话，缺少不可替换的章节事实和动作")
    if len(text) < 120:
        defects.append("信息量过低，无法独立支撑下游产物")
    if len(unique_tokens) < 18 and text:
        defects.append("独特名词和状态变量不足，换书复用风险高")
    score = 0.85
    if defects:
        score = max(0.15, 0.75 - 0.18 * len(defects))
    return {
        "artifact_path": artifact_path.as_posix(),
        "is_healthy": not defects,
        "defects": defects,
        "fix_directives": [
            "补入具体人物、地点、状态变量、因果动作和不可替换的本书信息"
            for _ in defects[:1]
        ],
        "independence_score": round(score, 2),
    }


def _normalize_health(value: Mapping[str, object], *, artifact_path: Path) -> ArtifactHealth:
    defects = _string_list(value.get("defects"))
    directives = _string_list(value.get("fix_directives") or value.get("repair_directives"))
    score = _coerce_score(value.get("independence_score"))
    is_healthy_raw = value.get("is_healthy")
    is_healthy = (
        bool(is_healthy_raw)
        if isinstance(is_healthy_raw, bool)
        else score >= 0.78 and not defects
    )
    return {
        "artifact_path": _string(value.get("artifact_path")) or artifact_path.as_posix(),
        "is_healthy": is_healthy,
        "defects": defects,
        "fix_directives": directives,
        "independence_score": score,
    }


def _read_text(path: Path, *, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _parse_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    candidates = [stripped, unfenced]
    match = re.search(r"\{.*\}", unfenced, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1.0:
        score = score / 10.0
    return max(0.0, min(1.0, score))


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


__all__ = ["ArtifactHealth", "audit_artifact_health"]
