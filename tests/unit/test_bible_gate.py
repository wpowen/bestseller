"""Unit tests for L2 Bible Completeness Gate validators.

Each test crafts a minimal ``BibleDraft`` focused on one validator so
failures pinpoint the defective check. We instantiate an invariants object
with ``seed_invariants`` — Phase 2 validators don't yet use invariants,
but accepting the arg keeps the signature forward-compatible for when the
NamingScheme / CliffhangerPolicy feed into the gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.story_bible import (
    CharacterIPAnchorInput,
    CharacterInput,
)
from bestseller.services.bible_gate import (
    AntagonistMotiveLedger,
    BibleDraft,
    CharacterIPAnchorCheck,
    NamingPoolSize,
    ThemeSignatureCheck,
    WorldTaxonomyUniqueness,
    build_draft_from_materialization_content,
    default_validators,
    validate_bible_completeness,
)
from bestseller.services.invariants import seed_invariants

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _invariants():
    return seed_invariants(
        project_id=uuid4(),
        language="zh-CN",
        words_per_chapter=SimpleNamespace(min=5000, target=6400, max=7500),
    )


def _character(
    name: str,
    role: str,
    *,
    quirks: list[str] | None = None,
    core_wound: str | None = None,
    goal: str | None = None,
    background: str | None = None,
    secret: str | None = None,
) -> CharacterInput:
    return CharacterInput(
        name=name,
        role=role,
        goal=goal,
        background=background,
        secret=secret,
        ip_anchor=CharacterIPAnchorInput(
            quirks=quirks or [],
            core_wound=core_wound,
        ),
    )


def _minimal_draft(
    *,
    characters: list[CharacterInput] | None = None,
    power_system_tiers: tuple[str, ...] = (),
    power_system_name: str | None = None,
    naming_pool: tuple[str, ...] = (),
    expected_character_count: int = 0,
    theme_statement: str | None = "真正的力量来自承认脆弱",
    dramatic_question: str | None = "林奚能否在救妹与守家之间两全？",
) -> BibleDraft:
    return BibleDraft(
        characters=tuple(characters or []),
        power_system_tiers=power_system_tiers,
        power_system_name=power_system_name,
        naming_pool=naming_pool,
        expected_character_count=expected_character_count,
        theme_statement=theme_statement,
        dramatic_question=dramatic_question,
    )


# ---------------------------------------------------------------------------
# CharacterIPAnchorCheck.
# ---------------------------------------------------------------------------


class TestCharacterIPAnchorCheck:
    def test_protagonist_with_three_quirks_and_wound_passes(self) -> None:
        hero = _character(
            "林奚",
            "protagonist",
            quirks=["左手食指关节断裂", "洁癖", "口头禅 '这不对劲'"],
            core_wound="七岁目睹母亲被处决",
        )
        deficiencies = list(
            CharacterIPAnchorCheck().check(
                _minimal_draft(characters=[hero]), _invariants()
            )
        )
        assert deficiencies == []

    def test_protagonist_with_two_quirks_fails(self) -> None:
        hero = _character(
            "林奚",
            "protagonist",
            quirks=["洁癖", "口头禅 '这不对劲'"],
            core_wound="目睹母亲被处决",
        )
        deficiencies = list(
            CharacterIPAnchorCheck().check(
                _minimal_draft(characters=[hero]), _invariants()
            )
        )
        assert len(deficiencies) == 1
        assert deficiencies[0].code == "CHARACTER_IP_ANCHOR_MISSING"
        assert "林奚" in deficiencies[0].prompt_feedback

    def test_protagonist_missing_core_wound_fails(self) -> None:
        hero = _character(
            "林奚",
            "protagonist",
            quirks=["q1", "q2", "q3"],
            core_wound=None,
        )
        deficiencies = list(
            CharacterIPAnchorCheck().check(
                _minimal_draft(characters=[hero]), _invariants()
            )
        )
        codes = {d.code for d in deficiencies}
        assert "CORE_WOUND_MISSING" in codes

    def test_antagonist_needs_only_two_quirks(self) -> None:
        villain = _character(
            "暗影",
            "antagonist",
            quirks=["戴黑色皮手套", "总是低声吟唱"],
        )
        deficiencies = list(
            CharacterIPAnchorCheck().check(
                _minimal_draft(characters=[villain]), _invariants()
            )
        )
        # Antagonist gets no core_wound requirement at L2 — only protagonists do.
        codes = {d.code for d in deficiencies}
        assert "CHARACTER_IP_ANCHOR_MISSING" not in codes
        assert "CORE_WOUND_MISSING" not in codes

    def test_supporting_cast_is_exempt(self) -> None:
        sidekick = _character("随从", "supporting", quirks=[])
        deficiencies = list(
            CharacterIPAnchorCheck().check(
                _minimal_draft(characters=[sidekick]), _invariants()
            )
        )
        assert deficiencies == []

    def test_empty_or_whitespace_quirks_do_not_count(self) -> None:
        hero = _character(
            "林奚",
            "protagonist",
            quirks=["", "  ", "唯一有效的 quirk"],
            core_wound="被流放",
        )
        deficiencies = list(
            CharacterIPAnchorCheck().check(
                _minimal_draft(characters=[hero]), _invariants()
            )
        )
        assert any(d.code == "CHARACTER_IP_ANCHOR_MISSING" for d in deficiencies)


# ---------------------------------------------------------------------------
# AntagonistMotiveLedger.
# ---------------------------------------------------------------------------


class TestAntagonistMotiveLedger:
    def test_single_antagonist_is_trivially_ok(self) -> None:
        v = _character("暗影", "antagonist", goal="统治世界，消灭一切反抗者")
        deficiencies = list(
            AntagonistMotiveLedger().check(
                _minimal_draft(characters=[v]), _invariants()
            )
        )
        assert deficiencies == []

    def test_two_antagonists_with_identical_revenge_motives_fail(self) -> None:
        v1 = _character(
            "赵烈",
            "antagonist",
            goal="复仇，洗刷当年被宗门轻视的耻辱",
            background="三百年前被同门轻视",
            secret="血脉被封印",
        )
        v2 = _character(
            "萧寒",
            "antagonist",
            goal="复仇，洗刷当年被宗门轻视的耻辱",
            background="三百年前被同门轻视",
            secret="血脉被封印",
        )
        deficiencies = list(
            AntagonistMotiveLedger().check(
                _minimal_draft(characters=[v1, v2]), _invariants()
            )
        )
        assert len(deficiencies) == 1
        assert deficiencies[0].code == "ANTAGONIST_MOTIVE_OVERLAP"
        assert "赵烈" in deficiencies[0].location
        assert "萧寒" in deficiencies[0].location

    def test_two_antagonists_with_distinct_motives_pass(self) -> None:
        v1 = _character(
            "赵烈",
            "antagonist",
            goal="永生，集齐天地九鼎成就无上",
            background="凡人出身，少年时目睹母亲病死",
            secret="其实是医者转修",
        )
        v2 = _character(
            "萧寒",
            "antagonist",
            goal="救赎妹妹，夺取仙宫圣血",
            background="孤儿，与妹妹相依为命",
            secret="体内寄生灵魂",
        )
        deficiencies = list(
            AntagonistMotiveLedger().check(
                _minimal_draft(characters=[v1, v2]), _invariants()
            )
        )
        assert deficiencies == []


# ---------------------------------------------------------------------------
# WorldTaxonomyUniqueness.
# ---------------------------------------------------------------------------


class TestWorldTaxonomyUniqueness:
    def test_xianxia_template_is_rejected(self) -> None:
        draft = _minimal_draft(
            power_system_name="仙道",
            power_system_tiers=("炼气", "筑基", "金丹", "元婴"),
        )
        deficiencies = list(
            WorldTaxonomyUniqueness().check(draft, _invariants())
        )
        assert len(deficiencies) == 1
        assert deficiencies[0].code == "WORLD_TAXONOMY_BOILERPLATE"

    def test_unique_tier_names_pass(self) -> None:
        draft = _minimal_draft(
            power_system_name="承脉",
            power_system_tiers=("承脉", "裂象", "铸神", "还虚"),
        )
        deficiencies = list(
            WorldTaxonomyUniqueness().check(draft, _invariants())
        )
        assert deficiencies == []

    def test_empty_tiers_skip_check(self) -> None:
        draft = _minimal_draft(power_system_tiers=())
        deficiencies = list(
            WorldTaxonomyUniqueness().check(draft, _invariants())
        )
        assert deficiencies == []


# ---------------------------------------------------------------------------
# NamingPoolSize.
# ---------------------------------------------------------------------------


class TestNamingPoolSize:
    def test_pool_smaller_than_twice_expected_fails(self) -> None:
        draft = _minimal_draft(
            naming_pool=("李", "王", "赵"),
            expected_character_count=5,
        )
        deficiencies = list(NamingPoolSize().check(draft, _invariants()))
        assert len(deficiencies) == 1
        assert deficiencies[0].code == "NAMING_POOL_UNDERSIZED"

    def test_pool_at_exactly_2x_passes(self) -> None:
        names = tuple(f"n{i}" for i in range(10))
        draft = _minimal_draft(
            naming_pool=names,
            expected_character_count=5,
        )
        deficiencies = list(NamingPoolSize().check(draft, _invariants()))
        assert deficiencies == []

    def test_no_expected_count_skips_check(self) -> None:
        draft = _minimal_draft(
            naming_pool=(),
            expected_character_count=0,
        )
        deficiencies = list(NamingPoolSize().check(draft, _invariants()))
        assert deficiencies == []


# ---------------------------------------------------------------------------
# ThemeSignatureCheck.
# ---------------------------------------------------------------------------


class TestThemeSignatureCheck:
    def test_both_present_passes(self) -> None:
        draft = _minimal_draft(
            theme_statement="真正的力量来自承认脆弱",
            dramatic_question="林奚能否两全？",
        )
        deficiencies = list(ThemeSignatureCheck().check(draft, _invariants()))
        assert deficiencies == []

    def test_missing_theme_fails(self) -> None:
        draft = _minimal_draft(theme_statement=None)
        deficiencies = list(ThemeSignatureCheck().check(draft, _invariants()))
        codes = {d.code for d in deficiencies}
        assert "THEME_STATEMENT_MISSING" in codes

    def test_missing_dramatic_question_fails(self) -> None:
        draft = _minimal_draft(dramatic_question=None)
        deficiencies = list(ThemeSignatureCheck().check(draft, _invariants()))
        codes = {d.code for d in deficiencies}
        assert "DRAMATIC_QUESTION_MISSING" in codes

    def test_whitespace_only_treated_as_missing(self) -> None:
        draft = _minimal_draft(
            theme_statement="   ",
            dramatic_question="\n\n",
        )
        deficiencies = list(ThemeSignatureCheck().check(draft, _invariants()))
        codes = {d.code for d in deficiencies}
        assert "THEME_STATEMENT_MISSING" in codes
        assert "DRAMATIC_QUESTION_MISSING" in codes


# ---------------------------------------------------------------------------
# Orchestrator: validate_bible_completeness.
# ---------------------------------------------------------------------------


class TestValidateBibleCompleteness:
    def test_clean_draft_passes(self) -> None:
        hero = _character(
            "林奚",
            "protagonist",
            quirks=["q1", "q2", "q3"],
            core_wound="mother executed",
        )
        draft = _minimal_draft(
            characters=[hero],
            power_system_tiers=("承脉", "裂象", "铸神"),
            power_system_name="承脉",
            naming_pool=tuple(f"n{i}" for i in range(10)),
            expected_character_count=5,
        )
        report = validate_bible_completeness(draft, _invariants())
        assert report.passes is True
        assert report.deficiencies == ()

    def test_all_validators_run_even_when_one_fails(self) -> None:
        broken_hero = _character(
            "林奚",
            "protagonist",
            quirks=[],  # triggers CharacterIPAnchorCheck
            core_wound=None,  # triggers CORE_WOUND_MISSING
        )
        draft = _minimal_draft(
            characters=[broken_hero],
            power_system_tiers=("炼气", "筑基", "金丹", "元婴"),  # triggers WORLD_TAXONOMY_BOILERPLATE
            naming_pool=("a",),
            expected_character_count=5,  # triggers NAMING_POOL_UNDERSIZED
            theme_statement=None,  # triggers THEME_STATEMENT_MISSING
            dramatic_question=None,  # triggers DRAMATIC_QUESTION_MISSING
        )
        report = validate_bible_completeness(draft, _invariants())
        codes = {d.code for d in report.deficiencies}
        assert "CHARACTER_IP_ANCHOR_MISSING" in codes
        assert "CORE_WOUND_MISSING" in codes
        assert "WORLD_TAXONOMY_BOILERPLATE" in codes
        assert "NAMING_POOL_UNDERSIZED" in codes
        assert "THEME_STATEMENT_MISSING" in codes
        assert "DRAMATIC_QUESTION_MISSING" in codes

    def test_feedback_for_regen_is_non_empty_when_failing(self) -> None:
        draft = _minimal_draft(theme_statement=None, dramatic_question=None)
        report = validate_bible_completeness(draft, _invariants())
        feedback = report.feedback_for_regen()
        assert feedback
        assert "THEME_STATEMENT_MISSING" in feedback or "DRAMATIC_QUESTION_MISSING" in feedback

    def test_feedback_for_regen_is_empty_when_passing(self) -> None:
        hero = _character(
            "林奚",
            "protagonist",
            quirks=["q1", "q2", "q3"],
            core_wound="wound",
        )
        draft = _minimal_draft(
            characters=[hero],
            naming_pool=tuple(f"n{i}" for i in range(10)),
            expected_character_count=5,
        )
        report = validate_bible_completeness(draft, _invariants())
        assert report.feedback_for_regen() == ""

    def test_default_validators_has_five_checks(self) -> None:
        assert len(default_validators()) == 5


class TestBuildDraftFromMaterializationContent:
    """The dict -> BibleDraft adapter used by the L2 audit wiring inside
    ``materialize_story_bible``. These tests pin down the extraction logic
    so the wiring can evolve without silently changing what the gate sees.
    """

    def test_empty_inputs_produce_empty_draft(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content=None,
            world_spec_content=None,
            cast_spec_content=None,
        )
        assert draft.characters == ()
        assert draft.power_system_tiers == ()
        assert draft.power_system_name is None
        assert draft.naming_pool == ()
        assert draft.expected_character_count == 0
        assert draft.theme_statement is None
        assert draft.dramatic_question is None

    def test_protagonist_antagonist_roles_are_forced(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={},
            world_spec_content={},
            cast_spec_content={
                "protagonist": {"name": "林奚", "role": "whatever"},
                "antagonist": {"name": "司徒霆", "role": "supporting"},
                "supporting_cast": [{"name": "老王", "role": "mentor"}],
            },
        )
        assert [c.name for c in draft.characters] == ["林奚", "司徒霆", "老王"]
        roles = {c.name: c.role for c in draft.characters}
        assert "protagonist" in roles["林奚"].lower()
        assert "antagonist" in roles["司徒霆"].lower()

    def test_power_system_tiers_extracted(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={},
            world_spec_content={
                "power_system": {
                    "tiers": ["炼气", "筑基", "金丹", "元婴"],
                    "name": "Cultivation",
                }
            },
            cast_spec_content={},
        )
        assert draft.power_system_tiers == ("炼气", "筑基", "金丹", "元婴")
        assert draft.power_system_name == "Cultivation"

    def test_naming_pool_includes_character_names_and_explicit_pool(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={"naming_pool": ["苏陌", "陆昭"]},
            world_spec_content={},
            cast_spec_content={
                "protagonist": {"name": "林奚", "role": "protagonist"},
                "antagonist": {"name": "司徒霆", "role": "antagonist"},
                "supporting_cast": [{"name": "老王", "role": "mentor"}],
            },
        )
        # Should include all character names + explicit pool, deduped + sorted.
        assert "林奚" in draft.naming_pool
        assert "司徒霆" in draft.naming_pool
        assert "老王" in draft.naming_pool
        assert "苏陌" in draft.naming_pool
        assert "陆昭" in draft.naming_pool

    def test_theme_statement_prefers_explicit_field(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={
                "theme_statement": "真正的力量来自承认自己的脆弱",
                "themes": ["alternative theme", "another"],
            },
            world_spec_content={},
            cast_spec_content={},
        )
        assert draft.theme_statement == "真正的力量来自承认自己的脆弱"

    def test_theme_statement_falls_back_to_first_theme(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={"themes": ["成长与代价", "friendship"]},
            world_spec_content={},
            cast_spec_content={},
        )
        assert draft.theme_statement == "成长与代价"

    def test_dramatic_question_extracted(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={"dramatic_question": "她能否原谅自己？"},
            world_spec_content={},
            cast_spec_content={},
        )
        assert draft.dramatic_question == "她能否原谅自己？"

    def test_expected_character_count_defaults_to_character_list_size(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={},
            world_spec_content={},
            cast_spec_content={
                "protagonist": {"name": "林奚", "role": "protagonist"},
                "antagonist": {"name": "司徒霆", "role": "antagonist"},
                "supporting_cast": [
                    {"name": "老王", "role": "mentor"},
                    {"name": "小红", "role": "sidekick"},
                ],
            },
        )
        assert draft.expected_character_count == 4

    def test_malformed_character_dict_is_skipped_not_raised(self) -> None:
        draft = build_draft_from_materialization_content(
            book_spec_content={},
            world_spec_content={},
            cast_spec_content={
                "protagonist": {"name": "林奚", "role": "protagonist"},
                "supporting_cast": [
                    {"description": "no name here"},  # missing required `name`
                    {"name": "老王", "role": "mentor"},
                ],
            },
        )
        # 林奚 + 老王 survive; the malformed dict is silently dropped.
        names = [c.name for c in draft.characters]
        assert "林奚" in names
        assert "老王" in names
        assert len(names) == 2
