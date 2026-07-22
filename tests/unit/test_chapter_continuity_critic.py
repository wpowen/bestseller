"""Guards for the intra-chapter continuity critic and the per-book unit switch.

Root cause (2026-07-20 generation-unit A/B): chapter-first beats scene-by-scene
on state coherence except when the chapter breaks its own facts — the arm that
weighed 低筋面粉 then kneaded 高筋面粉 lost that case 4-0. Verified before this
gate was built: ``common_sense_gate`` scores that sample zero findings.

The critic is advisory by construction. A book has already been killed once by a
checker that was allowed to block on a false positive (NAMING_OUT_OF_POOL).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bestseller.services.chapter_continuity_critic import (
    CONTINUITY_FINDING_CODE,
    ContinuityFinding,
    ContinuityReport,
    parse_continuity_findings,
)

pytestmark = pytest.mark.unit


_CHAPTER = (
    "赵师傅把袋子拆开，倒进秤盘。低筋面粉，一百二十克。\n\n"
    "林见夏没说话，只把自己的那袋推过去。\n\n"
    "阿禾已经把高筋面粉倒在案板上，手背上沾了一层白。\n\n"
    "两边同时开始揉面。围观的人挤成半圈。\n"
)


def _response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings}, ensure_ascii=False)


class TestParsing:
    def test_grounded_contradiction_is_kept(self) -> None:
        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "object",
                        "detail": "称的是低筋面粉，和面时变成高筋面粉",
                        "first_evidence": "低筋面粉，一百二十克",
                        "second_evidence": "阿禾已经把高筋面粉倒在案板上",
                    }
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert report.passed is False
        assert len(report.findings) == 1
        assert report.findings[0].category == "object"

    def test_paraphrased_evidence_is_dropped(self) -> None:
        """A quote the model cannot point at is the shape a false positive takes."""

        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "object",
                        "detail": "面粉类型前后不一致",
                        "first_evidence": "他称了一些低筋的面粉放在秤上",
                        "second_evidence": "后来用的却是高筋的面粉",
                    }
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert report.passed is True

    def test_punctuation_differences_do_not_reject_a_real_quote(self) -> None:
        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "object",
                        "detail": "面粉前后不一致",
                        "first_evidence": "低筋面粉一百二十克。",
                        "second_evidence": "阿禾已经把高筋面粉倒在案板上，",
                    }
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert len(report.findings) == 1

    def test_unknown_category_is_dropped(self) -> None:
        """A hallucinated category must not invent a new repair scope."""

        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "pacing",
                        "detail": "节奏太慢",
                        "first_evidence": "低筋面粉，一百二十克",
                        "second_evidence": "两边同时开始揉面",
                    }
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert report.passed is True

    def test_same_contradiction_reported_twice_is_deduped(self) -> None:
        """Observed live (2026-07-21, chapter 6): the envelope-count claim came
        back as two findings sharing one first_evidence — the same contradiction
        described from two angles. Forwarding both would double the repair hint."""

        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "quantity",
                        "detail": "筐里应有四封，后文只剩一封",
                        "first_evidence": "低筋面粉，一百二十克",
                        "second_evidence": "阿禾已经把高筋面粉倒在案板上",
                    },
                    {
                        "category": "quantity",
                        "detail": "其余三封未交代去向",
                        "first_evidence": "低筋面粉，一百二十克",
                        "second_evidence": "两边同时开始揉面",
                    },
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert len(report.findings) == 1

    def test_distinct_contradictions_both_survive(self) -> None:
        """Dedup must key on the anchor quote, not collapse unrelated findings."""

        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "object",
                        "detail": "面粉不一致",
                        "first_evidence": "低筋面粉，一百二十克",
                        "second_evidence": "阿禾已经把高筋面粉倒在案板上",
                    },
                    {
                        "category": "name",
                        "detail": "另一处无关矛盾",
                        "first_evidence": "林见夏没说话",
                        "second_evidence": "两边同时开始揉面",
                    },
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert len(report.findings) == 2

    def test_narrative_focus_is_excluded_by_the_prompt(self) -> None:
        """The live false positive: narrowing onto one item among several is
        normal prose, not a quantity contradiction."""

        from bestseller.services.chapter_continuity_critic import _SYSTEM_PROMPT

        assert "叙事聚焦不是数量矛盾" in _SYSTEM_PROMPT
        assert "同一处矛盾只报一条" in _SYSTEM_PROMPT

    def test_identical_evidence_is_dropped(self) -> None:
        report = parse_continuity_findings(
            _response(
                [
                    {
                        "category": "object",
                        "detail": "自相矛盾",
                        "first_evidence": "低筋面粉，一百二十克",
                        "second_evidence": "低筋面粉，一百二十克",
                    }
                ]
            ),
            chapter_text=_CHAPTER,
        )
        assert report.passed is True

    def test_empty_findings_pass(self) -> None:
        assert parse_continuity_findings(
            _response([]), chapter_text=_CHAPTER
        ).passed is True

    @pytest.mark.parametrize(
        "raw", ["", "not json at all", "{broken", '{"other_field": 1}']
    )
    def test_malformed_response_fails_open(self, raw: str) -> None:
        report = parse_continuity_findings(raw, chapter_text=_CHAPTER)
        assert report.passed is True
        assert report.skipped_reason is not None

    def test_fenced_json_is_parsed(self) -> None:
        raw = "```json\n" + _response(
            [
                {
                    "category": "object",
                    "detail": "面粉不一致",
                    "first_evidence": "低筋面粉，一百二十克",
                    "second_evidence": "阿禾已经把高筋面粉倒在案板上",
                }
            ]
        ) + "\n```"
        assert len(parse_continuity_findings(raw, chapter_text=_CHAPTER).findings) == 1


class TestRequestContract:
    """The request model is validated at construction; a missing required field
    raises at call time, not at import time, so only a real construction catches
    it. This shipped broken once and was found by a container smoke test."""

    def test_request_is_constructible_as_the_critic_builds_it(self) -> None:
        from bestseller.services.chapter_continuity_critic import _SYSTEM_PROMPT
        from bestseller.services.llm import LLMCompletionRequest

        request = LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt="章节号：第8章\n\n正文",
            fallback_response='{"findings": []}',
            prompt_template="chapter_continuity_critic",
            prompt_version="v1",
        )
        assert request.fallback_response

    def test_result_field_name_matches_what_the_critic_reads(self) -> None:
        """Shipped broken once: the module read ``completion.text`` while the
        result model exposes ``content``, so every call silently parsed an empty
        string and the gate found nothing forever."""

        from bestseller.services.llm import LLMCompletionResult

        assert "content" in LLMCompletionResult.model_fields
        assert "text" not in LLMCompletionResult.model_fields

    def test_fallback_response_parses_to_an_empty_clean_report(self) -> None:
        """The degraded answer must mean 'no contradictions found', never a
        crash and never an invented finding."""

        report = parse_continuity_findings(
            '{"findings": []}', chapter_text=_CHAPTER
        )
        assert report.passed is True
        assert report.findings == ()


class TestReportPayload:
    def test_payload_is_json_serialisable_and_tagged(self) -> None:
        report = ContinuityReport(
            findings=(
                ContinuityFinding(
                    category="quantity",
                    detail="三车对不上",
                    first_evidence="四十一车",
                    second_evidence="四十四车",
                ),
            )
        )
        payload = report.as_payload()
        json.dumps(payload)
        assert payload["code"] == CONTINUITY_FINDING_CODE
        assert payload["passed"] is False

    def test_repair_hints_carry_both_sides(self) -> None:
        report = ContinuityReport(
            findings=(
                ContinuityFinding(
                    category="object",
                    detail="面粉不一致",
                    first_evidence="低筋",
                    second_evidence="高筋",
                ),
            )
        )
        hint = report.repair_hints()[0]
        assert "低筋" in hint and "高筋" in hint


class TestRepairHintWiring:
    """Advisory findings are worthless if nothing consumes them."""

    def test_hints_are_read_off_chapter_metadata(self) -> None:
        from bestseller.services.pipelines import _chapter_continuity_repair_hints

        chapter = SimpleNamespace(
            metadata_json={
                "chapter_continuity_latest": {
                    "findings": [
                        {
                            "category": "object",
                            "detail": "称的是低筋，用的是高筋",
                            "first_evidence": "低筋面粉",
                            "second_evidence": "高筋面粉",
                        }
                    ]
                }
            }
        )
        hints = _chapter_continuity_repair_hints(chapter)
        assert len(hints) == 1
        assert "低筋" in hints[0]

    @pytest.mark.parametrize(
        "metadata", [None, {}, {"chapter_continuity_latest": {}}, "not-a-dict"]
    )
    def test_missing_payload_yields_no_hints(self, metadata) -> None:
        from bestseller.services.pipelines import _chapter_continuity_repair_hints

        assert _chapter_continuity_repair_hints(SimpleNamespace(metadata_json=metadata)) == ()


class TestPerBookGenerationUnit:
    """One book must be switchable without flipping the global default."""

    def _settings(self, *, enabled: bool):
        return SimpleNamespace(
            pipeline=SimpleNamespace(
                enable_chapter_first_generation=enabled,
                chapter_first_max_chapter_number=3,
                chapter_first_short_chapter_threshold=0,
            )
        )

    def _project(self, metadata):
        return SimpleNamespace(metadata_json=metadata)

    def test_explicit_argument_outranks_everything(self) -> None:
        from bestseller.services.pipelines import _chapter_first_requested

        assert (
            _chapter_first_requested(
                self._settings(enabled=False),
                50,
                False,
                None,
                self._project({"generation_unit_mode": "chapter"}),
            )
            is False
        )

    def test_per_book_chapter_mode_beats_disabled_global_flag(self) -> None:
        from bestseller.services.pipelines import _chapter_first_requested

        assert (
            _chapter_first_requested(
                self._settings(enabled=False),
                50,
                None,
                None,
                self._project({"generation_unit_mode": "chapter"}),
            )
            is True
        )

    def test_per_book_scene_mode_pins_back_against_enabled_global_flag(self) -> None:
        from bestseller.services.pipelines import _chapter_first_requested

        assert (
            _chapter_first_requested(
                self._settings(enabled=True),
                1,
                None,
                None,
                self._project({"generation_unit_mode": "scene"}),
            )
            is False
        )

    def test_legacy_repair_keys_stay_honoured(self) -> None:
        """repair.py already reads these; a book marked before this change must
        not be stranded on the global default."""

        from bestseller.services.pipelines import _chapter_first_requested

        for metadata in (
            {"chapter_first_generation": True},
            {"generation_mode": "chapter_first_single_pass"},
        ):
            assert (
                _chapter_first_requested(
                    self._settings(enabled=False), 50, None, None, self._project(metadata)
                )
                is True
            )

    def test_no_metadata_falls_back_to_global_settings(self) -> None:
        """No-op guard: books without the key must behave exactly as before."""

        from bestseller.services.pipelines import _chapter_first_requested

        for metadata in (None, {}, {"unrelated": 1}):
            assert (
                _chapter_first_requested(
                    self._settings(enabled=False), 1, None, None, self._project(metadata)
                )
                is False
            )
            assert (
                _chapter_first_requested(
                    self._settings(enabled=True), 1, None, None, self._project(metadata)
                )
                is True
            )

    def test_absent_project_behaves_as_before(self) -> None:
        from bestseller.services.pipelines import _chapter_first_requested

        assert _chapter_first_requested(self._settings(enabled=True), 1, None) is True
        assert _chapter_first_requested(self._settings(enabled=False), 1, None) is False
