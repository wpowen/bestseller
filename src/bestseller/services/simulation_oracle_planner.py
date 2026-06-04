"""Planner 侧的 Simulation Oracle 接入(Phase 3)。

把 :mod:`simulation_oracle` 的真推演挂进 planner 的 story_design_kernel 生成环节:
在 LLM 产出 kernel 后、校验/闸门前,用 oracle 增强 ``beat_schedule`` / ``plot_tree``。

设计红线(对生产管线零风险):
- **默认关闭**:仅当环境开关 ``MIROFISH_ORACLE_PLANNER`` 打开才生效;
- **安全降级**:任何异常都吞掉并原样返回 payload,绝不阻断规划;
- **隔离**:planner.py 只多一行调用,逻辑全在本模块;
- 复用项目已配 LLM(经 ``complete_text``,走 planner 角色),0 新 key。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ProjectModel
from bestseller.services.simulation_oracle import (
    CharacterSeed,
    OracleRequest,
    augment_kernel,
    build_llm_oracle_prompts,
    infer_entity_type,
    parse_llm_oracle_result,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


def planner_oracle_enabled(settings: AppSettings | None = None) -> bool:
    """是否在 planner 环节启用 oracle(默认关闭)。"""

    return os.getenv("MIROFISH_ORACLE_PLANNER", "").strip().lower() in {"1", "true", "yes", "on"}


def _seed(obj: object, *, default_type: str) -> CharacterSeed | None:
    """把 cast_spec 里的一个角色条目转成 CharacterSeed(尽量从描述推断类型)。"""

    if isinstance(obj, str):
        name, desc = obj.strip(), ""
    elif isinstance(obj, dict):
        name = str(obj.get("name") or obj.get("character_key") or "").strip()
        desc = str(
            obj.get("role")
            or obj.get("archetype")
            or obj.get("description")
            or obj.get("voice_profile")
            or ""
        ).strip()
    else:
        return None
    if not name:
        return None
    etype = infer_entity_type(desc)
    if etype == "Character":  # 描述没给出明确定位 → 用调用方的默认定位兜底
        etype = default_type
    return CharacterSeed(name=name, description=desc or name, entity_type=etype)


def _characters_from_cast(cast_spec_payload: dict[str, Any]) -> tuple[CharacterSeed, ...]:
    seeds: list[CharacterSeed] = []
    seen: set[str] = set()

    def _add(obj: object, default_type: str) -> None:
        s = _seed(obj, default_type=default_type)
        if s and s.name not in seen:
            seeds.append(s)
            seen.add(s.name)

    _add(cast_spec_payload.get("protagonist"), "Protagonist")
    _add(cast_spec_payload.get("antagonist"), "Rival")
    for force in cast_spec_payload.get("antagonist_forces") or []:
        _add(force, "Rival")
    for member in cast_spec_payload.get("supporting_cast") or []:
        _add(member, "Ally")
    return tuple(seeds)


def _build_request(
    project: ProjectModel,
    premise: str,
    cast_spec_payload: dict[str, Any],
) -> OracleRequest:
    return OracleRequest(
        slug=str(getattr(project, "slug", "") or "book"),
        target_chapters=int(getattr(project, "target_chapters", 0) or 0),
        premise=premise or "",
        characters=_characters_from_cast(cast_spec_payload),
        question="推演本书走向、角色驱动的 beat、涌现支线与动机漏洞。",
    )


async def augment_story_design_kernel_with_oracle(
    session: AsyncSession,
    settings: AppSettings,
    *,
    payload: dict[str, Any],
    project: ProjectModel,
    premise: str,
    cast_spec_payload: dict[str, Any],
) -> dict[str, Any]:
    """用 oracle 增强 story_design_kernel payload;关闭/失败时原样返回。"""

    if not planner_oracle_enabled(settings):
        return payload
    if not isinstance(payload, dict):
        return payload
    try:
        request = _build_request(project, premise, cast_spec_payload)
        if not request.characters or request.target_chapters <= 0:
            return payload
        system_prompt, user_prompt = build_llm_oracle_prompts(request)

        # 延迟导入,避免与 llm.py 形成模块级循环依赖
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        result = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response="{}",
                project_id=getattr(project, "id", None),
                metadata={"purpose": "simulation_oracle_kernel_augment"},
            ),
        )
        oracle_result = parse_llm_oracle_result(result.content, request)
        augmented = augment_kernel(payload, oracle_result, request.target_chapters)
        logger.info(
            "simulation_oracle augmented story_design_kernel "
            "(source=%s, beats=%d, subplots=%d, ranking_ready=%s)",
            oracle_result.source,
            len(oracle_result.beats),
            len(oracle_result.subplots),
            oracle_result.ranking_ready,
        )
        return augmented
    except Exception:  # oracle 永不阻断规划
        logger.warning("simulation_oracle augmentation skipped (degraded)", exc_info=True)
        return payload
