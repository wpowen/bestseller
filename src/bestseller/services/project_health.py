from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ClueModel
from bestseller.services.hype_engine import HypeType
from bestseller.services.projects import get_project_by_slug
from bestseller.services.reader_power import analyze_golden_three, serialize_golden_three_report
from bestseller.services.revealed_ledger import build_revealed_ledger
from bestseller.services.setup_payoff_tracker import analyze_setup_payoff
from bestseller.services.truth_version import (
    get_truth_materialization_statuses,
    truth_state_from_project,
)
from bestseller.settings import AppSettings


def _serialize_truth_status(status: Any) -> dict[str, Any]:
    return {
        "component": status.component,
        "workflow_type": status.workflow_type,
        "status": status.status,
        "required_truth_version": status.required_truth_version,
        "materialized_truth_version": status.materialized_truth_version,
        "materialized_at": status.materialized_at,
        "workflow_run_id": str(status.workflow_run_id) if status.workflow_run_id else None,
        "detail": status.detail,
    }


async def _load_setup_payoff_inputs(
    session: AsyncSession,
    *,
    project_id: Any,
) -> tuple[list[tuple[int, str]], list[tuple[int, HypeType | None]]]:
    rows = (
        await session.execute(
            select(
                ChapterModel.chapter_number,
                ChapterDraftVersionModel.content_md,
                ChapterModel.hype_type,
            )
            .join(
                ChapterDraftVersionModel,
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            )
            .where(
                ChapterModel.project_id == project_id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterModel.chapter_number.asc())
        )
    ).all()

    chapter_texts: list[tuple[int, str]] = []
    chapter_hype: list[tuple[int, HypeType | None]] = []
    for chapter_number, content_md, hype_type in rows:
        text = str(content_md or "").strip()
        if not text:
            continue
        chapter_texts.append((int(chapter_number), text))
        try:
            hype_enum = HypeType(str(hype_type)) if hype_type else None
        except ValueError:
            hype_enum = None
        chapter_hype.append((int(chapter_number), hype_enum))
    return chapter_texts, chapter_hype


async def build_project_health_report(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
) -> dict[str, Any]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    truth_state = truth_state_from_project(project)
    truth_statuses = await get_truth_materialization_statuses(session, project)
    stale_truth = [
        _serialize_truth_status(status)
        for status in truth_statuses
        if status.status != "fresh"
    ]

    latest_chapter = await session.scalar(
        select(ChapterModel.chapter_number)
        .where(ChapterModel.project_id == project.id)
        .order_by(ChapterModel.chapter_number.desc())
        .limit(1)
    )
    latest_chapter_number = int(latest_chapter or 0)

    overdue_clue_rows = list(
        await session.scalars(
            select(ClueModel)
            .where(
                ClueModel.project_id == project.id,
                ClueModel.status.in_(("planted", "active")),
                ClueModel.actual_paid_off_chapter_number.is_(None),
                ClueModel.expected_payoff_by_chapter_number.is_not(None),
                ClueModel.expected_payoff_by_chapter_number <= latest_chapter_number,
            )
            .order_by(
                ClueModel.expected_payoff_by_chapter_number.asc(),
                ClueModel.planted_in_chapter_number.asc().nullsfirst(),
            )
        )
    )

    ledger = await build_revealed_ledger(
        session,
        project.id,
        up_to_chapter=latest_chapter_number or None,
    )
    overused_hooks = [
        {
            "hook_type": item.hook_type,
            "total_count": item.total_count,
            "recent_count": item.recent_count,
            "recent_chapters": list(item.recent_chapters),
        }
        for item in ledger.overused_hooks()
    ]

    chapter_texts, chapter_hype = await _load_setup_payoff_inputs(
        session,
        project_id=project.id,
    )
    setup_report = analyze_setup_payoff(
        chapter_texts=tuple(chapter_texts),
        chapter_hype=tuple(chapter_hype),
        language=project.language,
    )
    setup_payoff_debts = [
        {
            "setup_chapter": debt.setup_chapter,
            "window_end_chapter": debt.window_end_chapter,
            "matched_keywords": list(debt.matched_keywords),
        }
        for debt in setup_report.debts
    ]
    if getattr(settings.pipeline, "enable_golden_three_health", True):
        golden_three_report = serialize_golden_three_report(
            analyze_golden_three(
                chapter_texts=tuple(chapter_texts),
                chapter_hype=tuple(chapter_hype),
                language=project.language,
                min_hype_chapters=getattr(
                    settings.pipeline,
                    "golden_three_min_hype_chapters",
                    2,
                ),
                min_ending_hook_chapters=getattr(
                    settings.pipeline,
                    "golden_three_min_ending_hook_chapters",
                    2,
                ),
            )
        )
    else:
        golden_three_report = {"enabled": False}

    return {
        "project_id": str(project.id),
        "project_slug": project.slug,
        "title": project.title,
        "truth_version": truth_state.version,
        "truth_updated_at": truth_state.updated_at,
        "truth_last_changed_artifact_type": truth_state.last_changed_artifact_type,
        "stale_truth_components": stale_truth,
        "latest_chapter_number": latest_chapter_number,
        "overdue_clues": [
            {
                "clue_code": clue.clue_code,
                "label": clue.label,
                "status": clue.status,
                "planted_in_chapter_number": clue.planted_in_chapter_number,
                "expected_payoff_by_chapter_number": clue.expected_payoff_by_chapter_number,
            }
            for clue in overdue_clue_rows[:20]
        ],
        "overused_hooks": overused_hooks,
        "setup_payoff_debts": setup_payoff_debts,
        "golden_three": golden_three_report,
        "query_broker": {
            "active_query_enabled": bool(
                getattr(settings.pipeline, "enable_story_query_brief", False)
            ),
            "truth_guard_enabled": bool(
                getattr(settings.pipeline, "enable_truth_version_guard", True)
            ),
        },
    }


def _materialization_result_payload(result: Any) -> dict[str, Any]:
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        if isinstance(payload, dict):
            return payload
    payload: dict[str, Any] = {}
    for attr in (
        "workflow_run_id",
        "project_id",
        "batch_name",
        "chapters_created",
        "scenes_created",
        "characters_upserted",
        "plot_arc_count",
        "arc_beat_count",
        "node_count",
    ):
        if hasattr(result, attr):
            value = getattr(result, attr)
            payload[attr] = str(value) if attr.endswith("_id") else value
    return payload


async def _materialize_truth_component(
    session: AsyncSession,
    project_slug: str,
    component: str,
    *,
    requested_by: str,
) -> dict[str, Any]:
    from bestseller.services.workflows import (
        materialize_latest_chapter_outline_batch,
        materialize_latest_narrative_graph,
        materialize_latest_story_bible,
    )

    if component == "story_bible":
        result = await materialize_latest_story_bible(
            session,
            project_slug,
            requested_by=requested_by,
        )
    elif component == "chapter_outline":
        result = await materialize_latest_chapter_outline_batch(
            session,
            project_slug,
            requested_by=requested_by,
        )
    elif component == "narrative_graph":
        result = await materialize_latest_narrative_graph(
            session,
            project_slug,
            requested_by=requested_by,
        )
    else:
        raise ValueError(f"Unsupported truth materialization component: {component}")
    return _materialization_result_payload(result)


async def repair_project_health(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    requested_by: str = "system",
    dry_run: bool = True,
    materialize_truth: bool = True,
) -> dict[str, Any]:
    before = await build_project_health_report(session, settings, project_slug)
    stale_components = [
        item["component"]
        for item in before.get("stale_truth_components", [])
        if isinstance(item, dict) and item.get("component")
    ]
    ordered_components = [
        component
        for component in ("story_bible", "chapter_outline", "narrative_graph")
        if component in set(stale_components)
    ]

    actions: list[dict[str, Any]] = []
    if materialize_truth:
        for component in ordered_components:
            action = {
                "action": "materialize_truth_component",
                "component": component,
                "status": "planned" if dry_run else "running",
            }
            if not dry_run:
                action["result"] = await _materialize_truth_component(
                    session,
                    project_slug,
                    component,
                    requested_by=requested_by,
                )
                action["status"] = "completed"
            actions.append(action)

    if before.get("overdue_clues"):
        actions.append(
            {
                "action": "review_overdue_clues",
                "status": "manual_required",
                "count": len(before["overdue_clues"]),
            }
        )
    if before.get("setup_payoff_debts"):
        actions.append(
            {
                "action": "repair_setup_payoff_debt",
                "status": "manual_or_rewrite_required",
                "count": len(before["setup_payoff_debts"]),
            }
        )
    golden_three = before.get("golden_three")
    if isinstance(golden_three, dict) and golden_three.get("issue_codes"):
        actions.append(
            {
                "action": "strengthen_golden_three",
                "status": "manual_or_rewrite_required",
                "issue_codes": list(golden_three.get("issue_codes") or []),
            }
        )

    after = (
        before
        if dry_run
        else await build_project_health_report(session, settings, project_slug)
    )
    return {
        "project_slug": project_slug,
        "dry_run": dry_run,
        "materialize_truth": materialize_truth,
        "actions": actions,
        "before": before,
        "after": after,
    }


__all__ = ["build_project_health_report", "repair_project_health"]
