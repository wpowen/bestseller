"""Prompt-ready mature-fiction design references from distillation aggregates.

The distillation pipeline stores anonymous, abstract design learnings under
``data/distillation/aggregates/<category>/``.  This module turns those files
into small, phase-specific prompt blocks for the planner.  It deliberately
renders *design paths* rather than source-book summaries: state variables,
reader rewards, mechanism candidates, safe craft controls, and anti-copy
boundaries.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from bestseller.services.story_design_grammars import resolve_story_design_grammar

logger = logging.getLogger(__name__)


_PHASE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "architecture": (
        "plot_patterns",
        "thematic_motifs",
        "anti_cliche_patterns",
    ),
    "world": (
        "world_settings",
        "power_systems",
        "factions",
        "locale_templates",
        "real_world_references",
    ),
    "cast": (
        "character_archetypes",
        "character_templates",
        "dialogue_styles",
        "emotion_arcs",
        "anti_cliche_patterns",
    ),
    "story_design": (
        "plot_patterns",
        "thematic_motifs",
        "scene_templates",
        "anti_cliche_patterns",
    ),
    "volume_plan": (
        "plot_patterns",
        "scene_templates",
        "thematic_motifs",
        "anti_cliche_patterns",
    ),
    "chapter_outline": (
        "scene_templates",
        "emotion_arcs",
        "dialogue_styles",
        "anti_cliche_patterns",
    ),
    "craft": (
        "dialogue_styles",
        "emotion_arcs",
        "scene_templates",
        "anti_cliche_patterns",
    ),
}

_PHASE_LABEL_ZH: dict[str, str] = {
    "architecture": "全书架构 / BookSpec",
    "world": "世界观设计 / WorldSpec",
    "cast": "人物设计 / CastSpec",
    "story_design": "剧情内核 / StoryDesignKernel",
    "volume_plan": "卷纲路径 / VolumePlan",
    "chapter_outline": "章纲节奏 / ChapterOutline",
    "craft": "写作手法 / Draft Craft",
}

_PHASE_LABEL_EN: dict[str, str] = {
    "architecture": "Series Architecture / BookSpec",
    "world": "World Design / WorldSpec",
    "cast": "Cast Design / CastSpec",
    "story_design": "Story Kernel / StoryDesignKernel",
    "volume_plan": "Volume Path / VolumePlan",
    "chapter_outline": "Chapter Rhythm / ChapterOutline",
    "craft": "Craft Controls / Draft Craft",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _string_items(value: object, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _truncate(text: object, limit: int = 120) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _confidence(row: dict[str, Any]) -> float:
    for key in ("max_confidence", "confidence"):
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _is_fallback_volume_path(row: dict[str, Any]) -> bool:
    if row.get("distillation_fallback") is True:
        return True
    haystack = " ".join(
        str(part or "")
        for part in (
            row.get("arc_function"),
            row.get("dominant_engine"),
            row.get("setup_payoff_rhythm"),
            " ".join(str(item) for item in _as_list(row.get("state_progression"))),
        )
    ).lower()
    return "fallback aggregation" in haystack or "llm output fallback" in haystack


def _usable_volume_paths(rows: Sequence[object], *, limit: int = 3) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or _is_fallback_volume_path(row):
            continue
        paths.append(row)
        if len(paths) >= limit:
            break
    return paths


def _candidate_keys(
    *,
    category_key: str | None,
    genre: str | None,
    sub_genre: str | None,
) -> list[str]:
    keys: list[str] = []
    if category_key:
        keys.append(category_key)
    try:
        grammar = resolve_story_design_grammar(
            category_key=category_key,
            genre=genre,
            sub_genre=sub_genre,
        )
        if grammar.key:
            keys.append(grammar.key)
    except Exception as exc:
        logger.debug("Unable to infer distillation aggregate key: %s", exc)
    keys.append("distillation-generic")
    return _dedupe_preserve_order(keys)


def find_distilled_design_aggregate_dir(
    *,
    category_key: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """Return the best matching aggregate directory, if one exists."""

    root = repo_root or _repo_root()
    aggregates_root = root / "data" / "distillation" / "aggregates"
    for key in _candidate_keys(category_key=category_key, genre=genre, sub_genre=sub_genre):
        candidate = aggregates_root / key
        if candidate.is_dir() and (
            (candidate / "aggregate_manifest.json").is_file()
            or (candidate / "grammar_patch.yaml").is_file()
        ):
            return candidate
    return None


@lru_cache(maxsize=64)
def _load_aggregate_cached(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    material_path = path / "material_entries.active.jsonl"
    if not material_path.is_file():
        material_path = path / "material_entries.review.jsonl"
    return {
        "path": str(path),
        "key": path.name,
        "manifest": _read_json(path / "aggregate_manifest.json"),
        "grammar": _read_yaml(path / "grammar_patch.yaml"),
        "mechanisms": _read_jsonl(path / "mechanism_registry.jsonl"),
        "materials": _read_jsonl(material_path),
        "author_craft": _read_jsonl(path / "author_craft_registry.jsonl"),
        "anti_copy": _read_json(path / "anti_copy_rules.json"),
        "book_designs": _read_jsonl(path / "book_design_registry.jsonl"),
        "volume_paths": _read_jsonl(path / "volume_design_paths.jsonl"),
    }


def load_distilled_design_reference(
    *,
    category_key: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Load the aggregate payload chosen for a project/category."""

    aggregate_dir = find_distilled_design_aggregate_dir(
        category_key=category_key,
        genre=genre,
        sub_genre=sub_genre,
        repo_root=repo_root,
    )
    if aggregate_dir is None:
        return {}
    return _load_aggregate_cached(str(aggregate_dir.resolve()))


def _phase_materials(
    rows: Sequence[dict[str, Any]],
    *,
    phase: str,
    limit: int,
) -> list[dict[str, Any]]:
    dims = set(_PHASE_DIMENSIONS.get(phase) or ())
    if not dims:
        return []
    picked = [row for row in rows if str(row.get("dimension") or "") in dims]
    picked.sort(key=_confidence, reverse=True)
    return picked[:limit]


def _phase_mechanisms(
    rows: Sequence[dict[str, Any]],
    *,
    phase: str,
    limit: int,
) -> list[dict[str, Any]]:
    dims = set(_PHASE_DIMENSIONS.get(phase) or ())
    if not dims:
        candidates = list(rows)
    else:
        candidates = []
        for row in rows:
            target = str(row.get("promotion_target") or "")
            ctype = str(row.get("candidate_type") or "")
            if any(dim in target for dim in dims) or ctype in dims:
                candidates.append(row)
    candidates.sort(key=_confidence, reverse=True)
    return candidates[:limit]


def _render_items_zh(label: str, values: Sequence[str]) -> list[str]:
    if not values:
        return []
    return [f"- {label}: " + "; ".join(values)]


def _render_items_en(label: str, values: Sequence[str]) -> list[str]:
    if not values:
        return []
    return [f"- {label}: " + "; ".join(values)]


def _render_zh(
    ref: dict[str, Any],
    *,
    phase: str,
    max_mechanisms: int,
    max_materials: int,
) -> str:
    grammar = ref.get("grammar") if isinstance(ref.get("grammar"), dict) else {}
    manifest = ref.get("manifest") if isinstance(ref.get("manifest"), dict) else {}
    anti_copy = ref.get("anti_copy") if isinstance(ref.get("anti_copy"), dict) else {}
    source_count = int(
        manifest.get("source_count")
        or len(_as_list(anti_copy.get("source_ids")))
        or 0
    )
    key = str(ref.get("key") or grammar.get("key") or "").strip()
    phase_label = _PHASE_LABEL_ZH.get(phase, phase)
    lines = [
        f"## 成熟小说设计参考(蒸馏聚合: {key or 'unknown'} / {phase_label})",
        f"来源: {source_count or '多'} 本匿名成熟作品的抽象设计经验。",
        "使用规则: 只引用机制、节奏、状态变量、角色功能和写法控制; "
        "不得复用源书专名、具体桥段、独特组合或句式。",
    ]

    if phase in {"architecture", "story_design", "volume_plan", "chapter_outline"}:
        lines.extend(
            _render_items_zh("状态变量", _string_items(grammar.get("state_variables"), limit=8))
        )
        lines.extend(
            _render_items_zh(
                "章节变化向量",
                _string_items(grammar.get("chapter_change_vectors"), limit=8),
            )
        )
        lines.extend(
            _render_items_zh("读者奖励", _string_items(grammar.get("reader_rewards"), limit=8))
        )
        lines.extend(
            _render_items_zh(
                "钩子/后效类型",
                _string_items(grammar.get("hook_or_aftereffect_types"), limit=6),
            )
        )

    mechanisms = _phase_mechanisms(
        _as_list(ref.get("mechanisms")),
        phase=phase,
        limit=max_mechanisms,
    )
    if mechanisms:
        lines.append("- 可借鉴的成熟机制路径:")
        for row in mechanisms:
            mid = str(row.get("mechanism_id") or row.get("slug") or "mechanism")
            lines.append(f"  - {mid}: {_truncate(row.get('summary'), 110)}")

    materials = _phase_materials(
        _as_list(ref.get("materials")),
        phase=phase,
        limit=max_materials,
    )
    if materials:
        lines.append("- 可转化为本书原创物料的抽象条目:")
        for row in materials:
            dimension = str(row.get("dimension") or "material")
            name = str(row.get("name") or row.get("slug") or "未命名条目")
            summary = _truncate(row.get("narrative_summary"), 110)
            lines.append(f"  - [{dimension}] {name}: {summary}")

    if phase in {"architecture", "volume_plan", "chapter_outline"}:
        volume_paths = _usable_volume_paths(_as_list(ref.get("volume_paths")), limit=3)
        if volume_paths:
            lines.append("- 卷/阶段参考路径:")
            for row in volume_paths:
                summary = _truncate(
                    row.get("arc_function")
                    or row.get("dominant_engine")
                    or row.get("setup_payoff_rhythm"),
                    120,
                )
                if summary:
                    lines.append(f"  - volume_path#{row.get('volume_no', '?')}: {summary}")

    if phase in {"architecture", "craft", "chapter_outline", "cast"}:
        craft_rows = _as_list(ref.get("author_craft"))[:2]
        if craft_rows:
            lines.append("- 安全写法控制:")
            for row in craft_rows:
                if not isinstance(row, dict):
                    continue
                controls = []
                controls.extend(_string_items(row.get("dialogue_system"), limit=2))
                controls.extend(_string_items(row.get("description_strategy"), limit=2))
                controls.extend(_string_items(row.get("hooking_and_transitions"), limit=2))
                if controls:
                    lines.append(f"  - {'; '.join(controls[:4])}")

    forbidden = _string_items(grammar.get("forbidden_defaults"), limit=8)
    replacement = _string_items(anti_copy.get("replacement_policy"), limit=5)
    blocked = _string_items(anti_copy.get("blocked_combinations"), limit=5)
    guardrails = [*forbidden, *replacement, *blocked]
    if guardrails:
        lines.append("- 反抄袭/反俗套边界: " + "; ".join(guardrails[:10]))

    return "\n".join(lines).rstrip()


def _render_en(
    ref: dict[str, Any],
    *,
    phase: str,
    max_mechanisms: int,
    max_materials: int,
) -> str:
    grammar = ref.get("grammar") if isinstance(ref.get("grammar"), dict) else {}
    manifest = ref.get("manifest") if isinstance(ref.get("manifest"), dict) else {}
    anti_copy = ref.get("anti_copy") if isinstance(ref.get("anti_copy"), dict) else {}
    source_count = int(
        manifest.get("source_count")
        or len(_as_list(anti_copy.get("source_ids")))
        or 0
    )
    key = str(ref.get("key") or grammar.get("key") or "").strip()
    phase_label = _PHASE_LABEL_EN.get(phase, phase)
    lines = [
        f"## Distilled Mature-Fiction Design Reference ({key or 'unknown'} / {phase_label})",
        "Source base: abstract learnings from "
        f"{source_count or 'multiple'} anonymous mature works.",
        "Use rule: borrow only mechanisms, rhythm, state variables, character "
        "functions, and safe craft controls; do not reuse source names, exact "
        "plot chains, distinctive combinations, or sentence patterns.",
    ]
    if phase in {"architecture", "story_design", "volume_plan", "chapter_outline"}:
        lines.extend(
            _render_items_en(
                "State variables",
                _string_items(grammar.get("state_variables"), limit=8),
            )
        )
        lines.extend(
            _render_items_en(
                "Chapter change vectors",
                _string_items(grammar.get("chapter_change_vectors"), limit=8),
            )
        )
        lines.extend(
            _render_items_en(
                "Reader rewards",
                _string_items(grammar.get("reader_rewards"), limit=8),
            )
        )
        lines.extend(
            _render_items_en(
                "Hook/aftereffect types",
                _string_items(grammar.get("hook_or_aftereffect_types"), limit=6),
            )
        )

    mechanisms = _phase_mechanisms(
        _as_list(ref.get("mechanisms")),
        phase=phase,
        limit=max_mechanisms,
    )
    if mechanisms:
        lines.append("- Mature mechanism paths to adapt:")
        for row in mechanisms:
            mid = str(row.get("mechanism_id") or row.get("slug") or "mechanism")
            lines.append(f"  - {mid}: {_truncate(row.get('summary'), 110)}")

    materials = _phase_materials(
        _as_list(ref.get("materials")),
        phase=phase,
        limit=max_materials,
    )
    if materials:
        lines.append("- Abstract entries that may be converted into original project materials:")
        for row in materials:
            dimension = str(row.get("dimension") or "material")
            name = str(row.get("name") or row.get("slug") or "unnamed")
            summary = _truncate(row.get("narrative_summary"), 110)
            lines.append(f"  - [{dimension}] {name}: {summary}")

    if phase in {"architecture", "volume_plan", "chapter_outline"}:
        volume_paths = _usable_volume_paths(_as_list(ref.get("volume_paths")), limit=3)
        if volume_paths:
            lines.append("- Volume/stage reference paths:")
            for row in volume_paths:
                summary = _truncate(
                    row.get("arc_function")
                    or row.get("dominant_engine")
                    or row.get("setup_payoff_rhythm"),
                    120,
                )
                if summary:
                    lines.append(f"  - volume_path#{row.get('volume_no', '?')}: {summary}")

    if phase in {"architecture", "craft", "chapter_outline", "cast"}:
        craft_rows = _as_list(ref.get("author_craft"))[:2]
        if craft_rows:
            lines.append("- Safe craft controls:")
            for row in craft_rows:
                if not isinstance(row, dict):
                    continue
                controls = []
                controls.extend(_string_items(row.get("dialogue_system"), limit=2))
                controls.extend(_string_items(row.get("description_strategy"), limit=2))
                controls.extend(_string_items(row.get("hooking_and_transitions"), limit=2))
                if controls:
                    lines.append(f"  - {'; '.join(controls[:4])}")

    guardrails = [
        *_string_items(grammar.get("forbidden_defaults"), limit=8),
        *_string_items(anti_copy.get("replacement_policy"), limit=5),
        *_string_items(anti_copy.get("blocked_combinations"), limit=5),
    ]
    if guardrails:
        lines.append("- Anti-copy / anti-cliche boundaries: " + "; ".join(guardrails[:10]))
    return "\n".join(lines).rstrip()


def render_distilled_design_reference_block(
    *,
    category_key: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    phase: str = "architecture",
    language: str = "zh-CN",
    repo_root: Path | None = None,
    max_mechanisms: int = 6,
    max_materials: int = 6,
) -> str:
    """Render one phase-specific prompt block for planner injection."""

    ref = load_distilled_design_reference(
        category_key=category_key,
        genre=genre,
        sub_genre=sub_genre,
        repo_root=repo_root,
    )
    if not ref:
        return ""
    if str(language or "").lower().startswith("en"):
        return _render_en(
            ref,
            phase=phase,
            max_mechanisms=max_mechanisms,
            max_materials=max_materials,
        )
    return _render_zh(
        ref,
        phase=phase,
        max_mechanisms=max_mechanisms,
        max_materials=max_materials,
    )


def render_all_distilled_design_reference_blocks(
    *,
    category_key: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str = "zh-CN",
    repo_root: Path | None = None,
    phases: Sequence[str] | None = None,
) -> dict[str, str]:
    """Render all planner-phase blocks from the selected aggregate."""

    selected = tuple(
        phases
        or (
            "architecture",
            "world",
            "cast",
            "story_design",
            "volume_plan",
            "chapter_outline",
            "craft",
        )
    )
    blocks: dict[str, str] = {}
    for phase in selected:
        block = render_distilled_design_reference_block(
            category_key=category_key,
            genre=genre,
            sub_genre=sub_genre,
            phase=phase,
            language=language,
            repo_root=repo_root,
        )
        if block:
            blocks[phase] = block
    return blocks


__all__ = [
    "find_distilled_design_aggregate_dir",
    "load_distilled_design_reference",
    "render_all_distilled_design_reference_blocks",
    "render_distilled_design_reference_block",
]
