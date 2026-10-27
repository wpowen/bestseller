"""Prompt-ready mature-fiction design references from distillation aggregates.

The distillation pipeline stores anonymous, abstract design learnings under
``data/distillation/aggregates/<category>/``.  This module turns those files
into small, phase-specific prompt blocks for the planner.  It deliberately
renders *design paths* rather than source-book summaries: state variables,
reader rewards, mechanism candidates, safe craft controls, and anti-copy
boundaries.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from typing import Any, cast

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


def _as_mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _dict_rows(value: object) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], item) for item in _as_list(value) if isinstance(item, dict)]


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


_PROMPT_REUSE_MARKERS = frozenset(
    {"abstract", "abstract_only", "approved_reusable", "prompt_safe"}
)


def _is_explicitly_prompt_reusable(row: dict[str, Any]) -> bool:
    """Accept only rows whose producer explicitly cleared prompt reuse.

    Review status, confidence, and a source id are provenance facts; none of
    them means that a source-bound observation is safe to teach to a new book.
    The distiller must opt a row into prompt reuse with a semantic marker.
    """

    for key in ("reuse_scope", "abstraction_status", "prompt_reuse"):
        marker = str(row.get(key) or "").strip().lower()
        if marker in _PROMPT_REUSE_MARKERS:
            return True
    return False


def _prompt_reusable_rows(
    rows: Sequence[dict[str, Any]], *, limit: int | None = None
) -> list[dict[str, Any]]:
    reusable = [row for row in rows if _is_explicitly_prompt_reusable(row)]
    return reusable if limit is None else reusable[:limit]


def _abstract_craft_rows(
    rows: Sequence[dict[str, Any]], *, limit: int = 2
) -> list[dict[str, Any]]:
    """Return craft profiles carrying the aggregator's abstract-only receipt."""

    out: list[dict[str, Any]] = []
    for row in rows:
        scope = str(row.get("craft_observation_scope") or "").strip().lower()
        policy = str(row.get("style_safety_policy") or "").strip().lower()
        if scope == "abstract craft_profile fields only" or policy.startswith(
            "use only abstract craft controls"
        ):
            out.append(row)
        if len(out) >= limit:
            break
    return out


def _prompt_safe_grammar_items(
    grammar: dict[str, Any], field: str, *, limit: int
) -> list[str]:
    """Render grammar fields only when the aggregate explicitly opts them in."""

    fields = {
        str(item).strip()
        for item in _as_list(grammar.get("prompt_safe_fields"))
        if str(item).strip()
    }
    if field not in fields:
        return []
    return _string_items(grammar.get(field), limit=limit)


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
    grammar = _read_yaml(path / "grammar_patch.yaml")
    mechanisms = _read_jsonl(path / "mechanism_registry.jsonl")
    materials = _read_jsonl(material_path)
    volume_paths = _read_jsonl(path / "volume_design_paths.jsonl")
    author_craft = _read_jsonl(path / "author_craft_registry.jsonl")
    return {
        "path": str(path),
        "key": path.name,
        "manifest": _read_json(path / "aggregate_manifest.json"),
        "grammar": grammar,
        "mechanisms": mechanisms,
        "materials": materials,
        "author_craft": author_craft,
        "anti_copy": _read_json(path / "anti_copy_rules.json"),
        "book_designs": _read_jsonl(path / "book_design_registry.jsonl"),
        "volume_paths": volume_paths,
        # Raw registries above remain available for audit.  These are the only
        # fields permitted to cross into planner/compiler consumers.
        "prompt_safe_grammar": {
            "chapter_change_vectors": _prompt_safe_grammar_items(
                grammar, "chapter_change_vectors", limit=10
            ),
            "forbidden_defaults": _string_items(
                grammar.get("forbidden_defaults"), limit=8
            ),
        },
        "prompt_safe_mechanisms": _prompt_reusable_rows(mechanisms),
        "prompt_safe_materials": _prompt_reusable_rows(materials),
        "prompt_safe_volume_paths": _prompt_reusable_rows(
            _usable_volume_paths(volume_paths, limit=3)
        ),
        "prompt_safe_craft": _abstract_craft_rows(author_craft),
    }


def _verified_source_receipt(
    *, aggregate_dir: Path, manifest: dict[str, Any], repo_root: Path
) -> dict[str, Any] | None:
    """Return a provenance receipt only for source packages we can verify locally.

    Aggregate ``source_count`` is metadata supplied by the distiller and is not
    evidence by itself.  In particular, anonymous ids in an aggregate without a
    corresponding source package must never become prompt authority.  The
    package manifest is already the privacy-preserving receipt used by the
    distillation pipeline (id + content hash, without title or raw text).
    """

    key = str(manifest.get("aggregate_key") or aggregate_dir.name).strip()
    if not key or key == "distillation-generic":
        return None
    try:
        source_count = int(manifest.get("source_count") or 0)
    except (TypeError, ValueError):
        return None
    source_ids = [str(item).strip() for item in _as_list(manifest.get("source_ids"))]
    if source_count <= 0 or len(source_ids) != source_count:
        return None
    if len(set(source_ids)) != len(source_ids) or any(
        not re.fullmatch(r"source-[A-Za-z0-9_-]+", item) for item in source_ids
    ):
        return None
    package_root = (repo_root / "data" / "distillation").resolve()
    packages: list[dict[str, str]] = []
    for source_id in source_ids:
        package_dir = package_root / source_id
        try:
            resolved_package_dir = package_dir.resolve(strict=True)
            resolved_package_dir.relative_to(package_root)
        except (OSError, ValueError):
            return None
        source_manifest_path = package_dir / "source_manifest.json"
        if not source_manifest_path.is_file():
            return None
        try:
            from bestseller.services.distillation_book_aggregator import (
                package_book_phase_complete,
            )

            if not package_book_phase_complete(package_dir):
                return None
        except Exception:
            # Trust is fail-closed if the package validator cannot run.
            return None
        source_manifest = _read_json(source_manifest_path)
        source_hash = str(source_manifest.get("source_hash_sha256") or "").strip()
        if (
            str(source_manifest.get("source_id") or "").strip() != source_id
            or not re.fullmatch(r"[0-9a-fA-F]{64}", source_hash)
            or not str(source_manifest.get("rights_status") or "").strip()
        ):
            return None
        packages.append({"source_id": source_id, "source_hash_sha256": source_hash})
    return {
        "aggregate_key": key,
        "source_count": source_count,
        "source_ids": source_ids,
        "packages": packages,
        "verification": "local_source_manifest_v1",
    }


def get_verified_distilled_source_receipt(
    reference: Mapping[str, Any] | None,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any] | None:
    """Validate a loaded aggregate before any non-rendering consumer uses it."""

    ref = dict(reference) if isinstance(reference, Mapping) else {}
    manifest = cast(
        dict[str, Any],
        ref.get("manifest") if isinstance(ref.get("manifest"), dict) else {},
    )
    path_text = str(ref.get("path") or "").strip()
    if not path_text:
        return None
    return _verified_source_receipt(
        aggregate_dir=Path(path_text),
        manifest=manifest,
        repo_root=repo_root or _repo_root(),
    )


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
    grammar = _as_mapping(ref.get("grammar"))
    safe_grammar = _as_mapping(ref.get("prompt_safe_grammar"))
    manifest = _as_mapping(ref.get("manifest"))
    anti_copy = _as_mapping(ref.get("anti_copy"))
    source_count = int(
        manifest.get("source_count")
        or len(_as_list(anti_copy.get("source_ids")))
        or 0
    )
    key = str(ref.get("key") or grammar.get("key") or "").strip()
    phase_label = _PHASE_LABEL_ZH.get(phase, phase)
    lines = [
        f"## 小说设计参考(蒸馏聚合: {key or 'unknown'} / {phase_label})",
        f"来源: {source_count} 份经来源核验的匿名叙事资料。",
        "证据范围: 仅参考已观察章节；来源核验不证明市场表现、作品质量或全书完整性。",
        "使用规则: 只引用明确标记为可复用的抽象机制与写法控制; "
        "不得复用源书专名、具体桥段、独特组合或句式。",
    ]

    if phase in {"architecture", "story_design", "volume_plan", "chapter_outline"}:
        lines.extend(
            _render_items_zh(
                "章节变化向量",
                _string_items(safe_grammar.get("chapter_change_vectors"), limit=8),
            )
        )

    mechanisms = _phase_mechanisms(
        _dict_rows(ref.get("prompt_safe_mechanisms")),
        phase=phase,
        limit=max_mechanisms,
    )
    if mechanisms:
        lines.append("- 可借鉴的成熟机制路径:")
        for row in mechanisms:
            mid = str(row.get("mechanism_id") or row.get("slug") or "mechanism")
            lines.append(f"  - {mid}: {_truncate(row.get('summary'), 110)}")

    materials = _phase_materials(
        _dict_rows(ref.get("prompt_safe_materials")),
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
        volume_paths = _dict_rows(ref.get("prompt_safe_volume_paths"))[:3]
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
        craft_rows = _dict_rows(ref.get("prompt_safe_craft"))
        if craft_rows:
            lines.append("- 安全写法控制:")
            for row in craft_rows:
                if not isinstance(row, dict):
                    continue
                controls = []
                controls.extend(_string_items(row.get("sentence_rhythm"), limit=2))
                controls.extend(_string_items(row.get("dialogue_system"), limit=2))
                controls.extend(_string_items(row.get("description_strategy"), limit=2))
                controls.extend(_string_items(row.get("exposition_strategy"), limit=2))
                controls.extend(_string_items(row.get("hooking_and_transitions"), limit=2))
                if controls:
                    lines.append(f"  - {'; '.join(controls[:6])}")

    forbidden = _string_items(safe_grammar.get("forbidden_defaults"), limit=8)
    guardrails = forbidden
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
    grammar = _as_mapping(ref.get("grammar"))
    safe_grammar = _as_mapping(ref.get("prompt_safe_grammar"))
    manifest = _as_mapping(ref.get("manifest"))
    anti_copy = _as_mapping(ref.get("anti_copy"))
    source_count = int(
        manifest.get("source_count")
        or len(_as_list(anti_copy.get("source_ids")))
        or 0
    )
    key = str(ref.get("key") or grammar.get("key") or "").strip()
    phase_label = _PHASE_LABEL_EN.get(phase, phase)
    lines = [
        f"## Distilled Fiction Design Reference ({key or 'unknown'} / {phase_label})",
        f"Source base: {source_count} provenance-checked anonymous narrative sources.",
        "Evidence scope: observed chapters only. Provenance checks do not establish "
        "market performance, literary quality, or coverage of a complete work.",
        "Use rule: borrow only explicitly reusable abstract mechanisms and safe "
        "craft controls; do not reuse source names, exact "
        "plot chains, distinctive combinations, or sentence patterns.",
    ]
    if phase in {"architecture", "story_design", "volume_plan", "chapter_outline"}:
        lines.extend(
            _render_items_en(
                "Chapter change vectors",
                _string_items(safe_grammar.get("chapter_change_vectors"), limit=8),
            )
        )

    mechanisms = _phase_mechanisms(
        _dict_rows(ref.get("prompt_safe_mechanisms")),
        phase=phase,
        limit=max_mechanisms,
    )
    if mechanisms:
        lines.append("- Mature mechanism paths to adapt:")
        for row in mechanisms:
            mid = str(row.get("mechanism_id") or row.get("slug") or "mechanism")
            lines.append(f"  - {mid}: {_truncate(row.get('summary'), 110)}")

    materials = _phase_materials(
        _dict_rows(ref.get("prompt_safe_materials")),
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
        volume_paths = _dict_rows(ref.get("prompt_safe_volume_paths"))[:3]
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
        craft_rows = _dict_rows(ref.get("prompt_safe_craft"))
        if craft_rows:
            lines.append("- Safe craft controls:")
            for row in craft_rows:
                if not isinstance(row, dict):
                    continue
                controls = []
                controls.extend(_string_items(row.get("sentence_rhythm"), limit=2))
                controls.extend(_string_items(row.get("dialogue_system"), limit=2))
                controls.extend(_string_items(row.get("description_strategy"), limit=2))
                controls.extend(_string_items(row.get("exposition_strategy"), limit=2))
                controls.extend(_string_items(row.get("hooking_and_transitions"), limit=2))
                if controls:
                    lines.append(f"  - {'; '.join(controls[:6])}")

    guardrails = _string_items(safe_grammar.get("forbidden_defaults"), limit=8)
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
    # ``distillation-generic`` mixes narrative summaries from unrelated source
    # genres. Even anonymised, those summaries are still concrete story seeds:
    # a progression book previously received an English debtor-rise arc here
    # and carried it into planning. Generic aggregates may support offline
    # analysis, but they are never safe prompt material for a new book. Only a
    # category/grammar-specific aggregate can be rendered into planner prompts.
    if str(ref.get("key") or "").strip() == "distillation-generic":
        return ""
    # Do not let a generated aggregate claim authority merely because its
    # manifest says it has sources.  Only source packages with matching,
    # privacy-preserving manifests are eligible for generation prompts.
    receipt = get_verified_distilled_source_receipt(ref, repo_root=repo_root)
    if receipt is None:
        return ""
    ref = {**ref, "source_receipt": receipt}
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
    "get_verified_distilled_source_receipt",
    "load_distilled_design_reference",
    "render_all_distilled_design_reference_blocks",
    "render_distilled_design_reference_block",
]
