"""P-3 (xianxia benchmark): scaffold text must not pose as plan data.

The deterministic fallback volume plan used to fill ``foreshadowing_planted``
/ ``foreshadowing_paid_off`` with INSTRUCTION sentences ("埋下一条必须在第N+1卷
继续发酵的未解变量。") and ``reader_hook_to_next`` with boilerplate ("眼前压力虽
然变形或后撤，但故事还不能停下来。"). Because ``_generate_structured_artifact``
merges fallback values into missing fields of accepted LLM payloads
(``_merge_planning_payload``), these sentences leaked into production
artifacts as if they were real plan content (zhaoshen-hr-v4 volume 1 shipped
the boilerplate hook verbatim). Honest emptiness beats fake content: empty
fields are caught by the volume-plan contract / foreshadowing gates, while
boilerplate sails through everything.
"""

from __future__ import annotations

from uuid import uuid4

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services

_INSTRUCTION_MARKERS = (
    "埋下一条必须在第",
    "回收至少一条前序铺垫",
    "Plant one unresolved variable",
    "Pay off at least one earlier setup",
)

_BOILERPLATE_HOOKS = (
    "眼前压力虽然变形或后撤，但故事还不能停下来。",
    "故事已经进入终局着陆阶段。",
    "The immediate pressure changes shape, but the story cannot settle yet.",
    "The story is ready for its final landing.",
)


def _build_project(language: str | None = None) -> ProjectModel:
    project = ProjectModel(
        slug="scaffold-leak",
        title="蚀漏砚",
        genre="仙侠",
        target_word_count=1_100_000,
        target_chapters=500,
        audience="男频",
        metadata_json={"language": language} if language else {},
    )
    project.id = uuid4()
    return project


def _fallback_plan(project: ProjectModel) -> list[dict]:
    premise = "凡人少年捡到吞噬寿数的古砚。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    return planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)


def test_fallback_foreshadowing_fields_carry_no_instruction_text() -> None:
    for language in (None, "en-US"):
        plan = _fallback_plan(_build_project(language))
        for volume in plan:
            for entry in (volume.get("foreshadowing_planted") or []) + (
                volume.get("foreshadowing_paid_off") or []
            ):
                for marker in _INSTRUCTION_MARKERS:
                    assert marker not in str(entry), (
                        f"instruction text leaked into volume {volume.get('volume_number')}: {entry!r}"
                    )


def test_fallback_hooks_carry_no_boilerplate() -> None:
    for language in (None, "en-US"):
        plan = _fallback_plan(_build_project(language))
        for volume in plan:
            hook = str(volume.get("reader_hook_to_next") or "")
            assert hook not in _BOILERPLATE_HOOKS or hook == "", (
                f"boilerplate hook leaked into volume {volume.get('volume_number')}: {hook!r}"
            )
