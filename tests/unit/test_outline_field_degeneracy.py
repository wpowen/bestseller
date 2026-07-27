"""T5 (2026-07-09) — 章纲 goal/opening_situation/main_conflict 退化查重。

真机根因(tracked-rulehorror-v1 ch1)：三字段字面相同，existence-only 闸门
("非空即过")照样全部通过。退化检测在 enrichment 之前跑，防止空壳被确定性
回填洗白成"看起来合格"；命中触发既有重生循环回炉，末轮软接受但打标。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.services.planner import (
    OutlineFieldDegeneracyError,
    PlannerFallbackError,
)

pytestmark = pytest.mark.unit


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="degeneracy-test",
        title="退化测试书",
        genre="悬疑推理",
        target_word_count=80000,
        target_chapters=10,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def _batch(chapters: list[dict]) -> ChapterOutlineBatchInput:
    return ChapterOutlineBatchInput.model_validate(
        {"batch_name": "v1-outline", "chapters": chapters}
    )


# ── _detect_degenerate_outline_fields ───────────────────────────────────────


class TestDetectDegenerateOutlineFields:
    def test_real_bug_case_identical_fields_flagged(self):
        # 真机 ch1：三字段字面相同。
        same = (
            "闻雀站在槐安公寓一楼大堂, 公司调度员余彤当面把钥匙和试用协议递给他, "
            "说出'报告不过你这周就办离职'——他刚刚亲手接下这单。"
        )
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": same,
                    "opening_situation": same,
                    "main_conflict": same,
                }
            ]
        )
        findings = planner_services._detect_degenerate_outline_fields(batch)
        pairs = {(f["field_a"], f["field_b"]) for f in findings}
        assert ("chapter_goal", "opening_situation") in pairs
        assert ("chapter_goal", "main_conflict") in pairs
        assert ("opening_situation", "main_conflict") in pairs
        assert all(f["similarity"] == 1.0 for f in findings)

    def test_trailing_punctuation_difference_still_flagged(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "纪渊要在封停前保住名册",
                    "opening_situation": "纪渊要在封停前保住名册。",
                    "main_conflict": "玄烈三日逼宫",
                }
            ]
        )
        findings = planner_services._detect_degenerate_outline_fields(batch)
        pairs = {(f["field_a"], f["field_b"]) for f in findings}
        assert ("chapter_goal", "opening_situation") in pairs

    def test_short_field_contained_in_long_field_above_ratio_flagged(self):
        # opening_situation 是 chapter_goal 的逐字前缀 + 极少量追加内容
        # (归一化后长度比 7/8=0.875 > 0.8 阈值)，非精确相等但仍应判定退化。
        goal = "纪渊要保住名册"
        situation = "纪渊要保住名册啊"
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": goal,
                    "opening_situation": situation,
                    "main_conflict": "无关的第三个字段内容完全不同",
                }
            ]
        )
        findings = planner_services._detect_degenerate_outline_fields(batch)
        pair = next(
            f for f in findings
            if f["field_a"] == "chapter_goal" and f["field_b"] == "opening_situation"
        )
        assert pair["similarity"] > 0.8
        assert pair["similarity"] < 1.0  # 非精确相等,走的是containment分支

    def test_genuinely_different_fields_not_flagged(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "纪渊要在封停前保住名册",
                    "opening_situation": "招神点大堂，玄烈的人已经封了门",
                    "main_conflict": "玄烈以三日为限逼纪渊交出名册",
                }
            ]
        )
        findings = planner_services._detect_degenerate_outline_fields(batch)
        assert findings == []

    def test_empty_fields_skipped_without_crash(self):
        batch = _batch(
            [
                {"chapter_number": 1, "chapter_goal": "", "opening_situation": None, "main_conflict": ""},
            ]
        )
        assert planner_services._detect_degenerate_outline_fields(batch) == []

    def test_no_chapters_returns_empty(self):
        batch = _batch([])
        assert planner_services._detect_degenerate_outline_fields(batch) == []


# ── _validate_generated_volume_outline_or_raise integration ────────────────


def _degenerate_payload(text: str = "闻雀接下试睡单子, 必须熬过七天") -> dict:
    return {
        "batch_name": "volume-1-outline",
        "chapters": [
            {
                "title": "按键下那张纸",
                "goal": text,
                "main_conflict": text,
                "hook_description": "电梯按键下贴着一张手写规则。",
                "scenes": [
                    {
                        "scene_number": 1,
                        # 第二参与者避免触发无关的 golden-three solo-chain 闸门，
                        # 让本测试只暴露退化查重这一个变量。
                        "participants": ["闻雀", "余彤"],
                        "purpose": {"story": "闻雀和余彤签下试睡协议。"},
                    }
                ],
            }
        ],
    }


class TestValidateGeneratedVolumeOutlineDegeneracy:
    def test_strict_mode_raises_outline_field_degeneracy_error(self):
        project = build_project()
        cast_spec = {"protagonist": {"name": "闻雀", "role": "protagonist"}, "antagonist": {"name": "余彤", "role": "antagonist"}}
        with pytest.raises(OutlineFieldDegeneracyError) as exc_info:
            planner_services._validate_generated_volume_outline_or_raise(
                _degenerate_payload(),
                project=project,
                logical_name="volume_1_chapter_outline",
                volume_number=1,
                expected_count=1,
                chapter_number_offset=1,
                cast_spec=cast_spec,
                strict_story_effects=True,
            )
        assert exc_info.value.findings
        assert exc_info.value.findings[0]["chapter_number"] == 1

    def test_final_attempt_soft_accepts_and_logs(self, caplog):
        project = build_project()
        cast_spec = {"protagonist": {"name": "闻雀", "role": "protagonist"}, "antagonist": {"name": "余彤", "role": "antagonist"}}
        with caplog.at_level("WARNING"):
            result = planner_services._validate_generated_volume_outline_or_raise(
                _degenerate_payload(),
                project=project,
                logical_name="volume_1_chapter_outline",
                volume_number=1,
                expected_count=1,
                chapter_number_offset=1,
                cast_spec=cast_spec,
                strict_story_effects=False,
            )
        assert result["chapters"][0]["chapter_number"] == 1
        assert any(
            "degenerate outline field" in rec.message for rec in caplog.records
        )
        # P2-4(检测报告)：软接受不能只打日志——日志不可查询，分段验收需要能
        # 从落库产物里核验退化字段，必须落到 degenerate_fields 标记里。
        assert result["chapters"][0]["degenerate_fields"], (
            "soft-accepted degenerate chapter must be tagged, not just logged"
        )
        assert "chapter_goal≈main_conflict" in result["chapters"][0]["degenerate_fields"]

    def test_non_degenerate_batch_passes_through_unaffected(self):
        project = build_project()
        cast_spec = {"protagonist": {"name": "闻雀", "role": "protagonist"}, "antagonist": {"name": "余彤", "role": "antagonist"}}
        payload = {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "title": "按键下那张纸",
                    "goal": "闻雀要保住试睡员的饭碗",
                    "main_conflict": "调度员余彤当面威胁不过就离职",
                    "opening_situation": "槐安公寓大堂，余彤把协议拍在桌上",
                    "hook_description": "电梯按键下贴着一张手写规则。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "participants": ["闻雀", "余彤"],
                            "purpose": {"story": "闻雀和余彤签下试睡协议。"},
                        }
                    ],
                }
            ],
        }
        result = planner_services._validate_generated_volume_outline_or_raise(
            payload,
            project=project,
            logical_name="volume_1_chapter_outline",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=1,
            cast_spec=cast_spec,
            # This test isolates field preservation; semantic word-budget
            # promotion is covered by the semantic-gate suite.
            strict_field_degeneracy=False,
        )
        assert result["chapters"][0]["main_conflict"].startswith("调度员余彤")

    def test_post_enrichment_degeneracy_reenters_llm_repair_loop(self, monkeypatch):
        """System backfill must not create a collapse after the early check."""

        project = build_project()
        cast_spec = {
            "protagonist": {"name": "闻雀", "role": "protagonist"},
            "antagonist": {"name": "余彤", "role": "antagonist"},
        }
        payload = {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "title": "按键下那张纸",
                    "goal": "闻雀要保住试睡员的饭碗",
                    "main_conflict": "调度员余彤当面威胁不过就离职",
                    "opening_situation": "槐安公寓大堂，余彤把协议拍在桌上",
                    "hook_description": "电梯按键下贴着一张手写规则。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "participants": ["闻雀", "余彤"],
                            "purpose": {"story": "闻雀和余彤签下试睡协议。"},
                        }
                    ],
                }
            ],
        }
        original = planner_services._enrich_generated_volume_outline_systemic_fields

        def collapse_after_early_check(batch, **kwargs):
            repaired = original(batch, **kwargs)
            batch.chapters[0].main_conflict = batch.chapters[0].chapter_goal
            return repaired + 1

        monkeypatch.setattr(
            planner_services,
            "_enrich_generated_volume_outline_systemic_fields",
            collapse_after_early_check,
        )

        with pytest.raises(OutlineFieldDegeneracyError) as exc_info:
            planner_services._validate_generated_volume_outline_or_raise(
                payload,
                project=project,
                logical_name="volume_1_chapter_outline",
                volume_number=1,
                expected_count=1,
                chapter_number_offset=1,
                cast_spec=cast_spec,
                strict_field_degeneracy=True,
            )

        assert "system enrichment and contract repair" in str(exc_info.value)
        assert exc_info.value.findings[0]["field_b"] == "main_conflict"


# ── _outline_repair_directives_from_error ───────────────────────────────────


class TestOutlineRepairDirectivesFromError:
    def test_degeneracy_error_yields_field_semantic_directives(self):
        error = OutlineFieldDegeneracyError(
            "boom",
            findings=[
                {
                    "chapter_number": 3,
                    "field_a": "chapter_goal",
                    "field_b": "opening_situation",
                    "text_a": "x",
                    "text_b": "x",
                    "similarity": 1.0,
                }
            ],
        )
        directives = planner_services._outline_repair_directives_from_error(
            error, language="zh-CN",
        )
        joined = " ".join(directives)
        assert "第3章" in joined
        assert "意图" in joined  # chapter_goal 语义说明
        assert "开场时空处境" in joined  # opening_situation 语义说明

    def test_degeneracy_error_english_directives(self):
        error = OutlineFieldDegeneracyError(
            "boom",
            findings=[
                {
                    "chapter_number": 5,
                    "field_a": "opening_situation",
                    "field_b": "main_conflict",
                    "text_a": "x",
                    "text_b": "x",
                    "similarity": 0.9,
                }
            ],
        )
        directives = planner_services._outline_repair_directives_from_error(
            error, language="en",
        )
        joined = " ".join(directives)
        assert "Chapter 5" in joined
        assert "obstacle" in joined.lower()

    def test_non_degeneracy_error_falls_through_to_generic_directive(self):
        error = PlannerFallbackError("some other planner failure")
        directives = planner_services._outline_repair_directives_from_error(
            error, language="zh-CN",
        )
        assert directives  # generic branch still produces something, doesn't crash


# ── enrichment tagging (planner.py side) ────────────────────────────────────


class TestPlannerEnrichmentTagging:
    _MANIFEST = [{"name": "闻雀", "role": "protagonist", "aliases": []}]

    def test_participants_backfill_is_tagged(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "闻雀要保住饭碗",
                    "main_conflict": "调度员逼他接单",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "participants": [],
                            "purpose": {"story": "闻雀和余彤在大堂签协议。"},
                        }
                    ],
                }
            ]
        )
        planner_services._enrich_generated_volume_outline_systemic_fields(
            batch, identity_manifest=self._MANIFEST, language="zh-CN"
        )
        assert "participants" in batch.chapters[0].enriched_fields

    def test_causal_contract_and_hook_type_backfill_tagged(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "闻雀要保住饭碗",
                    "main_conflict": "调度员逼他接单",
                    "hook_description": "系统弹出新提示。",
                    "scenes": [
                        {"scene_number": 1, "participants": ["闻雀"], "purpose": {}}
                    ],
                }
            ]
        )
        planner_services._enrich_generated_volume_outline_systemic_fields(
            batch, identity_manifest=self._MANIFEST, language="zh-CN"
        )
        tags = batch.chapters[0].enriched_fields
        assert "causal_contract" in tags

    def test_opening_situation_uses_alt_material_before_copying_goal(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "闻雀要在天亮前保住饭碗",
                    "main_conflict": "调度员逼他接单",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "time_label": "凌晨四点的大堂",
                            "participants": ["闻雀"],
                            "purpose": {},
                        }
                    ],
                }
            ]
        )
        planner_services._enrich_generated_volume_outline_systemic_fields(
            batch, identity_manifest=self._MANIFEST, language="zh-CN"
        )
        chapter = batch.chapters[0]
        assert "凌晨四点的大堂" in chapter.opening_situation
        tags = chapter.enriched_fields
        assert "opening_situation" in tags
        assert "opening_situation_copied_from_goal_no_alt_material" not in tags

    def test_opening_situation_copies_goal_only_when_no_alt_material(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "闻雀要在天亮前保住饭碗",
                    "main_conflict": "调度员逼他接单",
                    "scenes": [
                        {"scene_number": 1, "participants": ["闻雀"], "purpose": {}}
                    ],
                }
            ]
        )
        planner_services._enrich_generated_volume_outline_systemic_fields(
            batch, identity_manifest=self._MANIFEST, language="zh-CN"
        )
        chapter = batch.chapters[0]
        assert "闻雀要在天亮前保住饭碗" in chapter.opening_situation
        tags = chapter.enriched_fields
        assert "opening_situation_copied_from_goal_no_alt_material" in tags


class TestDeterministicChapterFieldRepair:
    def test_conflict_repair_does_not_copy_goal(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "姬衡要在宴席前护住小皇子",
                    "opening_situation": "宫门已闭，仇家的车驾堵在阶前",
                    "main_conflict": "本章需要强化冲突",
                }
            ]
        )
        chapter = batch.chapters[0]

        chapter.main_conflict = planner_services._outline_chapter_conflict_repair(
            chapter,
            protagonist_name="姬衡",
        )

        assert chapter.main_conflict != chapter.chapter_goal
        assert "反制" in chapter.main_conflict
        assert planner_services._detect_degenerate_outline_fields(batch) == []

    def test_goal_repair_does_not_copy_conflict(self):
        batch = _batch(
            [
                {
                    "chapter_number": 1,
                    "chapter_goal": "本章需要推进剧情",
                    "opening_situation": "宫门已闭，仇家的车驾堵在阶前",
                    "main_conflict": "裴家死士要在宴席前抢走小皇子",
                }
            ]
        )
        chapter = batch.chapters[0]

        chapter.chapter_goal = planner_services._outline_chapter_goal_repair(
            chapter,
            protagonist_name="姬衡",
        )

        assert chapter.chapter_goal != chapter.main_conflict
        assert "主动行动" in chapter.chapter_goal
        assert planner_services._detect_degenerate_outline_fields(batch) == []


# ── L3 真机验收回归(2026-07-09)：合并终验的退化查重必须软接受 ────────────────


class TestCombineTimeDegeneracyIsSoft:
    """真机死法(female-growth-romance-1783580950)：批内校验之后，管线自身的
    确定性修复制造了三字段退化(_repair_generated_volume_outline_contract_inputs
    把元话术 goal 用 main_conflict 首句重写、enrichment 再把 goal 复制进
    opening_situation)，而 _generate_volume_outline_batched 的合并终验以默认
    strict 复检、外面没有任何回炉循环——OutlineFieldDegeneracyError 直接逃逸
    毙掉整本书，违反 fail-open 红线(与 title 撞名同款老坑)。"""

    def _cast_spec(self) -> dict:
        return {
            "protagonist": {"name": "闻雀", "role": "protagonist"},
            "antagonist": {"name": "余彤", "role": "antagonist"},
        }

    def test_strict_field_degeneracy_false_soft_accepts_despite_strict_story_effects(self):
        project = build_project()
        result = planner_services._validate_generated_volume_outline_or_raise(
            _degenerate_payload(),
            project=project,
            logical_name="volume_1_chapter_outline",
            volume_number=1,
            expected_count=1,
            chapter_number_offset=1,
            cast_spec=self._cast_spec(),
            strict_story_effects=True,          # 合并终验对 story-effect 仍是 strict
            strict_field_degeneracy=False,      # 但退化必须软接受
        )
        assert result["chapters"][0]["chapter_number"] == 1
        assert result["chapters"][0]["degenerate_fields"], (
            "combine-time soft-accept must still tag degenerate_fields for audit"
        )

    def test_default_none_follows_strict_story_effects(self):
        # 修复循环的既有调用不传新参数 → 行为不变(strict 时照抛)。
        project = build_project()
        with pytest.raises(OutlineFieldDegeneracyError):
            planner_services._validate_generated_volume_outline_or_raise(
                _degenerate_payload(),
                project=project,
                logical_name="volume_1_chapter_outline",
                volume_number=1,
                expected_count=1,
                chapter_number_offset=1,
                cast_spec=self._cast_spec(),
                strict_story_effects=True,
            )

    def test_combine_call_site_requires_strict_degeneracy(self):
        """The final combined volume may not promote collapsed outline fields."""

        import inspect

        source = inspect.getsource(planner_services._generate_volume_outline_batched)
        combine_pos = source.index("validated = _validate_generated_volume_outline_or_raise(")
        call_region = source[combine_pos : combine_pos + 900]
        assert "strict_field_degeneracy=True," in call_region
