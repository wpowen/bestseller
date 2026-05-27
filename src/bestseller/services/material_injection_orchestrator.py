"""Render chapter-scoped material blocks under a shared token budget."""
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


@dataclass(frozen=True)
class MaterialBlock:
    key: str
    title: str
    priority: int
    token_budget: int
    content: str


DEFAULT_BUDGETS: tuple[tuple[str, str, int, int], ...] = (
    ("required_rules", "本章必演规则", 1, 600),
    ("required_reveals", "本章必揭/必铺揭示", 2, 600),
    ("required_evidence", "本章必呈证据", 3, 400),
    ("character_state_promises", "本章必维护角色状态", 4, 400),
    ("active_rules", "已激活规则", 5, 500),
    ("historical_clues", "历史线索", 6, 400),
    ("kernels", "叙事内核", 7, 600),
    ("cultural_archetypes", "文化原型", 8, 200),
    ("tease_reveals", "待铺垫揭示", 9, 200),
    ("signature_audit", "截图段要求", 10, 100),
    ("anti_cliche", "反套路约束", 11, 200),
    ("reference_corpora", "风格参照", 12, 200),
)


def render_material_injection_blocks(
    project_dir: Path,
    *,
    chapter_number: int,
    chapter_position: str | None = None,
    prompt_pack_key: str | None = None,
    total_token_budget: int = 4000,
) -> str:
    """Return a compact prompt block assembled from all available material loaders."""

    blocks = collect_material_blocks(
        project_dir,
        chapter_number=chapter_number,
        chapter_position=chapter_position,
        prompt_pack_key=prompt_pack_key,
        total_token_budget=total_token_budget,
    )
    if not blocks:
        return ""
    lines = ["=== Material obligation packet ==="]
    for block in blocks:
        if not block.content.strip():
            continue
        lines.append(f"\n## {block.title} [{block.key}; budget={block.token_budget}]")
        lines.append(block.content.strip())
    return "\n".join(lines).strip()


def collect_material_blocks(
    project_dir: Path,
    *,
    chapter_number: int,
    chapter_position: str | None = None,
    prompt_pack_key: str | None = None,
    total_token_budget: int = 4000,
) -> tuple[MaterialBlock, ...]:
    """Collect prioritized material blocks and scale budgets to ``total_token_budget``."""

    root = project_dir.resolve()
    raw_blocks = {
        "required_rules": _required_rules(root, chapter_number),
        "required_reveals": _reveals(root, chapter_number, mode="land"),
        "required_evidence": _volume_milestone_values(root, chapter_number, "required_evidence"),
        "character_state_promises": _volume_milestone_values(
            root, chapter_number, "character_state_promises"
        ),
        "active_rules": _active_rules(root, chapter_number),
        "historical_clues": _historical_clues(root),
        "kernels": _kernel_snippets(root),
        "cultural_archetypes": _cultural_archetypes(prompt_pack_key),
        "tease_reveals": _reveals(root, chapter_number, mode="tease"),
        "signature_audit": _signature_audit(root, chapter_position),
        "anti_cliche": _anti_cliche_patterns(root, chapter_number),
        "reference_corpora": _reference_corpora(prompt_pack_key),
    }
    scale = min(1.0, max(total_token_budget, 0) / sum(item[3] for item in DEFAULT_BUDGETS))
    blocks: list[MaterialBlock] = []
    for key, title, priority, base_budget in DEFAULT_BUDGETS:
        budget = max(40, int(base_budget * scale)) if total_token_budget > 0 else base_budget
        content = _truncate_to_budget(raw_blocks.get(key, ""), budget)
        if content.strip():
            blocks.append(MaterialBlock(key, title, priority, budget, content))
    return tuple(sorted(blocks, key=lambda block: block.priority))


def _required_rules(project_dir: Path, chapter_number: int) -> str:
    rows = _rule_rows(project_dir)
    selected = [
        row
        for row in rows
        if _chapter_range_contains(row.get("first_seen", ""), chapter_number)
        or _chapter_range_contains(row.get("future_use", ""), chapter_number)
    ]
    return _render_rule_rows(selected[:4])


def _active_rules(project_dir: Path, chapter_number: int) -> str:
    selected = [
        row
        for row in _rule_rows(project_dir)
        if _first_chapter_number(row.get("first_seen", "")) <= chapter_number
    ]
    return _render_rule_rows(selected[-8:])


def _rule_rows(project_dir: Path) -> list[dict[str, str]]:
    path = project_dir / "story-bible" / "rule-ledger.md"
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("| R-"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 7:
            continue
        rows.append(
            {
                "id": cells[0],
                "rule": cells[1],
                "first_seen": cells[2],
                "visible_effect": cells[3],
                "solution": cells[4],
                "cost": cells[5],
                "future_use": cells[6],
            }
        )
    return rows


def _render_rule_rows(rows: list[dict[str, str]]) -> str:
    return "\n".join(
        (
            f"- {row['id']} {row['rule']}；可见效果：{row['visible_effect']}；"
            f"破局：{row['solution']}；代价：{row['cost']}"
        )
        for row in rows
    )


def _reveals(project_dir: Path, chapter_number: int, *, mode: str) -> str:
    payload = _yaml(project_dir / "story-bible" / "reveal-schedule.yaml")
    reveals = payload.get("reveals") if isinstance(payload, dict) else None
    if not isinstance(reveals, list):
        return ""
    selected: list[str] = []
    for reveal in reveals:
        if not isinstance(reveal, dict):
            continue
        earliest = _int(reveal.get("earliest_chapter"), 0)
        tokens = ", ".join(str(token) for token in reveal.get("tokens") or ())
        if mode == "land" and earliest == chapter_number:
            selected.append(f"- LAND {reveal.get('id')}: {tokens}")
        elif mode == "tease" and chapter_number < earliest <= chapter_number + 3:
            selected.append(f"- TEASE {reveal.get('id')} by ch{earliest}: {tokens}")
    return "\n".join(selected)


def _volume_milestone_values(project_dir: Path, chapter_number: int, key: str) -> str:
    for filename in ("volume-plan-v2.yaml", "volume-plan-v2.yml", "volume-plan-v2.json"):
        path = project_dir / "story-bible" / filename
        if not path.exists():
            continue
        payload = _yaml(path)
        milestones = payload.get("milestones") if isinstance(payload, dict) else None
        if not isinstance(milestones, list):
            continue
        values: list[str] = []
        for milestone in milestones:
            if not isinstance(milestone, dict):
                continue
            if _int(milestone.get("chapter") or milestone.get("chapter_no"), -1) != chapter_number:
                continue
            raw = milestone.get(key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw if str(item).strip())
            elif raw:
                values.append(str(raw))
        return "\n".join(f"- {value}" for value in values)
    return ""


def _historical_clues(project_dir: Path) -> str:
    path = project_dir / "story-bible" / "clue-ledger.md"
    if not path.exists():
        return ""
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip().startswith(("-", "| C-", "| CLUE"))
    ]
    return "\n".join(lines[:12])


def _kernel_snippets(project_dir: Path) -> str:
    base = project_dir / "story-bible" / "kernels"
    if not base.exists():
        return ""
    snippets: list[str] = []
    preferred = ("mystery", "ensemble", "lineage", "cultural")
    paths = sorted(
        base.glob("*.json"),
        key=lambda path: (not any(token in path.stem for token in preferred), path.name),
    )
    for path in paths[:6]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        snippets.append(f"- {path.stem}: {text[:420].strip()}")
    return "\n".join(snippets)


def _cultural_archetypes(prompt_pack_key: str | None) -> str:
    keys = ["urban_modern", "classical_chinese"]
    root = Path("config") / "cultural_archetypes"
    lines: list[str] = []
    for key in keys:
        path = root / f"{key}.yaml"
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="ignore")[:500].strip()
            lines.append(f"- {key}: {text}")
    if prompt_pack_key:
        lines.append(f"- prompt_pack: {prompt_pack_key}")
    return "\n".join(lines)


def _signature_audit(project_dir: Path, chapter_position: str | None) -> str:
    candidates = [
        project_dir / "story-bible" / "chapter_signature_audit.md",
        project_dir / "story-bible" / "chapter-signature-audit.md",
        project_dir / "obsidian-vault" / "物料库" / "chapter_signature_audit.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")[:800]
    pos = chapter_position or "current"
    return f"- {pos}: 必须至少落地 1 个截图段类型（金句/神描写/神场景/神反转/神细节/反应放大）。"


def _anti_cliche_patterns(project_dir: Path, chapter_number: int) -> str:
    candidates = [
        project_dir / "obsidian-vault" / "物料库" / "全局物料维度.md",
        project_dir / "story-bible" / "anti-cliche-patterns.md",
    ]
    lines: list[str] = []
    for path in candidates:
        if path.exists():
            lines.extend(
                line.strip()
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if "反套路" in line or "anti" in line.lower() or line.strip().startswith("-")
            )
    if not lines:
        return ""
    start = max((chapter_number - 1) % len(lines), 0)
    picked = [lines[start], lines[(start + 1) % len(lines)]] if len(lines) > 1 else lines
    return "\n".join(f"- {line.lstrip('- ')}" for line in picked)


def _reference_corpora(prompt_pack_key: str | None) -> str:
    keys = [prompt_pack_key, "suspense-mystery"]
    root = Path("config") / "reference_corpora"
    for key in keys:
        if not key:
            continue
        path = root / f"{key}.yaml"
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")[:700]
    return ""


def _truncate_to_budget(text: str, token_budget: int) -> str:
    if not text:
        return ""
    char_budget = max(token_budget * 4, 120)
    return text if len(text) <= char_budget else text[: char_budget - 1].rstrip() + "…"


def _chapter_range_contains(text: str, chapter_number: int) -> bool:
    numbers = [int(item) for item in re.findall(r"\d+", str(text))]
    if not numbers:
        return False
    if len(numbers) >= 2:
        return min(numbers[:2]) <= chapter_number <= max(numbers[:2])
    return numbers[0] == chapter_number


def _first_chapter_number(text: str) -> int:
    numbers = re.findall(r"\d+", str(text))
    return int(numbers[0]) if numbers else 0


def _yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["MaterialBlock", "collect_material_blocks", "render_material_injection_blocks"]
