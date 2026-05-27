"""Loader for ``quality_gates.yaml::judge_rubrics``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_QUALITY_GATES_PATH = Path("config/quality_gates.yaml")


@dataclass(frozen=True)
class JudgeRubric:
    name: str
    system_prompt: str
    rubric_items: dict[str, Any]
    output_schema: dict[str, Any]

    def render_prompt_block(self) -> str:
        if not self.rubric_items:
            return ""
        lines = ["\n\n## Rubric 配置（来自 quality_gates.yaml）"]
        for key, value in self.rubric_items.items():
            if isinstance(value, dict):
                threshold = value.get("threshold")
                weight = value.get("weight")
                desc = value.get("description") or ""
                parts = [f"- {key}"]
                if threshold is not None:
                    parts.append(f"threshold={threshold}")
                if weight is not None:
                    parts.append(f"weight={weight}")
                if desc:
                    parts.append(str(desc))
                lines.append("；".join(parts))
            else:
                lines.append(f"- {key}: {value}")
        return "\n".join(lines)


def _load_all(path: Path = DEFAULT_QUALITY_GATES_PATH) -> dict[str, JudgeRubric]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("judge_rubrics") or {}
    if not isinstance(raw, dict):
        return {}
    rubrics: dict[str, JudgeRubric] = {}
    for name, blob in raw.items():
        if not isinstance(blob, dict):
            continue
        system_prompt = str(blob.get("system_prompt") or "").strip()
        if not system_prompt:
            continue
        rubric = blob.get("rubric") if isinstance(blob.get("rubric"), dict) else {}
        schema = blob.get("output_schema") if isinstance(blob.get("output_schema"), dict) else {}
        rubrics[str(name)] = JudgeRubric(
            name=str(name),
            system_prompt=system_prompt,
            rubric_items=dict(rubric),
            output_schema=dict(schema),
        )
    return rubrics


def get_judge_rubric(name: str) -> JudgeRubric:
    rubrics = _load_all()
    if name not in rubrics:
        raise KeyError(
            f"Judge rubric '{name}' not in config/quality_gates.yaml judge_rubrics"
        )
    return rubrics[name]


def reload_rubrics_cache() -> None:
    """Compatibility hook; rubrics are loaded fresh on every call."""

    return None


__all__ = ["JudgeRubric", "get_judge_rubric", "reload_rubrics_cache"]
