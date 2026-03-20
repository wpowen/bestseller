from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PromptPackFragments(BaseModel):
    global_rules: str | None = None
    planner_book_spec: str | None = None
    planner_world_spec: str | None = None
    planner_cast_spec: str | None = None
    planner_volume_plan: str | None = None
    planner_outline: str | None = None
    scene_writer: str | None = None
    scene_review: str | None = None
    scene_rewrite: str | None = None
    chapter_review: str | None = None
    chapter_rewrite: str | None = None


class PromptPack(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    writing_profile_overrides: dict[str, Any] = Field(default_factory=dict)
    fragments: PromptPackFragments = Field(default_factory=PromptPackFragments)


def _prompt_pack_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "prompt_packs"


@lru_cache(maxsize=1)
def load_prompt_pack_registry() -> dict[str, PromptPack]:
    registry: dict[str, PromptPack] = {}
    pack_dir = _prompt_pack_dir()
    if not pack_dir.exists():
        return registry
    for path in sorted(pack_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        pack = PromptPack.model_validate(raw)
        registry[pack.key] = pack
    return registry


def list_prompt_packs() -> list[PromptPack]:
    return list(load_prompt_pack_registry().values())


def get_prompt_pack(key: str | None) -> PromptPack | None:
    if not key:
        return None
    return load_prompt_pack_registry().get(key)


def infer_default_prompt_pack_key(genre: str, sub_genre: str | None = None) -> str | None:
    label = f"{genre} {sub_genre or ''}".lower()
    if any(token in label for token in ("末日", "科幻", "星际", "生存", "囤货")):
        return "apocalypse-supply-chain"
    if any(token in label for token in ("仙", "玄幻", "奇幻", "升级", "修真")):
        return "xianxia-upgrade-core"
    if any(token in label for token in ("都市", "异能", "现实", "悬疑")):
        return "urban-power-reversal"
    if any(token in label for token in ("女频", "言情", "成长", "宫斗", "恋爱")):
        return "romance-tension-growth"
    return None


def resolve_prompt_pack(key: str | None, *, genre: str, sub_genre: str | None = None) -> PromptPack | None:
    explicit = get_prompt_pack(key)
    if explicit is not None:
        return explicit
    return get_prompt_pack(infer_default_prompt_pack_key(genre, sub_genre))


def render_prompt_pack_prompt_block(pack: PromptPack | None) -> str:
    if pack is None:
        return ""
    lines = [
        f"Prompt Pack：{pack.name}（{pack.key} v{pack.version}）",
        f"- 定位：{pack.description}",
    ]
    if pack.tags:
        lines.append(f"- 关键词：{'、'.join(pack.tags)}")
    if pack.source_notes:
        lines.append(f"- 设计说明：{'；'.join(pack.source_notes)}")
    if pack.anti_patterns:
        lines.append(f"- 明确避免：{'；'.join(pack.anti_patterns)}")
    if pack.fragments.global_rules:
        lines.append(f"- Pack 级写法规则：{pack.fragments.global_rules}")
    return "\n".join(lines)


def render_prompt_pack_fragment(pack: PromptPack | None, fragment_name: str) -> str:
    if pack is None:
        return ""
    value = getattr(pack.fragments, fragment_name, None)
    return value.strip() if isinstance(value, str) and value.strip() else ""
