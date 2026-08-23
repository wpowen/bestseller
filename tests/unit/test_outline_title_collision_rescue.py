"""批内标题撞车打满修复轮后必须走确定性改名救援，而不是毙掉整条管线。

2026-08-23 真机：单卷书滚动生成第 46-48 章批次，ch46『血指印』与 ch2、
ch48『阴债』与 ch3 完全同名；LLM 修复循环 3 轮不收敛 →
PlannerFallbackError → generate_volume_plan / project_repair /
chapter_pipeline 三个 workflow 同刻全灭。

而框架在**整卷合并层**早已把政策写进 make_titles_unique 的 docstring：
「A duplicate chapter title is cosmetic and must never abort a whole
book」——同一种错在批内层却仍是死刑。本测试钉住：批内循环耗尽后，若
仅剩的缺陷是标题撞车，用同一条确定性改名路径救活。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from uuid import uuid4

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services


def _project() -> ProjectModel:
    project = ProjectModel(
        slug="title-rescue-test",
        title="标题救援测试书",
        genre="悬疑推理",
        target_word_count=80000,
        target_chapters=50,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


_CAST = {
    "protagonist": {"name": "闻雀", "role": "protagonist"},
    "antagonist": {"name": "余彤", "role": "antagonist"},
}

_EXISTING = [(2, "血指印"), (3, "阴债")]


def _payload(title: str = "血指印") -> dict:
    return {
        "batch_name": "volume-1-outline-46-46",
        "chapters": [
            {
                "chapter_number": 46,
                "title": title,
                "goal": "闻雀要在封停前拿回名册",
                "main_conflict": "余彤以三日为限逼闻雀交出抵押的名册",
                "opening_situation": "招神点大堂，余彤的人已经封了门",
                "hook_description": "名册夹层里掉出一枚不属于任何在册者的指环。",
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["闻雀", "余彤"],
                        "purpose": {"story": "闻雀与余彤当面摊牌名册归属。"},
                    }
                ],
            }
        ],
    }


class TestRescueHelper:
    def test_exact_collision_is_rescued_with_unique_title(self):
        rescued, changes = planner_services._rescue_title_collisions_or_none(
            _payload(),
            project=_project(),
            logical_name="volume_1_chapter_outline_batch_46_46",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=46,
            cast_spec=_CAST,
            volume_entry=None,
            existing_titles=_EXISTING,
        )
        assert rescued is not None
        new_title = rescued["chapters"][0]["title"]
        assert new_title != "血指印"
        assert new_title.strip()
        assert changes and changes[0][0] == 46

    def test_no_collision_returns_none_without_changes(self):
        rescued, changes = planner_services._rescue_title_collisions_or_none(
            _payload(title="指环认主"),
            project=_project(),
            logical_name="volume_1_chapter_outline_batch_46_46",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=46,
            cast_spec=_CAST,
            volume_entry=None,
            existing_titles=_EXISTING,
        )
        assert rescued is None
        assert changes == []

    def test_other_fatal_defect_still_fails_after_dedupe(self):
        # 标题撞车 + 字段退化并存：改名救不了退化，救援必须返回 None，
        # 让上层照旧抛 PlannerFallbackError——救援只吃「表面伤」。
        broken = _payload()
        same = "闻雀接下试睡单子必须熬过七天"
        broken["chapters"][0]["goal"] = same
        broken["chapters"][0]["main_conflict"] = same
        broken["chapters"][0]["opening_situation"] = same
        rescued, changes = planner_services._rescue_title_collisions_or_none(
            broken,
            project=_project(),
            logical_name="volume_1_chapter_outline_batch_46_46",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=46,
            cast_spec=_CAST,
            volume_entry=None,
            existing_titles=_EXISTING,
        )
        assert rescued is None
        assert changes  # 改名发生了，但重验没过


class TestRepairLoopWiring:
    def test_exhausted_loop_returns_rescued_payload_instead_of_raising(
        self, monkeypatch
    ):
        # 只钉接线：LLM 每轮都回撞车标题 → 循环耗尽 → 必须返回救援产物，
        # 且 repair_history 留下确定性改名的痕迹。
        import asyncio
        from types import SimpleNamespace

        monkeypatch.setattr(
            planner_services,
            "_volume_outline_prompts",
            lambda *a, **k: ("sys", "user 请仅生成第1卷"),
        )
        monkeypatch.setattr(
            planner_services,
            "attach_planner_methodology",
            lambda user_prompt, **k: user_prompt,
        )
        monkeypatch.setattr(
            planner_services,
            "_compile_volume_outline_prompt",
            lambda project, settings, *, system_prompt, user_prompt: (
                system_prompt,
                user_prompt,
            ),
        )

        async def _fake_fetch(session, project_id, *, exclude_volume_number=None):
            return list(_EXISTING)

        monkeypatch.setattr(
            planner_services, "_fetch_existing_chapter_titles", _fake_fetch
        )

        async def _fake_generate(*a, **k):
            return _payload(), uuid4()

        monkeypatch.setattr(
            planner_services, "_generate_structured_artifact", _fake_generate
        )

        settings = SimpleNamespace(
            pipeline=SimpleNamespace(chapter_outline_repair_attempts=2)
        )
        payload, llm_run_id, history = asyncio.run(
            planner_services._generate_volume_outline_with_repair_loop(
                None,
                settings,
                project=_project(),
                workflow_run_id=uuid4(),
                logical_name="volume_1_chapter_outline_batch_46_46",
                book_spec={},
                cast_spec=_CAST,
                volume_plan=[],
                volume_entry={},
                fallback_payload={},
                volume_number=1,
                expected_count=1,
                chapter_number_offset=46,
                revealed_ledger_block=None,
                base_constraints=[],
            )
        )
        assert payload["chapters"][0]["title"] != "血指印"
        assert llm_run_id is not None
        assert history[-1]["status"] == "passed_with_deterministic_title_dedupe"
        assert history[-1]["title_changes"][0]["chapter_number"] == 46
