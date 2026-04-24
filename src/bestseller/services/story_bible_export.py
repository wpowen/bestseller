"""Materialize a project's story bible to disk for out-of-band review.

Why this exists
---------------
External reviewers (人审 / ChatGPT 第二意见) need a single, plain-text
view of "what the framework currently believes about this book": who the
characters are, what the world rules say, what the volume plan promises,
which clues are in flight, etc. The Web UI can render this, but a flat
markdown export can be diffed in git, attached to a chat, or scanned in
seconds without touching the API.

The DB remains canonical; this is a one-way snapshot. Nothing here writes
back, and the writer pipeline never reads from these files. Run it ad-hoc
from ``scripts/export_story_bible.py``; do not wire it into ``pipelines.py``.

Output layout
-------------
::

    output/<slug>/story-bible/
        characters.md          # CAST_SPEC + latest character snapshots
        world.md               # WORLD_SPEC rules / power system / locations / factions
        premise.md             # BOOK_SPEC logline / protagonist / themes / promise
        volume-plan.md         # VOLUME_PLAN entries + per-volume current frontier
        plot-arcs.md           # deferred reveals, expansion gates, foreshadowing
        writing-profile.md     # POV / tone / banned words / chapter discipline
        raw/
            book_spec.json
            world_spec.json
            cast_spec.json
            volume_plan.json
            volume_outline_v{N}.json   # per-volume latest VOLUME_CHAPTER_OUTLINE

Format follows ``output/ai-generated/jin-gu-deng-tian-lu/story-bible/`` —
short headings, dense markdown tables for tabular data, bullet lists for
narrative-style fields. Missing data degrades to a "(尚未生成)" placeholder
rather than crashing, so partially-planned projects still produce
something useful.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.story_bible import StoryBibleOverview
from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services.inspection import (
    build_story_bible_overview,
    get_planning_artifact_detail,
)
from bestseller.services.projects import get_project_by_slug


_PLACEHOLDER = "_(尚未生成)_"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def export_story_bible_to_disk(
    session: AsyncSession,
    project_slug: str,
    output_root: Path,
) -> Path:
    """Write the 6 markdown files + raw artifact JSON dumps for ``project_slug``.

    Returns the destination directory ``output_root/<slug>/story-bible``.
    Caller is responsible for ``await session.commit()`` semantics — this
    function is read-only.
    """
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    overview = await build_story_bible_overview(session, project_slug)

    book_spec = await _load_artifact(session, project_slug, ArtifactType.BOOK_SPEC)
    world_spec = await _load_artifact(session, project_slug, ArtifactType.WORLD_SPEC)
    cast_spec = await _load_artifact(session, project_slug, ArtifactType.CAST_SPEC)
    volume_plan = await _load_artifact(session, project_slug, ArtifactType.VOLUME_PLAN)
    premise = await _load_artifact(session, project_slug, ArtifactType.PREMISE)
    volume_outlines = await _load_latest_per_volume(
        session,
        project.id,
        ArtifactType.VOLUME_CHAPTER_OUTLINE,
    )
    cast_expansions = await _load_latest_per_volume(
        session,
        project.id,
        ArtifactType.VOLUME_CAST_EXPANSION,
    )

    out_dir = output_root / project_slug / "story-bible"
    raw_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    title = overview.title

    (out_dir / "premise.md").write_text(
        _render_premise(title, book_spec, premise),
        encoding="utf-8",
    )
    (out_dir / "world.md").write_text(
        _render_world(title, overview, world_spec),
        encoding="utf-8",
    )
    (out_dir / "characters.md").write_text(
        _render_characters(title, overview, cast_spec, cast_expansions),
        encoding="utf-8",
    )
    (out_dir / "volume-plan.md").write_text(
        _render_volume_plan(title, overview, volume_plan, volume_outlines),
        encoding="utf-8",
    )
    (out_dir / "plot-arcs.md").write_text(
        _render_plot_arcs(title, overview, book_spec, volume_plan),
        encoding="utf-8",
    )
    (out_dir / "writing-profile.md").write_text(
        _render_writing_profile(title, project, book_spec),
        encoding="utf-8",
    )

    _dump_json(raw_dir / "book_spec.json", book_spec)
    _dump_json(raw_dir / "world_spec.json", world_spec)
    _dump_json(raw_dir / "cast_spec.json", cast_spec)
    _dump_json(raw_dir / "volume_plan.json", volume_plan)
    _dump_json(raw_dir / "premise.json", premise)
    for volume_no, content in sorted(volume_outlines.items()):
        _dump_json(raw_dir / f"volume_outline_v{volume_no}.json", content)
    for volume_no, content in sorted(cast_expansions.items()):
        _dump_json(raw_dir / f"cast_expansion_v{volume_no}.json", content)

    return out_dir


# ---------------------------------------------------------------------------
# Artifact loaders
# ---------------------------------------------------------------------------

async def _load_artifact(
    session: AsyncSession,
    project_slug: str,
    artifact_type: ArtifactType,
) -> dict[str, Any] | None:
    detail = await get_planning_artifact_detail(session, project_slug, artifact_type)
    if detail is None or not isinstance(detail.content, dict):
        return None
    return dict(detail.content)


async def _load_latest_per_volume(
    session: AsyncSession,
    project_id: Any,
    artifact_type: ArtifactType,
) -> dict[int, dict[str, Any]]:
    """Return ``{volume_number: latest content}`` for per-volume artifacts.

    Volume artifacts are scoped by ``scope_ref_id`` (the ``volumes.id`` UUID).
    We pick the highest ``version_no`` per scope and read the volume number
    from the content payload itself when present, falling back to the join
    on ``volumes`` if needed.
    """
    rows = list(
        await session.scalars(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type == artifact_type.value,
            )
            .order_by(
                PlanningArtifactVersionModel.scope_ref_id.asc(),
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
        )
    )

    seen_scope: set[Any] = set()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.scope_ref_id in seen_scope:
            continue
        seen_scope.add(row.scope_ref_id)
        content = row.content if isinstance(row.content, dict) else {}
        volume_no = _extract_volume_number(content)
        if volume_no is None:
            continue
        out[volume_no] = dict(content)
    return out


def _extract_volume_number(content: dict[str, Any]) -> int | None:
    for key in ("volume_number", "volume_no", "volume"):
        value = content.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


# ---------------------------------------------------------------------------
# Renderers — one per output file
# ---------------------------------------------------------------------------

def _render_premise(
    title: str,
    book_spec: dict[str, Any] | None,
    premise: dict[str, Any] | None,
) -> str:
    src = book_spec or premise or {}

    logline = _first_str(src, ["logline", "premise", "core_premise", "summary"])
    protagonist = src.get("protagonist") if isinstance(src.get("protagonist"), dict) else {}
    themes = _coerce_list(src.get("themes") or src.get("theme") or [])
    reader_promise = _coerce_list(
        src.get("reader_promise")
        or src.get("promises")
        or src.get("reader_promises")
        or []
    )
    stakes = src.get("stakes") if isinstance(src.get("stakes"), (dict, list)) else None

    lines: list[str] = [f"# Premise — {title}", ""]

    lines.append("## Logline")
    lines.append(logline or _PLACEHOLDER)
    lines.append("")

    lines.append("## Protagonist")
    if protagonist:
        lines.extend(_kv_table(protagonist, prefer_keys=[
            "name", "archetype", "external_goal", "internal_need",
            "flaw", "strength", "fear", "secret",
        ]))
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    lines.append("## Themes")
    if themes:
        for idx, item in enumerate(themes, start=1):
            lines.append(f"{idx}. {item}")
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    lines.append("## Reader Promise")
    if reader_promise:
        for item in reader_promise:
            lines.append(f"- {item}")
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    lines.append("## Stakes")
    if isinstance(stakes, dict) and stakes:
        lines.append("| 层级 | 内容 |")
        lines.append("|------|------|")
        for k, v in stakes.items():
            lines.append(f"| {k} | {_inline(v)} |")
    elif isinstance(stakes, list) and stakes:
        for item in stakes:
            lines.append(f"- {_inline(item)}")
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    return "\n".join(lines)


def _render_world(
    title: str,
    overview: StoryBibleOverview,
    world_spec: dict[str, Any] | None,
) -> str:
    spec = world_spec or {}
    backbone = overview.world_backbone
    rules = overview.world_rules
    locations = overview.locations
    factions = overview.factions
    power_system = spec.get("power_system") if isinstance(spec.get("power_system"), dict) else {}

    lines: list[str] = [f"# World Spec — {title}", ""]

    lines.append("## 世界前提")
    premise_text = (
        backbone.world_frame
        if backbone is not None and backbone.world_frame
        else _first_str(spec, ["world_premise", "world_name"])
    )
    lines.append(premise_text or _PLACEHOLDER)
    lines.append("")

    lines.append("## World Rules")
    if rules:
        lines.append("| 名称 | 描述 | 故事后果 | 可利用性 |")
        lines.append("|------|------|---------|---------|")
        for rule in rules:
            lines.append(
                f"| {_inline(rule.name)} | {_inline(rule.description)} | "
                f"{_inline(rule.story_consequence or '')} | "
                f"{_inline(rule.exploitation_potential or '')} |"
            )
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    lines.append("## Power System")
    tiers = power_system.get("tiers") if isinstance(power_system.get("tiers"), list) else []
    if tiers:
        lines.append("| 等级 | 名称 |")
        lines.append("|------|------|")
        for idx, tier in enumerate(tiers, start=1):
            lines.append(f"| {idx} | {_inline(tier)} |")
        for k in ("acquisition_method", "hard_limits", "protagonist_starting_tier"):
            v = power_system.get(k)
            if v:
                lines.append("")
                lines.append(f"**{k}**: {_inline(v)}")
        aliases = power_system.get("tier_aliases")
        if isinstance(aliases, dict) and aliases:
            lines.append("")
            lines.append("### Tier Aliases (扫描器使用)")
            lines.append("| 别名 | 规范 |")
            lines.append("|------|------|")
            for alias, canonical in aliases.items():
                lines.append(f"| {_inline(alias)} | {_inline(canonical)} |")
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    lines.append("## Locations")
    if locations:
        for loc in locations:
            atmos = f" — {loc.atmosphere}" if loc.atmosphere else ""
            lines.append(f"- **{loc.name}** ({loc.location_type}){atmos}")
            if loc.story_role:
                lines.append(f"  - 作用：{loc.story_role}")
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    lines.append("## Factions")
    if factions:
        for fac in factions:
            lines.append(f"- **{fac.name}**")
            for label, value in (
                ("目标", fac.goal),
                ("手段", fac.method),
                ("与主角关系", fac.relationship_to_protagonist),
                ("内部冲突", fac.internal_conflict),
            ):
                if value:
                    lines.append(f"  - {label}：{value}")
    else:
        lines.append(_PLACEHOLDER)
    lines.append("")

    if backbone is not None:
        lines.append("## World Backbone")
        lines.extend(_kv_table(
            {
                "core_promise": backbone.core_promise,
                "mainline_drive": backbone.mainline_drive,
                "protagonist_destiny": backbone.protagonist_destiny,
                "antagonist_axis": backbone.antagonist_axis,
                "thematic_melody": backbone.thematic_melody,
            },
            prefer_keys=[
                "core_promise", "mainline_drive", "protagonist_destiny",
                "antagonist_axis", "thematic_melody",
            ],
        ))
        if backbone.invariant_elements:
            lines.append("")
            lines.append("**不变元素**：")
            for item in backbone.invariant_elements:
                lines.append(f"- {item}")
        if backbone.stable_unknowns:
            lines.append("")
            lines.append("**稳定未知**：")
            for item in backbone.stable_unknowns:
                lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def _render_characters(
    title: str,
    overview: StoryBibleOverview,
    cast_spec: dict[str, Any] | None,
    cast_expansions: dict[int, dict[str, Any]],
) -> str:
    chars = list(overview.characters)
    chars.sort(key=lambda c: (
        0 if c.role == "protagonist"
        else 1 if c.role == "antagonist"
        else 2,
        c.name,
    ))

    lines: list[str] = [f"# Cast Spec — {title}", ""]

    if not chars:
        lines.append(_PLACEHOLDER)
        return "\n".join(lines)

    for char in chars:
        header = "主角" if char.role == "protagonist" else (
            "反派" if char.role == "antagonist" else char.role
        )
        lines.append(f"## {header}：{char.name}")
        lines.append("")
        rows = {
            "role": char.role,
            "goal": char.goal,
            "fear": char.fear,
            "flaw": char.flaw,
            "secret": char.secret,
            "arc_trajectory": char.arc_trajectory,
            "arc_state": char.arc_state,
            "power_tier": char.power_tier,
            "is_pov_character": "yes" if char.is_pov_character else "no",
        }
        latest = char.latest_state
        if latest is not None:
            rows["latest_chapter"] = (
                f"ch{latest.chapter_number}"
                + (f" / scene {latest.scene_number}" if latest.scene_number else "")
            )
            if latest.emotional_state:
                rows["latest_emotional_state"] = latest.emotional_state
            if latest.physical_state:
                rows["latest_physical_state"] = latest.physical_state
        lines.extend(_kv_table(rows))
        if char.knowledge_state:
            lines.append("")
            lines.append("**knowledge_state**:")
            for k in ("knows", "falsely_believes", "unaware_of"):
                items = char.knowledge_state.get(k) if isinstance(char.knowledge_state, dict) else None
                if isinstance(items, list) and items:
                    lines.append(f"- {k}:")
                    for it in items:
                        lines.append(f"  - {_inline(it)}")
        if char.voice_profile:
            lines.append("")
            lines.append("**voice_profile**:")
            for k, v in char.voice_profile.items():
                if v:
                    lines.append(f"- {k}: {_inline(v)}")
        if char.moral_framework:
            lines.append("")
            lines.append("**moral_framework**:")
            for k, v in char.moral_framework.items():
                if v:
                    lines.append(f"- {k}: {_inline(v)}")
        lines.append("")

    if overview.relationships:
        lines.append("## Relationships (DB)")
        lines.append("| A | B | 类型 | 强度 | 紧张点 |")
        lines.append("|---|---|------|------|--------|")
        for rel in overview.relationships:
            lines.append(
                f"| {_inline(rel.character_a)} | {_inline(rel.character_b)} | "
                f"{_inline(rel.relationship_type)} | {rel.strength:+.2f} | "
                f"{_inline(rel.tension_summary or '')} |"
            )
        lines.append("")

    if isinstance(cast_spec, dict):
        conflicts = cast_spec.get("conflict_map")
        if isinstance(conflicts, list) and conflicts:
            lines.append("## Conflict Map (CAST_SPEC)")
            lines.append("| A | B | 类型 | 触发条件 |")
            lines.append("|---|---|------|---------|")
            for c in conflicts:
                if not isinstance(c, dict):
                    continue
                lines.append(
                    f"| {_inline(c.get('character_a', ''))} | "
                    f"{_inline(c.get('character_b', ''))} | "
                    f"{_inline(c.get('conflict_type', ''))} | "
                    f"{_inline(c.get('trigger_condition', ''))} |"
                )
            lines.append("")

    if cast_expansions:
        lines.append("## Cast Expansions per Volume")
        for vol_no in sorted(cast_expansions):
            payload = cast_expansions[vol_no]
            new_cast = payload.get("new_characters") or payload.get("supporting_cast") or []
            if not isinstance(new_cast, list) or not new_cast:
                continue
            lines.append(f"### Volume {vol_no}")
            for entry in new_cast:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name") or entry.get("character_name") or "(未命名)"
                role = entry.get("role") or entry.get("function") or ""
                lines.append(f"- **{name}** — {_inline(role)}")
            lines.append("")

    return "\n".join(lines)


def _render_volume_plan(
    title: str,
    overview: StoryBibleOverview,
    volume_plan: dict[str, Any] | None,
    volume_outlines: dict[int, dict[str, Any]],
) -> str:
    lines: list[str] = [f"# Volume Plan — {title}", ""]

    entries: list[dict[str, Any]] = []
    if isinstance(volume_plan, dict):
        raw_volumes = volume_plan.get("volumes") or volume_plan.get("volume_plan") or []
        if isinstance(raw_volumes, list):
            entries = [v for v in raw_volumes if isinstance(v, dict)]

    if not entries and not overview.volume_frontiers:
        lines.append(_PLACEHOLDER)
        return "\n".join(lines)

    if entries:
        for entry in entries:
            num = entry.get("volume_number") or entry.get("volume_no") or "?"
            t = entry.get("volume_title") or entry.get("title") or ""
            lines.append(f"## Volume {num} · {t}")
            lines.append("")
            rows = {
                k: entry.get(k)
                for k in (
                    "chapter_count_target",
                    "word_count_target",
                    "volume_theme",
                    "volume_goal",
                    "volume_obstacle",
                    "volume_climax",
                    "reader_hook_to_next",
                )
                if entry.get(k)
            }
            opening = entry.get("opening_state")
            if isinstance(opening, dict):
                rows["opening_state"] = "; ".join(
                    f"{k}={v}" for k, v in opening.items() if v
                )
            resolution = entry.get("volume_resolution")
            if isinstance(resolution, dict):
                rows["volume_resolution"] = "; ".join(
                    f"{k}={v}" for k, v in resolution.items() if v is not None
                )
            lines.extend(_kv_table(rows))
            for label, key in (
                ("Key Reveals", "key_reveals"),
                ("Foreshadowing Planted", "foreshadowing_planted"),
                ("Foreshadowing Paid Off", "foreshadowing_paid_off"),
            ):
                items = entry.get(key)
                if isinstance(items, list) and items:
                    lines.append("")
                    lines.append(f"**{label}**:")
                    for it in items:
                        lines.append(f"- {_inline(it)}")
            lines.append("")

    if overview.volume_frontiers:
        lines.append("## Active Frontiers (DB)")
        lines.append(
            "| 卷 | 标题 | 起讫章 | 摘要 | 焦点 |"
        )
        lines.append("|----|------|--------|------|------|")
        for frontier in overview.volume_frontiers:
            end = frontier.end_chapter_number or "?"
            lines.append(
                f"| {frontier.volume_number} | {_inline(frontier.title)} | "
                f"ch{frontier.start_chapter_number}–{end} | "
                f"{_inline(frontier.frontier_summary)} | "
                f"{_inline(frontier.expansion_focus or '')} |"
            )
        lines.append("")

    if volume_outlines:
        lines.append("## Latest Chapter Outline per Volume")
        for vol_no in sorted(volume_outlines):
            payload = volume_outlines[vol_no]
            chapters = payload.get("chapters") or payload.get("chapter_outlines") or []
            if not isinstance(chapters, list) or not chapters:
                continue
            lines.append(f"### Volume {vol_no} — {len(chapters)} 章")
            for ch in chapters[:5]:
                if not isinstance(ch, dict):
                    continue
                ch_no = ch.get("chapter_number") or ch.get("chapter_no") or "?"
                ch_title = ch.get("title") or ch.get("chapter_title") or ""
                ch_hook = ch.get("hook") or ch.get("reader_hook") or ""
                lines.append(f"- ch{ch_no} · {_inline(ch_title)}")
                if ch_hook:
                    lines.append(f"  - hook: {_inline(ch_hook)}")
            if len(chapters) > 5:
                lines.append(f"- … (+{len(chapters) - 5} 章未展开，详见 raw/volume_outline_v{vol_no}.json)")
            lines.append("")

    return "\n".join(lines)


def _render_plot_arcs(
    title: str,
    overview: StoryBibleOverview,
    book_spec: dict[str, Any] | None,
    volume_plan: dict[str, Any] | None,
) -> str:
    lines: list[str] = [f"# Plot Arcs — {title}", ""]

    if isinstance(book_spec, dict):
        main_arcs = book_spec.get("plot_arcs") or book_spec.get("main_arcs") or []
        subplots = book_spec.get("subplots") or book_spec.get("sub_arcs") or []
        if isinstance(main_arcs, list) and main_arcs:
            lines.append("## 主线")
            for idx, arc in enumerate(main_arcs, start=1):
                lines.append(f"{idx}. {_inline(arc)}")
            lines.append("")
        if isinstance(subplots, list) and subplots:
            lines.append("## 副线")
            for sub in subplots:
                lines.append(f"- {_inline(sub)}")
            lines.append("")

    payoffs: list[dict[str, Any]] = []
    if isinstance(volume_plan, dict):
        for entry in volume_plan.get("volumes") or []:
            if not isinstance(entry, dict):
                continue
            num = entry.get("volume_number") or "?"
            for item in entry.get("foreshadowing_planted") or []:
                payoffs.append({"vol": num, "kind": "planted", "text": str(item)})
            for item in entry.get("foreshadowing_paid_off") or []:
                payoffs.append({"vol": num, "kind": "paid", "text": str(item)})
    if payoffs:
        lines.append("## Foreshadowing Trail (Volume Plan)")
        lines.append("| 卷 | 类型 | 内容 |")
        lines.append("|----|------|------|")
        for p in payoffs:
            lines.append(f"| {p['vol']} | {p['kind']} | {_inline(p['text'])} |")
        lines.append("")

    if overview.deferred_reveals:
        lines.append("## Deferred Reveals (DB)")
        lines.append("| code | 标签 | 类型 | 卷·章 | 状态 | 摘要 |")
        lines.append("|------|------|------|-------|------|------|")
        for reveal in overview.deferred_reveals:
            lines.append(
                f"| {reveal.reveal_code} | {_inline(reveal.label)} | "
                f"{_inline(reveal.category)} | v{reveal.reveal_volume_number}·"
                f"ch{reveal.reveal_chapter_number} | {reveal.status} | "
                f"{_inline(reveal.summary)} |"
            )
        lines.append("")

    if overview.expansion_gates:
        lines.append("## Expansion Gates (DB)")
        lines.append("| code | 标签 | 类型 | 解锁 v·ch | 状态 | 解锁内容 |")
        lines.append("|------|------|------|-----------|------|----------|")
        for gate in overview.expansion_gates:
            lines.append(
                f"| {gate.gate_code} | {_inline(gate.label)} | "
                f"{_inline(gate.gate_type)} | v{gate.unlock_volume_number}·"
                f"ch{gate.unlock_chapter_number} | {gate.status} | "
                f"{_inline(gate.unlocks_summary)} |"
            )
        lines.append("")

    if (
        not (isinstance(book_spec, dict) and (book_spec.get("plot_arcs") or book_spec.get("subplots")))
        and not overview.deferred_reveals
        and not overview.expansion_gates
        and not payoffs
    ):
        lines.append(_PLACEHOLDER)

    return "\n".join(lines)


def _render_writing_profile(
    title: str,
    project: Any,
    book_spec: dict[str, Any] | None,
) -> str:
    lines: list[str] = [f"# Writing Profile — {title}", ""]

    profile = (
        book_spec.get("writing_profile") if isinstance(book_spec, dict) else None
    ) or {}
    if not isinstance(profile, dict):
        profile = {}

    rows = {
        "POV": profile.get("pov") or profile.get("point_of_view"),
        "时态": profile.get("tense"),
        "基调": profile.get("tone"),
        "对白比例": profile.get("dialogue_ratio") or profile.get("dialogue_pct"),
        "章节平均字数": profile.get("avg_chapter_words")
            or profile.get("target_chapter_words"),
    }
    rows = {k: v for k, v in rows.items() if v}
    if rows:
        for k, v in rows.items():
            lines.append(f"- **{k}**: {_inline(v)}")
        lines.append("")

    banned = profile.get("banned_words") or profile.get("禁用词") or []
    if isinstance(banned, list) and banned:
        lines.append("## 禁用词")
        for word in banned:
            lines.append(f"- {_inline(word)}")
        lines.append("")

    discipline = profile.get("discipline") or profile.get("rules") or []
    if isinstance(discipline, list) and discipline:
        lines.append("## 写作纪律")
        for item in discipline:
            lines.append(f"- {_inline(item)}")
        lines.append("")

    invariants = getattr(project, "invariants_json", None) or {}
    if isinstance(invariants, dict) and invariants:
        lines.append("## Invariants (DB snapshot)")
        for k, v in invariants.items():
            if isinstance(v, (dict, list)):
                lines.append(f"- **{k}**: `{json.dumps(v, ensure_ascii=False)[:200]}`…")
            else:
                lines.append(f"- **{k}**: {_inline(v)}")
        lines.append("")

    if not rows and not banned and not discipline and not invariants:
        lines.append(_PLACEHOLDER)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _kv_table(rows: dict[str, Any], *, prefer_keys: list[str] | None = None) -> list[str]:
    items: list[tuple[str, Any]] = []
    seen: set[str] = set()
    if prefer_keys:
        for key in prefer_keys:
            if key in rows and rows[key] is not None and rows[key] != "":
                items.append((key, rows[key]))
                seen.add(key)
    for key, value in rows.items():
        if key in seen:
            continue
        if value is None or value == "":
            continue
        items.append((key, value))
    if not items:
        return [_PLACEHOLDER]
    out = ["| 字段 | 值 |", "|---|---|"]
    for key, value in items:
        out.append(f"| {key} | {_inline(value)} |")
    return out


def _first_str(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [v for v in value if v not in (None, "")]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _inline(value: Any) -> str:
    """Render a value as a single markdown-table-safe line."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return "; ".join(_inline(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        return "; ".join(f"{k}={_inline(v)}" for k, v in value.items() if v)
    text = str(value).replace("|", "\\|").replace("\n", " ").strip()
    return text


def _dump_json(path: Path, payload: Any) -> None:
    if payload is None:
        return
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
